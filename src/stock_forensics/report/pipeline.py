"""The analysis pipeline: ticker in, :class:`ForensicReport` out.

Steps (each reported to ``progress``): resolve the ticker, load the filing index, build the
statements, compute metrics and scores, check the filings for red flags, read the Form 4s,
summarize. A section that fails does not stop the others: it is ``None`` in the report and
``errors`` says why. Only an unknown ticker or an unreadable filing index stops the run, because
nothing meaningful can be said without them.

The filing index is loaded once for red flags and insiders (one history that reaches back far
enough for both), so the SEC is asked for it only once.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from stock_forensics import __version__
from stock_forensics import flags as flags_pkg
from stock_forensics import insiders as insiders_pkg
from stock_forensics.flags import DEFAULT_YEARS, years_before
from stock_forensics.flags.models import RedFlags
from stock_forensics.insiders import DEFAULT_MONTHS
from stock_forensics.insiders.aggregate import months_before
from stock_forensics.insiders.form4 import raw_xml_url
from stock_forensics.insiders.models import InsiderActivity
from stock_forensics.metrics import Metrics, compute_metrics
from stock_forensics.normalize.statements import Statements, build_statements
from stock_forensics.providers.sec_edgar import SecEdgarClient, SecEdgarError
from stock_forensics.providers.sec_models import Filing, Submissions
from stock_forensics.report.models import ForensicReport
from stock_forensics.report.sources import collect_sources
from stock_forensics.report.traffic import summarize
from stock_forensics.scores import Scores, compute_scores

Progress = Callable[[int, int, str], None]  # step number, number of steps, what happens now
TOTAL_STEPS = 7


def _describe(error: Exception) -> str:
    text = str(error).strip()
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def analyze(
    ticker: str,
    *,
    client: SecEdgarClient,
    as_of: date | None = None,
    progress: Progress | None = None,
    years: int = DEFAULT_YEARS,
    months: int = DEFAULT_MONTHS,
) -> ForensicReport:
    """Run the full forensic check on one ticker.

    Raises ``TickerNotFoundError`` for an unknown ticker and ``SecEdgarError`` if the filing
    index cannot be loaded. Everything else ends up in ``ForensicReport.errors``.
    """
    as_of = as_of or date.today()

    def step(number: int, message: str) -> None:
        if progress is not None:
            progress(number, TOTAL_STEPS, message)

    errors: dict[str, str] = {}

    step(1, "Looking up the ticker")
    company = client.ticker_to_cik(ticker)

    step(2, "Loading the filing index")
    since_flags = years_before(as_of, years)
    since_insiders = months_before(as_of, months)
    history = client.get_filing_history(company.cik, min(since_flags, since_insiders))

    step(3, "Building the financial statements")
    statements: Statements | None = None
    try:
        statements = build_statements(client.get_company_facts(company.cik))
    except Exception as error:  # noqa: BLE001 - a failed section must not stop the report
        errors["statements"] = _describe(error)

    step(4, "Computing metrics and forensic scores")
    metrics: Metrics | None = None
    scores: Scores | None = None
    if statements is None:
        errors["metrics"] = errors["scores"] = "needs the financial statements, which are missing"
    else:
        try:
            metrics = compute_metrics(statements)
        except Exception as error:  # noqa: BLE001
            errors["metrics"] = _describe(error)
        try:
            scores = compute_scores(
                statements, sic_code=history.sic_code, industry=history.industry
            )
        except Exception as error:  # noqa: BLE001
            errors["scores"] = _describe(error)

    step(5, "Checking filings for red flags")
    red_flags: RedFlags | None = None
    try:
        red_flags = _red_flags(client, history, as_of, since_flags)
    except Exception as error:  # noqa: BLE001
        errors["red_flags"] = _describe(error)

    step(6, "Reading insider transactions (Form 4)")
    insiders: InsiderActivity | None = None
    try:
        insiders = _insiders(client, history, as_of, months)
    except Exception as error:  # noqa: BLE001
        errors["insiders"] = _describe(error)

    step(7, "Building the report")
    summary = summarize(
        scores=scores,
        metrics=metrics,
        red_flags=red_flags,
        insiders=insiders,
        as_of=as_of,
        errors=errors,
    )
    return ForensicReport(
        ticker=company.ticker,
        company_name=history.name or company.name,
        cik=company.cik,
        sic_code=history.sic_code,
        industry=history.industry,
        fiscal_year_end=history.fiscal_year_end,
        as_of=as_of,
        retrieved_at=history.source.retrieved_at,
        version=__version__,
        statements=statements,
        metrics=metrics,
        scores=scores,
        red_flags=red_flags,
        insiders=insiders,
        summary=summary,
        sources=collect_sources(company.cik, statements, red_flags),
        errors=errors,
    )


def _red_flags(client: SecEdgarClient, history: Submissions, as_of: date, since: date) -> RedFlags:
    def read(filing: Filing) -> bytes | None:
        if filing.document_url is None:
            return None
        try:
            return client.get_document(filing.document_url)
        except SecEdgarError:
            return None

    return flags_pkg.analyze_filings(history, read, as_of=as_of, since=since)


def _insiders(
    client: SecEdgarClient, history: Submissions, as_of: date, months: int
) -> InsiderActivity:
    def read(filing: Filing) -> bytes | None:
        url = raw_xml_url(filing)
        if url is None:
            return None
        try:
            return client.get_document(url)
        except SecEdgarError:
            return None

    return insiders_pkg.analyze_filings(history, read, as_of=as_of, months=months)
