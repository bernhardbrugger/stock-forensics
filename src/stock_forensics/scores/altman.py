"""Altman Z-Score family: distress risk from five (or four) balance sheet and income ratios.

* Z   (Altman 1968, listed manufacturers):   1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4 + 1.0 X5,
  X4 = market value of equity / total liabilities. Zones: above 2.99 safe, 1.81 to 2.99 grey,
  below 1.81 distress. Needs market data.
* Z'  (Altman 1983, book-value version):      0.717 X1 + 0.847 X2 + 3.107 X3 + 0.420 X4 + 0.998 X5,
  X4 = book equity / total liabilities. Zones: above 2.90, 1.23 to 2.90, below 1.23.
* Z'' (Altman 1995, non-manufacturers):       6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4,
  X4 = book equity / total liabilities. Zones: above 2.60, 1.10 to 2.60, below 1.10.

X1 = working capital / total assets, X2 = retained earnings / total assets,
X3 = EBIT / total assets (operating income stands in for EBIT), X5 = revenue / total assets.

Manufacturers (SIC 2000-3999) get Z when a market value of equity is supplied, otherwise Z'.
All other companies get Z''.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from stock_forensics.metrics.engine import (
    MetricSpec,
    Ref,
    compute,
    divide,
    make_missing,
    make_value,
)
from stock_forensics.models import Unit, Value
from stock_forensics.normalize.statements import Statements
from stock_forensics.scores.base import (
    Component,
    ScoreResult,
    Threshold,
    evaluate,
    gaps,
    mapping_value,
    not_applicable,
    total_missing,
    total_value,
)
from stock_forensics.scores.industry import is_manufacturer

NAME = "altman_z"
LABEL = "Altman Z-Score"
REFERENCE = (
    "Altman, E. I. (1968). Financial Ratios, Discriminant Analysis and the Prediction of "
    "Corporate Bankruptcy. Journal of Finance 23(4), 589-609 (Z); Altman, E. I. (1983). "
    "Corporate Financial Distress (Z'); Altman, E. I. (1995). Predicting financial distress "
    "of companies: revisiting the Z-score and ZETA models (Z'')."
)


@dataclass(frozen=True)
class Variant:
    name: str
    weights: dict[str, Decimal]
    distress_below: Decimal
    safe_above: Decimal
    formula: str


Z_ORIGINAL = Variant(
    "Z",
    {
        "X1": Decimal("1.2"),
        "X2": Decimal("1.4"),
        "X3": Decimal("3.3"),
        "X4": Decimal("0.6"),
        "X5": Decimal("1.0"),
    },
    Decimal("1.81"),
    Decimal("2.99"),
    "Z = 1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4 + 1.0 X5 (X4 = market equity / total liabilities)",
)
Z_PRIME = Variant(
    "Z'",
    {
        "X1": Decimal("0.717"),
        "X2": Decimal("0.847"),
        "X3": Decimal("3.107"),
        "X4": Decimal("0.420"),
        "X5": Decimal("0.998"),
    },
    Decimal("1.23"),
    Decimal("2.90"),
    "Z' = 0.717 X1 + 0.847 X2 + 3.107 X3 + 0.420 X4 + 0.998 X5 "
    "(X4 = book equity / total liabilities)",
)
Z_DOUBLE_PRIME = Variant(
    "Z''",
    {
        "X1": Decimal("6.56"),
        "X2": Decimal("3.26"),
        "X3": Decimal("6.72"),
        "X4": Decimal("1.05"),
    },
    Decimal("1.10"),
    Decimal("2.60"),
    "Z'' = 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4 (X4 = book equity / total liabilities)",
)

LIMITATIONS = (
    "Built on US manufacturers (Z, 1968); Z' and Z'' are published adaptations for private "
    "firms and non-manufacturers. Zones come from the original samples and are not probabilities.",
    "Uses book values; equity can be negative or tiny after large buybacks (Apple), which "
    "lowers X4 without real distress.",
    "Operating income stands in for EBIT.",
    "Not meaningful for banks, insurers and other financial companies (marked not applicable).",
    "Asset-light and fast-growing companies can look weak on retained earnings (X2).",
)


def _x1(v: list[Decimal]) -> Decimal | str:
    ca, cl, assets = v
    return divide(ca - cl, assets, "total assets are zero")


def _over_assets(v: list[Decimal]) -> Decimal | str:
    return divide(v[0], v[1], "total assets are zero")


def _x4_book(v: list[Decimal]) -> Decimal | str:
    return divide(v[0], v[1], "total liabilities are zero")


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


_X1 = _spec(
    "X1",
    "X1 = (current assets - current liabilities) / total assets",
    (Ref("total_current_assets"), Ref("total_current_liabilities"), Ref("total_assets")),
    _x1,
)
_X2 = _spec(
    "X2",
    "X2 = retained earnings / total assets",
    (Ref("retained_earnings"), Ref("total_assets")),
    _over_assets,
)
_X3 = _spec(
    "X3",
    "X3 = EBIT (operating income) / total assets",
    (Ref("operating_income"), Ref("total_assets")),
    _over_assets,
)
_X4_BOOK = _spec(
    "X4",
    "X4 = book equity / total liabilities",
    (Ref("total_equity"), Ref("total_liabilities")),
    _x4_book,
)
_X5 = _spec(
    "X5",
    "X5 = revenue / total assets",
    (Ref("revenue"), Ref("total_assets")),
    _over_assets,
)
_X4_MARKET = MetricSpec(
    "X4",
    "X4",
    None,
    Unit.RATIO,
    "X4 = market value of equity / total liabilities",
    refs=(Ref("total_liabilities"),),
)


def _x4_market(statements: Statements, fiscal_year: int, market_cap: Value) -> Value:
    liabilities_spec = MetricSpec(
        "total_liabilities",
        "total_liabilities",
        None,
        Unit.USD,
        "total_liabilities",
        refs=(Ref("total_liabilities"),),
        calc=lambda v: v[0],
    )
    liabilities = compute(liabilities_spec, statements, fiscal_year)
    inputs = [market_cap, *liabilities.inputs]
    if market_cap.value is None:
        return make_missing(
            _X4_MARKET, statements, fiscal_year, "market value of equity is missing", inputs
        )
    if liabilities.value is None:
        return make_missing(
            _X4_MARKET, statements, fiscal_year, "total liabilities are missing", inputs
        )
    ratio = divide(market_cap.value, liabilities.value, "total liabilities are zero")
    if isinstance(ratio, str):
        return make_missing(_X4_MARKET, statements, fiscal_year, ratio, inputs)
    return make_value(_X4_MARKET, statements, fiscal_year, ratio, inputs)


def _components(
    variant: Variant,
    statements: Statements,
    fiscal_year: int,
    market_cap: Value | None,
) -> tuple[Component, ...]:
    specs = [_X1, _X2, _X3]
    if variant is Z_ORIGINAL:
        values = evaluate(tuple(specs), statements, fiscal_year)
        assert market_cap is not None
        values["X4"] = _x4_market(statements, fiscal_year, market_cap)
    else:
        specs.append(_X4_BOOK)
        values = evaluate(tuple(specs), statements, fiscal_year)
    if "X5" in variant.weights:
        values["X5"] = compute(_X5, statements, fiscal_year)
    ordered = {name: values[name] for name in variant.weights}
    return tuple(
        Component(
            name=name,
            label=name,
            value=value,
            weight=variant.weights[name],
            contribution=None if value.value is None else variant.weights[name] * value.value,
        )
        for name, value in ordered.items()
    )


def choose_variant(sic_code: str | None, market_cap: Value | None) -> tuple[Variant, str]:
    if is_manufacturer(sic_code):
        if market_cap is not None:
            return (
                Z_ORIGINAL,
                "manufacturer (SIC 2000-3999) with market value of equity: original Z",
            )
        return Z_PRIME, (
            "manufacturer (SIC 2000-3999) but no market value of equity is available: "
            "book-value variant Z' instead of the original Z"
        )
    if sic_code is None:
        return Z_DOUBLE_PRIME, "SIC code unknown: Z'' (non-manufacturer variant) used"
    return Z_DOUBLE_PRIME, "non-manufacturer (SIC outside 2000-3999): Z''"


def compute_altman(
    statements: Statements,
    fiscal_year: int,
    *,
    sic_code: str | None,
    excluded: str | None,
    market_cap: Mapping[int, Value] | None = None,
) -> ScoreResult:
    if excluded is not None:
        return not_applicable(
            name=NAME,
            label=LABEL,
            fiscal_year=fiscal_year,
            statements=statements,
            unit=Unit.SCORE,
            formula="Z family (Z, Z', Z'')",
            reference=REFERENCE,
            reason=excluded,
            limitations=LIMITATIONS,
        )
    cap = mapping_value(market_cap, fiscal_year)
    variant, why = choose_variant(sic_code, cap)
    components = _components(variant, statements, fiscal_year, cap)
    thresholds = (
        Threshold(label="distress below", value=variant.distress_below, meaning="distress zone"),
        Threshold(label="safe above", value=variant.safe_above, meaning="safe zone"),
    )
    common = dict(
        name=NAME,
        label=f"{LABEL} ({variant.name})",
        fiscal_year=fiscal_year,
        variant=variant.name,
        formula=variant.formula,
        reference=REFERENCE,
        applicable=True,
        components=components,
        thresholds=thresholds,
        limitations=LIMITATIONS,
    )
    missing_names = gaps(components)
    if missing_names:
        reason = f"cannot compute {variant.name}: no value for {', '.join(missing_names)} ({why})"
        return ScoreResult(
            **common,  # type: ignore[arg-type]
            total=total_missing(statements, fiscal_year, Unit.SCORE, reason, components),
            interpretation=f"{reason}. See the notes of those components.",
        )
    score = sum((c.contribution or Decimal(0) for c in components), Decimal(0))
    if score > variant.safe_above:
        zone, text = "safe", "safe zone"
    elif score < variant.distress_below:
        zone, text = "distress", "distress zone"
    else:
        zone, text = "grey", "grey zone (neither clearly safe nor clearly distressed)"
    interpretation = (
        f"{variant.name} = {score:.2f}: {text} (distress below {variant.distress_below}, "
        f"safe above {variant.safe_above}). Variant chosen because: {why}."
    )
    return ScoreResult(
        **common,  # type: ignore[arg-type]
        total=total_value(
            statements,
            fiscal_year,
            Unit.SCORE,
            score,
            f"{variant.formula}; {variant.name} = {score:.4f}; {why}",
            components,
        ),
        zone=zone,
        interpretation=interpretation,
    )
