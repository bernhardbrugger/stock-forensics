"""Market value and valuation metrics: price, market cap, P/E, EV/EBITDA, FCF yield.

* **Market value of equity by fiscal year** (feeds the original Altman Z): close on or before the
  fiscal year end times the shares outstanding of that balance sheet. If the price is adjusted
  for later splits, the share count is multiplied by the same splits, otherwise the product
  would be off by the split ratio. "Later" means after the *filing* the share count comes from:
  the statements take the newest filing for every period, and a newer 10-K already restates
  the share count of earlier years for splits since (NVIDIA's January 2024 count is reported
  as 24.4 billion in the 10-K for fiscal 2025, after the June 2024 10-for-1 split).
* **Latest market value**: latest close times the latest reported share count (balance sheet of
  the newest quarter or year). Companies with several share classes report one total or one
  class; the note shows the share count that was used.
* **P/E** = market value / net income. **EV/EBITDA** = (market value - net cash) / (operating
  income + depreciation and amortization). **FCF yield** = (operating cash flow - capex) /
  market value.

Flows are trailing twelve months when all of their inputs have one, otherwise the latest fiscal
year; the note says which. The net cash of the EV comes from the latest fiscal year end balance
sheet. A non-positive denominator (loss, negative EBITDA) is a gap with the reason, not a
negative multiple.
"""

from __future__ import annotations

from decimal import Decimal

from stock_forensics.market.common import computed_source, gap, number, price_value
from stock_forensics.market.models import MarketMetric
from stock_forensics.market.technicals import MAX_STALE_DAYS
from stock_forensics.metrics.engine import Metrics
from stock_forensics.models import Unit, Value, missing
from stock_forensics.normalize.statements import Statements
from stock_forensics.providers.prices import PriceBar, PriceSeries

PRICE_TOLERANCE_DAYS = 7

# name, label, formula, group, unit
SPECS: tuple[tuple[str, str, str, Unit], ...] = (
    (
        "market_cap",
        "Market value of equity",
        "latest close x latest reported shares outstanding",
        Unit.USD,
    ),
    (
        "enterprise_value",
        "Enterprise value",
        "market value of equity - net cash (latest fiscal year end)",
        Unit.USD,
    ),
    ("pe", "Price / earnings", "market value / net income", Unit.RATIO),
    (
        "ev_ebitda",
        "EV / EBITDA",
        "enterprise value / (operating income + depreciation and amortization)",
        Unit.RATIO,
    ),
    (
        "fcf_yield",
        "Free cash flow yield",
        "(operating cash flow - capital expenditures) / market value",
        Unit.RATIO,
    ),
)


def _short(note: str | None) -> str:
    return (note or "not available").split(" (tried:")[0]


def _cap(
    series: PriceSeries, bar: PriceBar, shares: Value, day_label: str
) -> tuple[Decimal, str, tuple[Value, ...]]:
    assert shares.value is not None
    # A share count reflects the splits up to the filing it comes from (a later 10-K restates
    # earlier balance sheets for splits since), so only splits after that filing date count.
    # Without a filing date the end of the period is the best available guess.
    reflects_until = shares.source.filed or shares.period.end
    factor = series.split_factor_after(reflects_until)
    amount = bar.close * shares.value * factor
    note = f"close on {bar.day} x shares outstanding {day_label}"
    if factor != 1:
        note += (
            f" x {factor.normalize():f} (splits after {reflects_until}, the filing the share "
            f"count comes from, to match the split-adjusted close)"
        )
    return amount, note, (price_value(series, bar), shares)


def market_caps(statements: Statements, series: PriceSeries) -> dict[int, Value]:
    """Market value of equity at the end of every fiscal year (gaps where there is no price)."""
    result: dict[int, Value] = {}
    for fiscal_year in statements.fiscal_years:
        shares = statements.annual("shares_outstanding", fiscal_year)
        day = shares.period.end
        reason: str | None = None
        bar = series.on_or_before(day)
        if shares.value is None:
            reason = f"shares outstanding FY{fiscal_year}: {_short(shares.note)}"
        elif bar is None:
            reason = f"the price history starts {series.first_day}, after the fiscal year end {day}"
        elif (day - bar.day).days > PRICE_TOLERANCE_DAYS:
            reason = f"the nearest close to {day} is from {bar.day}"
        if reason is not None or bar is None:
            result[fiscal_year] = missing(
                f"market value not available: {reason}",
                unit=Unit.USD,
                period=shares.period,
                source=computed_source(series),
                inputs=(shares,),
            )
            continue
        amount, note, inputs = _cap(series, bar, shares, f"on {day} (FY{fiscal_year})")
        result[fiscal_year] = Value(
            value=amount,
            unit=Unit.USD,
            period=shares.period,
            source=computed_source(series),
            note=note,
            inputs=inputs,
        )
    return result


def _latest_shares(statements: Statements) -> Value | None:
    series = statements.series["shares_outstanding"]
    candidates = [v for v in (*series.annual.values(), *series.quarterly.values()) if v.value]
    return max(candidates, key=lambda v: v.period.end, default=None)


