"""Beneish M-Score (8-variable model): a statistical screen for earnings manipulation.

M = -4.84 + 0.920 DSRI + 0.528 GMI + 0.404 AQI + 0.892 SGI + 0.115 DEPI
    - 0.172 SGAI + 4.679 TATA - 0.327 LVGI

Each index compares fiscal year t with t-1. A value above -1.78 flags a likely manipulator.
"""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.metrics.engine import MetricSpec, Ref
from stock_forensics.models import Unit
from stock_forensics.normalize.statements import Statements
from stock_forensics.scores.base import (
    Component,
    ScoreResult,
    Threshold,
    evaluate,
    gaps,
    not_applicable,
    total_missing,
    total_value,
)

NAME = "beneish_m"
LABEL = "Beneish M-Score"
FORMULA = (
    "M = -4.84 + 0.920 DSRI + 0.528 GMI + 0.404 AQI + 0.892 SGI + 0.115 DEPI "
    "- 0.172 SGAI + 4.679 TATA - 0.327 LVGI"
)
REFERENCE = (
    "Beneish, M. D. (1999). The Detection of Earnings Manipulation. "
    "Financial Analysts Journal 55(5), 24-36."
)
CONSTANT = Decimal("-4.84")
CUTOFF = Decimal("-1.78")
WEIGHTS = {
    "DSRI": Decimal("0.920"),
    "GMI": Decimal("0.528"),
    "AQI": Decimal("0.404"),
    "SGI": Decimal("0.892"),
    "DEPI": Decimal("0.115"),
    "SGAI": Decimal("-0.172"),
    "TATA": Decimal("4.679"),
    "LVGI": Decimal("-0.327"),
}
LIMITATIONS = (
    "Calibrated on US manufacturing companies from 1982 to 1992; it may not carry over to "
    "other industries or periods.",
    "High false-positive rate: many legitimate fast-growing companies score above the cutoff.",
    "A statistical screen for a closer look, not evidence of manipulation.",
    "Simplified definitions: TATA is (net income - operating cash flow) / total assets "
    "(cash-flow version) and AQI uses current assets plus net PP&E; depreciation comes from "
    "the cash flow statement.",
    "Needs two consecutive fiscal years.",
)

_ZERO = "a denominator is zero"


def _ratio_of_ratios(v: list[Decimal], zero: str = _ZERO) -> Decimal | str:
    """(a / b) / (c / d), with the inputs ordered a, b, c, d."""
    a, b, c, d = v
    if b == 0 or d == 0 or c == 0:
        return zero
    return (a / b) / (c / d)


def _gmi(v: list[Decimal]) -> Decimal | str:
    gross_before, revenue_before, gross_now, revenue_now = v
    if revenue_before == 0 or revenue_now == 0 or gross_now == 0:
        return _ZERO + " (a gross margin is zero)"
    return (gross_before / revenue_before) / (gross_now / revenue_now)


def _aqi(v: list[Decimal]) -> Decimal | str:
    ca, ppe, assets, ca_before, ppe_before, assets_before = v
    if assets == 0 or assets_before == 0:
        return "total assets are zero"
    quality = 1 - (ca + ppe) / assets
    quality_before = 1 - (ca_before + ppe_before) / assets_before
    if quality_before == 0:
        return "prior-year non-current asset share outside PP&E is zero"
    return quality / quality_before


def _sgi(v: list[Decimal]) -> Decimal | str:
    now, before = v
    if before == 0:
        return "prior-year revenue is zero"
    return now / before


def _depi(v: list[Decimal]) -> Decimal | str:
    dep_before, ppe_before, dep_now, ppe_now = v
    if dep_before + ppe_before == 0 or dep_now + ppe_now == 0:
        return _ZERO
    rate_before = dep_before / (dep_before + ppe_before)
    rate_now = dep_now / (dep_now + ppe_now)
    if rate_now == 0:
        return "current depreciation rate is zero"
    return rate_before / rate_now


def _tata(v: list[Decimal]) -> Decimal | str:
    net_income, operating_cash_flow, assets = v
    if assets == 0:
        return "total assets are zero"
    return (net_income - operating_cash_flow) / assets


def _lvgi(v: list[Decimal]) -> Decimal | str:
    cl, ltd, assets, cl_before, ltd_before, assets_before = v
    if assets == 0 or assets_before == 0:
        return "total assets are zero"
    leverage_before = (cl_before + ltd_before) / assets_before
    if leverage_before == 0:
        return "prior-year leverage is zero"
    return ((cl + ltd) / assets) / leverage_before


def _spec(name: str, formula: str, refs: tuple[Ref, ...], calc: object) -> MetricSpec:
    return MetricSpec(
        name,
        name,
        None,
        Unit.RATIO,
        formula,
        refs=refs,
        calc=calc,  # type: ignore[arg-type]
    )


