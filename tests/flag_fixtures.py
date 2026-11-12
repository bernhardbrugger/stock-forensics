"""Helpers to load the recorded red-flag fixtures (tests/fixtures/flags).

The fixtures are real: trimmed SEC filing indexes and verbatim excerpts of the documents that the
checks read, recorded by scripts/record_flag_fixtures.py.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any

from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing, Submissions, parse_submissions

FIXTURES = Path(__file__).parent / "fixtures" / "flags"
AS_OF = date(2026, 11, 12)
YEARS = {
    "smci": 6,
    "khc": 8,
    "wework": 6,
    "tupperware": 6,
    "bbby": 6,
    "spirit": 6,
    "aapl": 6,
    "wmt": 6,
}
CIKS = {
    "smci": 1375365,
    "khc": 1637459,
    "wework": 1813756,
    "tupperware": 1008654,
    "bbby": 886158,
    "spirit": 1498710,
    "aapl": 320193,
    "wmt": 104169,
}
SOURCE = Source(
    provider="sec_edgar",
    endpoint="https://data.sec.gov/submissions/fixture.json",
    retrieved_at=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
)


def raw_submissions(key: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / f"submissions_{key}.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@cache
def submissions(key: str) -> Submissions:
    return parse_submissions(raw_submissions(key), SOURCE)


@cache
def manifest() -> dict[str, dict[str, str]]:
    data = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def excerpt(accession: str) -> str:
    return (FIXTURES / "excerpts" / f"{accession}.txt").read_text(encoding="utf-8")


def find_accession(company: str, form: str, filing_date: str) -> str:
    """The accession number of a recorded excerpt, found by company, form and filing date."""
    matches = [
        accession
        for accession, entry in manifest().items()
        if entry["company"] == company
        and entry["form"] == form
        and entry["filing_date"] == filing_date
    ]
    assert len(matches) == 1, f"{company} {form} {filing_date}: {matches}"
    return matches[0]


def read_excerpt(filing: Filing) -> str | None:
    """A document reader for the offline analysis.

    Annual reports without a recorded excerpt had no going-concern sentence when the fixtures
    were recorded (checked live); other documents without an excerpt are unreadable.
    """
    if filing.accession_number in manifest():
        return excerpt(filing.accession_number)
    return "" if filing.form in ("10-K", "10-KT") else None
