"""Prices from Alpha Vantage (``TIME_SERIES_DAILY``), with an API key from the environment.

Limits of the free key, per the Alpha Vantage documentation: only ``outputsize=compact`` (the
latest 100 trading days) is free, and the quota is small (25 requests per day). Longer history
and the split-adjusted endpoint are premium. With a free key the technicals that need a year of
prices therefore show "n/a" with the reason, and market values of older fiscal years are gaps.

The close of ``TIME_SERIES_DAILY`` is documented as the raw, as-traded price. It is therefore
treated as *not* split-adjusted (``PriceSeries.split_adjusted=False``): the market value then
pairs the as-traded price with the share count of the same date, and the technicals refuse to
run across a jump that looks like a split. This provider has not been tried live by the project
(no key); see ``docs/market.md``.

The API key is sent as a query parameter, as the service requires, and is never part of the
cache key, of an error message or of the report.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from stock_forensics.models import Source
from stock_forensics.providers._cache import DiskCache
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


class AlphaVantageProvider:
    name = "alphavantage"
    benchmark = "SPY"  # the S&P 500 index itself is not available; the SPY ETF tracks it

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        cache_dir: Path | None = None,
        ttl: timedelta | None = timedelta(hours=12),
        use_cache: bool = True,
        timeout: float = 30.0,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get(ALPHAVANTAGE_KEY_ENV, "")).strip()
        if not key:
            raise PriceUnavailableError(
                f"Alpha Vantage needs an API key in the environment variable {ALPHAVANTAGE_KEY_ENV}"
            )
        self._key = key
        self._cache = DiskCache(cache_dir)
        self._ttl = ttl
        self._use_cache = use_cache
        self._http = httpx.Client(transport=transport, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
        symbol = ticker.upper().replace(".", "-")
        # The cache key leaves out the API key on purpose.
        cache_url = f"{BASE_URL}?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize=compact"
        entry = self._cache.get(cache_url, self._ttl) if self._use_cache else None
        if entry is None:
            body = self._download(symbol)
            _parse(body)  # never cache an error response
            entry = self._cache.put(cache_url, body)
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
                endpoint=f"{BASE_URL}?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize=compact",
                retrieved_at=entry.fetched_at.astimezone(UTC),
            ),
        )

    def _download(self, symbol: str) -> bytes:
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": self._key,
        }
        try:
            response = self._http.get(BASE_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as error:
            # The error text of httpx contains the URL, and with it the key: do not pass it on.
            raise PriceError(f"Alpha Vantage request failed ({type(error).__name__})") from None
        return response.content


def _parse(body: bytes) -> list[PriceBar]:
    try:
        data: Any = json.loads(body)
    except ValueError:
        raise PriceError("Alpha Vantage sent something that is not JSON") from None
    if not isinstance(data, dict) or SERIES_KEY not in data:
        message = next(
            (str(data[k]) for k in ("Error Message", "Information", "Note") if k in data), None
        )
        raise PriceError(f"Alpha Vantage: {message or 'unexpected response'}")
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
