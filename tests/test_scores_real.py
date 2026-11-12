"""Forensic scores on real statements of Apple, Microsoft and Walmart (trimmed SEC fixtures).

Apple (SIC 3571, a manufacturer) gets the book-value Altman Z', Microsoft (SIC 7372) and
Walmart (SIC 5331) get Z''. The input numbers behind the metrics were checked against the 10-K
documents (tests/test_statements_real.py and scripts/reconcile_statements.py).
"""

from __future__ import annotations

from decimal import Decimal
from functools import cache

import pytest

from stock_forensics.normalize.statements import Statements
from stock_forensics.scores import SCORE_NAMES, Scores, compute_scores
from tests.test_statements_real import statements

SIC = {"aapl": "3571", "msft": "7372", "wmt": "5331"}
COMPANIES = tuple(SIC)


@cache
def scores(company: str) -> Scores:
    return compute_scores(statements(company), sic_code=SIC[company])


def total(company: str, name: str, fy: int) -> Decimal:
    value = scores(company).get(name, fy).total.value
    assert value is not None, f"{company} {name} FY{fy} is a gap"
    return value


def raw(st: Statements, field: str, fy: int) -> Decimal:
    value = st.annual(field, fy).value
    assert value is not None, f"{field} FY{fy}"
    return value


# --- all components visible, totals plausible -------------------------------------------


@pytest.mark.parametrize("company", COMPANIES)
def test_all_scores_exist_for_the_latest_years(company: str) -> None:
    for name in SCORE_NAMES:
        for fy in (2025, 2026):
            result = scores(company).get(name, fy)
            assert result.applicable
            assert result.total.value is not None, f"{company} {name} FY{fy}"
            assert result.zone is not None
            assert result.interpretation
            assert result.limitations
            assert result.thresholds
            assert result.total.inputs  # the components, each traceable to the statements


@pytest.mark.parametrize("company", COMPANIES)
def test_components_are_visible(company: str) -> None:
    latest = {name: scores(company).latest(name) for name in SCORE_NAMES}
    assert [c.name for c in latest["beneish_m"].components] == [
        "DSRI",
        "GMI",
        "AQI",
        "SGI",
        "DEPI",
        "SGAI",
        "TATA",
        "LVGI",
    ]
    assert len(latest["piotroski_f"].components) == 9
    assert [c.name for c in latest["accruals_ratio"].components] == [
        "accruals",
        "average_total_assets",
    ]
    for result in latest.values():
        for component in result.components:
            assert component.value.value is not None
            assert component.value.inputs


@pytest.mark.parametrize("company", COMPANIES)
def test_totals_are_plausible(company: str) -> None:
    for fy in (2025, 2026):
        beneish = total(company, "beneish_m", fy)
        assert Decimal(-5) < beneish < Decimal(0), f"{company} FY{fy} M-score {beneish}"
        piotroski = total(company, "piotroski_f", fy)
        assert piotroski == piotroski.to_integral_value()
        assert Decimal(0) <= piotroski <= Decimal(9)
        altman = total(company, "altman_z", fy)
        assert Decimal(-1) < altman < Decimal(10)
        accruals = total(company, "accruals_ratio", fy)
        assert abs(accruals) < Decimal("0.25")  # none of these has high accruals


def test_altman_variant_follows_the_industry() -> None:
    assert scores("aapl").latest("altman_z").variant == "Z'"  # manufacturer, no market data
    assert scores("msft").latest("altman_z").variant == "Z''"
    assert scores("wmt").latest("altman_z").variant == "Z''"
    assert [c.name for c in scores("aapl").latest("altman_z").components][-1] == "X5"
    assert [c.name for c in scores("msft").latest("altman_z").components][-1] == "X4"


# --- known results of these fixtures (regression) ---------------------------------------


@pytest.mark.parametrize("company", COMPANIES)
def test_none_of_the_three_is_flagged_as_a_manipulator(company: str) -> None:
    for fy in (2024, 2025, 2026):
        assert scores(company).get("beneish_m", fy).zone == "unlikely_manipulator"


