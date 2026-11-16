"""Technicals from daily closes: momentum, volatility, drawdown, beta, 52-week range.

All of them are price based (closes, not intraday highs and lows) and ignore dividends. Windows
are calendar based (3 months, 1 year, ...), except the volatility, which uses the last 252
trading days. A window that the price history does not cover is a gap with the reason, never a
shorter window in disguise:

* momentum 3, 6 and 12 months: ``close / close N months earlier - 1``
* momentum 12-1: ``close 1 month ago / close 12 months ago - 1`` (skips the latest month)
* volatility: sample standard deviation of the last 252 daily log returns, times sqrt(252)
* maximum drawdown (1 and 3 years): ``min(close / highest earlier close in the window - 1)``
* beta (2 years): covariance of weekly returns with the index divided by the index variance
* distance to the 52-week high and low: ``close / highest (lowest) close of 365 days - 1``

If a series is not split-adjusted (Alpha Vantage), a jump that looks like a split
(``<= -30 %`` or ``>= +40 %`` in a day) inside a window makes the metric a gap, because the
numbers across that jump would be wrong.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from stock_forensics.flags import years_before
from stock_forensics.insiders.aggregate import months_before
from stock_forensics.market.common import gap, number, price_value
from stock_forensics.market.models import MarketMetric
from stock_forensics.models import Unit, Value
from stock_forensics.providers.prices import PriceBar, PriceSeries

TRADING_DAYS = 252
MAX_STALE_DAYS = 10  # the latest close must be this close to the analysis date
START_TOLERANCE_DAYS = 7  # the close at the start of a window may be this far before it
BETA_MIN_WEEKS = 52
BETA_YEARS = 2
JUMP_DOWN = Decimal("-0.30")
JUMP_UP = Decimal("0.40")

# name, label, formula
SPECS: tuple[tuple[str, str, str], ...] = (
    ("momentum_3m", "Return, 3 months", "close / close 3 months earlier - 1"),
    ("momentum_6m", "Return, 6 months", "close / close 6 months earlier - 1"),
    ("momentum_12m", "Return, 12 months", "close / close 12 months earlier - 1"),
    (
        "momentum_12_1",
        "Return, 12 months excluding the last month",
        "close 1 month ago / close 12 months ago - 1",
    ),
    (
        "volatility_1y",
        "Volatility, 1 year (annualized)",
        "standard deviation of the last 252 daily log returns x sqrt(252)",
    ),
    (
        "max_drawdown_1y",
        "Maximum drawdown, 1 year",
        "lowest value of close / highest earlier close in the window - 1",
    ),
    (
        "max_drawdown_3y",
        "Maximum drawdown, 3 years",
        "lowest value of close / highest earlier close in the window - 1",
    ),
    (
        "beta_2y",
        "Beta vs. S&P 500, 2 years (weekly)",
        "covariance(weekly stock return, weekly index return) / variance(weekly index return)",
    ),
    ("dist_52w_high", "Distance to 52-week high", "close / highest close of 365 days - 1"),
    ("dist_52w_low", "Distance to 52-week low", "close / lowest close of 365 days - 1"),
)


def _decimal(x: float) -> Decimal:
    return Decimal(f"{x:.6f}")


def first_jump(bars: tuple[PriceBar, ...], since: date) -> tuple[date, Decimal] | None:
    """The first day on or after ``since`` whose change looks like a split."""
    for previous, bar in zip(bars, bars[1:], strict=False):
        if bar.day < since:
            continue
        change = bar.close / previous.close - 1
        if change <= JUMP_DOWN or change >= JUMP_UP:
            return bar.day, change
    return None


class _Context:
    def __init__(self, series: PriceSeries, benchmark: PriceSeries | None, last: PriceBar) -> None:
        self.series = series
        self.benchmark = benchmark
        self.last = last
        self.now = price_value(series, last)

    def gap(self, reason: str, start: date | None = None) -> Value:
        return gap(self.series, reason, Unit.RATIO, self.last.day, (self.now,), start)

    def number(
        self, amount: Decimal, note: str, inputs: tuple[Value, ...], start: date | None
    ) -> Value:
        return number(self.series, amount, Unit.RATIO, self.last.day, note, inputs, start)

    def covered(self, start: date, label: str) -> str | None:
        """A reason if the history does not cover a window beginning on ``start``."""
        first = self.series.first_day
        if first is None or first > start + timedelta(days=START_TOLERANCE_DAYS):
            return f"needs prices from {start} ({label}); the history starts {first}"
        return None

    def split_suspect(self, start: date) -> str | None:
        if self.series.split_adjusted:
            return None
        jump = first_jump(self.series.bars, start)
        if jump is None:
            return None
        day, change = jump
        return (
            f"the series is not split-adjusted and shows a change of {float(change) * 100:.0f} % "
            f"on {day} (possibly a split), so figures across it would be wrong"
        )


def _windowed(
    ctx: _Context,
    start: date,
    label: str,
    compute: Callable[[], tuple[Decimal, str, tuple[Value, ...]] | str],
) -> Value:
    """Run ``compute`` for a window starting at ``start`` after the coverage and split checks."""
    reason = ctx.covered(start, label) or ctx.split_suspect(start)
    if reason:
        return ctx.gap(reason, start)
    outcome = compute()
    if isinstance(outcome, str):
        return ctx.gap(outcome, start)
    amount, note, inputs = outcome
    return ctx.number(amount, note, inputs, start)


def _close_at(ctx: _Context, day: date) -> PriceBar | None:
    return ctx.series.on_or_before(day)


def _momentum(ctx: _Context, months_from: int, months_to: int = 0) -> Value:
    """``close(now - months_to) / close(now - months_from) - 1``."""
    start = months_before(ctx.last.day, months_from)

    def compute() -> tuple[Decimal, str, tuple[Value, ...]] | str:
        begin = _close_at(ctx, start)
        end_day = months_before(ctx.last.day, months_to) if months_to else ctx.last.day
        end = _close_at(ctx, end_day)
        if begin is None or end is None:
            return f"no close on or before {start}"
        if (start - begin.day).days > START_TOLERANCE_DAYS:
            return f"the nearest close to {start} is from {begin.day}"
        change = end.close / begin.close - 1
        note = f"close {end.day} / close {begin.day} - 1"
        return change, note, (price_value(ctx.series, begin), price_value(ctx.series, end))

    return _windowed(ctx, start, f"{months_from} months", compute)


def _volatility(ctx: _Context) -> Value:
    bars = ctx.series.bars
    if len(bars) < TRADING_DAYS + 1:
        return ctx.gap(
            f"needs {TRADING_DAYS} daily returns ({TRADING_DAYS + 1} closes); have {len(bars) - 1}"
        )
    window = bars[-(TRADING_DAYS + 1) :]
    start = window[0].day
    if reason := ctx.split_suspect(start):
        return ctx.gap(reason, start)
    returns = [math.log(float(b.close / a.close)) for a, b in zip(window, window[1:], strict=False)]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    annual = math.sqrt(variance) * math.sqrt(TRADING_DAYS)
    note = f"{len(returns)} daily log returns from {window[0].day} to {window[-1].day}"
    inputs = (price_value(ctx.series, window[0]), price_value(ctx.series, window[-1]))
    return ctx.number(_decimal(annual), note, inputs, start)


def _drawdown(ctx: _Context, years: int) -> Value:
    start = years_before(ctx.last.day, years)

    def compute() -> tuple[Decimal, str, tuple[Value, ...]] | str:
        bars = ctx.series.window(start, ctx.last.day)
        if len(bars) < 2:
            return "fewer than two closes in the window"
        peak = bars[0]
        worst = Decimal(0)
        worst_peak, worst_low = peak, peak
        for bar in bars:
            if bar.close > peak.close:
                peak = bar
            drawdown = bar.close / peak.close - 1
            if drawdown < worst:
                worst, worst_peak, worst_low = drawdown, peak, bar
        note = f"from the high on {worst_peak.day} to the low on {worst_low.day}"
        return (
            worst,
            note,
            (price_value(ctx.series, worst_peak), price_value(ctx.series, worst_low)),
        )

    return _windowed(ctx, start, f"{years} years", compute)


def _range(ctx: _Context) -> tuple[Value, Value]:
    start = ctx.last.day - timedelta(days=365)

    def compute(kind: str) -> Callable[[], tuple[Decimal, str, tuple[Value, ...]] | str]:
        def run() -> tuple[Decimal, str, tuple[Value, ...]] | str:
            bars = ctx.series.window(start, ctx.last.day)
            if not bars:
                return "no closes in the last 365 days"
            extreme = (
                max(bars, key=lambda b: b.close)
                if kind == "high"
                else min(bars, key=lambda b: b.close)
            )
            distance = ctx.last.close / extreme.close - 1
            note = f"close {ctx.last.day} / 52-week {kind} {extreme.close} on {extreme.day} - 1"
            return distance, note, (ctx.now, price_value(ctx.series, extreme))

        return run

    high = _windowed(ctx, start, "1 year", compute("high"))
    low = _windowed(ctx, start, "1 year", compute("low"))
    return high, low


def _weekly(bars: tuple[PriceBar, ...]) -> dict[date, PriceBar]:
    """The last close of each calendar week, keyed by the Monday of that week."""
    weeks: dict[date, PriceBar] = {}
    for bar in bars:
        weeks[bar.day - timedelta(days=bar.day.weekday())] = bar
    return weeks


def _beta(ctx: _Context) -> Value:
    benchmark = ctx.benchmark
    if benchmark is None:
        return ctx.gap("the index series could not be loaded")
    start = years_before(ctx.last.day, BETA_YEARS)
    reason = ctx.covered(start, f"{BETA_YEARS} years") or ctx.split_suspect(start)
    if reason:
        return ctx.gap(reason, start)
    stock = _weekly(ctx.series.window(start, ctx.last.day))
    index = _weekly(benchmark.window(start, ctx.last.day))
    pairs: list[tuple[float, float]] = []
    for monday in sorted(stock):
        before = monday - timedelta(days=7)
        if before in stock and monday in index and before in index:
            pairs.append(
                (
                    float(stock[monday].close / stock[before].close - 1),
                    float(index[monday].close / index[before].close - 1),
                )
            )
    if len(pairs) < BETA_MIN_WEEKS:
        return ctx.gap(
            f"needs {BETA_MIN_WEEKS} weekly returns shared with the index; have {len(pairs)}",
            start,
        )
    mean_s = sum(s for s, _ in pairs) / len(pairs)
    mean_m = sum(m for _, m in pairs) / len(pairs)
    var_m = sum((m - mean_m) ** 2 for _, m in pairs)
    if var_m == 0:
        return ctx.gap("the index did not move in the window", start)
    cov = sum((s - mean_s) * (m - mean_m) for s, m in pairs)
    note = f"{len(pairs)} weekly returns against {benchmark.ticker}, {start} to {ctx.last.day}"
    inputs = (ctx.now,)
    return ctx.number(_decimal(cov / var_m), note, inputs, start)


def compute_technicals(
    series: PriceSeries, benchmark: PriceSeries | None, *, as_of: date
) -> tuple[MarketMetric, ...]:
    """All technicals as of the latest close in ``series``."""
    last = series.last_bar
    assert last is not None, "a price series always has at least one bar"
    ctx = _Context(series, benchmark, last)
    age = (as_of - last.day).days
    values: dict[str, Value]
    if age > MAX_STALE_DAYS:
        stale = f"the latest close is from {last.day}, {age} days before {as_of}"
        values = {name: ctx.gap(stale) for name, _, _ in SPECS}
    else:
        high, low = _range(ctx)
        values = {
            "momentum_3m": _momentum(ctx, 3),
            "momentum_6m": _momentum(ctx, 6),
            "momentum_12m": _momentum(ctx, 12),
            "momentum_12_1": _momentum(ctx, 12, 1),
            "volatility_1y": _volatility(ctx),
            "max_drawdown_1y": _drawdown(ctx, 1),
            "max_drawdown_3y": _drawdown(ctx, 3),
            "beta_2y": _beta(ctx),
            "dist_52w_high": high,
            "dist_52w_low": low,
        }
    return tuple(
        MarketMetric(
            name=name, label=label, group="technicals", formula=formula, value=values[name]
        )
        for name, label, formula in SPECS
    )
