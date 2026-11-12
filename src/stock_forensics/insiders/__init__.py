"""Insider activity from SEC Form 4: are the people with the most information buying or selling?

Only open-market purchases (code P) and sales (code S) count as buying and selling. Grants,
option exercises, tax withholding and gifts are listed separately. Sales are split into those
under a Rule 10b5-1 plan and the rest; several insiders buying within a short period are
reported as cluster buys. Every transaction links to its Form 4. See ``docs/insiders.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from stock_forensics.insiders.aggregate import (
    DEFAULT_CLUSTER_DAYS,
    DEFAULT_CLUSTER_INSIDERS,
    analyze_form4s,
    months_before,
)
from stock_forensics.insiders.form4 import Form4Document, Form4ParseError, parse_form4, raw_xml_url
from stock_forensics.insiders.models import (
    Category,
    ClusterBuy,
    Coverage,
    InsiderActivity,
    InsiderTransaction,
    PersonSummary,
    Role,
    Tally,
    Totals,
)
from stock_forensics.providers.sec_edgar import SecEdgarClient, SecEdgarError
from stock_forensics.providers.sec_models import Filing, Submissions

FORMS = ("4", "4/A")
DEFAULT_MONTHS = 12


def analyze_filings(
    submissions: Submissions,
    read_xml: Callable[[Filing], bytes | str | None],
    *,
    as_of: date,
    months: int = DEFAULT_MONTHS,
    cluster_min_insiders: int = DEFAULT_CLUSTER_INSIDERS,
    cluster_days: int = DEFAULT_CLUSTER_DAYS,
) -> InsiderActivity:
    """Insider activity from a filing history; ``read_xml`` returns the raw XML of a Form 4."""
    since = months_before(as_of, months)
    listed = [f for f in submissions.filings if f.form in FORMS and since <= f.filing_date <= as_of]
    items: list[tuple[Filing, Form4Document]] = []
    unreadable: list[str] = []
    other_issuer = 0
    for filing in listed:
        raw = read_xml(filing)
        if raw is None:
            unreadable.append(filing.accession_number)
            continue
        try:
            document = parse_form4(raw)
        except Form4ParseError:
            unreadable.append(filing.accession_number)
            continue
        if document.issuer_cik is not None and document.issuer_cik != submissions.cik:
            # The index also lists filings the company made as an insider of another issuer.
            other_issuer += 1
            continue
        items.append((filing, document))
    return analyze_form4s(
        items,
        cik=submissions.cik,
        company_name=submissions.name,
        ticker=submissions.tickers[0] if submissions.tickers else None,
        as_of=as_of,
        since=since,
        source=submissions.source,
        unreadable=tuple(unreadable),
        filings_listed=len(listed),
        other_issuer_filings=other_issuer,
        cluster_min_insiders=cluster_min_insiders,
        cluster_days=cluster_days,
    )


def get_insider_activity(
    client: SecEdgarClient,
    cik: int,
    *,
    months: int = DEFAULT_MONTHS,
    as_of: date | None = None,
    cluster_min_insiders: int = DEFAULT_CLUSTER_INSIDERS,
    cluster_days: int = DEFAULT_CLUSTER_DAYS,
) -> InsiderActivity:
    """Fetch the Form 4 filings of a company and summarize the last ``months`` months."""
    as_of = as_of or date.today()
    history = client.get_filing_history(cik, months_before(as_of, months))

    def read(filing: Filing) -> bytes | None:
        url = raw_xml_url(filing)
        if url is None:
            return None
        try:
            return client.get_document(url)
        except SecEdgarError:
            return None

    return analyze_filings(
        history,
        read,
        as_of=as_of,
        months=months,
        cluster_min_insiders=cluster_min_insiders,
        cluster_days=cluster_days,
    )


__all__ = [
    "Category",
    "ClusterBuy",
    "Coverage",
    "InsiderActivity",
    "InsiderTransaction",
    "PersonSummary",
    "Role",
    "Tally",
    "Totals",
    "analyze_filings",
    "get_insider_activity",
    "parse_form4",
]
