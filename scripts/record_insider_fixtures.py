"""Record real Form 4 filings as fixtures for the insider tests (makes live requests).

Usage:
    SEC_USER_AGENT="stock-forensics you@example.com" \
        uv run python scripts/record_insider_fixtures.py

Writes to tests/fixtures/insiders/<case>/:
  submissions.json   the filing index of the company, cut down to Form 4 and 4/A in the period
  <accession>.xml    the raw Form 4 XML, verbatim

The cases cover cluster buys (UnitedHealth), a CEO purchase (Intel), planned sales, tax
withholding, grants, gifts and option exercises (Apple), joint filings of a 10% owner and filings
that the company made about another issuer (Occidental / Berkshire Hathaway), and an amendment
(Microsoft, one reporting person). At the end the result computed from the fixtures is compared
with a live run.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from stock_forensics.insiders import analyze_filings, get_insider_activity
from stock_forensics.insiders.aggregate import months_before
from stock_forensics.insiders.form4 import parse_form4, raw_xml_url
from stock_forensics.providers.sec_edgar import SecEdgarClient
from stock_forensics.providers.sec_models import Filing, parse_submissions

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "insiders"
COLUMNS = ("accessionNumber", "filingDate", "reportDate", "form", "items", "primaryDocument")

# case -> (cik, as_of, months, only filings of this reporting person)
CASES: dict[str, tuple[int, date, int, int | None]] = {
    "aapl": (320193, date(2026, 11, 12), 12, None),
    "unh": (731766, date(2025, 6, 20), 2, None),
    "intc": (50863, date(2024, 9, 30), 2, None),
    "oxy": (797468, date(2024, 12, 31), 7, None),
    "msft_amendment": (789019, date(2024, 12, 31), 1, 1819285),  # Alice Jolla, 4/A on 2024-12-02
}


def summary(activity: Any) -> tuple[Any, ...]:
    t = activity.totals
    return (
        len(activity.transactions),
        t.purchases.count,
        t.sales.count,
        t.planned_sales.count,
        t.purchases.amount.value,
        t.sales.amount.value,
        len(activity.clusters),
        activity.coverage.filings_read,
        activity.coverage.superseded_filings,
    )


def main() -> None:
    with SecEdgarClient() as client:
        for key, (cik, as_of, months, only_owner) in CASES.items():
            since = months_before(as_of, months)
            history = client.get_filing_history(cik, since)
            chosen: list[Filing] = [
                f
                for f in history.filings
                if f.form in ("4", "4/A") and since <= f.filing_date <= as_of
            ]
            xmls: dict[str, bytes] = {}
            for f in chosen:
                url = raw_xml_url(f)
                assert url is not None
                body = client.get_document(url)
                if only_owner is not None:
                    owners = parse_form4(body).owners
                    if not owners or owners[0].cik != only_owner:
                        continue
                xmls[f.accession_number] = body
            chosen = [f for f in chosen if f.accession_number in xmls]

            folder = OUT / key
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("*"):
                old.unlink()
            rows = {
                "accessionNumber": [f.accession_number for f in chosen],
                "filingDate": [f.filing_date.isoformat() for f in chosen],
                "reportDate": [f.report_date.isoformat() if f.report_date else "" for f in chosen],
                "form": [f.form for f in chosen],
                "items": ["" for _ in chosen],
                "primaryDocument": [f.primary_document or "" for f in chosen],
            }
            index = {
                "cik": f"{cik:010d}",
                "name": history.name,
                "tickers": list(history.tickers),
                "filings": {"recent": {c: rows[c] for c in COLUMNS}, "files": []},
            }
            (folder / "submissions.json").write_text(
                json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            for accession, body in xmls.items():
                (folder / f"{accession}.xml").write_bytes(body)
            size = sum(p.stat().st_size for p in folder.iterdir()) / 1024
            print(f"{key}: {len(chosen)} filings, {size:.0f} KiB")

            # offline result from the fixtures must equal the live result
            submissions = parse_submissions(index, history.source)
            offline = analyze_filings(
                submissions,
                lambda f, saved=xmls: saved.get(f.accession_number),
                as_of=as_of,
                months=months,
            )
            if only_owner is None:
                live = get_insider_activity(client, cik, as_of=as_of, months=months)
                same = summary(offline) == summary(live)
                print("   offline == live:", same)
                if not same:
                    print("   offline:", summary(offline), "\n   live:   ", summary(live))


if __name__ == "__main__":
    main()
