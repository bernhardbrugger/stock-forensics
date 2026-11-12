"""Shared machinery for the metrics: evaluate a formula on normalized statements.

A metric is a formula over standard statement fields (``Ref``s). Evaluating it for a fiscal
year collects the input values, checks that all of them exist, and computes the number. The
result is a :class:`~stock_forensics.models.Value` whose ``inputs`` are the statement values
it was computed from (each with its own source), and whose ``note`` is the formula.

Missing data is explicit: if any input is missing, or the number would not be meaningful
(negative equity for a return on equity, a zero denominator, ...), the metric is a ``Value``
with ``value=None`` and the reason in ``note``. Nothing is filled with zeros or guesses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Period, Source, Unit, Value, missing
from stock_forensics.normalize.statements import Statements

PROVIDER = "stock_forensics"
Calc = Callable[[list[Decimal]], Decimal | str]  # a number, or the reason it is not meaningful


class Group(StrEnum):
    PROFITABILITY = "profitability"
    LIQUIDITY = "liquidity_leverage"
    CASH_FLOW = "cash_flow_quality"
    GROWTH = "growth"
    CAPITAL_ALLOCATION = "capital_allocation"


@dataclass(frozen=True)
class Ref:
    """An input: a statement field, ``offset`` fiscal years from the year being computed.

    ``fallback`` names a second field to use if the first is not reported (documented in the
    note of the metric), for example total equity when no noncontrolling interests exist.
    """

    field: str
    offset: int = 0
    fallback: str | None = None


@dataclass(frozen=True)
class MetricSpec:
    name: str
    label: str
    group: Group
    unit: Unit
    formula: str
    refs: tuple[Ref, ...] = ()
    calc: Calc | None = None
    custom: Callable[[MetricSpec, Statements, int], Value] | None = None


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricSeries(_Frozen):
    name: str
    label: str
    group: Group
    unit: Unit
    formula: str
    annual: dict[int, Value]  # fiscal year -> value (gaps are Values with value=None)


class Metrics(_Frozen):
    cik: int
    company_name: str
    fiscal_years: tuple[int, ...]
    series: dict[str, MetricSeries]
    source: Source  # where the underlying statements came from

    def get(self, name: str) -> MetricSeries:
        return self.series[name]

    def annual(self, name: str, fiscal_year: int) -> Value:
        return self.series[name].annual[fiscal_year]

    def latest(self, name: str) -> Value:
        return self.series[name].annual[self.fiscal_years[-1]]

    def of_group(self, group: Group) -> tuple[MetricSeries, ...]:
        return tuple(s for s in self.series.values() if s.group is group)


# --- building values --------------------------------------------------------------------


def metric_period(statements: Statements, fiscal_year: int) -> Period:
    """The fiscal year a metric refers to (taken from the revenue period of that year)."""
    return statements.series["revenue"].annual[fiscal_year].period


def _computed_source(statements: Statements) -> Source:
    # The timestamp is that of the input data, so the result stays deterministic.
    return Source(provider=PROVIDER, retrieved_at=statements.source.retrieved_at)


def _short(note: str | None) -> str:
    if not note:
        return "not available"
    return note.split(" (tried:")[0]


def _flags(used: list[tuple[str, Value]]) -> str | None:
    """Name inputs that are restated, derived or carry a caveat, so the reader notices."""
    flagged: list[str] = []
    for field, value in used:
        if not value.note:
            continue
        if value.note.startswith("restated"):
            kind = "restated"
        elif value.note.startswith("derived"):
            kind = "derived"
        else:
            kind = "caveat"
        flagged.append(f"{field} FY{value.period.fiscal_year} ({kind})")
    return f"inputs flagged: {', '.join(flagged)}" if flagged else None


def make_value(
    spec: MetricSpec,
    statements: Statements,
    fiscal_year: int,
    number: Decimal,
    inputs: list[Value],
    extra_note: str | None = None,
) -> Value:
    note = "; ".join(p for p in (spec.formula, extra_note) if p)
    return Value(
        value=number,
        unit=spec.unit,
        period=metric_period(statements, fiscal_year),
        source=_computed_source(statements),
        note=note,
        inputs=tuple(inputs),
    )


def make_missing(
    spec: MetricSpec,
    statements: Statements,
    fiscal_year: int,
    reason: str,
    inputs: list[Value],
) -> Value:
    return missing(
        f"cannot compute {spec.formula}: {reason}",
        unit=spec.unit,
        period=metric_period(statements, fiscal_year),
        source=_computed_source(statements),
        inputs=tuple(inputs),
    )


# --- evaluation -------------------------------------------------------------------------


def compute(spec: MetricSpec, statements: Statements, fiscal_year: int) -> Value:
    """Evaluate one metric for one fiscal year."""
    if spec.custom is not None:
        return spec.custom(spec, statements, fiscal_year)
    assert spec.calc is not None
    first, last = statements.fiscal_years[0], statements.fiscal_years[-1]

    used: list[tuple[str, Value]] = []
    problems: list[str] = []
    notes: list[str] = []
    for ref in spec.refs:
        year = fiscal_year + ref.offset
        field = ref.field
        value = statements.series[field].annual.get(year)
        if value is not None and value.value is None and ref.fallback:
            alternative = statements.series[ref.fallback].annual.get(year)
            if alternative is not None and alternative.value is not None:
                notes.append(f"{ref.fallback} used because {ref.field} is not reported")
                value, field = alternative, ref.fallback
        if value is None:
            problems.append(f"needs fiscal year {year} (statements cover {first}-{last})")
        else:
            if value.value is None:
                problems.append(f"{field} FY{year}: {_short(value.note)}")
            used.append((field, value))
    inputs = [v for _, v in used]
    if problems:
        return make_missing(spec, statements, fiscal_year, "; ".join(problems), inputs)

    numbers = [v.value for v in inputs if v.value is not None]
    result = spec.calc(numbers)
    if isinstance(result, str):
        return make_missing(spec, statements, fiscal_year, f"not meaningful: {result}", inputs)
    extra = "; ".join(n for n in (*dict.fromkeys(notes), _flags(used)) if n)
    return make_value(spec, statements, fiscal_year, result, inputs, extra or None)


# --- small helpers for the calc functions ----------------------------------------------


def divide(numerator: Decimal, denominator: Decimal, zero_reason: str) -> Decimal | str:
    if denominator == 0:
        return zero_reason
    return numerator / denominator


def average(a: Decimal, b: Decimal) -> Decimal:
    return (a + b) / 2
