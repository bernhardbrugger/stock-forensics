"""SEC EDGAR provider: polite, cached access to ticker, filing and XBRL data.

Rules from CLAUDE.md: always send a descriptive User-Agent with contact info
(``SEC_USER_AGENT``), stay below 10 requests per second (shared rate limiter), cache
responses on disk. No API key is needed.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from stock_forensics.models import Company, Source
from stock_forensics.providers._cache import CacheEntry, DiskCache
from stock_forensics.providers._ratelimit import RateLimiter, shared_limiter
from stock_forensics.providers.sec_models import (
    CompanyFacts,
    Submissions,
    parse_company_facts,
    parse_submissions,
)

USER_AGENT_ENV = "SEC_USER_AGENT"
PROVIDER = "sec_edgar"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_MAX_RETRY_AFTER_SECONDS = 60.0


class SecEdgarError(Exception):
    """Base class for all errors raised by this provider."""


class MissingUserAgentError(SecEdgarError):
    """No usable User-Agent: the SEC blocks requests without contact information."""


class TickerNotFoundError(SecEdgarError):
    """The ticker is not in the SEC ticker list."""


class NotFoundError(SecEdgarError):
    """The SEC answered 404 for the requested resource."""


class RequestFailedError(SecEdgarError):
    """The request failed permanently or kept failing after all retries."""


def _resolve_user_agent(user_agent: str | None) -> str:
    value = (user_agent if user_agent is not None else os.environ.get(USER_AGENT_ENV, "")).strip()
    if not value or "@" not in value:
        raise MissingUserAgentError(
            f"{USER_AGENT_ENV} is not set or has no contact address. The SEC blocks requests "
            f'without it. Set it to something like "stock-forensics you@example.com".'
        )
    return value


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return None  # HTTP-date form is not worth supporting here


class SecEdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        *,
        cache_dir: Path | None = None,
        ttl: timedelta | None = timedelta(hours=24),
        use_cache: bool = True,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        backoff_base: float = 1.0,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        cache: DiskCache | None = None,
    ) -> None:
        """
        ``ttl`` is how long cached responses stay fresh (``None`` = forever).
        ``use_cache=False`` skips reading the cache but still refreshes it.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._user_agent = _resolve_user_agent(user_agent)
        self._ttl = ttl
        self._use_cache = use_cache
        self._cache = cache if cache is not None else DiskCache(cache_dir)
        self._limiter = rate_limiter if rate_limiter is not None else shared_limiter()
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._jitter = jitter
        self._http = httpx.Client(
            headers={"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- public API ---------------------------------------------------------------------

    def ticker_to_cik(self, ticker: str) -> Company:
        """Look up a ticker in the SEC ticker list (``company_tickers.json``).

        Returns a ``Company`` with ticker, CIK and name. SIC code and industry come from
        ``get_submissions(...).to_company(ticker)``.
        """
        wanted = ticker.strip().upper().replace(".", "-")  # SEC writes BRK-B, not BRK.B
        data, _ = self._get_json(TICKERS_URL)
        for entry in data.values():
            if str(entry["ticker"]).upper() == wanted:
                return Company(ticker=wanted, cik=int(entry["cik_str"]), name=entry["title"])
        raise TickerNotFoundError(f"Ticker {ticker!r} is not in the SEC ticker list.")

    def get_submissions(self, cik: int) -> Submissions:
        """Filing index of a company, including 8-K items."""
        data, source = self._get_json(SUBMISSIONS_URL.format(cik=_pad(cik)))
        return parse_submissions(data, source)

    def get_company_facts(self, cik: int) -> CompanyFacts:
        """All XBRL facts a company has ever reported."""
        data, source = self._get_json(COMPANY_FACTS_URL.format(cik=_pad(cik)))
        return parse_company_facts(data, source)

    # --- internals ----------------------------------------------------------------------

    def _get_json(self, url: str) -> tuple[dict[str, Any], Source]:
        entry = self._fetch(url, allow_cache=self._use_cache)
        try:
            data = self._loads(entry)
        except ValueError:
            # A damaged cache entry must not stick: fetch once more, bypassing the cache.
            entry = self._fetch(url, allow_cache=False)
            try:
                data = self._loads(entry)
            except ValueError as exc:
                raise SecEdgarError(f"{url} did not return valid JSON.") from exc
        source = Source(
            provider=PROVIDER,
            endpoint=url,
            retrieved_at=entry.fetched_at,
        )
        return data, source

    @staticmethod
    def _loads(entry: CacheEntry) -> dict[str, Any]:
        data = json.loads(entry.body, parse_float=Decimal)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def _fetch(self, url: str, *, allow_cache: bool) -> CacheEntry:
        if allow_cache:
            cached = self._cache.get(url, self._ttl)
            if cached is not None:
                return cached
        return self._cache.put(url, self._download(url))

    def _download(self, url: str) -> bytes:
        last_problem = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            self._limiter.acquire()
            delay: float | None = None
            try:
                response = self._http.get(url)
            except httpx.TransportError as exc:
                last_problem = f"network error: {exc}"
            else:
                status = response.status_code
                if status == 200:
                    return response.content
                if status == 404:
                    raise NotFoundError(f"SEC answered 404 for {url}")
                if status == 429 or status >= 500:
                    last_problem = f"HTTP {status}"
                    delay = _retry_after_seconds(response)
                else:
                    hint = " Check SEC_USER_AGENT." if status == 403 else ""
                    raise RequestFailedError(f"HTTP {status} for {url}.{hint}")
            if attempt < self._max_attempts:
                if delay is None:
                    delay = self._backoff_base * 2 ** (attempt - 1)
                    delay += self._jitter() * self._backoff_base * 0.25
                self._sleep(delay)
        raise RequestFailedError(
            f"Giving up on {url} after {self._max_attempts} attempts ({last_problem})."
        )


def _pad(cik: int) -> str:
    if cik <= 0:
        raise ValueError("cik must be a positive integer")
    return f"{cik:010d}"
