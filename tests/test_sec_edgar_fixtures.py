"""Tests against real SEC responses (trimmed), recorded with scripts/record_sec_fixtures.py."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from stock_forensics.providers.sec_edgar import TickerNotFoundError
from stock_forensics.providers.sec_models import CompanyFacts, Submissions
from tests.test_sec_edgar import Env, Server

FIXTURES = Path(__file__).parent / "fixtures" / "sec_edgar"


def fixture_response(name: str) -> httpx.Response:
    return httpx.Response(200, content=(FIXTURES / name).read_bytes())


@pytest.fixture
def submissions(tmp_path: Path) -> Submissions:
    env = Env(tmp_path, Server(fixture_response("submissions_aapl.json")))
    return env.client().get_submissions(320193)


@pytest.fixture
def facts(tmp_path: Path) -> CompanyFacts:
    env = Env(tmp_path, Server(fixture_response("companyfacts_aapl.json")))
    return env.client().get_company_facts(320193)


# --- tickers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "cik", "name"),
    [
        ("AAPL", 320193, "Apple Inc."),
        ("msft", 789019, "MICROSOFT CORP"),
        ("BRK.B", 1067983, "BERKSHIRE HATHAWAY INC"),
        ("JPM", 19617, "JPMORGAN CHASE & CO"),
    ],
)
def test_real_ticker_list(tmp_path: Path, ticker: str, cik: int, name: str) -> None:
    env = Env(tmp_path, Server(fixture_response("company_tickers.json")))
    company = env.client().ticker_to_cik(ticker)
    assert (company.cik, company.name) == (cik, name)


def test_real_ticker_list_unknown_ticker(tmp_path: Path) -> None:
    env = Env(tmp_path, Server(fixture_response("company_tickers.json")))
    with pytest.raises(TickerNotFoundError):
        env.client().ticker_to_cik("ZZZZZ")


# --- submissions ------------------------------------------------------------------------


def test_real_submissions_company_info(submissions: Submissions) -> None:
    assert submissions.name == "Apple Inc."
    assert submissions.cik == 320193
    assert submissions.sic_code == "3571"
    assert submissions.industry == "Electronic Computers"
    assert submissions.tickers == ("AAPL",)
    assert len(submissions.filings) == 150
    assert submissions.older_files == ("CIK0000320193-submissions-001.json",)


def test_real_submissions_recent_10ks(submissions: Submissions) -> None:
    tenks = submissions.filings_of_form("10-K")
    assert [f.accession_number for f in tenks] == ["0000320193-26-000026", "0000320193-25-000079"]
    latest = tenks[0]
    assert latest.filing_date == date(2026, 11, 3)
    assert latest.report_date == date(2026, 9, 26)
    assert latest.document_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm"
    )


def test_real_submissions_8k_items(submissions: Submissions) -> None:
    by_accession = {f.accession_number: f for f in submissions.filings}
    assert by_accession["0000320193-26-000024"].items == ("2.02", "9.01")  # earnings release
    assert by_accession["0001140361-26-015711"].items == ("5.02",)
    assert by_accession["0000320193-26-000026"].items == ()  # a 10-K has no items


def test_real_submissions_amendments_are_separate(submissions: Submissions) -> None:
    plain = submissions.filings_of_form("8-K")
    with_amendments = submissions.filings_of_form("8-K", include_amendments=True)
    assert len(with_amendments) > len(plain)
    assert all(f.form == "8-K" for f in plain)


def test_real_submissions_filings_are_newest_first(submissions: Submissions) -> None:
    dates = [f.filing_date for f in submissions.filings]
    assert dates == sorted(dates, reverse=True)


# --- company facts: reconciliation with Apple's FY2023 10-K -------------------------------

FY2023_10K = "0000320193-23-000106"
FY2023_END = date(2023, 9, 30)


@pytest.mark.parametrize(
    ("concept", "unit", "expected"),
    [
        # Figures as printed in Apple's 10-K for the fiscal year ended Sep 30, 2023
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", Decimal("383285000000")),
        ("NetIncomeLoss", "USD", Decimal("96995000000")),
        ("Assets", "USD", Decimal("352583000000")),
        ("EarningsPerShareBasic", "USD/shares", Decimal("6.16")),
    ],
)
def test_real_facts_match_the_fy2023_10k(
    facts: CompanyFacts, concept: str, unit: str, expected: Decimal
) -> None:
    concept_facts = facts.concept("us-gaap", concept)
    assert concept_facts is not None
    matches = [
        f
        for f in concept_facts.units[unit]
        if f.accn == FY2023_10K and f.end == FY2023_END and f.form == "10-K"
    ]
    assert matches, f"{concept} for FY2023 not found"
    assert {f.val for f in matches} == {expected}


def test_real_facts_carry_provenance_fields(facts: CompanyFacts) -> None:
    assets = facts.concept("us-gaap", "Assets")
    assert assets is not None
    fact = next(f for f in assets.units["USD"] if f.accn == FY2023_10K and f.end == FY2023_END)
    assert fact.fy == 2023
    assert fact.fp == "FY"
    assert fact.filed == date(2023, 11, 3)
    assert fact.start is None  # balance sheet items are point-in-time


def test_real_facts_duration_items_have_a_start_date(facts: CompanyFacts) -> None:
    revenue = facts.concept("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")
    assert revenue is not None
    annual = [f for f in revenue.units["USD"] if f.accn == FY2023_10K and f.end == FY2023_END]
    assert annual
    assert all(f.start is not None for f in annual)


def test_real_facts_entity_info(facts: CompanyFacts) -> None:
    assert facts.entity_name == "Apple Inc."
    assert facts.cik == 320193
    shares = facts.concept("dei", "EntityCommonStockSharesOutstanding")
    assert shares is not None
    assert "shares" in shares.units
