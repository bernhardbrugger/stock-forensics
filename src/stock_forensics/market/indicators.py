"""Trend indicators for investors: moving averages, crosses, weekly RSI and MACD, relative strength.

Weekly and long-term only, no day trading. Everything is computed from the daily closes and says
so in its formula. These indicators **look back**: they describe what the price did, they do not
predict what it will do, and the readings are worded that way (no buy or sell words).

* **Moving averages:** simple averages of the last 50 and 200 closes. The report shows the
  distance of the price to each, and the 50-day average relative to the 200-day average.
* **Cross:** the latest day the 50-day average moved above the 200-day average ("golden cross")
  or below it ("death cross"), and how many days ago that was.
* **RSI (14, weekly):** Wilder's relative strength index on weekly closes (the last close of each
  calendar week; the newest, possibly unfinished week counts). Average gains and losses are
  smoothed as ``(previous x 13 + current) / 14``, started with the simple average of the first 14
  changes. Rules of thumb: above 70 stretched upward, below 30 stretched downward.
* **MACD (12, 26, 9, weekly):** the difference of two exponential averages of weekly closes
  (``alpha = 2 / (n + 1)``, started with the first close), its 9-week signal line and the
  histogram (MACD minus signal). All history that was loaded is used as warm-up.
* **Relative strength:** ``(1 + stock return) / (1 + index return) - 1`` over 6 and 12 months.

A window that the history does not cover is a gap with the reason, never a shorter window in
disguise (the 200-day average needs 200 closes, RSI and MACD at least 52 weekly closes). A series
that is not split-adjusted refuses to run across a jump that looks like a split.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from stock_forensics.flags import years_before
from stock_forensics.insiders.aggregate import months_before
from stock_forensics.market.common import gap, number, price_value
from stock_forensics.market.models import ChartPoint, MarketMetric, PriceChart
from stock_forensics.market.technicals import MAX_STALE_DAYS, START_TOLERANCE_DAYS, Context
from stock_forensics.models import Unit, Value
from stock_forensics.providers.prices import PriceBar, PriceSeries

SMA_FAST = 50
SMA_SLOW = 200
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
WEEKLY_MIN = 52  # weekly closes needed so that RSI and MACD are warmed up
RELATIVE_MONTHS = (6, 12)
NEAR = Decimal("0.02")  # within 2 % of an average counts as "at" it
RELATIVE_NEAR = Decimal("0.01")  # within 1 % of the index counts as "in line"
RSI_HIGH = Decimal(70)
RSI_LOW = Decimal(30)
CHART_YEARS = 3
CHART_MIN_POINTS = 26

Result = tuple[Value, str | None]  # the value and its reading in plain words

# name, label, formula
SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "dist_sma_50",
        "Price vs. 50-day average",
        "close / average of the last 50 closes - 1",
    ),
    (
        "dist_sma_200",
        "Price vs. 200-day average",
        "close / average of the last 200 closes - 1",
    ),
    (
        "sma_50_vs_200",
        "50-day vs. 200-day average",
        "average of the last 50 closes / average of the last 200 closes - 1",
    ),
    (
        "last_cross",
        "Days since the last 50/200-day cross",
        "days since the 50-day average last moved above (golden) or below (death) the "
        "200-day average",
    ),
    (
        "rsi_14w",
        "RSI (14 weeks)",
        "100 - 100 / (1 + average gain / average loss) of the weekly changes, "
        "Wilder smoothing over 14 weeks",
    ),
    (
        "macd_w",
        "MACD (weekly, 12/26/9)",
        "EMA(12) - EMA(26) of weekly closes; signal = EMA(9) of the MACD; "
        "histogram = MACD - signal",
    ),
    (
        "rel_strength_6m",
        "Relative strength vs. S&P 500, 6 months",
        "(1 + stock return) / (1 + index return) - 1 over 6 months",
    ),
    (
        "rel_strength_12m",
        "Relative strength vs. S&P 500, 12 months",
        "(1 + stock return) / (1 + index return) - 1 over 12 months",
    ),
)


# --- pure calculations (also tested on tiny series with other parameters) -----------------------


def sma_series(closes: list[Decimal], n: int) -> list[Decimal | None]:
    """Simple moving average of the last ``n`` values; ``None`` until there are ``n``."""
    out: list[Decimal | None] = [None] * len(closes)
    total = Decimal(0)
    for i, close in enumerate(closes):
        total += close
        if i >= n:
            total -= closes[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def cross_events(
    days: list[date], fast: list[Decimal | None], slow: list[Decimal | None]
) -> list[tuple[date, str]]:
    """The days the fast average moved above ("golden") or below ("death") the slow one."""
    events: list[tuple[date, str]] = []
    previous = 0
    for day, f, s in zip(days, fast, slow, strict=True):
        if f is None or s is None or f == s:
            continue  # no result yet, or exactly equal: the side is decided by the next day
        side = 1 if f > s else -1
        if previous and side != previous:
            events.append((day, "golden" if side > 0 else "death"))
        previous = side
    return events


def weekly_closes(bars: tuple[PriceBar, ...]) -> list[PriceBar]:
    """The last close of every calendar week (weeks start on Monday), oldest first."""
    weeks: dict[date, PriceBar] = {}
    for bar in bars:
        weeks[bar.day - timedelta(days=bar.day.weekday())] = bar
    return [weeks[monday] for monday in sorted(weeks)]


def rsi(closes: list[Decimal], period: int = RSI_PERIOD) -> Decimal | None:
    """Wilder's RSI of the last value; ``None`` if there are fewer than ``period + 1`` closes."""
    if len(closes) < period + 1:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:], strict=False)]
    gains = [max(c, Decimal(0)) for c in changes]
    losses = [max(-c, Decimal(0)) for c in changes]
    average_gain = sum(gains[:period], Decimal(0)) / period
    average_loss = sum(losses[:period], Decimal(0)) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
    if average_loss == 0:
        return Decimal(100) if average_gain > 0 else Decimal(50)
    return 100 - 100 / (1 + average_gain / average_loss)


