"""The pipeline end to end on Apple's recorded data, with a fake SEC on the wire."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from stock_forensics.providers.sec_edgar import (
    RequestFailedError,
    SecEdgarError,
    TickerNotFoundError,
)
from stock_forensics.report import ForensicReport, analyze
from stock_forensics.report.models import Light
from stock_forensics.report.sources import filing_url
from tests.report_fixtures import AAPL_CIK, AS_OF, USER_AGENT, FakeSec, make_client

ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def test_report_identity(aapl_report: ForensicReport) -> None:
    r = aapl_report
    assert (r.ticker, r.cik, r.company_name) == ("AAPL", AAPL_CIK, "Apple Inc.")
    assert r.sic_code == "3571"
    assert r.industry == "Electronic Computers"
    assert r.fiscal_year_end == "0926"
    assert r.as_of == AS_OF
    assert r.errors == {}


def test_every_section_is_present(aapl_report: ForensicReport) -> None:
    r = aapl_report
    assert r.statements is not None and r.statements.latest_fiscal_year == 2026
    assert r.metrics is not None and len(r.metrics.series) == 25
    assert r.scores is not None and set(r.scores.results) == {
        "beneish_m",
        "altman_z",
        "piotroski_f",
        "accruals_ratio",
    }
    assert r.red_flags is not None and r.red_flags.as_of == AS_OF
    assert r.insiders is not None and r.insiders.totals.sales.count == 47
    assert r.summary.overall is Light.YELLOW


def test_figures_match_the_10k(aapl_report: ForensicReport) -> None:
    """Revenue and net income of fiscal 2026, as printed in the 10-K (USD millions)."""
    assert aapl_report.statements is not None
    revenue = aapl_report.statements.annual("revenue", 2026)
    income = aapl_report.statements.annual("net_income", 2026)
    assert revenue.value is not None and revenue.value // 1_000_000 == 478_834
    assert income.value is not None and income.value // 1_000_000 == 132_175
    assert revenue.source.accession_number == "0000320193-26-000026"


def test_progress_is_reported_in_order(tmp_path: Path) -> None:
    steps: list[tuple[int, int, str]] = []
    with make_client(tmp_path, FakeSec()) as client:
        analyze("AAPL", client=client, as_of=AS_OF, progress=lambda *a: steps.append(a))
    assert [s[0] for s in steps] == [1, 2, 3, 4, 5, 6, 7]
    assert {s[1] for s in steps} == {7}
    assert all(s[2] for s in steps)


def test_requests_are_few_polite_and_only_to_the_sec(tmp_path: Path) -> None:
    fake = FakeSec()
    with make_client(tmp_path, fake) as client:
        analyze("AAPL", client=client, as_of=AS_OF)
    hosts = {r.url.host for r in fake.requests}
    assert hosts <= {"www.sec.gov", "data.sec.gov"}
    assert {r.headers["User-Agent"] for r in fake.requests} == {USER_AGENT}
    # The filing index is shared by red flags and insiders: loaded once.
    assert sum("/submissions/" in str(r.url) for r in fake.requests) == 1
    assert sum("companyfacts" in str(r.url) for r in fake.requests) == 1


def test_second_run_is_served_from_the_cache(tmp_path: Path, aapl_report: ForensicReport) -> None:
    first = FakeSec()
    with make_client(tmp_path, first) as client:
        analyze("AAPL", client=client, as_of=AS_OF)
    second = FakeSec()
    with make_client(tmp_path, second) as client:
        again = analyze("AAPL", client=client, as_of=AS_OF)
    # Unreadable 10-K documents (404) are not cached, everything else is.
    assert len(second.requests) < len(first.requests) / 4
    assert again.summary == aapl_report.summary


def test_unknown_ticker_stops_the_run(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeSec()) as client, pytest.raises(TickerNotFoundError):
        analyze("NOSUCH", client=client, as_of=AS_OF)


def test_unreadable_filing_index_stops_the_run(tmp_path: Path) -> None:
    fake = FakeSec(fail={"/submissions/": 500})
    with make_client(tmp_path, fake) as client, pytest.raises(SecEdgarError):
        analyze("AAPL", client=client, as_of=AS_OF)


def test_missing_statements_leave_the_other_sections_intact(tmp_path: Path) -> None:
    fake = FakeSec(fail={"companyfacts": 500})
    with make_client(tmp_path, fake) as client:
        r = analyze("AAPL", client=client, as_of=AS_OF)
    assert (r.statements, r.metrics, r.scores) == (None, None, None)
    assert set(r.errors) == {"statements", "metrics", "scores"}
    assert RequestFailedError.__name__ in r.errors["statements"]
    assert "missing" in r.errors["scores"]
    assert r.red_flags is not None and r.insiders is not None
    # Without scores the light comes from the red flags alone.
    assert r.summary.overall is Light.GREEN
    scores_area = r.summary.areas[0]
    assert scores_area.light is Light.GRAY and "not available" in scores_area.summary
    assert "sections missing" in r.summary.areas[-1].summary


def test_unreadable_form_4s_are_counted_not_hidden(tmp_path: Path) -> None:
    fake = FakeSec(fail={".xml": 500})
    with make_client(tmp_path, fake) as client:
        r = analyze("AAPL", client=client, as_of=AS_OF)
    assert r.insiders is not None
    assert r.insiders.totals.sales.count == 0
    assert len(r.insiders.coverage.unreadable) == r.insiders.coverage.filings_listed > 0


def test_sources_list_the_filings_behind_the_numbers(aapl_report: ForensicReport) -> None:
    sources = aapl_report.sources
    assert sources, "statement values name their filings"
    accessions = [s.accession_number for s in sources]
    assert len(set(accessions)) == len(accessions)
    assert all(ACCESSION.match(a) for a in accessions)
    assert all(s.url.startswith("https://www.sec.gov/Archives/edgar/data/320193/") for s in sources)
    ends = [s.period_end or date.min for s in sources]
    assert ends == sorted(ends, reverse=True)
    latest = sources[0]
    assert (latest.accession_number, latest.form_type) == ("0000320193-26-000026", "10-K")
    assert latest.used_for == ("financial statements",)


def test_filing_url_prefers_the_document_and_falls_back_to_the_index() -> None:
    doc = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm"
    assert filing_url(320193, "0000320193-26-000026", doc) == doc
    assert filing_url(320193, "0000320193-26-000026", "https://data.sec.gov/api/x.json") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/"
        "0000320193-26-000026-index.htm"
    )
    assert filing_url(1, "0000000001-26-000001") == (
        "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm"
    )
