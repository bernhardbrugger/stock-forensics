"""Red flags from SEC filings: warning signs that are not in the numbers but in the filings.

* auditor changes (8-K Item 4.01), with who ended the relationship
* restatements (8-K Item 4.02, non-reliance on previously issued financial statements)
* late filings (NT 10-K, NT 10-Q)
* going-concern doubt in the 10-K text
* substantive amendments of annual reports (10-K/A)

Every flag has date, form, a link to the original document and the quoted sentences it is based
on. ``RedFlags.coverage`` says what was examined. See ``docs/red-flags.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from stock_forensics.flags.amendments import check_amendments
from stock_forensics.flags.auditor import check_auditor_changes
from stock_forensics.flags.going_concern import check_going_concern
from stock_forensics.flags.late_filing import check_late_filings
from stock_forensics.flags.models import Coverage, FlagKind, RedFlag, RedFlags, Severity
from stock_forensics.flags.restatement import check_restatements
from stock_forensics.models import Source
from stock_forensics.providers.sec_edgar import SecEdgarClient, SecEdgarError
from stock_forensics.providers.sec_models import Filing, Submissions

DEFAULT_YEARS = 5


def years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # February 29
        return day.replace(year=day.year - years, day=28)


def analyze_filings(
    submissions: Submissions,
    read_text: Callable[[Filing], bytes | str | None],
    *,
    as_of: date,
    since: date,
) -> RedFlags:
    """Run all checks on a filing history. ``read_text`` returns a filing's document or ``None``."""
    filings = submissions.filings
    retrieved_at = submissions.source.retrieved_at

    def source_for(filing: Filing) -> Source:
        return Source(
            provider="sec_edgar",
            endpoint=filing.document_url,
            form_type=filing.form,
            accession_number=filing.accession_number,
            retrieved_at=retrieved_at,
        )

    aggregate_source = Source(provider="stock_forensics", retrieved_at=retrieved_at)

    results = [
        check_auditor_changes(filings, read_text, since=since, source_for=source_for),
        check_restatements(filings, read_text, since=since, source_for=source_for),
        check_late_filings(filings, since=since, source_for=source_for),
        check_going_concern(filings, read_text, since=since, source_for=source_for),
        check_amendments(
            filings,
            read_text,
            since=since,
            source_for=source_for,
            aggregate_source=aggregate_source,
        ),
    ]
    flags: list[RedFlag] = [flag for found, _ in results for flag in found]
    flags.sort(key=lambda f: (f.filing_date, f.accession_number or ""), reverse=True)

    history_note = _history_note(submissions, since)
    coverage = tuple(
        c.model_copy(update={"notes": (*c.notes, history_note)}) if history_note else c
        for _, c in results
    )
    return RedFlags(
        cik=submissions.cik,
        company_name=submissions.name,
        as_of=as_of,
        since=since,
        flags=tuple(flags),
        coverage=coverage,
        source=submissions.source,
    )


def _history_note(submissions: Submissions, since: date) -> str | None:
    unloaded = [p for p in submissions.older_pages if p.filing_to is None or p.filing_to >= since]
    if unloaded:
        return "older filing index pages were not loaded: the history may be incomplete"
    oldest = min((f.filing_date for f in submissions.filings), default=None)
    if oldest is not None and oldest > since:
        return f"the filing history starts on {oldest}, after the start of the examined period"
    return None


def check_red_flags(
    client: SecEdgarClient,
    cik: int,
    *,
    years: int = DEFAULT_YEARS,
    as_of: date | None = None,
) -> RedFlags:
    """Fetch the filing history of a company and check the last ``years`` years for red flags."""
    as_of = as_of or date.today()
    since = years_before(as_of, years)
    history = client.get_filing_history(cik, since)

    def read(filing: Filing) -> bytes | None:
        if filing.document_url is None:
            return None
        try:
            return client.get_document(filing.document_url)
        except SecEdgarError:
            return None

    return analyze_filings(history, read, as_of=as_of, since=since)


__all__ = [
    "Coverage",
    "FlagKind",
    "RedFlag",
    "RedFlags",
    "Severity",
    "analyze_filings",
    "check_red_flags",
]