def test_piotroski_known_values() -> None:
    assert total("aapl", "piotroski_f", 2026) == Decimal(9)
    assert scores("aapl").get("piotroski_f", 2026).zone == "strong"
    assert total("msft", "piotroski_f", 2026) == Decimal(6)
    assert scores("msft").get("piotroski_f", 2026).zone == "neutral"
    microsoft = scores("msft").get("piotroski_f", 2026)
    assert [c.name for c in microsoft.components if c.passed is False] == [
        "F_DELTA_LIQUID",
        "F_DELTA_MARGIN",
        "F_DELTA_TURN",
    ]
    assert "Failed: F_DELTA_LIQUID, F_DELTA_MARGIN, F_DELTA_TURN" in microsoft.interpretation


def test_microsoft_is_in_the_safe_zone_and_apple_in_the_grey_zone_of_z_prime() -> None:
    assert scores("msft").latest("altman_z").zone == "safe"
    # Apple's book equity is small after years of buybacks, which holds Z' in the grey zone
    assert scores("aapl").latest("altman_z").zone == "grey"


# --- independent recalculation of one score from the raw statement values ---------------


def test_apple_beneish_fiscal_2025_recalculated_independently() -> None:
    st = statements("aapl")
    t, p = 2025, 2024

    def r(field: str, fy: int) -> Decimal:
        return raw(st, field, fy)

    dsri = (r("accounts_receivable", t) / r("revenue", t)) / (
        r("accounts_receivable", p) / r("revenue", p)
    )
    gmi = (r("gross_profit", p) / r("revenue", p)) / (r("gross_profit", t) / r("revenue", t))
    aqi = (
        1
        - (r("total_current_assets", t) + r("property_plant_equipment_net", t))
        / r("total_assets", t)
    ) / (
        1
        - (r("total_current_assets", p) + r("property_plant_equipment_net", p))
        / r("total_assets", p)
    )
    sgi = r("revenue", t) / r("revenue", p)
    depi = (
        r("depreciation_amortization", p)
        / (r("depreciation_amortization", p) + r("property_plant_equipment_net", p))
    ) / (
        r("depreciation_amortization", t)
        / (r("depreciation_amortization", t) + r("property_plant_equipment_net", t))
    )
    sgai = (r("selling_general_admin", t) / r("revenue", t)) / (
        r("selling_general_admin", p) / r("revenue", p)
    )
    tata = (r("net_income", t) - r("operating_cash_flow", t)) / r("total_assets", t)
    lvgi = ((r("total_current_liabilities", t) + r("long_term_debt", t)) / r("total_assets", t)) / (
        (r("total_current_liabilities", p) + r("long_term_debt", p)) / r("total_assets", p)
    )
    expected = (
        Decimal("-4.84")
        + Decimal("0.920") * dsri
        + Decimal("0.528") * gmi
        + Decimal("0.404") * aqi
        + Decimal("0.892") * sgi
        + Decimal("0.115") * depi
        - Decimal("0.172") * sgai
        + Decimal("4.679") * tata
        - Decimal("0.327") * lvgi
    )
    assert abs(total("aapl", "beneish_m", 2025) - expected) < Decimal("1e-20")


def test_apple_accruals_ratio_fiscal_2025_by_hand() -> None:
    # 10-K (USD millions): net income 112,010, operating cash flow 111,482, total assets
    # 359,241 (2025) and 364,980 (2024). (112,010 - 111,482) / ((359,241 + 364,980) / 2) = 0.00146
    assert round(total("aapl", "accruals_ratio", 2025), 5) == Decimal("0.00146")


# --- banks ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sic", "industry"),
    [("6021", "National Commercial Banks"), ("6311", "Life Insurance"), ("6798", "REIT")],
)
def test_financial_companies_are_not_applicable(sic: str, industry: str) -> None:
    result = compute_scores(statements("aapl"), sic_code=sic, industry=industry)
    for name in SCORE_NAMES:
        for fy in result.fiscal_years:
            r = result.get(name, fy)
            assert r.applicable is False
            assert r.total.value is None
            assert r.not_applicable_reason is not None
            assert f"SIC {sic}" in r.not_applicable_reason


def test_scores_roundtrip_through_json() -> None:
    s = scores("msft")
    assert Scores.model_validate_json(s.model_dump_json()) == s
