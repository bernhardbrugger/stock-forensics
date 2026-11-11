"""Live demo of the SEC EDGAR provider (makes real requests).

Usage:
    SEC_USER_AGENT="stock-forensics you@example.com" uv run python scripts/demo_sec_edgar.py AAPL

Prints CIK, name and the most recent 10-Ks. Everything is fetched twice to show that the
second round is served from the disk cache (no network requests).
"""

from __future__ import annotations

import sys
import time

import httpx

from stock_forensics.providers.sec_edgar import SecEdgarClient


class CountingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self._inner = httpx.HTTPTransport()
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return self._inner.handle_request(request)


def run_once(client: SecEdgarClient, ticker: str) -> None:
    company = client.ticker_to_cik(ticker)
    submissions = client.get_submissions(company.cik)
    print(f"{company.ticker}: CIK {company.cik_padded}, {submissions.name}")
    print(f"  SIC {submissions.sic_code} ({submissions.industry})")
    for filing in submissions.filings_of_form("10-K")[:5]:
        print(
            f"  10-K  filed {filing.filing_date}  {filing.accession_number}  {filing.document_url}"
        )


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    transport = CountingTransport()
    with SecEdgarClient(transport=transport) as client:
        for label in ("first run", "second run"):
            before = transport.requests
            start = time.perf_counter()
            print(f"--- {label} ---")
            run_once(client, ticker)
            elapsed = time.perf_counter() - start
            print(f"  network requests: {transport.requests - before}, took {elapsed:.2f}s")


if __name__ == "__main__":
    main()