def ema(values: list[Decimal], n: int) -> list[Decimal]:
    """Exponential moving average with ``alpha = 2 / (n + 1)``, started with the first value."""
    alpha = Decimal(2) / (n + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def macd(
    closes: list[Decimal],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> tuple[Decimal, Decimal, Decimal]:
    """The latest MACD line, signal line and histogram."""
    line = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow), strict=True)]
    signal_line = ema(line, signal)
    return line[-1], signal_line[-1], line[-1] - signal_line[-1]


# --- values with their readings -----------------------------------------------------------------


def _pct(x: Decimal) -> str:
    return f"{abs(float(x)) * 100:.0f}%"


def distance_reading(ratio: Decimal, n: int, trend: str) -> str:
    """The distance of the price to its ``n``-day average in plain words."""
    if abs(ratio) <= NEAR:
        return f"The price sits right at its {n}-day average: no clear {trend} trend."
    way, direction = ("above", "up") if ratio > 0 else ("below", "down")
    return (
        f"The price is {_pct(ratio)} {way} its {n}-day average: "
        f"the {trend} trend points {direction}."
    )


def rsi_reading(value: Decimal) -> str:
    if value >= RSI_HIGH:
        return (
            f"RSI {float(value):.0f}: the price has risen fast lately, stretched upward "
            f"(above {RSI_HIGH})."
        )
    if value <= RSI_LOW:
        return (
            f"RSI {float(value):.0f}: the price has fallen hard lately, stretched downward "
            f"(below {RSI_LOW})."
        )
    return f"RSI {float(value):.0f}: in the middle range, neither stretched up nor down."


def macd_reading(line: Decimal, histogram: Decimal) -> str:
    """Where the weekly MACD stands relative to its signal line and to zero, in plain words."""
    if histogram > 0 and line > 0:
        return "Momentum is up: the weekly MACD is above its signal line and above zero."
    if histogram > 0:
        return "Momentum is improving: above its signal line, but still below zero."
    if line > 0:
        return "Momentum is fading: below its signal line, though still above zero."
    return "Momentum is down: the weekly MACD is below its signal line and below zero."


def relative_reading(relative: Decimal, months: int) -> str:
    if abs(relative) < RELATIVE_NEAR:
        return f"Over {months} months the stock moved about in line with the S&P 500."
    way = "better" if relative > 0 else "worse"
    return f"Over {months} months the stock did {_pct(relative)} {way} than the S&P 500."


def _round(x: Decimal, places: str = "0.0001") -> Decimal:
    return x.quantize(Decimal(places))


def _sma_value(ctx: Context, closes: list[Decimal], n: int) -> Value:
    bars = ctx.series.bars[-n:]
    average = sum(closes[-n:], Decimal(0)) / n
    return number(
        ctx.series,
        average,
        Unit.USD_PER_SHARE,
        bars[-1].day,
        f"average of the {n} closes from {bars[0].day} to {bars[-1].day}",
        (price_value(ctx.series, bars[0]), price_value(ctx.series, bars[-1])),
        bars[0].day,
    )


def _ratio_gap(ctx: Context, reason: str, start: date | None = None) -> Result:
    return ctx.gap(reason, start), None


def _need(ctx: Context, n: int, what: str) -> str | None:
    have = len(ctx.series.bars)
    if have < n:
        return f"needs {n} daily closes for {what}; have {have}"
    return ctx.split_suspect(ctx.series.bars[-n].day)


