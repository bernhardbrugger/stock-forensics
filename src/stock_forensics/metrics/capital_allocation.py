"""Capital allocation: payouts, buybacks, share count change and R&D intensity."""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.metrics.engine import Group, MetricSpec, Ref, compute, divide
from stock_forensics.models import Unit, Value
from stock_forensics.normalize.statements import Statements

_NI_NOT_POSITIVE = "net income is not positive"


def _payout(v: list[Decimal]) -> Decimal | str:
    paid, net_income = v[:-1], v[-1]
    if net_income <= 0:
        return _NI_NOT_POSITIVE
    return sum(paid, Decimal(0)) / net_income


def _share_count_change(v: list[Decimal]) -> Decimal | str:
    now, before = v
    if before <= 0:
        return "prior-year share count is not positive"
    return now / before - 1


def _rnd_ratio(v: list[Decimal]) -> Decimal | str:
    return divide(v[0], v[1], "revenue is zero")


SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "payout_ratio",
        "Dividend payout ratio",
        Group.CAPITAL_ALLOCATION,
        Unit.RATIO,
        "dividends_paid / net_income",
        refs=(Ref("dividends_paid"), Ref("net_income")),
        calc=_payout,
    ),
    MetricSpec(
        "buyback_ratio",
        "Share repurchases to net income",
        Group.CAPITAL_ALLOCATION,
        Unit.RATIO,
        "share_repurchases / net_income",
        refs=(Ref("share_repurchases"), Ref("net_income")),
        calc=_payout,
    ),
    MetricSpec(
        "total_payout_ratio",
        "Total shareholder payout ratio",
        Group.CAPITAL_ALLOCATION,
        Unit.RATIO,
        "(dividends_paid + share_repurchases) / net_income",
        refs=(Ref("dividends_paid"), Ref("share_repurchases"), Ref("net_income")),
        calc=_payout,
    ),
    MetricSpec(
        "share_count_change_yoy",
        "Share count change YoY (positive = dilution)",
        Group.CAPITAL_ALLOCATION,
        Unit.RATIO,
        "shares_diluted_weighted / prior-year shares_diluted_weighted - 1",
        refs=(Ref("shares_diluted_weighted"), Ref("shares_diluted_weighted", -1)),
        calc=_share_count_change,
    ),
    MetricSpec(
        "rnd_ratio",
        "R&D to revenue",
        Group.CAPITAL_ALLOCATION,
        Unit.RATIO,
        "research_and_development / revenue",
        refs=(Ref("research_and_development"), Ref("revenue")),
        calc=_rnd_ratio,
    ),
)


def compute_capital_allocation_metrics(
    statements: Statements, fiscal_year: int
) -> dict[str, Value]:
    return {spec.name: compute(spec, statements, fiscal_year) for spec in SPECS}