SPECS: tuple[MetricSpec, ...] = (
    _spec(
        "DSRI",
        "DSRI = (receivables / revenue) / (prior-year receivables / prior-year revenue)",
        (
            Ref("accounts_receivable"),
            Ref("revenue"),
            Ref("accounts_receivable", -1),
            Ref("revenue", -1),
        ),
        _ratio_of_ratios,
    ),
    _spec(
        "GMI",
        "GMI = prior-year gross margin / gross margin",
        (Ref("gross_profit", -1), Ref("revenue", -1), Ref("gross_profit"), Ref("revenue")),
        _gmi,
    ),
    _spec(
        "AQI",
        "AQI = (1 - (current assets + net PP&E) / total assets) / same ratio of the prior year",
        (
            Ref("total_current_assets"),
            Ref("property_plant_equipment_net"),
            Ref("total_assets"),
            Ref("total_current_assets", -1),
            Ref("property_plant_equipment_net", -1),
            Ref("total_assets", -1),
        ),
        _aqi,
    ),
    _spec("SGI", "SGI = revenue / prior-year revenue", (Ref("revenue"), Ref("revenue", -1)), _sgi),
    _spec(
        "DEPI",
        "DEPI = prior-year D&A rate / D&A rate, with rate = D&A / (D&A + net PP&E)",
        (
            Ref("depreciation_amortization", -1),
            Ref("property_plant_equipment_net", -1),
            Ref("depreciation_amortization"),
            Ref("property_plant_equipment_net"),
        ),
        _depi,
    ),
    _spec(
        "SGAI",
        "SGAI = (SG&A / revenue) / (prior-year SG&A / prior-year revenue)",
        (
            Ref("selling_general_admin"),
            Ref("revenue"),
            Ref("selling_general_admin", -1),
            Ref("revenue", -1),
        ),
        _ratio_of_ratios,
    ),
    _spec(
        "TATA",
        "TATA = (net income - operating cash flow) / total assets",
        (Ref("net_income"), Ref("operating_cash_flow"), Ref("total_assets")),
        _tata,
    ),
    _spec(
        "LVGI",
        "LVGI = ((current liabilities + long-term debt) / total assets) / "
        "same ratio of the prior year",
        (
            Ref("total_current_liabilities"),
            Ref("long_term_debt"),
            Ref("total_assets"),
            Ref("total_current_liabilities", -1),
            Ref("long_term_debt", -1),
            Ref("total_assets", -1),
        ),
        _lvgi,
    ),
)

THRESHOLDS = (
    Threshold(
        label="cutoff",
        value=CUTOFF,
        meaning="above the cutoff: likely manipulator; at or below: unlikely",
    ),
)


def compute_beneish(
    statements: Statements, fiscal_year: int, *, excluded: str | None
) -> ScoreResult:
    if excluded is not None:
        return not_applicable(
            name=NAME,
            label=LABEL,
            fiscal_year=fiscal_year,
            statements=statements,
            unit=Unit.SCORE,
            formula=FORMULA,
            reference=REFERENCE,
            reason=excluded,
            limitations=LIMITATIONS,
        )
    values = evaluate(SPECS, statements, fiscal_year)
    components = tuple(
        Component(
            name=name,
            label=name,
            value=value,
            weight=WEIGHTS[name],
            contribution=None if value.value is None else WEIGHTS[name] * value.value,
        )
        for name, value in values.items()
    )
    missing_names = gaps(components)
    common = dict(
        name=NAME,
        label=LABEL,
        fiscal_year=fiscal_year,
        formula=FORMULA,
        reference=REFERENCE,
        applicable=True,
        constant=CONSTANT,
        components=components,
        thresholds=THRESHOLDS,
        limitations=LIMITATIONS,
    )
    if missing_names:
        reason = f"cannot compute {LABEL}: no value for {', '.join(missing_names)}"
        return ScoreResult(
            **common,  # type: ignore[arg-type]
            total=total_missing(statements, fiscal_year, Unit.SCORE, reason, components),
            interpretation=f"{reason}. See the notes of those components.",
        )
    score = CONSTANT + sum((c.contribution or Decimal(0) for c in components), Decimal(0))
    likely = score > CUTOFF
    biggest = sorted(components, key=lambda c: c.contribution or Decimal(0), reverse=True)[:2]
    drivers = ", ".join(f"{c.name} ({c.contribution:+.2f})" for c in biggest)
    interpretation = (
        f"M-Score {score:.2f} is {'above' if likely else 'at or below'} the cutoff of {CUTOFF}: "
        + (
            "the model flags this company as a likely earnings manipulator. "
            if likely
            else "the model does not flag this company as a likely earnings manipulator. "
        )
        + f"Largest contributions: {drivers}. This is a screen, not proof."
    )
    return ScoreResult(
        **common,  # type: ignore[arg-type]
        total=total_value(
            statements, fiscal_year, Unit.SCORE, score, f"{FORMULA}; M = {score:.4f}", components
        ),
        zone="likely_manipulator" if likely else "unlikely_manipulator",
        interpretation=interpretation,
    )
