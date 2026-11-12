"""The filings behind the numbers of a report, so that every figure can be checked.

The list is built from the provenance (:class:`~stock_forensics.models.Source`) that every value
already carries: statement values name the 10-K or 10-Q they come from, red flags name their
8-K, NT or 10-K filing. Form 4 filings are not listed here, because each insider transaction
links to its own Form 4.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from stock_forensics.flags.models import RedFlags
from stock_forensics.models import Source, Value
from stock_forensics.normalize.statements import Statements
from stock_forensics.providers.sec_edgar import ARCHIVES_PREFIX
from stock_forensics.report.models import SourceEntry

STATEMENTS = "financial statements"
RED_FLAGS = "red flags"


def filing_url(cik: int, accession_number: str, endpoint: str | None = None) -> str:
    """The document URL if the source names one, else the filing index on sec.gov."""
    if endpoint and endpoint.startswith(ARCHIVES_PREFIX):
        return endpoint
    folder = accession_number.replace("-", "")
    return f"{ARCHIVES_PREFIX}{cik}/{folder}/{accession_number}-index.htm"


def _walk(value: Value) -> Iterator[Value]:
    yield value
    for item in value.inputs:
        yield from _walk(item)


def _statement_values(statements: Statements) -> Iterator[Value]:
    for series in statements.series.values():
        yield from series.annual.values()
        yield from series.quarterly.values()
        if series.ttm is not None:
            yield series.ttm


def collect_sources(
    cik: int, statements: Statements | None, red_flags: RedFlags | None
) -> tuple[SourceEntry, ...]:
    """All filings the statements and red flags are based on, newest period first."""
    found: dict[str, tuple[Source, date | None, set[str]]] = {}

    def add(source: Source, period_end: date | None, used_for: str) -> None:
        accession = source.accession_number
        if accession is None:
            return
        known = found.get(accession)
        if known is None:
            found[accession] = (source, period_end, {used_for})
            return
        known_source, known_end, uses = known
        uses.add(used_for)
        if period_end is not None and (known_end is None or period_end > known_end):
            found[accession] = (known_source, period_end, uses)

    if statements is not None:
        for top in _statement_values(statements):
            for value in _walk(top):
                add(value.source, value.period.end, STATEMENTS)
    if red_flags is not None:
        for flag in red_flags.flags:
            add(flag.source, flag.period, RED_FLAGS)

    entries = [
        SourceEntry(
            accession_number=accession,
            form_type=source.form_type,
            period_end=period_end,
            url=filing_url(cik, accession, source.endpoint),
            used_for=tuple(sorted(uses)),
        )
        for accession, (source, period_end, uses) in found.items()
    ]
    entries.sort(key=lambda e: (e.period_end or date.min, e.accession_number), reverse=True)
    return tuple(entries)
