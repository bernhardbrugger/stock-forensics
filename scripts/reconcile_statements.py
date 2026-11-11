"""Reconcile normalized statements with the actual 10-K documents (makes live requests).

Usage:
    SEC_USER_AGENT="stock-forensics you@example.com" \
        uv run python scripts/reconcile_statements.py AAPL MSFT [--years 3]

For the last fiscal years it downloads the 10-K of each year and checks that our normalized
revenue, net income, total assets and operating cash flow appear in the document itself:
the inline-XBRL tags of the filing (``ix:nonFraction``) are read with their displayed
number and scale, so the check compares against what the 10-K prints, not against the API.
Exits with status 1 if anything does not match.
"""

from __future__ import annotations

import argparse
import sys

from stock_forensics.normalize.statements import build_statements
from stock_forensics.providers.ixbrl import inline_xbrl_values
from stock_forensics.providers.sec_edgar import SecEdgarClient

CHECKS = ("revenue", "net_income", "total_assets", "operating_cash_flow")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()

    failures = 0
    with SecEdgarClient() as client:
        for ticker in args.tickers:
            company = client.ticker_to_cik(ticker)
            submissions = client.get_submissions(company.cik)
            statements = build_statements(client.get_company_facts(company.cik))
            tenks = {f.report_date: f for f in submissions.filings_of_form("10-K")}
            print(f"\n{ticker} ({statements.company_name})")
            for fy in statements.fiscal_years[-args.years :]:
                end = statements.annual("total_assets", fy).period.end
                filing = tenks.get(end)
                if filing is None or filing.document_url is None:
                    print(f"  FY{fy}: no 10-K for period ending {end} in the recent filings")
                    failures += 1
                    continue
                html = client.get_document(filing.document_url).decode("utf-8", "replace")
                printed = inline_xbrl_values(html)
                print(f"  FY{fy} (ends {end}, 10-K {filing.accession_number})")
                for name in CHECKS:
                    value = statements.annual(name, fy)
                    concept = value.source.xbrl_concept or ""
                    if value.value is None:
                        print(f"    {name:20s} n/a in our data: {value.note}")
                        failures += 1
                        continue
                    ok = value.value in printed.get(concept, set())
                    failures += 0 if ok else 1
                    status = "OK      " if ok else "MISMATCH"
                    print(f"    {status} {name:20s} {value.value:>20,.0f}  {concept}")
    print("\nAll figures match the 10-K." if failures == 0 else f"\n{failures} problem(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
