"""Cash flow quality: free cash flow, its margin, and cash conversion of earnings."""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.metrics.engine import Group, MetricSpec, Ref, compute, divide
from stock_forensics.models import Unit, Value
from stock_forensics.normalize.statements import Statements


def _fcf_margin(v: list[Decimal]) -> Decimal | str:
    operating_cash_flow, capex, revenue = v
    return divide(operating_cash_flow - capex, revenue, "revenue is zero")


def _cash_conversion(v: list[Decimal]) -> Decimal | str:
    operating_cash_flow, net_income = v
    if net_income <= 0:
        return "net income is not positive"
    return operating_cash_flow / net_income


SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "free_cash_flow",
        "Free cash flow",
        Group.CASH_FLOW,
        Unit.USD,
        "operating_cash_flow - capital_expenditures",
        refs=(Ref("operating_cash_flow"), Ref("capital_expenditures")),
        calc=lambda v: v[0] - v[1],
    ),
    MetricSpec(
        "fcf_margin",
        "Free cash flow margin",
        Group.CASH_FLOW,
        Unit.RATIO,
        "(operating_cash_flow - capital_expenditures) / revenue",
        refs=(Ref("operating_cash_flow"), Ref("capital_expenditures"), Ref("revenue")),
        calc=_fcf_margin,
    ),
    MetricSpec(
        "cash_conversion",
        "Cash conversion",
        Group.CASH_FLOW,
        Unit.RATIO,
        "operating_cash_flow / net_income",
        refs=(Ref("operating_cash_flow"), Ref("net_income")),
        calc=_cash_conversion,
    ),
)


def compute_cash_flow_metrics(statements: Statements, fiscal_year: int) -> dict[str, Value]:
    return {spec.name: compute(spec, statements, fiscal_year) for spec in SPECS}
