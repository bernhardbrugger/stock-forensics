"""Hand-calculated tests for the forensic scores.

Each score has one fully worked example; the calculation is written next to the assertions so
that it can be checked with pencil and paper.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from stock_forensics.models import Period, Source, Unit, Value
from stock_forensics.scores import SCORE_NAMES, ScoreResult, Scores, compute_scores
from stock_forensics.scores.industry import exclusion_reason, is_manufacturer
from tests.test_metrics import RETRIEVED, make_statements

TOL = Decimal("1e-20")


def close(actual: Decimal | None, expected: str) -> bool:
    return actual is not None and abs(actual - Decimal(expected)) < TOL


def score(data: dict[str, dict[int, Any]], years: tuple[int, ...], **kwargs: Any) -> Scores:
    kwargs.setdefault("sic_code", "7372")
    return compute_scores(make_statements(data, years), **kwargs)


def component(result: ScoreResult, name: str) -> Decimal | None:
    return next(c for c in result.components if c.name == name).value.value


# =========================================================================================
# Beneish M-Score
#
#                                 2024     2025
#   revenue                        800     1000
#   accounts_receivable             80      120
#   gross_profit                   320      320
#   total_current_assets           300      400
#   property_plant_equipment_net   400      400
#   total_assets                  1000     1250
#   depreciation_amortization      100       80
#   selling_general_admin           80      130
#   net_income                              100
#   operating_cash_flow                      50
#   total_current_liabilities      200      300
#   long_term_debt                 300      400
#
#   DSRI = (120/1000) / (80/800)                    = 0.12 / 0.10        = 1.2
#   GMI  = (320/800) / (320/1000)                   = 0.40 / 0.32        = 1.25
#   AQI  = (1 - (400+400)/1250) / (1 - (300+400)/1000) = 0.36 / 0.30     = 1.2
#   SGI  = 1000 / 800                                                    = 1.25
#   DEPI = (100/(100+400)) / (80/(80+400))          = 0.2 / (1/6)        = 1.2
#   SGAI = (130/1000) / (80/800)                    = 0.13 / 0.10        = 1.3
#   TATA = (100 - 50) / 1250                                             = 0.04
#   LVGI = ((300+400)/1250) / ((200+300)/1000)      = 0.56 / 0.50        = 1.12
#
#   M = -4.84 + 0.920*1.2 + 0.528*1.25 + 0.404*1.2 + 0.892*1.25 + 0.115*1.2
#             - 0.172*1.3 + 4.679*0.04 - 0.327*1.12
#     = -4.84 + 1.104 + 0.66 + 0.4848 + 1.115 + 0.138 - 0.2236 + 0.18716 - 0.36624
#     = -4.84 + 3.09912 = -1.74088      (above -1.78: likely manipulator)
# =========================================================================================

YEARS2 = (2024, 2025)
BENEISH: dict[str, dict[int, Any]] = {
    "revenue": {2024: 800, 2025: 1000},
    "accounts_receivable": {2024: 80, 2025: 120},
    "gross_profit": {2024: 320, 2025: 320},
    "total_current_assets": {2024: 300, 2025: 400},
    "property_plant_equipment_net": {2024: 400, 2025: 400},
    "total_assets": {2024: 1000, 2025: 1250},
    "depreciation_amortization": {2024: 100, 2025: 80},
    "selling_general_admin": {2024: 80, 2025: 130},
    "net_income": {2024: 60, 2025: 100},
    "operating_cash_flow": {2024: 55, 2025: 50},
    "total_current_liabilities": {2024: 200, 2025: 300},
    "long_term_debt": {2024: 300, 2025: 400},
}


def test_beneish_worked_example() -> None:
    result = score(BENEISH, YEARS2).get("beneish_m", 2025)
    for name, expected in {
        "DSRI": "1.2",
        "GMI": "1.25",
        "AQI": "1.2",
        "SGI": "1.25",
        "DEPI": "1.2",
        "SGAI": "1.3",
        "TATA": "0.04",
        "LVGI": "1.12",
    }.items():
        assert close(component(result, name), expected), name
    assert result.constant == Decimal("-4.84")
    assert close(result.total.value, "-1.74088")
    assert result.zone == "likely_manipulator"
    assert result.applicable
    assert result.total.unit is Unit.SCORE


def test_beneish_components_carry_weights_and_contributions() -> None:
    result = score(BENEISH, YEARS2).get("beneish_m", 2025)
    by_name = {c.name: c for c in result.components}
    assert by_name["DSRI"].weight == Decimal("0.920")
    assert by_name["SGAI"].weight == Decimal("-0.172")
    assert close(by_name["DSRI"].contribution, "1.104")  # 0.920 * 1.2
    assert close(by_name["TATA"].contribution, "0.18716")  # 4.679 * 0.04
    assert close(by_name["LVGI"].contribution, "-0.36624")  # -0.327 * 1.12
    assert tuple(by_name) == ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "TATA", "LVGI")
    # the total is traceable: its inputs are the eight component values
    assert [v.value for v in result.total.inputs] == [c.value.value for c in result.components]


def test_beneish_thresholds_and_texts() -> None:
    result = score(BENEISH, YEARS2).get("beneish_m", 2025)
    assert [(t.label, t.value) for t in result.thresholds] == [("cutoff", Decimal("-1.78"))]
    assert "above the cutoff of -1.78" in result.interpretation
    assert "likely earnings manipulator" in result.interpretation
    assert "Largest contributions: " in result.interpretation
    assert "Beneish" in result.reference
    assert any("false-positive" in text for text in result.limitations)
    assert "-4.84" in result.formula


def test_beneish_unlikely_when_accruals_are_low() -> None:
    # operating cash flow 100 instead of 50: TATA = (100 - 100) / 1250 = 0
    # M = -1.74088 - 4.679*0.04 = -1.74088 - 0.18716 = -1.92804 (below -1.78)
    data = {**BENEISH, "operating_cash_flow": {2024: 55, 2025: 100}}
    result = score(data, YEARS2).get("beneish_m", 2025)
    assert close(result.total.value, "-1.92804")
    assert result.zone == "unlikely_manipulator"
    assert "does not flag" in result.interpretation


def test_beneish_needs_the_prior_year() -> None:
    result = score(BENEISH, YEARS2).get("beneish_m", 2024)
    assert result.total.value is None
    assert result.zone is None
    assert result.total.note is not None
    assert "no value for DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI" in result.total.note
    assert "See the notes of those components" in result.interpretation
    gsi = next(c for c in result.components if c.name == "DSRI")
    assert gsi.value.note is not None
    assert "needs fiscal year 2023 (statements cover 2024-2025)" in gsi.value.note
    # TATA needs only the current year, so it is still visible:
    # (net income 60 - operating cash flow 55) / total assets 1000 = +0.005
    assert component(result, "TATA") == Decimal("0.005")


def test_beneish_gap_names_the_missing_input() -> None:
    data = {k: v for k, v in BENEISH.items() if k != "selling_general_admin"}
    result = score(data, YEARS2).get("beneish_m", 2025)
    assert result.total.value is None
    assert result.total.note is not None
    assert "no value for SGAI" in result.total.note
    sgai = next(c for c in result.components if c.name == "SGAI")
    assert sgai.value.note is not None
    assert "selling_general_admin FY2025" in sgai.value.note
    assert close(component(result, "DSRI"), "1.2")  # the other indices are still computed


def test_beneish_zero_denominators_are_gaps_not_errors() -> None:
    data = {**BENEISH, "accounts_receivable": {2024: 0, 2025: 120}}
    result = score(data, YEARS2).get("beneish_m", 2025)
    dsri = next(c for c in result.components if c.name == "DSRI")
    assert dsri.value.value is None
    assert dsri.value.note is not None
    assert "not meaningful" in dsri.value.note
    assert result.total.value is None


# =========================================================================================
# Altman Z-Score
#
#   current assets 500, current liabilities 300, total assets 1000, retained earnings 400,
#   operating income 150, book equity 500, total liabilities 500, revenue 1500
#   X1 = (500 - 300) / 1000 = 0.2     X2 = 400 / 1000 = 0.4     X3 = 150 / 1000 = 0.15
#   X4 = 500 / 500 = 1.0 (book)       X5 = 1500 / 1000 = 1.5
#
#   Z''  (SIC 7372)
#        = 6.56*0.2 + 3.26*0.4 + 6.72*0.15 + 1.05*1.0
#        = 1.312 + 1.304 + 1.008 + 1.05                         = 4.674    -> safe (> 2.60)
#   Z'   (SIC 3571, no market cap)
#        = 0.717*0.2 + 0.847*0.4 + 3.107*0.15 + 0.420*1.0 + 0.998*1.5
#        = 0.1434 + 0.3388 + 0.46605 + 0.42 + 1.497             = 2.86525  -> grey (1.23 to 2.90)
#   Z    (SIC 3571, market value of equity 1500, X4 = 1500 / 500 = 3.0)
#        = 1.2*0.2 + 1.4*0.4 + 3.3*0.15 + 0.6*3.0 + 1.0*1.5
#        = 0.24 + 0.56 + 0.495 + 1.8 + 1.5                      = 4.595    -> safe (> 2.99)
# =========================================================================================

ALTMAN: dict[str, dict[int, Any]] = {
    "total_current_assets": {2025: 500},
    "total_current_liabilities": {2025: 300},
    "total_assets": {2025: 1000},
    "retained_earnings": {2025: 400},
    "operating_income": {2025: 150},
    "total_equity": {2025: 500},
    "total_liabilities": {2025: 500},
    "revenue": {2025: 1500},
}


def altman(sic: str | None, **kwargs: Any) -> ScoreResult:
    return score(ALTMAN, (2025,), sic_code=sic, **kwargs).get("altman_z", 2025)


def market_cap(value: str) -> dict[int, Value]:
    return {
        2025: Value(
            value=Decimal(value),
            unit=Unit.USD,
            period=Period(fiscal_year=2025, end=RETRIEVED.date()),
            source=Source(provider="test", retrieved_at=RETRIEVED),
            note="market value of equity at fiscal year end",
        )
    }


def test_altman_z_double_prime_for_a_non_manufacturer() -> None:
    result = altman("7372")
    assert result.variant == "Z''"
    assert close(result.total.value, "4.674")
    assert result.zone == "safe"
    assert [c.name for c in result.components] == ["X1", "X2", "X3", "X4"]  # no X5 in Z''
    assert close(component(result, "X1"), "0.2")
    assert close(component(result, "X4"), "1.0")
    assert [(t.label, t.value) for t in result.thresholds] == [
        ("distress below", Decimal("1.10")),
        ("safe above", Decimal("2.60")),
    ]
    assert "non-manufacturer" in result.interpretation


def test_altman_z_prime_for_a_manufacturer_without_market_data() -> None:
    result = altman("3571")
    assert result.variant == "Z'"
    assert close(result.total.value, "2.86525")
    assert result.zone == "grey"  # between 1.23 and 2.90
    assert [c.name for c in result.components] == ["X1", "X2", "X3", "X4", "X5"]
    assert close(component(result, "X5"), "1.5")
    assert result.thresholds[1].value == Decimal("2.90")
    assert "no market value of equity" in result.interpretation


def test_altman_original_z_for_a_manufacturer_with_market_value_of_equity() -> None:
    result = altman("3571", market_cap=market_cap("1500"))
    assert result.variant == "Z"
    assert close(result.total.value, "4.595")
    assert result.zone == "safe"
    assert close(component(result, "X4"), "3")  # 1500 / 500
    x4 = next(c for c in result.components if c.name == "X4")
    assert x4.value.inputs[0].value == Decimal(1500)  # the market value is an input
    assert [t.value for t in result.thresholds] == [Decimal("1.81"), Decimal("2.99")]


@pytest.mark.parametrize(
    ("revenue", "expected_zone"),
    [
        ("299", "grey"),  # Z = 2.99 exactly: not above the safe threshold
        ("299.01", "safe"),
        ("181", "grey"),  # Z = 1.81 exactly: not below the distress threshold
        ("180.99", "distress"),
    ],
)
def test_altman_zone_boundaries_are_exact(revenue: str, expected_zone: str) -> None:
    # Only X5 contributes: current assets = current liabilities, retained earnings = 0,
    # operating income = 0, market value of equity = 0. Z = 1.0 * X5 = revenue / 100.
    data = {
        "total_current_assets": {2025: 100},
        "total_current_liabilities": {2025: 100},
        "total_assets": {2025: 100},
        "retained_earnings": {2025: 0},
        "operating_income": {2025: 0},
        "total_liabilities": {2025: 500},
        "revenue": {2025: revenue},
    }
    result = score(data, (2025,), sic_code="3571", market_cap=market_cap("0")).get("altman_z", 2025)
    assert result.variant == "Z"
    assert result.total.value == Decimal(revenue) / 100
    assert result.zone == expected_zone


def test_altman_distress_zone_with_negative_retained_earnings() -> None:
    # X2 = -300 / 1000 = -0.3
    # Z'' = 1.312 + 3.26*(-0.3) + 1.008 + 1.05 = 1.312 - 0.978 + 1.008 + 1.05 = 2.392
    data = {**ALTMAN, "retained_earnings": {2025: -300}}
    result = score(data, (2025,), sic_code="7372").get("altman_z", 2025)
    assert close(result.total.value, "2.392")
    assert result.zone == "grey"
    # also weak operating income: X3 = 0 -> Z'' = 2.392 - 1.008 = 1.384 (grey), X4 negative equity
    data2 = {**data, "operating_income": {2025: 0}, "total_equity": {2025: -200}}
    # X4 = -200 / 500 = -0.4 -> 1.05 * -0.4 = -0.42 ; Z'' = 1.312 - 0.978 + 0 - 0.42 = -0.086
    result2 = score(data2, (2025,), sic_code="7372").get("altman_z", 2025)
    assert close(result2.total.value, "-0.086")
    assert result2.zone == "distress"


def test_altman_gaps_and_unknown_sic() -> None:
    data = {k: v for k, v in ALTMAN.items() if k != "retained_earnings"}
    result = score(data, (2025,), sic_code="7372").get("altman_z", 2025)
    assert result.total.value is None
    assert result.total.note is not None
    assert "no value for X2" in result.total.note
    assert close(component(result, "X1"), "0.2")
    unknown = score(ALTMAN, (2025,), sic_code=None).get("altman_z", 2025)
    assert unknown.variant == "Z''"
    assert "SIC code unknown" in unknown.interpretation
    assert any("SIC code is unknown" in text for text in unknown.limitations)


def test_altman_original_z_is_a_gap_when_the_market_value_is_missing_for_that_year() -> None:
    # a market value for another fiscal year does not count: Z' is used instead
    result = score(ALTMAN, (2025,), sic_code="3571", market_cap={2024: market_cap("1")[2025]}).get(
        "altman_z", 2025
    )
    assert result.variant == "Z'"


# =========================================================================================
# Piotroski F-Score
#
#                               2023    2024    2025
#   total_assets                1000    1100    1200
#   net_income                          66      88
#   operating_cash_flow                         110
#   long_term_debt                      126     115
#   total_current_assets                450     600
#   total_current_liabilities           300     300
#   shares_outstanding                  100     98
#   gross_profit                        360     450
#   revenue                             900     1000
#
#   F_ROA          ROA = 88 / 1100 (beginning assets) = 0.08            > 0         -> 1
#   F_CFO          CFO = 110 / 1100 = 0.10                              > 0         -> 1
#   F_DELTA_ROA    0.08 - 66/1000 = 0.08 - 0.066 = 0.014                > 0         -> 1
#   F_ACCRUAL      (88 - 110) / 1100 = -0.02                            < 0         -> 1
#   F_DELTA_LEVER  115/avg(1200,1100) - 126/avg(1100,1000)
#                  = 115/1150 - 126/1050 = 0.10 - 0.12 = -0.02          <= 0        -> 1
#   F_DELTA_LIQUID 600/300 - 450/300 = 2.0 - 1.5 = +0.5                 > 0         -> 1
#   F_EQ_OFFER     98/100 - 1 = -0.02                                   <= 0        -> 1
#   F_DELTA_MARGIN 450/1000 - 360/900 = 0.45 - 0.40 = +0.05             > 0         -> 1
#   F_DELTA_TURN   1000/1100 - 900/1000 = 0.909090... - 0.9 = +0.00909  > 0         -> 1
#                                                                      F = 9 (strong)
# =========================================================================================

YEARS3 = (2023, 2024, 2025)
PIOTROSKI: dict[str, dict[int, Any]] = {
    "total_assets": {2023: 1000, 2024: 1100, 2025: 1200},
    "net_income": {2024: 66, 2025: 88},
    "operating_cash_flow": {2025: 110},
    "long_term_debt": {2024: 126, 2025: 115},
    "total_current_assets": {2024: 450, 2025: 600},
    "total_current_liabilities": {2024: 300, 2025: 300},
    "shares_outstanding": {2024: 100, 2025: 98},
    "gross_profit": {2024: 360, 2025: 450},
    "revenue": {2024: 900, 2025: 1000},
}


def piotroski(data: dict[str, dict[int, Any]], years: tuple[int, ...] = YEARS3) -> ScoreResult:
    return score(data, years).get("piotroski_f", 2025)


def test_piotroski_worked_example_all_nine_pass() -> None:
    result = piotroski(PIOTROSKI)
    expected = {
        "F_ROA": "0.08",
        "F_CFO": "0.1",
        "F_DELTA_ROA": "0.014",
        "F_ACCRUAL": "-0.02",
        "F_DELTA_LEVER": "-0.02",
        "F_DELTA_LIQUID": "0.5",
        "F_EQ_OFFER": "-0.02",
        "F_DELTA_MARGIN": "0.05",
    }
    for name, value in expected.items():
        assert close(component(result, name), value), name
    assert close(component(result, "F_DELTA_TURN"), str(Decimal(1000) / 1100 - Decimal("0.9")))
    assert all(c.passed for c in result.components)
    assert [c.contribution for c in result.components] == [Decimal(1)] * 9
    assert result.total.value == Decimal(9)
    assert result.zone == "strong"
    assert len(result.components) == 9
    assert "All nine signals are positive" in result.interpretation


def test_piotroski_criteria_are_reported_individually_with_their_rule() -> None:
    result = piotroski(PIOTROSKI)
    assert [c.name for c in result.components] == [
        "F_ROA",
        "F_CFO",
        "F_DELTA_ROA",
        "F_ACCRUAL",
        "F_DELTA_LEVER",
        "F_DELTA_LIQUID",
        "F_EQ_OFFER",
        "F_DELTA_MARGIN",
        "F_DELTA_TURN",
    ]
    liquid = next(c for c in result.components if c.name == "F_DELTA_LIQUID")
    assert liquid.label == "Current ratio improved"
    assert liquid.value.note is not None
    assert "pass if > 0" in liquid.value.note
    assert [t.value for t in result.thresholds] == [Decimal(8), Decimal(2)]


def test_piotroski_eight_is_still_strong_and_the_failure_is_named() -> None:
    data = {**PIOTROSKI, "shares_outstanding": {2024: 100, 2025: 105}}  # shares up 5 %
    result = piotroski(data)
    assert result.total.value == Decimal(8)
    assert result.zone == "strong"
    assert [c.name for c in result.components if c.passed is False] == ["F_EQ_OFFER"]
    assert "Failed: F_EQ_OFFER" in result.interpretation


def test_piotroski_all_signals_failing_is_weak() -> None:
    data = {
        "total_assets": {2023: 1000, 2024: 1100, 2025: 1200},
        "net_income": {2024: 66, 2025: -10},  # ROA < 0, ROA fell
        "operating_cash_flow": {2025: -20},  # CFO < 0, and (NI - CFO) / assets = +0.009
        "long_term_debt": {2024: 126, 2025: 300},  # leverage up
        "total_current_assets": {2024: 450, 2025: 300},
        "total_current_liabilities": {2024: 300, 2025: 300},  # current ratio 1.5 -> 1.0
        "shares_outstanding": {2024: 100, 2025: 110},  # shares up
        "gross_profit": {2024: 360, 2025: 240},  # margin 0.40 -> 0.30
        "revenue": {2024: 900, 2025: 800},  # turnover 0.9 -> 0.727
    }
    result = piotroski(data)
    assert result.total.value == Decimal(0)
    assert result.zone == "weak"
    assert not any(c.passed for c in result.components)


def test_piotroski_unchanged_leverage_does_not_cost_a_point() -> None:
    # no long-term debt at all in both years: the leverage change is exactly 0 -> pass
    data = {**PIOTROSKI, "long_term_debt": {2024: 0, 2025: 0}}
    lever = next(c for c in piotroski(data).components if c.name == "F_DELTA_LEVER")
    assert lever.value.value == Decimal(0)
    assert lever.passed is True


def test_piotroski_needs_three_balance_sheet_dates() -> None:
    data = {k: {y: v for y, v in d.items() if y >= 2024} for k, d in PIOTROSKI.items()}
    result = piotroski(data, years=(2024, 2025))
    assert result.total.value is None
    assert result.zone is None
    assert result.total.note is not None
    assert "no value for F_DELTA_ROA, F_DELTA_LEVER, F_DELTA_TURN" in result.total.note
    assert "6 of 6 evaluable criteria passed" in result.total.note
    # the criteria that can be evaluated are still shown with their result
    assert [c.name for c in result.components if c.passed] == [
        "F_ROA",
        "F_CFO",
        "F_ACCRUAL",
        "F_DELTA_LIQUID",
        "F_EQ_OFFER",
        "F_DELTA_MARGIN",
    ]


def test_piotroski_equity_offer_falls_back_to_diluted_weighted_shares() -> None:
    data = {
        **{k: v for k, v in PIOTROSKI.items() if k != "shares_outstanding"},
        "shares_diluted_weighted": {2024: 100, 2025: 99},
    }
    offer = next(c for c in piotroski(data).components if c.name == "F_EQ_OFFER")
    assert close(offer.value.value, "-0.01")  # 99 / 100 - 1
    assert offer.passed is True
    assert offer.value.note is not None
    assert "shares_diluted_weighted used because shares_outstanding" in offer.value.note


def test_piotroski_equity_offer_never_mixes_the_two_share_counts() -> None:
    # year-end shares only for 2025, diluted weighted for both: use diluted for both years
    data = {
        **PIOTROSKI,
        "shares_outstanding": {2025: 50},
        "shares_diluted_weighted": {2024: 100, 2025: 101},
    }
    offer = next(c for c in piotroski(data).components if c.name == "F_EQ_OFFER")
    assert close(offer.value.value, "0.01")  # 101 / 100 - 1, not 50 / 100 - 1
    assert offer.passed is False
    assert {v.source.xbrl_concept for v in offer.value.inputs} == {
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
    }


def test_piotroski_equity_offer_gap_when_no_share_count_exists() -> None:
    data = {k: v for k, v in PIOTROSKI.items() if k != "shares_outstanding"}
    result = piotroski(data)
    offer = next(c for c in result.components if c.name == "F_EQ_OFFER")
    assert offer.value.value is None
    assert offer.passed is None
    assert result.total.value is None
    assert result.total.note is not None
    assert "no value for F_EQ_OFFER" in result.total.note
    assert "8 of 8 evaluable criteria passed" in result.total.note


# =========================================================================================
# Accruals ratio
#
#   accruals = net income - operating cash flow = 150 - 40 = 110
#   average total assets = (1000 + 1200) / 2 = 1100
#   ratio = 110 / 1100 = 0.10     (exactly on the 10 % line: not above it -> normal)
# =========================================================================================


def accruals(net_income: int, cash_flow: int) -> ScoreResult:
    data = {
        "net_income": {2025: net_income},
        "operating_cash_flow": {2025: cash_flow},
        "total_assets": {2024: 1000, 2025: 1200},
    }
    return score(data, YEARS2).get("accruals_ratio", 2025)


def test_accruals_worked_example() -> None:
    result = accruals(150, 40)
    assert result.total.value == Decimal("0.1")  # 110 / 1100
    assert result.total.unit is Unit.RATIO
    assert result.zone == "normal"
    assert {c.name: c.value.value for c in result.components} == {
        "accruals": Decimal(110),
        "average_total_assets": Decimal(1100),
    }
    assert [(t.label, t.value) for t in result.thresholds] == [
        ("elevated", Decimal("0.10")),
        ("high", Decimal("0.25")),
    ]
    assert "Sloan" in result.reference


@pytest.mark.parametrize(
    ("net_income", "cash_flow", "expected_ratio", "zone"),
    [
        (160, 40, "0.10909090909090909090909090909", "elevated"),  # 120 / 1100
        (315, 40, "0.25", "elevated"),  # 275 / 1100, exactly on the 25 % line
        (340, 40, "0.27272727272727272727272727273", "high"),  # 300 / 1100
        (40, 300, "-0.23636363636363636363636363636", "elevated"),  # -260 / 1100
        (40, 330, "-0.26363636363636363636363636364", "high"),  # -290 / 1100
    ],
)
def test_accruals_bands(net_income: int, cash_flow: int, expected_ratio: str, zone: str) -> None:
    result = accruals(net_income, cash_flow)
    assert result.zone == zone
    assert close(result.total.value, expected_ratio)


def test_accruals_text_names_the_direction() -> None:
    assert "earnings exceed operating cash flow" in accruals(340, 40).interpretation
    assert "operating cash flow exceeds earnings" in accruals(40, 330).interpretation


def test_accruals_needs_two_years_of_assets() -> None:
    data = {
        "net_income": {2024: 150},
        "operating_cash_flow": {2024: 40},
        "total_assets": {2024: 1000},
    }
    result = score(data, (2024,)).get("accruals_ratio", 2024)
    assert result.total.value is None
    assert result.total.note is not None
    assert "no value for average_total_assets" in result.total.note
    assert component(result, "accruals") == Decimal(110)  # still visible


# =========================================================================================
# Industry exclusions
# =========================================================================================


@pytest.mark.parametrize(
    ("sic", "expected"),
    [
        ("6021", "bank or other depository institution"),  # JPMorgan, Bank of America
        ("6199", "non-depository credit institution"),
        ("6211", "securities or commodity broker, dealer or exchange"),  # Goldman Sachs
        ("6311", "insurance carrier"),  # life insurers
        ("6411", "insurance agent or broker"),
        ("6798", "real estate investment trust (REIT)"),  # REITs
        ("6770", "holding company or investment office"),  # blank check companies
    ],
)
def test_financial_companies_are_excluded_with_a_reason(sic: str, expected: str) -> None:
    reason = exclusion_reason(sic, "Some Industry")
    assert reason is not None
    assert expected in reason
    assert f"SIC {sic}" in reason
    assert "Some Industry" in reason


@pytest.mark.parametrize("sic", ["3571", "7372", "5331", "2834", "1311", "6500", "6531", "4911"])
def test_operating_companies_are_not_excluded(sic: str) -> None:
    assert exclusion_reason(sic) is None


@pytest.mark.parametrize("sic", [None, "", "abcd"])
def test_unknown_sic_does_not_exclude(sic: str | None) -> None:
    assert exclusion_reason(sic) is None


@pytest.mark.parametrize(
    ("sic", "manufacturer"),
    [
        ("2000", True),
        ("3571", True),
        ("3999", True),
        ("1999", False),
        ("4000", False),
        (None, False),
    ],
)
def test_manufacturer_classification(sic: str | None, manufacturer: bool) -> None:
    assert is_manufacturer(sic) is manufacturer


def test_a_bank_is_not_applicable_for_all_four_scores() -> None:
    data = {**BENEISH, **PIOTROSKI, **ALTMAN}
    scores = compute_scores(
        make_statements(data, YEARS3),
        sic_code="6021",
        industry="National Commercial Banks",
    )
    for name in SCORE_NAMES:
        result = scores.latest(name)
        assert result.applicable is False
        assert result.not_applicable_reason is not None
        assert "bank or other depository institution" in result.not_applicable_reason
        assert result.total.value is None
        assert result.total.note is not None
        assert "not applicable" in result.total.note
        assert result.zone is None
        assert result.components == ()
        assert result.interpretation.startswith("Not applicable:")
        assert result.limitations  # the limitations are still delivered
        assert result.reference


# =========================================================================================
# The container
# =========================================================================================


def test_scores_cover_every_fiscal_year_and_all_four_scores() -> None:
    scores = score({**BENEISH, **ALTMAN}, YEARS2)
    assert tuple(scores.results) == SCORE_NAMES
    for name in SCORE_NAMES:
        assert tuple(scores.results[name]) == YEARS2
    assert scores.latest("beneish_m").fiscal_year == 2025
    assert scores.company_name == "TestCo"
    assert scores.sic_code == "7372"


def test_total_is_a_value_with_provenance() -> None:
    total = score(BENEISH, YEARS2).get("beneish_m", 2025).total
    assert total.source.provider == "stock_forensics"
    assert total.source.retrieved_at == RETRIEVED
    assert total.period.fiscal_year == 2025
    assert total.note is not None
    assert total.note.startswith("M = -4.84 + 0.920 DSRI")
    # a component's value goes back to statement values with accession numbers
    dsri = total.inputs[0]
    assert dsri.inputs[0].source.accession_number == "0000000001-25-000001"


def test_scores_roundtrip_through_json() -> None:
    scores = score({**BENEISH, **PIOTROSKI, **ALTMAN}, YEARS3)
    assert Scores.model_validate_json(scores.model_dump_json()) == scores


def test_unknown_sic_adds_a_limitation_to_every_score() -> None:
    scores = score(BENEISH, YEARS2, sic_code=None)
    for name in SCORE_NAMES:
        assert any("SIC code is unknown" in text for text in scores.latest(name).limitations)
