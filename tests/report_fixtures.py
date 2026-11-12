"""A fake SEC EDGAR for the report tests, served from the recorded fixtures.

Apple's real data (company facts, filing index, 54 raw Form 4 XML files) answers the same URLs
as sec.gov, so the whole pipeline and the CLI run end to end without network access.
``AS_OF`` is the date the Form 4 fixtures were recorded for.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import httpx

from stock_forensics.providers._ratelimit import RateLimiter
from stock_forensics.providers.sec_edgar import SecEdgarClient

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 11, 12)
USER_AGENT = "stock-forensics-tests tests@example.invalid"
AAPL_CIK = 320193


@cache
def _load(*parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


def aapl_submissions() -> dict[str, Any]:
    """Apple's filing index with its 10-K/10-Q history and the Form 4 filings of the last year."""
    index = json.loads(json.dumps(_load("flags", "submissions_aapl.json")))
    insiders = _load("insiders", "aapl", "submissions.json")["filings"]["recent"]
    recent = index["filings"]["recent"]
    for column, values in recent.items():
        values.extend(insiders[column])
    index["filings"]["files"] = []  # the older index pages are not needed for the last 5 years
    return index  # type: ignore[no-any-return]


def _accession(folder: str) -> str:
    return f"{folder[:10]}-{folder[10:12]}-{folder[12:]}"


class FakeSec:
    """Handler for ``httpx.MockTransport``; ``fail`` makes paths containing a text fail."""

    def __init__(self, *, fail: dict[str, int] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = fail or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for part, status in self.fail.items():
            if part in url:
                return httpx.Response(status)
        path = request.url.path
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json=_load("sec_edgar", "company_tickers.json"))
        if path == f"/submissions/CIK{AAPL_CIK:010d}.json":
            return httpx.Response(200, json=aapl_submissions())
        if path == f"/api/xbrl/companyfacts/CIK{AAPL_CIK:010d}.json":
            return httpx.Response(200, json=_load("sec_edgar", "statements", "aapl_facts.json"))
        if path.startswith("/Archives/edgar/data/") and path.endswith(".xml"):
            folder = path.split("/")[5]
            xml = FIXTURES / "insiders" / "aapl" / f"{_accession(folder)}.xml"
            if xml.exists():
                return httpx.Response(200, content=xml.read_bytes())
        return httpx.Response(404)


def make_client(cache_dir: Path, fake: Callable[[httpx.Request], httpx.Response]) -> SecEdgarClient:
    return SecEdgarClient(
        USER_AGENT,
        cache_dir=cache_dir,
        rate_limiter=RateLimiter(1_000_000),
        transport=httpx.MockTransport(fake),
        max_attempts=1,
        sleep=lambda _: None,
    )
