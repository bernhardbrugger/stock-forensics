"""Statements built from real SEC company facts of Apple, Microsoft and Walmart.

The fixtures (tests/fixtures/sec_edgar/statements) are real responses, cut down by
scripts/record_statement_fixtures.py. The three companies use different tags and fiscal
calendars: Apple has 52/53-week years ending in September, Microsoft ends in June, Walmart
ends in January and reports "Revenues".

RECONCILIATION: the expected figures below (in USD millions, as printed in the 10-Ks) were
verified against the actual 10-K documents with ``scripts/reconcile_statements.py``, which
reads the inline-XBRL numbers of each filing. All 36 checks (3 companies x 3 fiscal years x
4 figures) matched.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path

import pytest

from stock_forensics.models import Source
from stock_forensics.normalize.concepts import FIELDS, Statement
from stock_forensics.normalize.statements import Statements, build_statements
from stock_forensics.providers.sec_models import parse_company_facts

FIXTURES = Path(__file__).parent / "fixtures" / "sec_edgar" / "statements"
SOURCE = Source(
    provider="sec_edgar",
    endpoint="https://data.sec.gov/api/xbrl/companyfacts/CIK-fixture.json",
    retrieved_at=datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
)
MILLION = Decimal(1_000_000)

# (company, fiscal year): revenue, net income, total assets, operating cash flow in USD millions
TEN_K = {
    ("aapl", 2024): (391_035, 93_736, 364_980, 118_254),
    ("aapl", 2025): (416_161, 112_010, 359_241, 111_482),
    ("aapl", 2026): (478_834, 132_175, 404_548, 145_097),
    ("msft", 2024): (245_122, 88_136, 512_163, 118_548),
    ("msft", 2025): (281_724, 101_832, 619_003, 136_162),
    ("msft", 2026): (331_839, 133_749, 758_376, 182_935),
    ("wmt", 2024): (648_125, 15_511, 252_399, 35_726),
    ("wmt", 2025): (680_985, 19_436, 260_823, 36_443),
    ("wmt", 2026): (713_163, 21_893, 284_668, 41_565),
}
CORE = ("revenue", "net_income", "total_assets", "operating_cash_flow")


@cache
def statements(company: str) -> Statements:
    raw = json.loads(
        (FIXTURES / f"{company}_facts.json").read_text(encoding="utf-8"), parse_float=Decimal
    )
    return build_statements(parse_company_facts(raw, SOURCE), quarters=40)


def millions(statements_: Statements, name: str, fy: int) -> Decimal | None:
    value = statements_.annual(name, fy).value
    return None if value is None else value / MILLION


# --- reconciliation with the 10-Ks ------------------------------------------------------


@pytest.mark.parametrize(("company", "fy"), sorted(TEN_K))
def test_core_figures_match_the_10k(company: str, fy: int) -> None:
    st = statements(company)
    for name, expected in zip(CORE, TEN_K[(company, fy)], strict=True):
        assert millions(st, name, fy) == Decimal(expected), f"{company} FY{fy} {name}"
        value = st.annual(name, fy)
        assert value.source.form_type in {"10-K", "10-K/A"}
        assert value.source.accession_number is not None
        assert value.source.xbrl_concept is not None


# --- different tags and fiscal calendars ------------------------------------------------


def test_companies_use_different_revenue_concepts() -> None:
    assert statements("aapl").annual("revenue", 2026).source.xbrl_concept == (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert statements("wmt").annual("revenue", 2026).source.xbrl_concept == "us-gaap:Revenues"


def test_fiscal_years_follow_each_companys_own_calendar() -> None:
    apple = statements("aapl").annual("revenue", 2023)  # 52/53-week year, 370 days
    assert (apple.period.start, apple.period.end) == (date(2022, 9, 25), date(2023, 9, 30))
    msft = statements("msft").annual("revenue", 2024)
    assert (msft.period.start, msft.period.end) == (date(2023, 7, 1), date(2024, 6, 30))
    walmart = statements("wmt").annual("revenue", 2024)  # fiscal 2024 ended January 2024
    assert (walmart.period.start, walmart.period.end) == (date(2023, 2, 1), date(2024, 1, 31))


def test_latest_years_and_all_fields_are_present() -> None:
    for company in ("aapl", "msft", "wmt"):
        st = statements(company)
        assert st.latest_fiscal_year == 2026
        assert set(st.fiscal_years) >= {2023, 2024, 2025, 2026}
        assert set(st.series) == {spec.name for spec in FIELDS}
        assert {s.statement for s in st.series.values()} == set(Statement)


# --- derived quarters and TTM -----------------------------------------------------------


def test_apple_fiscal_2025_quarters() -> None:
    q = statements("aapl").get("revenue").quarterly
    assert [(q[f"2025Q{i}"].value or 0) / MILLION for i in (1, 2, 3, 4)] == [
        Decimal(124_300),
        Decimal(95_359),
        Decimal(94_036),
        Decimal(102_466),  # Q4 is not reported in a 10-Q: derived as FY - 9 months
    ]
    assert q["2025Q4"].note is not None
    assert q["2025Q4"].note.startswith("derived: FY ")


@pytest.mark.parametrize(
    "name", ["revenue", "net_income", "operating_cash_flow", "cost_of_revenue"]
)
@pytest.mark.parametrize("company", ["aapl", "msft", "wmt"])
def test_four_quarters_add_up_to_the_fiscal_year(company: str, name: str) -> None:
    st = statements(company)
    series = st.get(name)
    checked = 0
    for fy in st.fiscal_years:
        quarters = [series.quarterly.get(f"{fy}Q{i}") for i in (1, 2, 3, 4)]
        if any(q is None or q.value is None for q in quarters):
            continue
        total = sum((q.value for q in quarters if q and q.value is not None), Decimal(0))
        assert total == series.annual[fy].value, f"{company} {name} FY{fy}"
        checked += 1
    assert checked >= 2


def test_ttm_equals_the_year_when_the_latest_period_is_a_year_end() -> None:
    st = statements("aapl")
    assert st.quarters[-1] == "2026Q4"
    ttm = st.get("revenue").ttm
    assert ttm is not None
    assert ttm.value == st.annual("revenue", 2026).value


@pytest.mark.parametrize(("company", "name"), [("msft", "revenue"), ("wmt", "net_income")])
def test_ttm_rolls_the_last_year_forward(company: str, name: str) -> None:
    # TTM = last fiscal year + this year's quarters so far - the same quarters a year earlier
    st = statements(company)
    series = st.get(name)
    latest = st.quarters[-1]
    year, quarter = int(latest[:4]), int(latest[-1])
    assert quarter < 4
    ttm = series.ttm
    assert ttm is not None
    assert ttm.value is not None

    def quarter_value(y: int, q: int) -> Decimal:
        value = series.quarterly[f"{y}Q{q}"].value
        assert value is not None
        return value

    last_year = series.annual[year - 1].value
    assert last_year is not None
    this_year_so_far = sum((quarter_value(year, q) for q in range(1, quarter + 1)), Decimal(0))
    a_year_ago = sum((quarter_value(year - 1, q) for q in range(1, quarter + 1)), Decimal(0))
    assert ttm.value == last_year + this_year_so_far - a_year_ago
    assert ttm.period.end == series.quarterly[latest].period.end
    assert ttm.note is not None
    assert ttm.note.startswith("TTM: sum of last four quarters")


# --- consistency and explicit gaps ------------------------------------------------------


@pytest.mark.parametrize("company", ["aapl", "msft", "wmt"])
def test_balance_sheet_balances(company: str) -> None:
    st = statements(company)
    for fy in st.fiscal_years:
        assets = st.annual("total_assets", fy).value
        both = st.annual("total_liabilities_and_equity", fy).value
        assert assets is not None
        assert assets == both


def test_short_term_debt_components_are_summed_and_labelled() -> None:
    apple = statements("aapl").annual(
        "short_term_debt", 2024
    )  # commercial paper + current term debt
    assert apple.value == Decimal(20_879) * MILLION
    assert apple.note is not None
    assert apple.note.startswith("derived: sum of us-gaap:")


def test_microsoft_sga_is_selling_plus_general_and_administrative() -> None:
    sga = statements("msft").annual("selling_general_admin", 2024)
    assert sga.source.xbrl_concept == (
        "us-gaap:SellingAndMarketingExpense + us-gaap:GeneralAndAdministrativeExpense"
    )
    assert sga.value == Decimal(32_065) * MILLION


def test_gross_profit_is_derived_for_walmart_which_does_not_report_it() -> None:
    st = statements("wmt")
    gross = st.annual("gross_profit", 2024)
    revenue = st.annual("revenue", 2024).value
    cost = st.annual("cost_of_revenue", 2024).value
    assert revenue is not None
    assert cost is not None
    assert gross.value == revenue - cost
    assert gross.source.xbrl_concept == "derived: revenue - cost_of_revenue"


def test_gaps_are_explained_not_filled() -> None:
    apple_interest = statements("aapl").annual("interest_expense", 2026)  # no longer tagged
    assert apple_interest.value is None
    assert apple_interest.note is not None
    assert apple_interest.note.startswith("not reported for this period")

    walmart_rnd = statements("wmt").annual("research_and_development", 2026)
    assert walmart_rnd.value is None
    assert walmart_rnd.note is not None


def test_every_gap_in_every_series_has_a_reason() -> None:
    for company in ("aapl", "msft", "wmt"):
        for series in statements(company).series.values():
            values = [*series.annual.values(), *series.quarterly.values()]
            if series.ttm is not None:
                values.append(series.ttm)
            for v in values:
                assert v.value is not None or (v.note and v.note.strip())
                assert v.source.provider == "sec_edgar"


def test_statements_roundtrip_through_json() -> None:
    st = statements("aapl")
    assert Statements.model_validate_json(st.model_dump_json()) == st
