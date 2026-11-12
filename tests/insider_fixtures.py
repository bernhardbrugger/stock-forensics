"""Helpers to load the recorded Form 4 fixtures (tests/fixtures/insiders).

The fixtures are real: trimmed filing indexes and the verbatim raw XML of the Form 4 filings,
recorded by scripts/record_insider_fixtures.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path

from stock_forensics.insiders import InsiderActivity, analyze_filings
from stock_forensics.models import Source
from stock_forensics.providers.sec_models import Filing, Submissions, parse_submissions

FIXTURES = Path(__file__).parent / "fixtures" / "insiders"
SOURCE = Source(
    provider="sec_edgar",
    endpoint="https://data.sec.gov/submissions/fixture.json",
    retrieved_at=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
)
# case -> (as_of, months)
PERIODS: dict[str, tuple[date, int]] = {
    "aapl": (date(2026, 11, 12), 12),
    "unh": (date(2025, 6, 20), 2),
    "intc": (date(2024, 9, 30), 2),
    "oxy": (date(2024, 12, 31), 7),
    "msft_amendment": (date(2024, 12, 31), 1),
}


@cache
def submissions(case: str) -> Submissions:
    index = json.loads((FIXTURES / case / "submissions.json").read_text(encoding="utf-8"))
    return parse_submissions(index, SOURCE)


def xml(case: str, accession: str) -> bytes:
    return (FIXTURES / case / f"{accession}.xml").read_bytes()


def reader(case: str) -> Callable[[Filing], bytes | None]:
    def read(filing: Filing) -> bytes | None:
        path = FIXTURES / case / f"{filing.accession_number}.xml"
        return path.read_bytes() if path.exists() else None

    return read


@cache
def activity(case: str) -> InsiderActivity:
    as_of, months = PERIODS[case]
    return analyze_filings(submissions(case), reader(case), as_of=as_of, months=months)