def _distance(ctx: Context, closes: list[Decimal], n: int, trend: str) -> Result:
    if reason := _need(ctx, n, f"the {n}-day average"):
        return _ratio_gap(ctx, reason)
    average = _sma_value(ctx, closes, n)
    assert average.value is not None
    ratio = ctx.last.close / average.value - 1
    start = ctx.series.bars[-n].day
    value = ctx.number(
        ratio,
        f"close {ctx.last.day} / average of the last {n} closes - 1 (the average is "
        f"${float(average.value):,.2f})",
        (ctx.now, average),
        start,
    )
    reading = distance_reading(ratio, n, trend)
    return value, reading


def _fast_vs_slow(ctx: Context, closes: list[Decimal]) -> Result:
    if reason := _need(ctx, SMA_SLOW, f"the {SMA_SLOW}-day average"):
        return _ratio_gap(ctx, reason)
    fast, slow = _sma_value(ctx, closes, SMA_FAST), _sma_value(ctx, closes, SMA_SLOW)
    assert fast.value is not None and slow.value is not None
    ratio = fast.value / slow.value - 1
    value = ctx.number(
        ratio,
        f"average of the last {SMA_FAST} closes / average of the last {SMA_SLOW} closes - 1",
        (fast, slow),
        ctx.series.bars[-SMA_SLOW].day,
    )
    if ratio > 0:
        reading = (
            "The 50-day average is above the 200-day average: the shorter trend is stronger "
            "than the longer one."
        )
    elif ratio < 0:
        reading = (
            "The 50-day average is below the 200-day average: the shorter trend is weaker "
            "than the longer one."
        )
    else:
        reading = "The 50-day and 200-day averages are equal."
    return value, reading


def _last_cross(ctx: Context, closes: list[Decimal]) -> Result:
    if reason := _need(ctx, SMA_SLOW + 1, "a cross of the averages"):
        return _ratio_gap_unit(ctx, reason, Unit.DAYS)
    days = [b.day for b in ctx.series.bars]
    fast, slow = sma_series(closes, SMA_FAST), sma_series(closes, SMA_SLOW)
    events = cross_events(days, fast, slow)
    first_day = days[SMA_SLOW - 1]
    if not events:
        side = "above" if (fast[-1] or 0) > (slow[-1] or 0) else "below"
        reason = (
            f"no cross since {first_day}, when both averages exist: the 50-day average has been "
            f"{side} the 200-day average the whole time"
        )
        return gap(ctx.series, reason, Unit.DAYS, ctx.last.day, (ctx.now,), first_day), None
    day, kind = events[-1]
    since = (ctx.last.day - day).days
    name = "Golden cross" if kind == "golden" else "Death cross"
    move = "above" if kind == "golden" else "below"
    value = ctx.number_in(
        Decimal(since),
        Unit.DAYS,
        f"{name} on {day}: the 50-day average moved {move} the 200-day average",
        (ctx.now,),
        first_day,
    )
    reading = (
        f"{name} on {day} ({since} days ago): the 50-day average moved {move} the 200-day "
        f"average. It looks back and says nothing about what comes next."
    )
    return value, reading


def _weekly_window(ctx: Context) -> tuple[list[PriceBar], str | None]:
    weeks = weekly_closes(ctx.series.bars)
    if len(weeks) < WEEKLY_MIN:
        return weeks, (
            f"needs {WEEKLY_MIN} weekly closes (about a year) to be warmed up; have {len(weeks)}"
        )
    return weeks, ctx.split_suspect(weeks[0].day)


def _rsi(ctx: Context) -> Result:
    weeks, reason = _weekly_window(ctx)
    if reason:
        return _ratio_gap_unit(ctx, reason, Unit.SCORE)
    closes = [w.close for w in weeks]
    result = rsi(closes)
    assert result is not None
    value = number(
        ctx.series,
        _round(result, "0.01"),
        Unit.SCORE,
        ctx.last.day,
        f"{len(closes)} weekly closes from {weeks[0].day} to {weeks[-1].day}, Wilder smoothing",
        (ctx.now,),
        weeks[0].day,
    )
    reading = rsi_reading(result)
    return value, reading


def _macd(ctx: Context) -> Result:
    weeks, reason = _weekly_window(ctx)
    if reason:
        return _ratio_gap_unit(ctx, reason, Unit.USD_PER_SHARE)
    closes = [w.close for w in weeks]
    line, signal, histogram = macd(closes)
    value = number(
        ctx.series,
        _round(line),
        Unit.USD_PER_SHARE,
        ctx.last.day,
        f"MACD {float(line):.2f}, signal {float(signal):.2f}, histogram {float(histogram):+.2f} "
        f"({len(closes)} weekly closes from {weeks[0].day})",
        (ctx.now,),
        weeks[0].day,
    )
    reading = macd_reading(line, histogram)
    return value, reading


