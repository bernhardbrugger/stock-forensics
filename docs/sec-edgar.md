# SEC EDGAR provider

Code: `src/stock_forensics/providers/sec_edgar.py` (client), `sec_models.py` (response models), `_cache.py` and `_ratelimit.py`. No API key is needed.

## Setup

The SEC blocks requests without contact information. Set a descriptive User-Agent with an email address:

```bash
export SEC_USER_AGENT="stock-forensics you@example.com"
```

Without it (or without an `@`) the client raises `MissingUserAgentError` with this hint. The library reads environment variables only; a `.env` loader comes with the CLI.

## Usage

```python
from stock_forensics.providers.sec_edgar import SecEdgarClient

with SecEdgarClient() as client:
    company = client.ticker_to_cik("AAPL")  # Company(ticker, cik, name)
    sub = client.get_submissions(company.cik)  # filing index
    for f in sub.filings_of_form("10-K")[:3]:
        print(f.filing_date, f.accession_number, f.document_url)
    facts = client.get_company_facts(company.cik)  # all XBRL facts
    revenue = facts.concept("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")
```

Live demo: `uv run python scripts/demo_sec_edgar.py AAPL` (runs everything twice; the second run makes no network requests).

| Method | Returns |
|---|---|
| `ticker_to_cik(ticker)` | `Company` (ticker, CIK, name). Case-insensitive, `BRK.B` also finds `BRK-B`. Unknown ticker raises `TickerNotFoundError`. |
| `get_submissions(cik)` | `Submissions`: name, SIC code and industry, fiscal year end, and `filings` with form, dates, accession number, 8-K `items` (e.g. `("4.01", "9.01")`) and a link to the document. `filings_of_form("10-K", include_amendments=False)` filters. |
| `get_company_facts(cik)` | `CompanyFacts`: taxonomy, then concept, then unit, then `Fact` (`val` as `Decimal`, `start`, `end`, `fy`, `fp`, `form`, `accn`, `filed`, `frame`). |
| `get_document(url)` | Raw bytes of a filing document (for example `Filing.document_url` of a 10-K). Only `https://www.sec.gov/Archives/edgar/data/...` URLs are accepted. Cached without expiry, because filed documents never change. `providers/ixbrl.py` reads the numbers such a document prints. |

Every result carries a `Source` (provider, endpoint, `retrieved_at`).

## Politeness and reliability

- **Rate limit:** at most 8 requests per second by default (the SEC allows 10), shared by all clients in the process. Cache hits do not use up the budget.
- **Retry:** 429, 5xx and network errors are retried up to 4 times with exponential backoff (1 s, 2 s, 4 s plus a little jitter). A `Retry-After` header is honoured (capped at 60 s). 404 raises `NotFoundError` immediately, other 4xx raise `RequestFailedError` (403 hints at the User-Agent).
- **Cache:** one file per URL in `~/.cache/stock-forensics` (override with `STOCK_FORENSICS_CACHE_DIR` or `cache_dir=`). `ttl` is how long entries stay fresh (default 24 h, `None` = forever). `use_cache=False` skips reading but refreshes the entry (for `--no-cache`). The original fetch time is stored, so `Source.retrieved_at` stays truthful on cache hits. Damaged cache files are ignored and refetched.

## Limitations

- `get_submissions` reads `filings.recent` only. The SEC keeps at least the last year or 1000 filings there, so for companies with many filings (for example banks with thousands of structured-note prospectuses) it can contain only about one 10-K. Older pages are listed in `Submissions.older_files` but not fetched yet; step 6 (red flags) will add that where needed.
- Company facts contain what companies reported, including restated and duplicate values. Picking the right one (annual vs. quarterly, amendments) is step 3.

## Tests and fixtures

No live requests in CI. Tests use `httpx.MockTransport`. `tests/fixtures/sec_edgar/` holds real responses, trimmed (a few tickers, 150 recent filings, four XBRL concepts) by `scripts/record_sec_fixtures.py`. We do not use VCR cassettes because they would record the User-Agent header, and with it your email address, in the public repo. The tests reconcile Apple's FY2023 revenue, net income, total assets and EPS with the 10-K.

Refresh the fixtures with `SEC_USER_AGENT=... uv run python scripts/record_sec_fixtures.py` (3 requests).
