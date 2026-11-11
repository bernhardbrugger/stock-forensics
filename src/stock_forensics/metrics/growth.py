"""Growth: year-over-year growth, 3-year CAGR and growth consistency of revenue and earnings.

"Earnings" here means net income. Growth rates are only defined when the base value is
positive; otherwise the metric is a gap with the reason.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from stock_forensics.metrics.engine import (
    Group,
    MetricSpec,
    Ref,
    compute,
    make_missing,
    make_value,
)
from stock_forensics.models import Unit, Value
from stock_forensics.normalize.statements import Statements

CAGR_YEARS = 3
MAX_CHANGES = 5  # consistency looks at up to the last five year-over-year changes
MIN_CHANGES = 3  # and needs at least three of them


def _yoy(field: str) -> Callable[[list[Decimal]], Decimal | str]:
    def calc(v: list[Decimal]) -> Decimal | str:
        now, before = v
        if before <= 0:
            return f"prior-year {field} is not positive, so a growth rate is undefined"
        return now / before - 1

    return calc


def _cagr(field: str) -> Callable[[list[Decimal]], Decimal | str]:
    def calc(v: list[Decimal]) -> Decimal | str:
        now, start = v
        if start <= 0 or now <= 0:
            return f"{field} is not positive at both ends of the period, so a CAGR is undefined"
        return (now / start) ** (Decimal(1) / Decimal(CAGR_YEARS)) - 1

    return calc


def _consistency(spec: MetricSpec, statements: Statements, fiscal_year: int) -> Value:
    """Share of the latest year-over-year changes in which the value grew."""
    field = spec.refs[0].field
    series = statements.series[field].annual
    newest_first: list[Value] = []
    for back in range(MAX_CHANGES + 1):
        value = series.get(fiscal_year - back)
        if value is None or value.value is None:
            break  # the chain of consecutive years ends at the first gap
        newest_first.append(value)
    values = newest_first[::-1]  # oldest first

    positives = 0
    marks: list[str] = []
    for older, newer in zip(values, values[1:], strict=False):
        assert older.value is not None
        assert newer.value is not None
        if older.value <= 0:
            continue  # growth from a non-positive base is undefined: not counted
        grew = newer.value > older.value
        positives += int(grew)
        marks.append(f"{newer.period.fiscal_year}{'+' if grew else '-'}")
    defined = len(marks)
    if defined < MIN_CHANGES:
        return make_missing(
            spec,
            statements,
            fiscal_year,
            f"needs at least {MIN_CHANGES} year-over-year changes of {field} ending in "
            f"FY{fiscal_year} from a positive base (found {defined})",
            values,
        )
    return make_value(
        spec,
        statements,
        fiscal_year,
        Decimal(positives) / Decimal(defined),
        values,
        f"{positives} of {defined} year-over-year changes positive ({', '.join(marks)})",
    )


def _consistency_spec(name: str, label: str, field: str) -> MetricSpec:
    return MetricSpec(
        name,
        label,
        Group.GROWTH,
        Unit.RATIO,
        f"share of the latest {MAX_CHANGES} year-over-year changes of {field} that are positive "
        f"(at least {MIN_CHANGES} changes needed)",
        refs=(Ref(field),),
        custom=_consistency,
    )


SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "revenue_growth_yoy",
        "Revenue growth YoY",
        Group.GROWTH,
        Unit.RATIO,
        "revenue / prior-year revenue - 1",
        refs=(Ref("revenue"), Ref("revenue", -1)),
        calc=_yoy("revenue"),
    ),
    MetricSpec(
        "net_income_growth_yoy",
        "Earnings growth YoY",
        Group.GROWTH,
        Unit.RATIO,
        "net_income / prior-year net_income - 1",
        refs=(Ref("net_income"), Ref("net_income", -1)),
        calc=_yoy("net income"),
    ),
    MetricSpec(
        "revenue_cagr_3y",
        "Revenue CAGR (3 years)",
        Group.GROWTH,
        Unit.RATIO,
        f"(revenue / revenue {CAGR_YEARS} years earlier) ^ (1 / {CAGR_YEARS}) - 1",
        refs=(Ref("revenue"), Ref("revenue", -CAGR_YEARS)),
        calc=_cagr("revenue"),
    ),
    MetricSpec(
        "net_income_cagr_3y",
        "Earnings CAGR (3 years)",
        Group.GROWTH,
        Unit.RATIO,
        f"(net_income / net_income {CAGR_YEARS} years earlier) ^ (1 / {CAGR_YEARS}) - 1",
        refs=(Ref("net_income"), Ref("net_income", -CAGR_YEARS)),
        calc=_cagr("net income"),
    ),
    _consistency_spec("revenue_growth_consistency", "Revenue growth consistency", "revenue"),
    _consistency_spec("net_income_growth_consistency", "Earnings growth consistency", "net_income"),
)


def compute_growth_metrics(statements: Statements, fiscal_year: int) -> dict[str, Value]:
    return {spec.name: compute(spec, statements, fiscal_year) for spec in SPECS}


def compute_growth_consistency(
    statements: Statements, fiscal_year: int, field: str = "revenue"
) -> Value:
    """Growth consistency of ``revenue`` or ``net_income`` for one fiscal year."""
    spec = next(s for s in SPECS if s.refs and s.refs[0].field == field and s.custom is not None)
    return compute(spec, statements, fiscal_year)
