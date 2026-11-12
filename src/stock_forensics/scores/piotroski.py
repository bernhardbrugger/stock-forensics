"""Piotroski F-Score: nine yes/no signals of financial strength.

Profitability:  F_ROA, F_CFO, F_DELTA_ROA, F_ACCRUAL
Leverage, liquidity, source of funds:  F_DELTA_LEVER, F_DELTA_LIQUID, F_EQ_OFFER
Operating efficiency:  F_DELTA_MARGIN, F_DELTA_TURN

Each signal scores 1 if it is good. ROA, CFO and asset turnover are scaled by total assets at
the beginning of the year, leverage uses long-term debt over average total assets. The score
needs three balance sheet dates (t-2, t-1, t). 8 to 9 is strong, 0 to 2 is weak.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from stock_forensics.metrics.engine import (
    MetricSpec,
    Ref,
    average,
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
    gaps,
    not_applicable,
    total_missing,
    total_value,
)

NAME = "piotroski_f"
LABEL = "Piotroski F-Score"
FORMULA = (
    "F = F_ROA + F_CFO + F_DELTA_ROA + F_ACCRUAL + F_DELTA_LEVER + F_DELTA_LIQUID + "
    "F_EQ_OFFER + F_DELTA_MARGIN + F_DELTA_TURN (each 0 or 1)"
)
REFERENCE = (
    "Piotroski, J. D. (2000). Value Investing: The Use of Historical Financial Statement "
    "Information to Separate Winners from Losers. Journal of Accounting Research 38 "
    "(Supplement), 1-41."
)
STRONG_FROM = Decimal(8)
WEAK_UP_TO = Decimal(2)
LIMITATIONS = (
    "Designed for high book-to-market (value) stocks; less informative for growth companies.",
    "Binary signals: a tiny improvement counts as much as a large one.",
    "F_EQ_OFFER is a proxy: it checks that the share count did not rise (year-end shares "
    "outstanding, or diluted weighted shares if those are not reported for both years), not "
    "the cash raised from share issues.",
    "F_DELTA_LEVER passes if leverage did not increase, so debt-free companies are not penalised.",
    "The asset base of each ratio (beginning of year or average) follows the original "
    "paper; it was not re-checked against the paper's text.",
    "Not meaningful for banks, insurers and other financial companies (marked not applicable).",
    "Needs three consecutive balance sheet dates (fiscal years t-2 to t).",
)

_DIV = "total assets are zero"


def _roa(v: list[Decimal]) -> Decimal | str:
    return divide(v[0], v[1], _DIV)


def _delta_roa(v: list[Decimal]) -> Decimal | str:
    ni, assets_start, ni_before, assets_start_before = v
    if assets_start == 0 or assets_start_before == 0:
        return _DIV
    return ni / assets_start - ni_before / assets_start_before


def _accrual(v: list[Decimal]) -> Decimal | str:
    ni, cfo, assets_start = v
    return divide(ni - cfo, assets_start, _DIV)


def _delta_lever(v: list[Decimal]) -> Decimal | str:
    ltd, assets, assets_before, ltd_before, assets_before2 = v
    average_now = average(assets, assets_before)
    average_before = average(assets_before, assets_before2)
    if average_now == 0 or average_before == 0:
        return "average total assets are zero"
    return ltd / average_now - ltd_before / average_before


def _delta_liquid(v: list[Decimal]) -> Decimal | str:
    ca, cl, ca_before, cl_before = v
    if cl == 0 or cl_before == 0:
        return "current liabilities are zero"
    return ca / cl - ca_before / cl_before


def _delta_margin(v: list[Decimal]) -> Decimal | str:
    gp, revenue, gp_before, revenue_before = v
    if revenue == 0 or revenue_before == 0:
        return "revenue is zero"
    return gp / revenue - gp_before / revenue_before


def _delta_turn(v: list[Decimal]) -> Decimal | str:
    revenue, assets_start, revenue_before, assets_start_before = v
    if assets_start == 0 or assets_start_before == 0:
        return _DIV
    return revenue / assets_start - revenue_before / assets_start_before


def _eq_offer(spec: MetricSpec, statements: Statements, fiscal_year: int) -> Value:
    """Change of the share count; year-end shares outstanding, else diluted weighted shares."""
    first, last = statements.fiscal_years[0], statements.fiscal_years[-1]
    if fiscal_year - 1 < first:
        return make_missing(
            spec,
            statements,
            fiscal_year,
            f"needs fiscal year {fiscal_year - 1} (statements cover {first}-{last})",
            [],
        )
    for field in ("shares_outstanding", "shares_diluted_weighted"):
        now = statements.series[field].annual[fiscal_year]
        before = statements.series[field].annual[fiscal_year - 1]
        if now.value is None or before.value is None:
            continue
        if before.value <= 0:
            return make_missing(
                spec,
                statements,
                fiscal_year,
                "prior-year share count is not positive",
                [now, before],
            )
        note = None
        if field == "shares_diluted_weighted":
            note = (
                "shares_diluted_weighted used because shares_outstanding is not reported for "
                "both years (a weighted average, so only a proxy)"
            )
        return make_value(
            spec, statements, fiscal_year, now.value / before.value - 1, [now, before], note
        )
    return make_missing(
        spec,
        statements,
        fiscal_year,
        "no share count (shares_outstanding or shares_diluted_weighted) for both years",
        [],
    )


@dataclass(frozen=True)
class Criterion:
    name: str
    label: str
    spec: MetricSpec
    passes: Callable[[Decimal], bool]


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


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "F_ROA",
        "ROA is positive",
        _spec(
            "F_ROA",
            "ROA = net income / beginning-of-year total assets; pass if > 0",
            (Ref("net_income"), Ref("total_assets", -1)),
            _roa,
        ),
        lambda x: x > 0,
    ),
    Criterion(
        "F_CFO",
        "Operating cash flow is positive",
        _spec(
            "F_CFO",
            "CFO = operating cash flow / beginning-of-year total assets; pass if > 0",
            (Ref("operating_cash_flow"), Ref("total_assets", -1)),
            _roa,
        ),
        lambda x: x > 0,
    ),
    Criterion(
        "F_DELTA_ROA",
        "ROA improved",
        _spec(
            "F_DELTA_ROA",
            "change in ROA = ROA - prior-year ROA; pass if > 0",
            (
                Ref("net_income"),
                Ref("total_assets", -1),
                Ref("net_income", -1),
                Ref("total_assets", -2),
            ),
            _delta_roa,
        ),
        lambda x: x > 0,
    ),
    Criterion(
        "F_ACCRUAL",
        "Operating cash flow exceeds net income",
        _spec(
            "F_ACCRUAL",
            "ACCRUAL = (net income - operating cash flow) / beginning-of-year total assets; "
            "pass if < 0",
            (Ref("net_income"), Ref("operating_cash_flow"), Ref("total_assets", -1)),
            _accrual,
        ),
        lambda x: x < 0,
    ),
    Criterion(
        "F_DELTA_LEVER",
        "Leverage did not increase",
        _spec(
            "F_DELTA_LEVER",
            "change in long-term debt / average total assets; pass if <= 0",
            (
                Ref("long_term_debt"),
                Ref("total_assets"),
                Ref("total_assets", -1),
                Ref("long_term_debt", -1),
                Ref("total_assets", -2),
            ),
            _delta_lever,
        ),
        lambda x: x <= 0,
    ),
    Criterion(
        "F_DELTA_LIQUID",
        "Current ratio improved",
        _spec(
            "F_DELTA_LIQUID",
            "change in current ratio (current assets / current liabilities); pass if > 0",
            (
                Ref("total_current_assets"),
                Ref("total_current_liabilities"),
                Ref("total_current_assets", -1),
                Ref("total_current_liabilities", -1),
            ),
            _delta_liquid,
        ),
        lambda x: x > 0,
    ),
    Criterion(
        "F_EQ_OFFER",
        "No increase in the share count",
        MetricSpec(
            "F_EQ_OFFER",
            "F_EQ_OFFER",
            None,
            Unit.RATIO,
            "change in share count = shares / prior-year shares - 1; pass if <= 0",
            refs=(Ref("shares_outstanding"),),
            custom=_eq_offer,
        ),
        lambda x: x <= 0,
    ),
    Criterion(
        "F_DELTA_MARGIN",
        "Gross margin improved",
        _spec(
            "F_DELTA_MARGIN",
            "change in gross margin (gross profit / revenue); pass if > 0",
            (
                Ref("gross_profit"),
                Ref("revenue"),
                Ref("gross_profit", -1),
                Ref("revenue", -1),
            ),
            _delta_margin,
        ),
        lambda x: x > 0,
    ),
    Criterion(
        "F_DELTA_TURN",
        "Asset turnover improved",
        _spec(
            "F_DELTA_TURN",
            "change in asset turnover (revenue / beginning-of-year total assets); pass if > 0",
            (
                Ref("revenue"),
                Ref("total_assets", -1),
                Ref("revenue", -1),
                Ref("total_assets", -2),
            ),
            _delta_turn,
        ),
        lambda x: x > 0,
    ),
)

THRESHOLDS = (
    Threshold(label="strong from", value=STRONG_FROM, meaning="8 or 9 points: strong"),
    Threshold(label="weak up to", value=WEAK_UP_TO, meaning="0 to 2 points: weak"),
)


def compute_piotroski(
    statements: Statements, fiscal_year: int, *, excluded: str | None
) -> ScoreResult:
    if excluded is not None:
        return not_applicable(
            name=NAME,
            label=LABEL,
            fiscal_year=fiscal_year,
            statements=statements,
            unit=Unit.SCORE,
            formula=FORMULA,
            reference=REFERENCE,
            reason=excluded,
            limitations=LIMITATIONS,
        )
    components: list[Component] = []
    for criterion in CRITERIA:
        value = compute(criterion.spec, statements, fiscal_year)
        passed = None if value.value is None else criterion.passes(value.value)
        components.append(
            Component(
                name=criterion.name,
                label=criterion.label,
                value=value,
                weight=Decimal(1),
                contribution=None if passed is None else Decimal(int(passed)),
                passed=passed,
            )
        )
    parts = tuple(components)
    common = dict(
        name=NAME,
        label=LABEL,
        fiscal_year=fiscal_year,
        formula=FORMULA,
        reference=REFERENCE,
        applicable=True,
        components=parts,
        thresholds=THRESHOLDS,
        limitations=LIMITATIONS,
    )
    missing_names = gaps(parts)
    if missing_names:
        evaluable = [c for c in parts if c.passed is not None]
        points = sum(1 for c in evaluable if c.passed)
        reason = (
            f"cannot total {LABEL}: no value for {', '.join(missing_names)}; "
            f"{points} of {len(evaluable)} evaluable criteria passed"
        )
        return ScoreResult(
            **common,  # type: ignore[arg-type]
            total=total_missing(statements, fiscal_year, Unit.SCORE, reason, parts),
            interpretation=f"{reason}. Each criterion is listed with its result.",
        )
    points = sum(1 for c in parts if c.passed)
    failed = [c.name for c in parts if not c.passed]
    if points >= STRONG_FROM:
        zone, text = "strong", "strong"
    elif points <= WEAK_UP_TO:
        zone, text = "weak", "weak"
    else:
        zone, text = "neutral", "neutral"
    interpretation = f"F-Score {points} of 9: {text}. " + (
        f"Failed: {', '.join(failed)}." if failed else "All nine signals are positive."
    )
    return ScoreResult(
        **common,  # type: ignore[arg-type]
        total=total_value(
            statements,
            fiscal_year,
            Unit.SCORE,
            Decimal(points),
            f"{FORMULA}; F = {points}",
            parts,
        ),
        zone=zone,
        interpretation=interpretation,
    )
