"""Metrics on real statements of Apple, Microsoft and Walmart (trimmed SEC fixtures).

The 10-K figures used for the hand recalculation (revenue, net income, total assets, operating
cash flow; USD millions) were verified against the actual 10-K documents, see
tests/test_statements_real.py.
"""

from __future__ import annotations

from decimal import Decimal
from functools import cache

import pytest

from stock_forensics.metrics import Metrics, compute_metrics
from tests.test_statements_real import TEN_K, statements

MILLION = Decimal(1_000_000)
COMPANIES = ("aapl", "msft", "wmt")


@cache
def metrics(company: str) -> Metrics:
    return compute_metrics(statements(company))


def value(company: str, name: str, fy: int) -> Decimal:
    number = metrics(company).annual(name, fy).value
    assert number is not None, f"{company} {name} FY{fy} is a gap"
    return number


# --- hand recalculation from 10-K figures -----------------------------------------------


@pytest.mark.parametrize(("company", "fy"), sorted(TEN_K))
def test_margins_and_cash_conversion_match_a_hand_calculation(company: str, fy: int) -> None:
    revenue, net_income, _assets, operating_cash_flow = (Decimal(x) for x in TEN_K[(company, fy)])
    assert value(company, "net_margin", fy) == net_income / revenue
    assert value(company, "cash_conversion", fy) == operating_cash_flow / net_income


def test_apple_fiscal_2025_by_hand() -> None:
    # 10-K figures in USD millions: revenue 416,161, net income 112,010, operating cash flow
    # 111,482. Net margin = 112,010 / 416,161 = 0.26915 = 26.9 %; cash conversion =
    # 111,482 / 112,010 = 0.9953.
    assert round(value("aapl", "net_margin", 2025), 4) == Decimal("0.2692")
    assert round(value("aapl", "cash_conversion", 2025), 4) == Decimal("0.9953")
    # revenue growth 416,161 / 391,035 - 1 = 6.43 %
    assert round(value("aapl", "revenue_growth_yoy", 2025), 4) == Decimal("0.0643")
    # earnings growth 112,010 / 93,736 - 1 = 19.5 %
    assert round(value("aapl", "net_income_growth_yoy", 2025), 3) == Decimal("0.195")


def test_apple_roe_uses_the_average_of_both_year_ends() -> None:
    st = statements("aapl")
    net_income = st.annual("net_income", 2025).value
    equity_now = st.annual("total_equity", 2025).value
    equity_before = st.annual("total_equity", 2024).value
    assert net_income is not None
    assert equity_now is not None
    assert equity_before is not None
    assert value("aapl", "roe", 2025) == net_income / ((equity_now + equity_before) / 2)


def test_apple_net_cash_is_cash_plus_securities_minus_debt() -> None:
    st = statements("aapl")

    def field(name: str) -> Decimal:
        number = st.annual(name, 2024).value
        assert number is not None
        return number

    expected = (
        field("cash_and_equivalents")
        + field("short_term_investments")
        - field("short_term_debt")
        - field("long_term_debt")
    )
    assert value("aapl", "net_cash", 2024) == expected
    assert expected < 0  # Apple carried more debt than cash and securities in fiscal 2024


# --- consistency across the metric set --------------------------------------------------


@pytest.mark.parametrize("company", COMPANIES)
def test_every_metric_exists_for_every_fiscal_year(company: str) -> None:
    m = metrics(company)
    assert len(m.series) == 25
    for series in m.series.values():
        assert tuple(series.annual) == m.fiscal_years
        for v in series.annual.values():
            assert v.value is not None or (v.note and v.note.strip())
            if v.value is not None:
                assert v.inputs


@pytest.mark.parametrize("company", COMPANIES)
def test_identities_between_metrics(company: str) -> None:
    for fy in metrics(company).fiscal_years:
        m = metrics(company)
        free_cash_flow = m.annual("free_cash_flow", fy).value
        margin = m.annual("fcf_margin", fy).value
        revenue = statements(company).annual("revenue", fy).value
        if free_cash_flow is not None and margin is not None and revenue is not None:
            assert margin == free_cash_flow / revenue

        dividends = m.annual("payout_ratio", fy).value
        buybacks = m.annual("buyback_ratio", fy).value
        total = m.annual("total_payout_ratio", fy).value
        if dividends is not None and buybacks is not None and total is not None:
            assert abs(total - (dividends + buybacks)) < Decimal("1e-20")  # Decimal rounding


@pytest.mark.parametrize("company", COMPANIES)
def test_plausible_ranges(company: str) -> None:
    m = metrics(company)
    for fy in m.fiscal_years:
        for name in ("gross_margin", "operating_margin", "net_margin", "fcf_margin"):
            number = m.annual(name, fy).value
            if number is not None:
                assert Decimal("-0.5") < number < Decimal(1), f"{company} {name} FY{fy}"
        quick = m.annual("quick_ratio", fy).value
        current = m.annual("current_ratio", fy).value
        if quick is not None and current is not None:
            assert quick <= current  # inventory can only lower the ratio
        roic = m.annual("roic", fy).value
        if roic is not None:
            assert Decimal(0) < roic < Decimal(3)


# --- explicit gaps in real data ---------------------------------------------------------


def test_apple_has_no_interest_coverage_since_it_stopped_tagging_interest() -> None:
    v = metrics("aapl").annual("interest_coverage", 2026)
    assert v.value is None
    assert v.note is not None
    assert v.note.startswith("cannot compute operating_income / interest_expense")
    assert "interest_expense FY2026" in v.note


def test_walmart_has_gaps_where_it_reports_nothing() -> None:
    m = metrics("wmt")
    rnd = m.annual("rnd_ratio", 2026)
    assert rnd.value is None
    assert rnd.note is not None
    assert "research_and_development FY2026" in rnd.note
    net_cash = m.annual("net_cash", 2026)  # Walmart reports no short-term investments
    assert net_cash.value is None
    assert net_cash.note is not None
    assert "short_term_investments FY2026" in net_cash.note
    assert m.annual("quick_ratio", 2026).value is not None  # needs only reported items


def test_first_year_in_the_data_cannot_have_average_based_returns() -> None:
    m = metrics("aapl")
    first = m.fiscal_years[0]
    v = m.annual("roe", first)
    assert v.value is None
    assert v.note is not None
    assert f"needs fiscal year {first - 1}" in v.note


def test_microsoft_inputs_that_are_derived_are_flagged() -> None:
    # Microsoft's SG&A is built from two lines; metrics using derived inputs say so.
    flagged = [
        v
        for s in metrics("msft").series.values()
        for v in s.annual.values()
        if v.note and "inputs flagged" in v.note and "(derived)" in v.note
    ]
    assert flagged  # e.g. gross profit when it is derived rather than reported


def test_metrics_roundtrip_through_json() -> None:
    m = metrics("aapl")
    assert Metrics.model_validate_json(m.model_dump_json()) == m
