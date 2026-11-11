"""Profitability: margins and returns on equity, assets and invested capital."""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.metrics.engine import Group, MetricSpec, Ref, average, compute, divide
from stock_forensics.models import Unit, Value
from stock_forensics.normalize.statements import Statements


def _margin(name: str, label: str, numerator: str) -> MetricSpec:
    return MetricSpec(
        name,
        label,
        Group.PROFITABILITY,
        Unit.RATIO,
        f"{numerator} / revenue",
        refs=(Ref(numerator), Ref("revenue")),
        calc=lambda v: divide(v[0], v[1], "revenue is zero"),
    )


def _roe(v: list[Decimal]) -> Decimal | str:
    net_income, equity_now, equity_before = v
    if equity_now <= 0 or equity_before <= 0:
        return "stockholders' equity is not positive in one of the two years"
    return net_income / average(equity_now, equity_before)


def _roa(v: list[Decimal]) -> Decimal | str:
    net_income, assets_now, assets_before = v
    return divide(net_income, average(assets_now, assets_before), "average total assets are zero")


def _roic(v: list[Decimal]) -> Decimal | str:
    operating_income, tax, pretax = v[0], v[1], v[2]
    capital_now = v[3] + v[4] + v[5]  # short-term debt + long-term debt + equity
    capital_before = v[6] + v[7] + v[8]
    if pretax <= 0:
        return "pre-tax income is not positive, so the effective tax rate is undefined"
    rate = tax / pretax
    if rate < 0 or rate > 1:
        return f"effective tax rate of {rate:.1%} is outside 0-100%"
    capital = average(capital_now, capital_before)
    if capital <= 0:
        return "average invested capital is not positive"
    return operating_income * (1 - rate) / capital


_EQUITY = "equity_incl_noncontrolling"

SPECS: tuple[MetricSpec, ...] = (
    _margin("gross_margin", "Gross margin", "gross_profit"),
    _margin("operating_margin", "Operating margin", "operating_income"),
    _margin("net_margin", "Net margin", "net_income"),
    MetricSpec(
        "roe",
        "Return on equity",
        Group.PROFITABILITY,
        Unit.RATIO,
        "net_income / average(total_equity, prior-year total_equity)",
        refs=(
            Ref("net_income"),
            Ref("total_equity"),
            Ref("total_equity", -1),
        ),
        calc=_roe,
    ),
    MetricSpec(
        "roa",
        "Return on assets",
        Group.PROFITABILITY,
        Unit.RATIO,
        "net_income / average(total_assets, prior-year total_assets)",
        refs=(Ref("net_income"), Ref("total_assets"), Ref("total_assets", -1)),
        calc=_roa,
    ),
    MetricSpec(
        "roic",
        "Return on invested capital",
        Group.PROFITABILITY,
        Unit.RATIO,
        "operating_income * (1 - income_tax_expense / income_before_tax) / "
        "average(invested capital, prior-year invested capital); "
        "invested capital = short_term_debt + long_term_debt + "
        "equity incl. noncontrolling interests",
        refs=(
            Ref("operating_income"),
            Ref("income_tax_expense"),
            Ref("income_before_tax"),
            Ref("short_term_debt"),
            Ref("long_term_debt"),
            Ref(_EQUITY, fallback="total_equity"),
            Ref("short_term_debt", -1),
            Ref("long_term_debt", -1),
            Ref(_EQUITY, -1, fallback="total_equity"),
        ),
        calc=_roic,
    ),
)


def compute_profitability_metrics(statements: Statements, fiscal_year: int) -> dict[str, Value]:
    return {spec.name: compute(spec, statements, fiscal_year) for spec in SPECS}
