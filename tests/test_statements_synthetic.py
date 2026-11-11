"""Hand-calculated tests for the statements normalization.

A fictional company ("TestCo", fiscal year = calendar year) with hand-built SEC facts, so every
expected number below can be checked with pencil and paper. The calculation is written next to
each assertion.

Filings (accession numbers are made up but have the real format):
  10-K  FY2022  0000000001-23-000001  filed 2023-02-15
  10-K  FY2023  0000000001-24-000001  filed 2024-02-15
  10-K/A FY2023 0000000001-24-000002  filed 2024-04-01   (amendment)
  10-K  FY2024  0000000001-25-000001  filed 2025-02-14
  10-Q  2023 Q1/Q2/Q3  0000000001-23-000010 / 11 / 12
  10-Q  2024 Q1/Q2/Q3  0000000001-24-000010 / 11 / 12
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from stock_forensics.models import Source, Unit, Value
from stock_forensics.normalize.concepts import FIELDS
from stock_forensics.normalize.statements import (
    NoFinancialDataError,
    Statements,
    build_statements,
)
from stock_forensics.providers.sec_models import CompanyFacts, parse_company_facts

A22 = "0000000001-23-000001"
A23 = "0000000001-24-000001"
A23_AMEND = "0000000001-24-000002"
A24 = "0000000001-25-000001"
Q1_23, Q2_23, Q3_23 = "0000000001-23-000010", "0000000001-23-000011", "0000000001-23-000012"
Q1_24, Q2_24, Q3_24 = "0000000001-24-000010", "0000000001-24-000011", "0000000001-24-000012"

FILED = {
    A22: "2023-02-15",
    A23: "2024-02-15",
    A23_AMEND: "2024-04-01",
    A24: "2025-02-14",
    Q1_23: "2023-05-10",
    Q2_23: "2023-08-10",
    Q3_23: "2023-11-09",
    Q1_24: "2024-05-10",
    Q2_24: "2024-08-09",
    Q3_24: "2024-11-08",
}
FY_OF = {A22: 2022, A23: 2023, A23_AMEND: 2023, A24: 2024}
FP_OF = {Q1_23: ("Q1", 2023), Q2_23: ("Q2", 2023), Q3_23: ("Q3", 2023)}
FP_OF |= {Q1_24: ("Q1", 2024), Q2_24: ("Q2", 2024), Q3_24: ("Q3", 2024)}

SOURCE = Source(
    provider="sec_edgar",
    endpoint="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
    retrieved_at=datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
)


def row(
    start: str | None, end: str, val: Any, accn: str, form: str | None = None
) -> dict[str, Any]:
    """One fact. fy/fp/form follow from the accession number, like in real SEC data."""
    if accn in FP_OF:
        fp, fy = FP_OF[accn]
        form = form or "10-Q"
    else:
        fp, fy = "FY", FY_OF[accn]
        form = form or ("10-K/A" if accn == A23_AMEND else "10-K")
    out: dict[str, Any] = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": FILED[accn],
    }
    if start is not None:
        out["start"] = start
    return out


def facts_from(concepts: dict[str, dict[str, list[dict[str, Any]]]]) -> CompanyFacts:
    raw = {
        "cik": 1,
        "entityName": "TestCo",
        "facts": {
            "us-gaap": {name: {"label": name, "units": units} for name, units in concepts.items()}
        },
    }
    return parse_company_facts(raw, SOURCE)


def usd(*rows: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {"USD": list(rows)}


def build_testco_concepts() -> dict[str, dict[str, list[dict[str, Any]]]]:
    return {
        # Revenue: FY2022 under the old tag; FY2023/FY2024 plus all quarters under "Revenues".
        "SalesRevenueNet": usd(row("2022-01-01", "2022-12-31", 900, A22)),
        "Revenues": usd(
            row("2023-01-01", "2023-12-31", 1000, A23),
            row("2023-01-01", "2023-12-31", 1000, A24),  # comparative in the next 10-K
            row("2024-01-01", "2024-12-31", 1180, A24),
            # 2023: Q1 200, Q2 240 (YTD 440), Q3 260 (YTD 700)
            row("2023-01-01", "2023-03-31", 200, Q1_23),
            row("2023-04-01", "2023-06-30", 240, Q2_23),
            row("2023-01-01", "2023-06-30", 440, Q2_23),
            row("2023-07-01", "2023-09-30", 260, Q3_23),
            row("2023-01-01", "2023-09-30", 700, Q3_23),
            # 2024: Q1 260, Q2 280 (YTD 540), Q3 300 (YTD 840). The Q1 10-Q repeats Q1 2023.
            row("2024-01-01", "2024-03-31", 260, Q1_24),
            row("2023-01-01", "2023-03-31", 200, Q1_24),
            row("2024-04-01", "2024-06-30", 280, Q2_24),
            row("2024-01-01", "2024-06-30", 540, Q2_24),
            row("2024-07-01", "2024-09-30", 300, Q3_24),
            row("2024-01-01", "2024-09-30", 840, Q3_24),
            # An 8-K must be ignored, even though it is the newest filing and has another number
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "val": 9999,
                "accn": "0000000001-25-000099",
                "fy": 2024,
                "fp": "FY",
                "form": "8-K",
                "filed": "2025-03-01",
            },
        ),
        "CostOfRevenue": usd(
            row("2022-01-01", "2022-12-31", 540, A22),
            row("2023-01-01", "2023-12-31", 600, A23),
            row("2024-01-01", "2024-12-31", 700, A24),
        ),
        # Net income FY2023 was restated in the next 10-K: 100 -> 90
        "NetIncomeLoss": usd(
            row("2023-01-01", "2023-12-31", 100, A23),
            row("2023-01-01", "2023-12-31", 90, A24),
            row("2024-01-01", "2024-12-31", 120, A24),
        ),
        # Operating cash flow: 10-Qs report year-to-date only (no discrete quarters)
        "NetCashProvidedByUsedInOperatingActivities": usd(
            row("2023-01-01", "2023-12-31", 400, A23),
            row("2023-01-01", "2023-03-31", 90, Q1_23),
            row("2023-01-01", "2023-06-30", 210, Q2_23),
            row("2023-01-01", "2023-09-30", 330, Q3_23),
        ),
        # Balance sheet instants; the FY2023 total was changed by a 10-K/A: 5000 -> 5050
        "Assets": usd(
            row(None, "2022-12-31", 4800, A22),
            row(None, "2023-12-31", 5000, A23),
            row(None, "2023-12-31", 5050, A23_AMEND),
            # The next 10-Q repeats the year-end balance sheet as a comparative
            row(None, "2023-12-31", 5050, Q1_24),
            row(None, "2024-12-31", 5200, A24),
        ),
        "LiabilitiesAndStockholdersEquity": usd(
            row(None, "2023-12-31", 5050, A23_AMEND),
        ),
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": usd(
            row(None, "2023-12-31", 2000, A23_AMEND),
        ),
        # Composite fields
        "SellingAndMarketingExpense": usd(
            row("2023-01-01", "2023-12-31", 70, A23),
            row("2024-01-01", "2024-12-31", 80, A24),
        ),
        "GeneralAndAdministrativeExpense": usd(row("2023-01-01", "2023-12-31", 30, A23)),
        "CommercialPaper": usd(row(None, "2023-12-31", 30, A23)),
        "LongTermDebtCurrent": usd(
            row(None, "2023-12-31", 50, A23),
            row(None, "2024-12-31", 40, A24),
        ),
        # Per share: reported per quarter, cannot be derived for Q4
        "EarningsPerShareDiluted": {
            "USD/shares": [
                row("2023-01-01", "2023-12-31", Decimal("2.50"), A23),
                row("2023-01-01", "2023-03-31", Decimal("0.50"), Q1_23),
                row("2023-04-01", "2023-06-30", Decimal("0.60"), Q2_23),
                row("2023-07-01", "2023-09-30", Decimal("0.70"), Q3_23),
            ]
        },
        # Reported in euros only
        "OperatingIncomeLoss": {"EUR": [row("2023-01-01", "2023-12-31", 123, A23)]},
    }


@pytest.fixture(scope="module")
def st() -> Statements:
    return build_statements(facts_from(build_testco_concepts()))


def num(value: Value) -> Decimal | None:
    return value.value


# --- calendar and labels ----------------------------------------------------------------


def test_fiscal_years_and_quarters_follow_the_filings(st: Statements) -> None:
    assert st.fiscal_years == (2022, 2023, 2024)
    # 2022Q4 (year end) plus 2023Q1..2024Q4; default keeps the last 8
    assert st.quarters == tuple(f"{y}Q{q}" for y in (2023, 2024) for q in (1, 2, 3, 4))
    assert st.company_name == "TestCo"
    assert st.latest_fiscal_year == 2024


def test_history_limits(st: Statements) -> None:
    facts = facts_from(build_testco_concepts())
    short = build_statements(facts, years=2, quarters=3)
    assert short.fiscal_years == (2023, 2024)
    assert short.quarters == ("2024Q2", "2024Q3", "2024Q4")
    assert set(short.get("revenue").annual) == {2023, 2024}


def test_no_annual_data_raises() -> None:
    quarterly_only = {"Revenues": usd(row("2024-01-01", "2024-03-31", 260, Q1_24))}
    with pytest.raises(NoFinancialDataError):
        build_statements(facts_from(quarterly_only))
    with pytest.raises(NoFinancialDataError):
        build_statements(facts_from({}))


# --- annual values, concept priority, forms ---------------------------------------------


def test_annual_revenue_switches_concept_per_period(st: Statements) -> None:
    revenue = st.get("revenue")
    assert num(revenue.annual[2022]) == Decimal(900)
    assert revenue.annual[2022].source.xbrl_concept == "us-gaap:SalesRevenueNet"
    assert num(revenue.annual[2023]) == Decimal(1000)
    assert revenue.annual[2023].source.xbrl_concept == "us-gaap:Revenues"
    assert num(revenue.annual[2024]) == Decimal(1180)


def test_8k_facts_are_ignored(st: Statements) -> None:
    # The 8-K says 9999 for FY2023; only 10-K/10-Q style filings count
    assert num(st.annual("revenue", 2023)) == Decimal(1000)


def test_value_carries_full_provenance(st: Statements) -> None:
    v = st.annual("revenue", 2024)
    assert v.unit is Unit.USD
    assert (v.period.fiscal_year, v.period.quarter) == (2024, None)
    assert (v.period.start, v.period.end) == (date(2024, 1, 1), date(2024, 12, 31))
    assert v.source.provider == "sec_edgar"
    assert v.source.form_type == "10-K"
    assert v.source.accession_number == A24
    assert v.source.endpoint == SOURCE.endpoint
    assert v.source.retrieved_at == SOURCE.retrieved_at
    assert v.note is None


def test_duplicate_with_same_value_has_no_restatement_note(st: Statements) -> None:
    # FY2023 revenue is 1000 in both the FY2023 and the FY2024 10-K: latest filing, no note
    v = st.annual("revenue", 2023)
    assert v.note is None
    assert v.source.accession_number == A24


# --- restatements and amendments --------------------------------------------------------


def test_restated_value_uses_latest_filing_and_names_the_original(st: Statements) -> None:
    v = st.annual("net_income", 2023)
    assert num(v) == Decimal(90)  # restated from 100
    assert v.source.accession_number == A24
    assert v.note == f"restated: originally 100 in {A23} (filed 2024-02-15), now 90 in {A24}"
    assert num(st.annual("net_income", 2024)) == Decimal(120)
    assert st.annual("net_income", 2024).note is None


def test_amendment_replaces_the_original_balance(st: Statements) -> None:
    v = st.annual("total_assets", 2023)
    assert num(v) == Decimal(5050)  # 10-K/A: 5000 -> 5050
    # The Q1 2024 10-Q is filed later and shows 5050 too, but a year-end figure cites the
    # annual report (here the 10-K/A), never the next 10-Q
    assert v.source.form_type == "10-K/A"
    assert v.source.accession_number == A23_AMEND
    assert v.note is not None
    assert "originally 5000" in v.note
    assert v.period.start is None  # point in time
    assert v.period.end == date(2023, 12, 31)


# --- quarters: reported, derived, TTM ---------------------------------------------------


def test_reported_quarters_are_taken_as_is(st: Statements) -> None:
    q = st.get("revenue").quarterly
    assert num(q["2023Q1"]) == Decimal(200)
    assert num(q["2023Q2"]) == Decimal(240)  # reported as a discrete quarter
    assert num(q["2023Q3"]) == Decimal(260)
    assert q["2023Q2"].note is None
    assert q["2024Q1"].period.start == date(2024, 1, 1)
    assert q["2024Q1"].period.end == date(2024, 3, 31)
    assert q["2024Q1"].period.quarter == 1


def test_q4_is_derived_from_year_minus_nine_months(st: Statements) -> None:
    q = st.get("revenue").quarterly
    # Q4 2023 = FY 1000 - 9M YTD 700 = 300
    assert num(q["2023Q4"]) == Decimal(300)
    assert q["2023Q4"].note is not None
    assert q["2023Q4"].note.startswith("derived: FY 1000")
    assert f"9M YTD 700 ({Q3_23})" in q["2023Q4"].note
    # Q4 2024 = FY 1180 - 9M YTD 840 = 340
    assert num(q["2024Q4"]) == Decimal(340)
    assert (q["2024Q4"].period.start, q["2024Q4"].period.end) == (
        date(2024, 10, 1),
        date(2024, 12, 31),
    )


def test_quarters_add_up_to_the_year(st: Statements) -> None:
    q = st.get("revenue").quarterly
    # 2023: 200 + 240 + 260 + 300 = 1000;  2024: 260 + 280 + 300 + 340 = 1180
    assert sum(num(q[f"2023Q{i}"]) or 0 for i in range(1, 5)) == Decimal(1000)
    assert sum(num(q[f"2024Q{i}"]) or 0 for i in range(1, 5)) == Decimal(1180)


def test_cash_flow_quarters_are_derived_from_year_to_date_figures(st: Statements) -> None:
    q = st.get("operating_cash_flow").quarterly
    # YTD: Q1 90, 6M 210, 9M 330, FY 400  ->  Q1 90, Q2 120, Q3 120, Q4 70
    assert num(q["2023Q1"]) == Decimal(90)
    assert num(q["2023Q2"]) == Decimal(120)
    assert num(q["2023Q3"]) == Decimal(120)
    assert num(q["2023Q4"]) == Decimal(70)
    assert q["2023Q2"].note is not None
    assert q["2023Q2"].note.startswith("derived: 6M YTD 210")
    assert sum(num(q[f"2023Q{i}"]) or 0 for i in range(1, 5)) == Decimal(400)


def test_ttm_equals_the_year_when_the_latest_period_is_a_year_end(st: Statements) -> None:
    ttm = st.get("revenue").ttm
    assert ttm is not None
    assert num(ttm) == Decimal(1180)
    assert ttm.note == "TTM equals fiscal year 2024"
    assert (ttm.period.start, ttm.period.end) == (date(2024, 1, 1), date(2024, 12, 31))


def test_ttm_is_the_sum_of_the_last_four_quarters() -> None:
    # Drop the FY2024 10-K: the newest period is then Q3 2024
    concepts = build_testco_concepts()
    for units in concepts.values():
        for unit, rows in units.items():
            units[unit] = [r for r in rows if r["accn"] != A24]
    stm = build_statements(facts_from(concepts))
    ttm = stm.get("revenue").ttm
    assert ttm is not None
    # Q4 2023 300 + Q1 260 + Q2 280 + Q3 300 = 1140
    assert num(ttm) == Decimal(1140)
    assert ttm.period.start == date(2023, 10, 1)
    assert ttm.period.end == date(2024, 9, 30)
    assert ttm.period.fiscal_year == 2024
    assert ttm.note is not None
    assert ttm.note.startswith("TTM: sum of last four quarters: 2023Q4 300")
    assert ttm.source.accession_number == Q3_24


def test_ttm_is_missing_when_a_quarter_is_missing(st: Statements) -> None:
    ttm = st.get("operating_cash_flow").ttm  # the latest year 2024 has no cash flow data
    assert ttm is not None
    assert ttm.value is None
    assert ttm.note is not None
    assert ttm.note.startswith("TTM not available")


def test_non_additive_fields_have_no_ttm_and_no_derived_quarters(st: Statements) -> None:
    eps = st.get("eps_diluted")
    assert eps.ttm is None
    assert num(eps.annual[2023]) == Decimal("2.50")
    assert eps.annual[2023].unit is Unit.USD_PER_SHARE
    assert num(eps.quarterly["2023Q1"]) == Decimal("0.50")
    assert num(eps.quarterly["2023Q3"]) == Decimal("0.70")
    q4 = eps.quarterly["2023Q4"]
    assert q4.value is None  # 2.50 - (0.50 + 0.60 + 0.70) would be wrong in general
    assert q4.note is not None
    assert "cannot be derived by subtraction" in q4.note


def test_balance_sheet_has_quarterly_points_but_no_ttm(st: Statements) -> None:
    assets = st.get("total_assets")
    assert assets.ttm is None
    assert num(assets.quarterly["2023Q4"]) == Decimal(5050)  # same as the fiscal year end
    assert assets.quarterly["2023Q1"].value is None  # not reported for that quarter end


# --- explicit gaps ----------------------------------------------------------------------


def test_missing_field_is_explained_not_zero(st: Statements) -> None:
    rnd = st.get("research_and_development")
    for fy in st.fiscal_years:
        v = rnd.annual[fy]
        assert v.value is None
        assert v.note is not None
        assert v.note.startswith(
            "not reported for this period (tried: us-gaap:ResearchAndDevelopmentExpense"
        )
        assert v.source.endpoint == SOURCE.endpoint
        assert v.period.fiscal_year == fy


def test_non_usd_reporting_is_called_out(st: Statements) -> None:
    v = st.annual("operating_income", 2023)
    assert v.value is None
    assert v.note is not None
    assert "only in EUR" in v.note


def test_every_field_has_a_value_for_every_year_and_quarter(st: Statements) -> None:
    assert set(st.series) == {spec.name for spec in FIELDS}
    for series in st.series.values():
        assert tuple(series.annual) == st.fiscal_years
        assert tuple(series.quarterly) == st.quarters
        for v in (*series.annual.values(), *series.quarterly.values()):
            assert v.value is not None or (v.note and v.note.strip())


# --- composite fields -------------------------------------------------------------------


def test_gross_profit_is_derived_when_not_reported(st: Statements) -> None:
    gp = st.get("gross_profit")
    assert num(gp.annual[2022]) == Decimal(360)  # 900 - 540
    assert num(gp.annual[2023]) == Decimal(400)  # 1000 - 600
    assert num(gp.annual[2024]) == Decimal(480)  # 1180 - 700
    assert gp.annual[2023].source.xbrl_concept == "derived: revenue - cost_of_revenue"
    assert gp.annual[2023].note is not None
    assert gp.annual[2023].note.startswith("derived: revenue (us-gaap:Revenues) minus")


def test_sga_is_the_sum_of_selling_and_general_when_both_are_reported(st: Statements) -> None:
    sga = st.get("selling_general_admin")
    assert num(sga.annual[2023]) == Decimal(100)  # 70 + 30
    assert sga.annual[2023].source.xbrl_concept == (
        "us-gaap:SellingAndMarketingExpense + us-gaap:GeneralAndAdministrativeExpense"
    )
    # FY2024 has only the selling part: a partial total would be wrong, so it stays missing
    assert sga.annual[2024].value is None


def test_short_term_debt_sums_available_components_and_says_what_is_missing(
    st: Statements,
) -> None:
    std = st.get("short_term_debt")
    assert num(std.annual[2023]) == Decimal(80)  # commercial paper 30 + current LTD 50
    assert std.annual[2023].note is not None
    assert "(not reported: us-gaap:ShortTermBorrowings)" in std.annual[2023].note
    assert num(std.annual[2024]) == Decimal(40)  # only current LTD reported
    assert std.annual[2024].note is not None
    assert "us-gaap:CommercialPaper" in std.annual[2024].note


def test_total_liabilities_is_derived_from_the_balance_sheet_identity(st: Statements) -> None:
    liabilities = st.get("total_liabilities")
    assert num(liabilities.annual[2023]) == Decimal(3050)  # 5050 - 2000
    assert liabilities.annual[2023].source.xbrl_concept == (
        "derived: total_liabilities_and_equity - equity_incl_noncontrolling"
    )
    assert liabilities.annual[2022].value is None
    assert liabilities.annual[2022].note is not None
    assert "also not derivable as total_liabilities_and_equity - equity_incl_noncontrolling" in (
        liabilities.annual[2022].note
    )


# --- serialization ----------------------------------------------------------------------


def test_statements_roundtrip_through_json(st: Statements) -> None:
    again = Statements.model_validate_json(st.model_dump_json())
    assert again == st
    assert again.annual("net_income", 2023).note == st.annual("net_income", 2023).note
