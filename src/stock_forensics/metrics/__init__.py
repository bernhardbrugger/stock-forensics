"""Deterministic metrics computed from normalized statements.

Every metric is a :class:`~stock_forensics.models.Value` with the formula in ``note`` and the
statement values it was computed from in ``inputs``. Gaps are explicit (``value=None`` plus a
reason). See ``docs/metrics.md``.
"""

from __future__ import annotations

from stock_forensics.metrics import (
    capital_allocation,
    cash_flow,
    growth,
    liquidity,
    profitability,
)
from stock_forensics.metrics.engine import Group, Metrics, MetricSeries, MetricSpec, compute
from stock_forensics.normalize.statements import Statements

ALL_SPECS: tuple[MetricSpec, ...] = (
    *profitability.SPECS,
    *liquidity.SPECS,
    *cash_flow.SPECS,
    *growth.SPECS,
    *capital_allocation.SPECS,
)
assert len({s.name for s in ALL_SPECS}) == len(ALL_SPECS), "metric names must be unique"


def compute_metrics(statements: Statements) -> Metrics:
    """All metrics for every fiscal year in ``statements``."""
    series = {
        spec.name: MetricSeries(
            name=spec.name,
            label=spec.label,
            group=spec.group,
            unit=spec.unit,
            formula=spec.formula,
            annual={fy: compute(spec, statements, fy) for fy in statements.fiscal_years},
        )
        for spec in ALL_SPECS
    }
    return Metrics(
        cik=statements.cik,
        company_name=statements.company_name,
        fiscal_years=statements.fiscal_years,
        series=series,
        source=statements.source,
    )


__all__ = ["ALL_SPECS", "Group", "MetricSeries", "MetricSpec", "Metrics", "compute_metrics"]