def _flows(statements: Statements, names: tuple[str, ...]) -> tuple[list[Value], str] | str:
    """The values of flow fields: all trailing twelve months, else all latest fiscal year."""
    ttm = [statements.series[n].ttm for n in names]
    if all(t is not None and t.value is not None for t in ttm):
        values = [t for t in ttm if t is not None]
        return values, f"trailing twelve months to {max(v.period.end for v in values)}"
    year = statements.latest_fiscal_year
    annual = [statements.annual(n, year) for n in names]
    if all(v.value is not None for v in annual):
        return annual, f"fiscal year {year}"
    problems = [
        f"{n}: {_short(v.note)}" for n, v in zip(names, annual, strict=True) if v.value is None
    ]
    return "; ".join(problems)


def compute_valuation(
    statements: Statements | None,
    metrics: Metrics | None,
    series: PriceSeries,
    *,
    as_of_days_old: int,
) -> tuple[MarketMetric, ...]:
    """The valuation metrics as of the latest close.

    ``as_of_days_old`` is how many days the latest close is older than the analysis date.
    """
    last = series.last_bar
    assert last is not None
    now = price_value(series, last)
    day = last.day

    def gap_all(reason: str) -> dict[str, Value]:
        return {name: gap(series, reason, unit, day, (now,)) for name, _, _, unit in SPECS}

    if as_of_days_old > MAX_STALE_DAYS:
        values = gap_all(
            f"the latest close is from {day}, {as_of_days_old} days before the analysis date"
        )
    elif statements is None:
        values = gap_all("needs the financial statements, which are missing")
    else:
        values = _values(statements, metrics, series, last, now)
    return tuple(
        MarketMetric(name=name, label=label, group="valuation", formula=formula, value=values[name])
        for name, label, formula, _ in SPECS
    )


def _values(
    statements: Statements,
    metrics: Metrics | None,
    series: PriceSeries,
    last: PriceBar,
    now: Value,
) -> dict[str, Value]:
    day = last.day

    def fail(unit: Unit, reason: str, inputs: tuple[Value, ...] = (now,)) -> Value:
        return gap(series, reason, unit, day, inputs)

    shares = _latest_shares(statements)
    if shares is None:
        reason = "no shares outstanding reported in the statements"
        return {name: fail(unit, reason) for name, _, _, unit in SPECS}
    amount, note, inputs = _cap(series, last, shares, f"({shares.period.end})")
    cap = number(series, amount, Unit.USD, day, note, inputs)
    out: dict[str, Value] = {"market_cap": cap}
    inputs_cap = (cap,)

    # enterprise value
    net_cash = metrics.latest("net_cash") if metrics is not None else None
    if net_cash is None or net_cash.value is None:
        why = "net cash is not available" + (f": {_short(net_cash.note)}" if net_cash else "")
        out["enterprise_value"] = fail(Unit.USD, why, inputs_cap)
    else:
        out["enterprise_value"] = number(
            series,
            amount - net_cash.value,
            Unit.USD,
            day,
            f"market value - net cash (fiscal year {net_cash.period.fiscal_year} balance sheet)",
            (cap, net_cash),
        )

    # P/E
    earnings = _flows(statements, ("net_income",))
    if isinstance(earnings, str):
        out["pe"] = fail(Unit.RATIO, earnings, inputs_cap)
    else:
        values, basis = earnings
        income = values[0]
        assert income.value is not None
        if income.value <= 0:
            out["pe"] = fail(Unit.RATIO, f"net income is not positive ({basis})", (cap, income))
        else:
            out["pe"] = number(
                series,
                amount / income.value,
                Unit.RATIO,
                day,
                f"net income: {basis}",
                (cap, income),
            )

    # EV / EBITDA
    ebitda_parts = _flows(statements, ("operating_income", "depreciation_amortization"))
    ev = out["enterprise_value"]
    if ev.value is None:
        out["ev_ebitda"] = fail(
            Unit.RATIO, f"enterprise value is not available: {_short(ev.note)}", (ev,)
        )
    elif isinstance(ebitda_parts, str):
        out["ev_ebitda"] = fail(Unit.RATIO, ebitda_parts, (ev,))
    else:
        parts, basis = ebitda_parts
        assert parts[0].value is not None and parts[1].value is not None
        ebitda = parts[0].value + parts[1].value
        if ebitda <= 0:
            out["ev_ebitda"] = fail(Unit.RATIO, f"EBITDA is not positive ({basis})", (ev, *parts))
        else:
            out["ev_ebitda"] = number(
                series, ev.value / ebitda, Unit.RATIO, day, f"EBITDA: {basis}", (ev, *parts)
            )

    # FCF yield
    cash_flows = _flows(statements, ("operating_cash_flow", "capital_expenditures"))
    if isinstance(cash_flows, str):
        out["fcf_yield"] = fail(Unit.RATIO, cash_flows, inputs_cap)
    else:
        parts, basis = cash_flows
        assert parts[0].value is not None and parts[1].value is not None
        out["fcf_yield"] = number(
            series,
            (parts[0].value - parts[1].value) / amount,
            Unit.RATIO,
            day,
            f"free cash flow: {basis}",
            (cap, *parts),
        )
    return out
