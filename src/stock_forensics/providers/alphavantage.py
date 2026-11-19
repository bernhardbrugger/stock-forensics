"""Prices from Alpha Vantage (``TIME_SERIES_DAILY``), with an API key from the environment.

``outputsize=full`` returns the whole history (20+ years), ``compact`` only the latest 100
trading days. Which one a key may use depends on its plan, so the default ``auto`` tries ``full``
first and falls back to ``compact`` if the service refuses it (``ALPHAVANTAGE_OUTPUTSIZE`` or the
``outputsize`` argument can force either). With 100 days everything that needs more history shows
"n/a" with the reason: the 200-day average, weekly RSI and MACD, 12-month figures, beta and the
market value of older fiscal years.

Alpha Vantage allows one request per second and, on the free plan, 25 per day. The provider keeps
a pause of ``min_interval`` seconds between network requests and caches every successful
response for 12 hours, which also saves the daily quota.

The close of ``TIME_SERIES_DAILY`` is the raw, as-traded price, so the series is *not*
split-adjusted (``PriceSeries.split_adjusted=False``): the market value pairs it with the share
count of the same date, and the technicals refuse to run across a jump that looks like a split.
This was checked live against Apple's 4-for-1 split of 2020-08-31 (see ``docs/market.md``).

The API key is sent as a query parameter, as the service requires, and is never part of the
cache key, of an error message or of the report.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from stock_forensics.models import Source
from stock_forensics.providers._cache import CacheEntry, DiskCache
from stock_forensics.providers.prices import (
    ALPHAVANTAGE_KEY_ENV,
    PriceBar,
    PriceError,
    PriceSeries,
    PriceUnavailableError,
)

BASE_URL = "https://www.alphavantage.co/query"
SERIES_KEY = "Time Series (Daily)"
CLOSE_KEY = "4. close"
OUTPUTSIZE_ENV = "ALPHAVANTAGE_OUTPUTSIZE"
OUTPUTSIZES = ("auto", "full", "compact")
MIN_INTERVAL = 1.1  # seconds between requests: the service allows one per second
RATE_LIMIT_WORDS = ("sparingly", "requests per", "request per", "rate limit", "per minute")


class PremiumRequiredError(PriceError):
    """The service says this request (for example ``outputsize=full``) needs a premium plan."""


class RateLimitedError(PriceError):
    """The service says too many requests came in; waiting may help."""


def _refusal(message: str | None) -> PriceError:
    """The right error for an ``Information`` or ``Note`` message of the service."""
    text = message or "unexpected response"
    lowered = text.lower()
    if any(word in lowered for word in RATE_LIMIT_WORDS):
        return RateLimitedError(f"Alpha Vantage: {text}")
    if "premium" in lowered or "outputsize" in lowered:
        return PremiumRequiredError(f"Alpha Vantage: {text}")
    return PriceError(f"Alpha Vantage: {text}")


class AlphaVantageProvider:
    name = "alphavantage"
    benchmark = "SPY"  # the S&P 500 index itself is not available; the SPY ETF tracks it

    def __init__(
        self,
        api_key: str | None = None,
        *,
        outputsize: str | None = None,
        transport: httpx.BaseTransport | None = None,
        cache_dir: Path | None = None,
        ttl: timedelta | None = timedelta(hours=12),
        use_cache: bool = True,
        timeout: float = 60.0,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get(ALPHAVANTAGE_KEY_ENV, "")).strip()
        if not key:
            raise PriceUnavailableError(
                f"Alpha Vantage needs an API key in the environment variable {ALPHAVANTAGE_KEY_ENV}"
            )
        size = (outputsize or os.environ.get(OUTPUTSIZE_ENV, "") or "auto").strip().lower()
        if size not in OUTPUTSIZES:
            raise ValueError(f"outputsize must be one of {OUTPUTSIZES}, not {size!r}")
        self._key = key
        self._outputsize = size
        self._full_refused = False  # set once the service refused "full" for this key
        self._cache = DiskCache(cache_dir)
        self._ttl = ttl
        self._use_cache = use_cache
        self._http = httpx.Client(transport=transport, timeout=timeout)
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_request: float | None = None

    def close(self) -> None:
        self._http.close()

    def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
        symbol = ticker.upper().replace(".", "-")
        entry, size = self._load(symbol)
        bars = [b for b in _parse(entry.body) if start <= b.day <= end]
        if not bars:
            raise PriceError(
                f"Alpha Vantage returned no prices for {symbol} between {start} and {end}"
            )
        return PriceSeries(
            ticker=symbol,
            bars=tuple(bars),
            split_adjusted=False,
            source=Source(
                provider=self.name,
                endpoint=_url(symbol, size),
                retrieved_at=entry.fetched_at.astimezone(UTC),
            ),
        )

    # --- internals ----------------------------------------------------------------------

    def _sizes(self) -> tuple[str, ...]:
        if self._outputsize == "auto":
            return ("compact",) if self._full_refused else ("full", "compact")
        return (self._outputsize,)

    def _load(self, symbol: str) -> tuple[CacheEntry, str]:
        """The response for a symbol, from the cache or the service, and the size it has."""
        error: PriceError | None = None
        for size in self._sizes():
            # The cache key leaves out the API key on purpose.
            cache_url = _url(symbol, size)
            cached = self._cache.get(cache_url, self._ttl) if self._use_cache else None
            if cached is not None:
                return cached, size
            try:
                body = self._fetch_with_one_retry(symbol, size)
            except PriceError as refused:
                error = refused
                if self._outputsize == "auto" and size == "full":
                    if isinstance(refused, PremiumRequiredError):
                        self._full_refused = True  # this key is limited to "compact": stop trying
                    continue  # any other trouble with "full": use "compact" this time only
                raise
            return self._cache.put(cache_url, body), size
        assert error is not None
        raise error

    def _fetch_with_one_retry(self, symbol: str, size: str) -> bytes:
        """Download and check a response; after a rate-limit message wait a little and retry."""
        for attempt in (1, 2):
            body = self._download(symbol, size)
            try:
                _parse(body)  # never cache an error response
            except RateLimitedError:
                if attempt == 2:
                    raise
                self._sleep(2 * self._min_interval)
                continue
            return body
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _download(self, symbol: str, size: str) -> bytes:
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": size,
            "apikey": self._key,
        }
        self._wait_turn()
        try:
            response = self._http.get(BASE_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as error:
            # The error text of httpx contains the URL, and with it the key: do not pass it on.
            raise PriceError(f"Alpha Vantage request failed ({type(error).__name__})") from None
        return response.content


def _url(symbol: str, size: str) -> str:
    return f"{BASE_URL}?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize={size}"


def _parse(body: bytes) -> list[PriceBar]:
    try:
        data: Any = json.loads(body)
    except ValueError:
        raise PriceError("Alpha Vantage sent something that is not JSON") from None
    if not isinstance(data, dict) or SERIES_KEY not in data:
        if isinstance(data, dict) and "Error Message" in data:
            raise PriceError(f"Alpha Vantage: {data['Error Message']}")
        message = None
        if isinstance(data, dict):
            message = next((str(data[k]) for k in ("Information", "Note") if k in data), None)
        raise _refusal(message)
    bars: list[PriceBar] = []
    for day_text, fields in data[SERIES_KEY].items():
        try:
            close = Decimal(str(fields[CLOSE_KEY]))
            day = date.fromisoformat(day_text)
        except (ValueError, KeyError, InvalidOperation, TypeError):
            continue
        if close > 0:
            bars.append(PriceBar(day=day, close=close))
    bars.sort(key=lambda b: b.day)
    return bars
