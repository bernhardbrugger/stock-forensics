"""Liquidity and leverage: current and quick ratio, debt to equity, coverage, net cash."""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.metrics.engine import Group, MetricSpec, Ref, compute, divide
from stock_forensics.models import Unit, Value
from stock_forensics.normalize.statements import Statements


def _current_ratio(v: list[Decimal]) -> Decimal | str:
    return divide(v[0], v[1], "current liabilities are zero")


def _quick_ratio(v: list[Decimal]) -> Decimal | str:
    current_assets, inventory, current_liabilities = v
    return divide(current_assets - inventory, current_liabilities, "current liabilities are zero")


def _debt_to_equity(v: list[Decimal]) -> Decimal | str:
    short_term, long_term, equity = v
    if equity <= 0:
        return "stockholders' equity is not positive"
    return (short_term + long_term) / equity


def _interest_coverage(v: list[Decimal]) -> Decimal | str:
    operating_income, interest = v
    if interest <= 0:
        return "interest expense is not positive (no interest to cover)"
    return operating_income / interest


def _net_cash(v: list[Decimal]) -> Decimal | str:
    cash, investments, short_term, long_term = v
    return cash + investments - short_term - long_term


SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "current_ratio",
        "Current ratio",
        Group.LIQUIDITY,
        Unit.RATIO,
        "total_current_assets / total_current_liabilities",
        refs=(Ref("total_current_assets"), Ref("total_current_liabilities")),
        calc=_current_ratio,
    ),
    MetricSpec(
        "quick_ratio",
        "Quick ratio",
        Group.LIQUIDITY,
        Unit.RATIO,
        "(total_current_assets - inventory) / total_current_liabilities",
        refs=(Ref("total_current_assets"), Ref("inventory"), Ref("total_current_liabilities")),
        calc=_quick_ratio,
    ),
    MetricSpec(
        "debt_to_equity",
        "Debt to equity",
        Group.LIQUIDITY,
        Unit.RATIO,
        "(short_term_debt + long_term_debt) / total_equity",
        refs=(Ref("short_term_debt"), Ref("long_term_debt"), Ref("total_equity")),
        calc=_debt_to_equity,
    ),
    MetricSpec(
        "interest_coverage",
        "Interest coverage",
        Group.LIQUIDITY,
        Unit.RATIO,
        "operating_income / interest_expense",
        refs=(Ref("operating_income"), Ref("interest_expense")),
        calc=_interest_coverage,
    ),
    MetricSpec(
        "net_cash",
        "Net cash (negative = net debt)",
        Group.LIQUIDITY,
        Unit.USD,
        "cash_and_equivalents + short_term_investments - short_term_debt - long_term_debt",
        refs=(
            Ref("cash_and_equivalents"),
            Ref("short_term_investments"),
            Ref("short_term_debt"),
            Ref("long_term_debt"),
        ),
        calc=_net_cash,
    ),
)


def compute_liquidity_metrics(statements: Statements, fiscal_year: int) -> dict[str, Value]:
    return {spec.name: compute(spec, statements, fiscal_year) for spec in SPECS}
