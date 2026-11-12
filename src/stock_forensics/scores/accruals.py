"""Accruals ratio: how much of reported earnings is not backed by cash.

accruals ratio = (net income - operating cash flow) / average total assets

Positive means earnings exceed cash flow (accrual-driven earnings, lower quality); strongly
negative means cash flow exceeds earnings. Based on Sloan (1996), who found that firms with
high accruals relative to cash flows have lower earnings persistence.
"""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.metrics.engine import MetricSpec, Ref, average, divide
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

NAME = "accruals_ratio"
LABEL = "Accruals ratio"
FORMULA = (
    "accruals ratio = (net income - operating cash flow) / "
    "average(total assets, prior-year total assets)"
)
REFERENCE = (
    "Sloan, R. G. (1996). Do Stock Prices Fully Reflect Information in Accruals and Cash "
    "Flows about Future Earnings? The Accounting Review 71(3), 289-315."
)
ELEVATED = Decimal("0.10")
HIGH = Decimal("0.25")
LIMITATIONS = (
    "The 10 % and 25 % bands are practitioner rules of thumb; sources disagree, and Sloan "
    "ranked firms against each other instead of using a fixed cutoff.",
    "Acquisitions, divestitures and one-off non-cash items distort accruals.",
    "Growing companies naturally build working capital, which creates accruals without "
    "manipulation.",
    "Not meaningful for banks, insurers and other financial companies (marked not applicable).",
    "Needs two consecutive fiscal years (average total assets).",
)

_ACCRUALS = MetricSpec(
    "accruals",
    "accruals",
    None,
    Unit.USD,
    "accruals = net income - operating cash flow",
    refs=(Ref("net_income"), Ref("operating_cash_flow")),
    calc=lambda v: v[0] - v[1],
)
_AVG_ASSETS = MetricSpec(
    "average_total_assets",
    "average_total_assets",
    None,
    Unit.USD,
    "average total assets = average(total assets, prior-year total assets)",
    refs=(Ref("total_assets"), Ref("total_assets", -1)),
    calc=lambda v: average(v[0], v[1]),
)

THRESHOLDS = (
    Threshold(label="elevated", value=ELEVATED, meaning="absolute ratio above 10 %: elevated"),
    Threshold(label="high", value=HIGH, meaning="absolute ratio above 25 %: high"),
)


def compute_accruals(
    statements: Statements, fiscal_year: int, *, excluded: str | None
) -> ScoreResult:
    if excluded is not None:
        return not_applicable(
            name=NAME,
            label=LABEL,
            fiscal_year=fiscal_year,
            statements=statements,
            unit=Unit.RATIO,
            formula=FORMULA,
            reference=REFERENCE,
            reason=excluded,
            limitations=LIMITATIONS,
        )
    values = evaluate((_ACCRUALS, _AVG_ASSETS), statements, fiscal_year)
    components = tuple(Component(name=n, label=n, value=v) for n, v in values.items())
    common = dict(
        name=NAME,
        label=LABEL,
        fiscal_year=fiscal_year,
        formula=FORMULA,
        reference=REFERENCE,
        applicable=True,
        components=components,
        thresholds=THRESHOLDS,
        limitations=LIMITATIONS,
    )
    missing_names = gaps(components)
    if missing_names:
        reason = f"cannot compute {LABEL}: no value for {', '.join(missing_names)}"
        return ScoreResult(
            **common,  # type: ignore[arg-type]
            total=total_missing(statements, fiscal_year, Unit.RATIO, reason, components),
            interpretation=f"{reason}. See the notes of those components.",
        )
    accruals = values["accruals"].value
    assets = values["average_total_assets"].value
    assert accruals is not None
    assert assets is not None
    ratio = divide(accruals, assets, "average total assets are zero")
    if isinstance(ratio, str):
        reason = f"cannot compute {LABEL}: {ratio}"
        return ScoreResult(
            **common,  # type: ignore[arg-type]
            total=total_missing(statements, fiscal_year, Unit.RATIO, reason, components),
            interpretation=reason + ".",
        )
    size = abs(ratio)
    if size > HIGH:
        zone, level = "high", "high"
    elif size > ELEVATED:
        zone, level = "elevated", "elevated"
    else:
        zone, level = "normal", "normal"
    direction = (
        "earnings exceed operating cash flow (accrual-driven)"
        if ratio > 0
        else "operating cash flow exceeds earnings"
    )
    return ScoreResult(
        **common,  # type: ignore[arg-type]
        total=total_value(
            statements,
            fiscal_year,
            Unit.RATIO,
            ratio,
            f"{FORMULA}; ratio = {ratio:.4f}",
            components,
        ),
        zone=zone,
        interpretation=(
            f"Accruals ratio {ratio:.1%} is {level}: {direction}. Bands of 10 % and 25 % "
            "are rules of thumb."
        ),
    )
