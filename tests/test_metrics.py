"""Hand-calculated tests for the metrics engine.

The numbers of the fictional company are chosen so that every metric can be checked with
pencil and paper; the calculation is written next to each expectation.

Dataset A (fiscal years 2024 and 2025, USD):

                              2024     2025
  revenue                      900     1000
  gross_profit                 360      400
  research_and_development      90      100
  operating_income             135      150
  interest_expense               5        6
  income_before_tax            130      144
  income_tax_expense            26       36        (tax rate 2025: 36 / 144 = 25 %)
  net_income                   100      108
  shares_diluted_weighted      100       96
  cash_and_equivalents          80      300
  short_term_investments        40       50
  inventory                     40       50
  total_current_assets         400      500
  total_current_liabilities    250      250
  total_assets                1000     1160
  short_term_debt               50       20
  long_term_debt               250      250
  total_equity                 400      500
  equity_incl_noncontrolling   410      520
  operating_cash_flow          130      162
  capital_expenditures          30       42
  dividends_paid                20       27
  share_repurchases             40       54
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from stock_forensics.metrics import ALL_SPECS, Group, Metrics, compute_metrics
from stock_forensics.metrics.growth import compute_growth_consistency
from stock_forensics.models import Period, Source, Value
from stock_forensics.normalize.concepts import FIELDS, Kind
from stock_forensics.normalize.statements import Series, Statements

RETRIEVED = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SOURCE = Source(
    provider="sec_edgar",
    endpoint="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
    retrieved_at=RETRIEVED,
)

DATASET_A: dict[str, dict[int, Any]] = {
    "revenue": {2024: 900, 2025: 1000},
    "gross_profit": {2024: 360, 2025: 400},
    "research_and_development": {2024: 90, 2025: 100},
    "operating_income": {2024: 135, 2025: 150},
    "interest_expense": {2024: 5, 2025: 6},
    "income_before_tax": {2024: 130, 2025: 144},
    "income_tax_expense": {2024: 26, 2025: 36},
    "net_income": {2024: 100, 2025: 108},
    "shares_diluted_weighted": {2024: 100, 2025: 96},
    "cash_and_equivalents": {2024: 80, 2025: 300},
    "short_term_investments": {2024: 40, 2025: 50},
    "inventory": {2024: 40, 2025: 50},
    "total_current_assets": {2024: 400, 2025: 500},
    "total_current_liabilities": {2024: 250, 2025: 250},
    "total_assets": {2024: 1000, 2025: 1160},
    "short_term_debt": {2024: 50, 2025: 20},
    "long_term_debt": {2024: 250, 2025: 250},
    "total_equity": {2024: 400, 2025: 500},
    "equity_incl_noncontrolling": {2024: 410, 2025: 520},
    "operating_cash_flow": {2024: 130, 2025: 162},
    "capital_expenditures": {2024: 30, 2025: 42},
    "dividends_paid": {2024: 20, 2025: 27},
    "share_repurchases": {2024: 40, 2025: 54},
}


def make_statements(
    data: dict[str, dict[int, Any]],
    years: tuple[int, ...],
    notes: dict[tuple[str, int], str] | None = None,
) -> Statements:
    """Statements with the given annual values; every other field is an explained gap."""
    notes = notes or {}
    series: dict[str, Series] = {}
    for spec in FIELDS:
        annual: dict[int, Value] = {}
        for fy in years:
            period = Period(
                fiscal_year=fy,
                start=None if spec.kind is Kind.INSTANT else date(fy, 1, 1),
                end=date(fy, 12, 31),
            )
            raw = data.get(spec.name, {}).get(fy)
            if raw is None:
                annual[fy] = Value(
                    value=None,
                    unit=spec.unit,
                    period=period,
                    source=SOURCE,
                    note="not reported for this period (tried: us-gaap:Example)",
                )
            else:
                annual[fy] = Value(
                    value=Decimal(str(raw)),
                    unit=spec.unit,
                    period=period,
                    source=Source(
                        provider="sec_edgar",
                        endpoint=SOURCE.endpoint,
                        form_type="10-K",
                        accession_number=f"0000000001-{fy % 100:02d}-000001",
                        xbrl_concept=f"us-gaap:{spec.concepts[0]}",
                        retrieved_at=RETRIEVED,
                    ),
                    note=notes.get((spec.name, fy)),
                )
        series[spec.name] = Series(
            name=spec.name,
            label=spec.label,
            statement=spec.statement,
            unit=spec.unit,
            annual=annual,
            quarterly={},
        )
    return Statements(
        cik=1,
        company_name="TestCo",
        fiscal_years=years,
        quarters=(),
        series=series,
        source=SOURCE,
    )


def dataset_a(**overrides: dict[int, Any]) -> Statements:
    return make_statements({**DATASET_A, **overrides}, (2024, 2025))


@pytest.fixture(scope="module")
def metrics_a() -> Metrics:
    return compute_metrics(dataset_a())


def number(metrics: Metrics, name: str, fy: int = 2025) -> Decimal | None:
    return metrics.annual(name, fy).value


# --- profitability ----------------------------------------------------------------------


def test_margins(metrics_a: Metrics) -> None:
    assert number(metrics_a, "gross_margin") == Decimal("0.4")  # 400 / 1000
    assert number(metrics_a, "operating_margin") == Decimal("0.15")  # 150 / 1000
    assert number(metrics_a, "net_margin") == Decimal("0.108")  # 108 / 1000
    assert number(metrics_a, "gross_margin", 2024) == Decimal("0.4")  # 360 / 900


def test_roe_uses_average_equity(metrics_a: Metrics) -> None:
    # 108 / ((500 + 400) / 2) = 108 / 450 = 0.24
    assert number(metrics_a, "roe") == Decimal("0.24")


def test_roa_uses_average_assets(metrics_a: Metrics) -> None:
    # 108 / ((1160 + 1000) / 2) = 108 / 1080 = 0.1
    assert number(metrics_a, "roa") == Decimal("0.1")


def test_roic(metrics_a: Metrics) -> None:
    # tax rate 36 / 144 = 0.25, NOPAT = 150 * (1 - 0.25) = 112.5
    # invested capital 2025 = 20 + 250 + 520 = 790, 2024 = 50 + 250 + 410 = 710, average 750
    # ROIC = 112.5 / 750 = 0.15
    assert number(metrics_a, "roic") == Decimal("0.15")


def test_roic_falls_back_to_parent_equity_and_says_so() -> None:
    metrics = compute_metrics(dataset_a(equity_incl_noncontrolling={}))
    # invested capital 2025 = 20 + 250 + 500 = 770, 2024 = 50 + 250 + 400 = 700, average 735
    value = metrics.annual("roic", 2025)
    assert value.value == Decimal("112.5") / Decimal(735)
    assert value.note is not None
    assert "total_equity used because equity_incl_noncontrolling is not reported" in value.note


# --- liquidity and leverage -------------------------------------------------------------


def test_liquidity_ratios(metrics_a: Metrics) -> None:
    assert number(metrics_a, "current_ratio") == Decimal(2)  # 500 / 250
    assert number(metrics_a, "quick_ratio") == Decimal("1.8")  # (500 - 50) / 250
    assert number(metrics_a, "debt_to_equity") == Decimal("0.54")  # (20 + 250) / 500
    assert number(metrics_a, "interest_coverage") == Decimal(25)  # 150 / 6
    assert number(metrics_a, "net_cash") == Decimal(80)  # 300 + 50 - 20 - 250


# --- cash flow quality ------------------------------------------------------------------


def test_cash_flow_metrics(metrics_a: Metrics) -> None:
    assert number(metrics_a, "free_cash_flow") == Decimal(120)  # 162 - 42
    assert number(metrics_a, "fcf_margin") == Decimal("0.12")  # 120 / 1000
    assert number(metrics_a, "cash_conversion") == Decimal("1.5")  # 162 / 108


# --- capital allocation -----------------------------------------------------------------


def test_capital_allocation_metrics(metrics_a: Metrics) -> None:
    assert number(metrics_a, "payout_ratio") == Decimal("0.25")  # 27 / 108
    assert number(metrics_a, "buyback_ratio") == Decimal("0.5")  # 54 / 108
    assert number(metrics_a, "total_payout_ratio") == Decimal("0.75")  # (27 + 54) / 108
    assert number(metrics_a, "share_count_change_yoy") == Decimal("-0.04")  # 96 / 100 - 1
    assert number(metrics_a, "rnd_ratio") == Decimal("0.1")  # 100 / 1000


# --- growth -----------------------------------------------------------------------------

YEARS_B = (2020, 2021, 2022, 2023, 2024, 2025)
DATASET_B: dict[str, dict[int, Any]] = {
    # revenue grows 10 % every year
    "revenue": dict(
        zip(YEARS_B, ["1000", "1100", "1210", "1331", "1464.1", "1610.51"], strict=True)
    ),
    # net income: +25 %, -50 %, +50 %, +60 %, +233.3 %
    "net_income": dict(zip(YEARS_B, [80, 100, 50, 75, 120, 400], strict=True)),
}


@pytest.fixture(scope="module")
def metrics_b() -> Metrics:
    return compute_metrics(make_statements(DATASET_B, YEARS_B))


def test_yoy_growth(metrics_b: Metrics) -> None:
    assert number(metrics_b, "revenue_growth_yoy") == Decimal("0.1")  # 1610.51 / 1464.1 - 1
    assert number(metrics_b, "net_income_growth_yoy") == Decimal(400) / Decimal(120) - 1
    assert number(metrics_b, "net_income_growth_yoy", 2022) == Decimal("-0.5")  # 50 / 100 - 1


def test_cagr_over_three_years(metrics_b: Metrics) -> None:
    # revenue: (1610.51 / 1210) ^ (1/3) - 1 = 1.331 ^ (1/3) - 1 = 1.1 - 1 = 0.1
    revenue_cagr = number(metrics_b, "revenue_cagr_3y")
    assert revenue_cagr is not None
    assert abs(revenue_cagr - Decimal("0.1")) < Decimal("1e-20")
    # net income: (400 / 50) ^ (1/3) - 1 = 8 ^ (1/3) - 1 = 2 - 1 = 1
    income_cagr = number(metrics_b, "net_income_cagr_3y")
    assert income_cagr is not None
    assert abs(income_cagr - Decimal(1)) < Decimal("1e-20")


def test_growth_consistency_counts_positive_years(metrics_b: Metrics) -> None:
    # revenue: all five changes positive -> 5 / 5
    assert number(metrics_b, "revenue_growth_consistency") == Decimal(1)
    # net income 2021+, 2022-, 2023+, 2024+, 2025+ -> 4 / 5
    income = metrics_b.annual("net_income_growth_consistency", 2025)
    assert income.value == Decimal("0.8")
    assert income.note is not None
    assert "4 of 5 year-over-year changes positive (2021+, 2022-, 2023+, 2024+, 2025+)" in (
        income.note
    )
    assert len(income.inputs) == 6  # the six annual net income values it looked at


def test_growth_consistency_with_three_changes_is_the_minimum() -> None:
    statements = make_statements(DATASET_B, YEARS_B)
    # FY2023 sees 2020..2023: three changes (2021+, 2022-, 2023+) -> 2 / 3
    value = compute_growth_consistency(statements, 2023, "net_income")
    assert value.value == Decimal(2) / Decimal(3)
    # FY2022 sees 2020..2022: only two changes -> not enough
    short = compute_growth_consistency(statements, 2022, "net_income")
    assert short.value is None
    assert short.note is not None
    assert "needs at least 3 year-over-year changes" in short.note
    assert "found 2" in short.note


def test_growth_consistency_skips_changes_from_a_non_positive_base() -> None:
    data = {"net_income": dict(zip(YEARS_B, [10, 20, -5, 8, 16, 32], strict=True))}
    value = compute_growth_consistency(make_statements(data, YEARS_B), 2025, "net_income")
    # changes: 2021 + (10->20), 2022 - (20->-5), 2023 skipped (base -5 is not positive),
    #          2024 + (8->16), 2025 + (16->32)  -> 3 of 4
    assert value.value == Decimal("0.75")
    assert value.note is not None
    assert "3 of 4" in value.note


def test_growth_needs_prior_years(metrics_b: Metrics) -> None:
    first = metrics_b.annual("revenue_growth_yoy", 2020)
    assert first.value is None
    assert first.note is not None
    assert "needs fiscal year 2019 (statements cover 2020-2025)" in first.note
    cagr = metrics_b.annual("revenue_cagr_3y", 2022)  # would need 2019
    assert cagr.value is None
    assert cagr.note is not None
    assert "needs fiscal year 2019" in cagr.note
    assert metrics_b.annual("revenue_cagr_3y", 2023).value is not None  # 2020 exists


# --- explicit gaps: not meaningful, missing, needs two years ----------------------------


def reason(metrics: Metrics, name: str, fy: int = 2025) -> str:
    value = metrics.annual(name, fy)
    assert value.value is None, f"{name} should be a gap"
    assert value.note is not None
    return value.note


def test_average_based_returns_need_the_prior_year(metrics_a: Metrics) -> None:
    for name in ("roe", "roa", "roic", "revenue_growth_yoy", "share_count_change_yoy"):
        assert "needs fiscal year 2023 (statements cover 2024-2025)" in reason(
            metrics_a, name, 2024
        )
    # single-year ratios still work for the first year
    assert number(metrics_a, "current_ratio", 2024) == Decimal("1.6")  # 400 / 250


def test_roe_is_not_meaningful_with_negative_equity() -> None:
    metrics = compute_metrics(dataset_a(total_equity={2024: -400, 2025: 500}))
    assert "stockholders' equity is not positive" in reason(metrics, "roe")
    metrics = compute_metrics(dataset_a(total_equity={2024: 400, 2025: -500}))
    assert "stockholders' equity is not positive" in reason(metrics, "debt_to_equity")


def test_ratios_with_net_income_need_it_to_be_positive() -> None:
    metrics = compute_metrics(dataset_a(net_income={2024: 100, 2025: -8}))
    for name in ("cash_conversion", "payout_ratio", "buyback_ratio", "total_payout_ratio"):
        assert "net income is not positive" in reason(metrics, name)
    assert number(metrics, "net_margin") == Decimal("-0.008")  # a loss is a real margin


def test_zero_interest_expense_is_not_a_coverage_ratio() -> None:
    metrics = compute_metrics(dataset_a(interest_expense={2024: 5, 2025: 0}))
    assert "interest expense is not positive" in reason(metrics, "interest_coverage")


def test_roic_needs_a_sensible_tax_rate() -> None:
    too_high = compute_metrics(dataset_a(income_tax_expense={2024: 26, 2025: 200}))
    assert "effective tax rate of 138.9% is outside 0-100%" in reason(too_high, "roic")
    no_profit = compute_metrics(dataset_a(income_before_tax={2024: 130, 2025: -10}))
    assert "pre-tax income is not positive" in reason(no_profit, "roic")


def test_a_missing_input_makes_the_metric_a_gap_with_the_field_named() -> None:
    metrics = compute_metrics(dataset_a(short_term_investments={}))
    note = reason(metrics, "net_cash")
    assert "short_term_investments FY2025: not reported for this period" in note
    assert "(tried:" not in note  # the long list of tried concepts stays in the statements
    # the inputs that were available are still attached
    value = metrics.annual("net_cash", 2025)
    assert {v.source.xbrl_concept for v in value.inputs if v.value is not None} >= {
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:LongTermDebtNoncurrent",
    }
    assert number(metrics, "current_ratio") == Decimal(2)  # unrelated metrics are unaffected


def test_zero_denominators_are_gaps_not_errors() -> None:
    metrics = compute_metrics(dataset_a(revenue={2024: 900, 2025: 0}))
    assert "revenue is zero" in reason(metrics, "gross_margin")
    metrics = compute_metrics(dataset_a(total_current_liabilities={2024: 250, 2025: 0}))
    assert "current liabilities are zero" in reason(metrics, "current_ratio")


# --- provenance -------------------------------------------------------------------------


def test_metric_carries_formula_and_its_inputs(metrics_a: Metrics) -> None:
    roe = metrics_a.annual("roe", 2025)
    assert roe.note == "net_income / average(total_equity, prior-year total_equity)"
    assert [(v.value, v.period.fiscal_year) for v in roe.inputs] == [
        (Decimal(108), 2025),
        (Decimal(500), 2025),
        (Decimal(400), 2024),
    ]
    assert roe.inputs[2].source.accession_number == "0000000001-24-000001"
    assert roe.inputs[0].source.xbrl_concept == "us-gaap:NetIncomeLoss"


def test_computed_source_is_deterministic(metrics_a: Metrics) -> None:
    value = metrics_a.annual("net_margin", 2025)
    assert value.source.provider == "stock_forensics"
    assert value.source.retrieved_at == RETRIEVED  # the time of the input data, not "now"
    assert (value.period.fiscal_year, value.period.end) == (2025, date(2025, 12, 31))
    assert value.period.start == date(2025, 1, 1)


def test_flagged_inputs_are_named_in_the_note() -> None:
    notes = {
        ("net_income", 2025): "restated: originally 100 in 0000000001-26-000001 (filed ...)",
        ("gross_profit", 2025): "derived: revenue (us-gaap:Revenues) minus cost_of_revenue",
        ("revenue", 2025): "includes noncontrolling interests",
    }
    metrics = compute_metrics(make_statements(DATASET_A, (2024, 2025), notes))
    assert metrics.annual("net_margin", 2025).note == (
        "net_income / revenue; "
        "inputs flagged: net_income FY2025 (restated), revenue FY2025 (caveat)"
    )
    gross = metrics.annual("gross_margin", 2025).note
    assert gross == (
        "gross_profit / revenue; "
        "inputs flagged: gross_profit FY2025 (derived), revenue FY2025 (caveat)"
    )


def test_units(metrics_a: Metrics) -> None:
    assert metrics_a.annual("net_cash", 2025).unit.value == "USD"
    assert metrics_a.annual("free_cash_flow", 2025).unit.value == "USD"
    assert metrics_a.annual("roe", 2025).unit.value == "ratio"


# --- the whole set ----------------------------------------------------------------------


def test_all_25_metrics_in_five_groups(metrics_a: Metrics) -> None:
    assert len(ALL_SPECS) == 25
    assert set(metrics_a.series) == {s.name for s in ALL_SPECS}
    assert {g: len(metrics_a.of_group(g)) for g in Group} == {
        Group.PROFITABILITY: 6,
        Group.LIQUIDITY: 5,
        Group.CASH_FLOW: 3,
        Group.GROWTH: 6,
        Group.CAPITAL_ALLOCATION: 5,
    }
    assert metrics_a.fiscal_years == (2024, 2025)
    assert metrics_a.latest("roe") == metrics_a.annual("roe", 2025)


def test_every_gap_has_a_reason_and_every_value_has_inputs(metrics_a: Metrics) -> None:
    for series in metrics_a.series.values():
        for value in series.annual.values():
            assert value.note and value.note.strip()
            if value.value is not None:
                assert value.inputs, f"{series.name} has a number but no inputs"
            assert value.source.provider == "stock_forensics"


def test_metrics_roundtrip_through_json(metrics_a: Metrics) -> None:
    again = Metrics.model_validate_json(metrics_a.model_dump_json())
    assert again == metrics_a
    assert again.annual("roe", 2025).inputs[0].source.xbrl_concept == "us-gaap:NetIncomeLoss"
