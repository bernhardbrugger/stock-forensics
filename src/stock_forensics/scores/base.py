"""Result models shared by the forensic scores.

Every score returns a :class:`ScoreResult`: the total (a ``Value`` whose ``inputs`` are the
component values), all components, the thresholds, the zone, a plain-language interpretation,
the known limitations and the formula with its literature reference. If a score cannot be
computed or does not apply, ``total.value`` is ``None`` and the reason is in ``total.note``;
the components that could be computed stay visible.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from stock_forensics.metrics.engine import MetricSpec, compute, computed_source, metric_period
from stock_forensics.models import Source, Unit, Value, missing
from stock_forensics.normalize.statements import Statements


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Threshold(_Frozen):
    label: str
    value: Decimal
    meaning: str


class Component(_Frozen):
    """One building block of a score."""

    name: str
    label: str
    value: Value  # the measured quantity (an index, a ratio, a change); may be a gap
    weight: Decimal | None = None  # coefficient in the weighted sum (Beneish, Altman)
    contribution: Decimal | None = None  # weight x value, or the 0/1 point (Piotroski)
    passed: bool | None = None  # Piotroski: did the criterion pass?


class ScoreResult(_Frozen):
    name: str  # "beneish_m", "altman_z", "piotroski_f", "accruals_ratio"
    label: str
    fiscal_year: int
    variant: str | None = None  # Altman: "Z", "Z'" or "Z''"
    formula: str
    reference: str
    applicable: bool
    not_applicable_reason: str | None = None
    total: Value
    constant: Decimal | None = None  # intercept of the weighted sum, if any
    components: tuple[Component, ...] = ()
    thresholds: tuple[Threshold, ...] = ()
    zone: str | None = None  # machine-readable classification, None if no total
    interpretation: str
    limitations: tuple[str, ...] = ()


class Scores(_Frozen):
    cik: int
    company_name: str
    sic_code: str | None
    industry: str | None
    fiscal_years: tuple[int, ...]
    results: dict[str, dict[int, ScoreResult]]  # score name -> fiscal year -> result
    source: Source

    def get(self, name: str, fiscal_year: int) -> ScoreResult:
        return self.results[name][fiscal_year]

    def latest(self, name: str) -> ScoreResult:
        return self.results[name][self.fiscal_years[-1]]


# --- helpers used by the individual scores ------------------------------------------------


def evaluate(
    specs: tuple[MetricSpec, ...], statements: Statements, fiscal_year: int
) -> dict[str, Value]:
    """Compute the component values of a score with the metrics engine."""
    return {spec.name: compute(spec, statements, fiscal_year) for spec in specs}


def total_value(
    statements: Statements,
    fiscal_year: int,
    unit: Unit,
    number: Decimal,
    note: str,
    components: tuple[Component, ...],
) -> Value:
    return Value(
        value=number,
        unit=unit,
        period=metric_period(statements, fiscal_year),
        source=computed_source(statements),
        note=note,
        inputs=tuple(c.value for c in components),
    )


def total_missing(
    statements: Statements,
    fiscal_year: int,
    unit: Unit,
    reason: str,
    components: tuple[Component, ...],
) -> Value:
    return missing(
        reason,
        unit=unit,
        period=metric_period(statements, fiscal_year),
        source=computed_source(statements),
        inputs=tuple(c.value for c in components),
    )


def gaps(components: tuple[Component, ...]) -> list[str]:
    """Names of the components that could not be computed."""
    return [c.name for c in components if c.value.value is None]


def not_applicable(
    *,
    name: str,
    label: str,
    fiscal_year: int,
    statements: Statements,
    unit: Unit,
    formula: str,
    reference: str,
    reason: str,
    limitations: tuple[str, ...],
) -> ScoreResult:
    return ScoreResult(
        name=name,
        label=label,
        fiscal_year=fiscal_year,
        formula=formula,
        reference=reference,
        applicable=False,
        not_applicable_reason=reason,
        total=total_missing(statements, fiscal_year, unit, f"{label} not applicable: {reason}", ()),
        interpretation=f"Not applicable: {reason}.",
        limitations=limitations,
    )


def mapping_value(values: Mapping[int, Value] | None, fiscal_year: int) -> Value | None:
    return values.get(fiscal_year) if values else None