def _ratio_gap_unit(ctx: Context, reason: str, unit: Unit) -> Result:
    return gap(ctx.series, reason, unit, ctx.last.day, (ctx.now,)), None


def _relative(ctx: Context, months: int) -> Result:
    benchmark = ctx.benchmark
    if benchmark is None:
        return _ratio_gap(ctx, "the index series could not be loaded")
    start = months_before(ctx.last.day, months)
    reason = ctx.covered(start, f"{months} months") or ctx.split_suspect(start)
    if reason:
        return _ratio_gap(ctx, reason, start)
    stock_then, stock_now = ctx.series.on_or_before(start), ctx.last
    index_then, index_now = benchmark.on_or_before(start), benchmark.on_or_before(ctx.last.day)
    if stock_then is None or index_then is None or index_now is None:
        return _ratio_gap(ctx, f"no close on or before {start} in the stock or index series", start)
    if (start - stock_then.day).days > START_TOLERANCE_DAYS:
        return _ratio_gap(ctx, f"the nearest close to {start} is from {stock_then.day}", start)
    if (ctx.last.day - index_now.day).days > MAX_STALE_DAYS:
        return _ratio_gap(ctx, f"the index series ends on {index_now.day}", start)
    stock_return = stock_now.close / stock_then.close - 1
    index_return = index_now.close / index_then.close - 1
    relative = (1 + stock_return) / (1 + index_return) - 1
    value = ctx.number(
        relative,
        f"stock {float(stock_return) * 100:+.1f}%, {benchmark.ticker} "
        f"{float(index_return) * 100:+.1f}% since {start}",
        (
            price_value(ctx.series, stock_then),
            ctx.now,
            price_value(benchmark, index_then),
            price_value(benchmark, index_now),
        ),
        start,
    )
    reading = relative_reading(relative, months)
    return value, reading


def compute_trend(
    series: PriceSeries, benchmark: PriceSeries | None, *, as_of: date
) -> tuple[MarketMetric, ...]:
    """All trend indicators as of the latest close in ``series``."""
    last = series.last_bar
    assert last is not None, "a price series always has at least one bar"
    ctx = Context(series, benchmark, last)
    age = (as_of - last.day).days
    results: dict[str, Result]
    if age > MAX_STALE_DAYS:
        stale = f"the latest close is from {last.day}, {age} days before {as_of}"
        results = {name: (ctx.gap(stale), None) for name, _, _ in SPECS}
    else:
        closes = [b.close for b in series.bars]
        results = {
            "dist_sma_50": _distance(ctx, closes, SMA_FAST, "medium-term"),
            "dist_sma_200": _distance(ctx, closes, SMA_SLOW, "long-term"),
            "sma_50_vs_200": _fast_vs_slow(ctx, closes),
            "last_cross": _last_cross(ctx, closes),
            "rsi_14w": _rsi(ctx),
            "macd_w": _macd(ctx),
            "rel_strength_6m": _relative(ctx, RELATIVE_MONTHS[0]),
            "rel_strength_12m": _relative(ctx, RELATIVE_MONTHS[1]),
        }
    return tuple(
        MarketMetric(
            name=name,
            label=label,
            group="trend",
            formula=formula,
            value=results[name][0],
            reading=results[name][1],
        )
        for name, label, formula in SPECS
    )


def build_chart(series: PriceSeries, *, years: int = CHART_YEARS) -> PriceChart | None:
    """Weekly points of the last ``years`` years with both averages (computed on daily closes)."""
    closes = [b.close for b in series.bars]
    fast, slow = sma_series(closes, SMA_FAST), sma_series(closes, SMA_SLOW)
    last = series.last_bar
    if last is None:
        return None
    start = years_before(last.day, years)
    by_day = {b.day: i for i, b in enumerate(series.bars)}
    points = [
        ChartPoint(
            day=w.day, close=w.close, sma_50=fast[by_day[w.day]], sma_200=slow[by_day[w.day]]
        )
        for w in weekly_closes(series.bars)
        if w.day >= start
    ]
    if len(points) < CHART_MIN_POINTS:
        return None
    return PriceChart(
        ticker=series.ticker,
        source=series.source,
        note=(
            "last close of each week; the 50 and 200-day averages are computed on the daily "
            "closes and read off at the same day"
            + ("; closes are split-adjusted" if series.split_adjusted else "; closes are as traded")
        ),
        points=tuple(points),
    )
