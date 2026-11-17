"""Trend indicators against hand-calculated references, plus every gap path and the chart.

The working of each expected number is in the test, on series small enough to follow on paper.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_forensics.market.indicators import (
    SPECS,
    build_chart,
    compute_trend,
    cross_events,
    distance_reading,
    ema,
    macd,
    macd_reading,
    relative_reading,
    rsi,
    rsi_reading,
    sma_series,
    weekly_closes,
)
from stock_forensics.market.models import MarketMetric, PriceChart
from stock_forensics.providers.prices import PriceBar, PriceSeries
from stock_forensics.report.chart import HEIGHT, WIDTH, price_chart_svg
from tests.price_fixtures import bars, series, synthetic

START = date(2024, 1, 1)
D = Decimal


def trend(
    stock: PriceSeries, benchmark: PriceSeries | None = None, as_of: date | None = None
) -> dict[str, MarketMetric]:
    last = stock.last_bar
    assert last is not None
    return {m.name: m for m in compute_trend(stock, benchmark, as_of=as_of or last.day)}


def daily(closes: list[int | float], start: date = START) -> PriceSeries:
    return series("X", bars(*closes, start=start))


def weekly(closes: list[int | float], start: date = date(2023, 1, 6)) -> PriceSeries:
    """One close per week (Fridays)."""
    return series(
        "X",
        tuple(
            PriceBar(day=start + timedelta(weeks=i), close=D(str(c))) for i, c in enumerate(closes)
        ),
    )


# --- the calculations on tiny series ----------------------------------------------------------


def test_sma_series() -> None:
    # averages of 3: (1+2+3)/3 = 2, (2+3+4)/3 = 3, (3+4+5)/3 = 4, (4+5+6)/3 = 5
    assert sma_series([D(c) for c in (1, 2, 3, 4, 5, 6)], 3) == [None, None, D(2), D(3), D(4), D(5)]
    assert sma_series([D(1), D(2)], 3) == [None, None]


def test_cross_events() -> None:
    days = [START + timedelta(days=i) for i in range(8)]
    slow = [None, None, D(3), D(3), D(3), D(3), D(3), D(3)]
    fast = [None, None, D(2), D(2), D(4), D(5), D(3), D(2)]
    # d2, d3: fast below slow. d4: above -> golden cross. d5: above. d6: equal (no side change).
    # d7: below -> death cross.
    assert cross_events(days, fast, slow) == [(days[4], "golden"), (days[7], "death")]
    # touching the slow average and turning away is not a cross
    touch = [None, None, D(2), D(2), D(3), D(2), D(2), D(2)]
    assert cross_events(days, touch, slow) == []
    # no averages yet, no events
    assert cross_events(days[:2], [None, None], [None, None]) == []


def test_weekly_closes_take_the_last_close_of_each_week() -> None:
    # Mon 2024-01-01 ... Wed 2024-01-10 on consecutive days: week 1 ends Sun Jan 7 (close 107),
    # week 2 has Jan 8 to Jan 10 and its last close is Wed Jan 10 (110).
    weeks = weekly_closes(bars(*range(101, 111), start=START))
    assert [(w.day, w.close) for w in weeks] == [
        (date(2024, 1, 7), D(107)),
        (date(2024, 1, 10), D(110)),
    ]


def test_rsi_with_wilder_smoothing_by_hand() -> None:
    # closes 10, 11, 10, 12, 11, 13 -> changes +1, -1, +2, -1, +2; period 3
    # first averages (changes 1..3): gain (1+0+2)/3 = 1, loss (0+1+0)/3 = 1/3
    # change 4 (-1): gain (1*2+0)/3 = 2/3, loss (1/3*2+1)/3 = 5/9
    # change 5 (+2): gain (2/3*2+2)/3 = 10/9, loss (5/9*2+0)/3 = 10/27
    # RS = (10/9) / (10/27) = 3 -> RSI = 100 - 100 / (1 + 3) = 75
    result = rsi([D(c) for c in (10, 11, 10, 12, 11, 13)], 3)
    assert result is not None and abs(result - 75) < D("1e-20")


def test_rsi_edges() -> None:
    assert rsi([D(c) for c in (1, 2, 3, 4, 5)], 3) == 100  # only gains
    assert rsi([D(c) for c in (5, 4, 3, 2, 1)], 3) == 0  # only losses
    assert rsi([D(5)] * 5, 3) == 50  # nothing moved
    assert rsi([D(1), D(2), D(3)], 3) is None  # needs period + 1 closes


def test_ema_by_hand() -> None:
    # alpha = 2/3 for n = 2: 10, 10 + 2/3 * 2 = 34/3, 34/3 + 2/3 * (11 - 34/3) = 100/9
    out = ema([D(10), D(12), D(11)], 2)
    assert out[0] == 10
    assert abs(out[1] - D(34) / 3) < D("1e-20") and abs(out[2] - D(100) / 9) < D("1e-20")


def test_macd_by_hand() -> None:
    # closes 10, 12, 11, 13 with fast 2, slow 3, signal 2
    # EMA2 (alpha 2/3): 10, 34/3, 100/9, 334/27     EMA3 (alpha 1/2): 10, 11, 11, 12
    # MACD line: 0, 1/3, 1/9, 10/27
    # signal (EMA2 of the line): 0, 2/9, 4/27, 8/27    histogram: 10/27 - 8/27 = 2/27
    line, signal, histogram = macd([D(c) for c in (10, 12, 11, 13)], fast=2, slow=3, signal=2)
    assert abs(line - D(10) / 27) < D("1e-20")
    assert abs(signal - D(8) / 27) < D("1e-20")
    assert abs(histogram - D(2) / 27) < D("1e-20")


# --- the readings -----------------------------------------------------------------------------


def test_distance_readings() -> None:
    assert "right at its 200-day average: no clear long-term trend" in distance_reading(
        D("0.02"), 200, "long-term"
    )
    assert distance_reading(D("0.12"), 200, "long-term") == (
        "The price is 12% above its 200-day average: the long-term trend points up."
    )
    assert distance_reading(D("-0.30"), 50, "medium-term") == (
        "The price is 30% below its 50-day average: the medium-term trend points down."
    )
    assert "right at" in distance_reading(D("-0.02"), 50, "medium-term")  # the edge counts as "at"
    assert "above" in distance_reading(D("0.0201"), 50, "medium-term")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (D(70), "stretched upward (above 70)"),
        (D("69.9"), "middle range"),
        (D("30.1"), "middle range"),
        (D(30), "stretched downward (below 30)"),
        (D(100), "stretched upward"),
        (D(0), "stretched downward"),
    ],
)
def test_rsi_readings(value: Decimal, text: str) -> None:
    assert text in rsi_reading(value)


@pytest.mark.parametrize(
    ("line", "histogram", "text"),
    [
        (D(1), D(1), "Momentum is up"),
        (D(-1), D(1), "Momentum is improving"),
        (D(1), D(-1), "Momentum is fading"),
        (D(-1), D(-1), "Momentum is down"),
        (D(1), D(0), "Momentum is fading"),  # a zero histogram is not above the signal line
    ],
)
def test_macd_readings(line: Decimal, histogram: Decimal, text: str) -> None:
    assert macd_reading(line, histogram).startswith(text)


def test_relative_readings() -> None:
    assert "about in line" in relative_reading(D("0.0099"), 6)
    assert (
        relative_reading(D("0.09"), 6) == "Over 6 months the stock did 9% better than the S&P 500."
    )
    assert relative_reading(D("-0.20"), 12) == (
        "Over 12 months the stock did 20% worse than the S&P 500."
    )


def test_no_reading_uses_advice_words() -> None:
    texts = [
        distance_reading(D("0.5"), 200, "long-term"),
        distance_reading(D("-0.5"), 200, "long-term"),
        *(rsi_reading(D(v)) for v in (10, 50, 90)),
        *(macd_reading(D(a), D(b)) for a in (1, -1) for b in (1, -1)),
        relative_reading(D("0.3"), 6),
    ]
    banned = re.compile(r"\b(buy|sell|bullish|bearish|signal to|should|recommend)\b", re.IGNORECASE)
    assert not [t for t in texts if banned.search(t)]


# --- moving averages and the cross on whole series --------------------------------------------


def test_averages_on_a_linear_series() -> None:
    # closes 100 .. 349 on 250 consecutive days. Last 50 closes 300..349 average 324.5,
    # last 200 closes 150..349 average 249.5.
    values = trend(daily([100 + i for i in range(250)]))
    assert values["dist_sma_50"].value.value == D(349) / D("324.5") - 1
    assert values["dist_sma_200"].value.value == D(349) / D("249.5") - 1
    assert values["sma_50_vs_200"].value.value == D("324.5") / D("249.5") - 1
    inputs = values["dist_sma_50"].value.inputs
    assert [v.value for v in inputs] == [D(349), D("324.5")]
    assert "average of the 50 closes from" in (inputs[1].note or "")
    reading = values["dist_sma_200"].reading or ""
    assert reading.startswith("The price is 40% above its 200-day average")
    assert (values["sma_50_vs_200"].reading or "").startswith("The 50-day average is above")


def test_golden_cross_after_a_v_shaped_price() -> None:
    # 250 days down from 500 by 1 a day, then 250 days up by 2 a day: while falling the 50-day
    # average is below the 200-day average; on the way up it overtakes it once.
    closes = [500 - i for i in range(250)] + [250 + 2 * i for i in range(1, 251)]
    stock = daily(closes)
    values = trend(stock)
    cross = values["last_cross"]
    assert cross.value.value is not None and cross.value.unit.value == "days"
    assert (cross.value.note or "").startswith("Golden cross on ")
    # the reported day agrees with the events found directly
    prices = [b.close for b in stock.bars]
    events = cross_events(
        [b.day for b in stock.bars], sma_series(prices, 50), sma_series(prices, 200)
    )
    assert [kind for _, kind in events] == ["golden"]
    day = events[0][0]
    assert day > START + timedelta(days=250)  # after the turn
    assert cross.value.value == (stock.bars[-1].day - day).days
    assert f"Golden cross on {day} ({cross.value.value} days ago)" in (cross.reading or "")
    assert "says nothing about what comes next" in (cross.reading or "")


def test_death_cross_after_an_inverted_v() -> None:
    closes = [100 + 2 * i for i in range(250)] + [598 - 2 * i for i in range(1, 251)]
    cross = trend(daily(closes))["last_cross"]
    assert (cross.value.note or "").startswith("Death cross on ")
    assert "moved below the 200-day average" in (cross.value.note or "")


def test_no_cross_is_a_gap_that_says_where_the_averages_stand() -> None:
    cross = trend(daily([100 + i for i in range(250)]))["last_cross"]  # always rising
    assert cross.value.value is None
    assert "no cross since" in (cross.value.note or "")
    assert "the 50-day average has been above the 200-day average the whole time" in (
        cross.value.note or ""
    )


# --- weekly RSI and MACD on whole series ------------------------------------------------------


def test_weekly_rsi_of_pure_trends_and_of_a_flat_price() -> None:
    up = trend(weekly([100 + i for i in range(60)]))["rsi_14w"]
    assert up.value.value == D("100.00") and "stretched upward" in (up.reading or "")
    down = trend(weekly([200 - i for i in range(60)]))["rsi_14w"]
    assert down.value.value == D("0.00") and "stretched downward" in (down.reading or "")
    flat = trend(weekly([100] * 60))["rsi_14w"]
    assert flat.value.value == D("50.00") and "middle range" in (flat.reading or "")
    assert "60 weekly closes" in (up.value.note or "")


def test_weekly_rsi_and_macd_need_a_year_of_weeks() -> None:
    values = trend(weekly([100 + i for i in range(40)]))
    for name in ("rsi_14w", "macd_w"):
        assert values[name].value.value is None
        assert "needs 52 weekly closes (about a year) to be warmed up; have 40" in (
            values[name].value.note or ""
        )
        assert values[name].reading is None


def test_weekly_macd_of_an_accelerating_price_is_up() -> None:
    # closes 100 + 0.05 i^2 for 80 weeks: the short average runs away from the long one
    values = trend(weekly([100 + 0.05 * i * i for i in range(80)]))
    macd_metric = values["macd_w"]
    assert macd_metric.value.value is not None and macd_metric.value.value > 0
    assert "histogram +" in (macd_metric.value.note or "")
    assert (macd_metric.reading or "").startswith("Momentum is up")
    assert macd_metric.value.unit.value == "USD/share"


# --- relative strength ------------------------------------------------------------------------


def flat_then_jump(days: int, base: int, last: int) -> PriceSeries:
    return daily([base] * (days - 1) + [last])


def test_relative_strength_by_hand() -> None:
    # stock 100 -> 120 (+20 %), index 1000 -> 1100 (+10 %): 1.20 / 1.10 - 1 = 0.0909...
    stock, index = flat_then_jump(420, 100, 120), flat_then_jump(420, 1000, 1100)
    values = trend(stock, index)
    for name in ("rel_strength_6m", "rel_strength_12m"):
        assert abs(values[name].value.value - (D("1.2") / D("1.1") - 1)) < D("1e-20")
    assert (values["rel_strength_6m"].reading or "") == (
        "Over 6 months the stock did 9% better than the S&P 500."
    )
    note = values["rel_strength_6m"].value.note or ""
    assert "stock +20.0%" in note and "+10.0%" in note
    assert len(values["rel_strength_6m"].value.inputs) == 4  # both prices of both series


def test_relative_strength_gaps() -> None:
    stock = flat_then_jump(420, 100, 120)
    assert "could not be loaded" in (trend(stock, None)["rel_strength_6m"].value.note or "")
    ending_early = daily([1000] * 300)  # the index stops months before the stock
    note = trend(stock, ending_early)["rel_strength_6m"].value.note or ""
    assert "the index series ends on" in note
    short = trend(daily([100] * 150), daily([1000] * 150))
    assert "needs prices from" in (short["rel_strength_12m"].value.note or "")


# --- gaps, staleness, split guard -------------------------------------------------------------


def test_a_short_history_gives_reasons_not_numbers() -> None:
    values = trend(daily(list(range(100, 200))))  # 100 days, like a free Alpha Vantage key
    assert values["dist_sma_50"].value.value is not None
    assert values["dist_sma_200"].value.value is None
    assert "needs 200 daily closes for the 200-day average; have 100" in (
        values["dist_sma_200"].value.note or ""
    )
    for name in ("sma_50_vs_200", "last_cross", "rsi_14w", "macd_w", "rel_strength_12m"):
        assert values[name].value.value is None, name
        assert values[name].value.note, name


def test_a_stale_price_makes_everything_a_gap() -> None:
    values = trend(daily([100 + i for i in range(250)]), as_of=START + timedelta(days=249 + 30))
    assert all(m.value.value is None for m in values.values())
    assert "30 days before" in (values["dist_sma_50"].value.note or "")


def test_unadjusted_series_with_a_split_like_jump_is_not_trusted() -> None:
    closes = [100] * 230 + [50] * 20  # a 2-for-1 split, as traded
    raw = trend(series("X", bars(*closes, start=START), split_adjusted=False))
    assert raw["dist_sma_200"].value.value is None
    assert "not split-adjusted" in (raw["dist_sma_200"].value.note or "")
    adjusted = trend(series("X", bars(*closes, start=START), split_adjusted=True))
    assert adjusted["dist_sma_200"].value.value is not None


def test_every_trend_value_has_provenance_or_a_reason() -> None:
    long = synthetic("X", date(2023, 1, 2), date(2026, 6, 30))
    index = synthetic("^I", date(2023, 1, 2), date(2026, 6, 30), base=4000, drift=0.0003)
    for stock, bench in ((long, index), (daily(list(range(100, 130))), None)):
        for metric in trend(stock, bench).values():
            value = metric.value
            if value.value is None:
                assert value.note and value.note.strip()
                assert metric.reading is None
            else:
                assert value.inputs and value.note and metric.reading
                assert value.source.provider == "stock_forensics"
            assert metric.group == "trend" and metric.formula and metric.label


def test_specs_are_complete_and_in_order() -> None:
    names = [name for name, _, _ in SPECS]
    assert names == [m for m in trend(daily(list(range(100, 130))))]


# --- the chart --------------------------------------------------------------------------------


def long_series() -> PriceSeries:
    return synthetic("AAPL", date(2023, 1, 2), date(2026, 6, 30))


def test_chart_points_are_weekly_with_both_averages() -> None:
    chart = build_chart(long_series())
    assert chart is not None
    days = [p.day for p in chart.points]
    assert days == sorted(days) and len(set(days)) == len(days)
    assert chart.points[0].day >= date(2023, 6, 30)  # the last 3 years only
    assert {(b - a).days for a, b in zip(days, days[1:], strict=False)} <= {
        4,
        5,
        6,
        7,
        8,
        9,
    }  # weeks; 4 = the unfinished newest week
    first, last = chart.points[0], chart.points[-1]
    assert last.sma_50 is not None and last.sma_200 is not None
    # the series starts 2023-01-02: at the first shown week there are fewer than 200 closes
    assert first.sma_50 is not None and first.sma_200 is None
    assert chart.source.provider == "fakeprices" and "last close of each week" in chart.note


def test_chart_needs_enough_weeks() -> None:
    assert build_chart(synthetic("X", date(2026, 1, 2), date(2026, 6, 30))) is not None  # 26 weeks
    assert build_chart(synthetic("X", date(2026, 3, 2), date(2026, 6, 30))) is None


def parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def test_the_svg_is_well_formed_accessible_and_finite() -> None:
    chart = build_chart(long_series())
    assert chart is not None
    svg = str(price_chart_svg(chart))
    root = parse(svg)
    ns = "{http://www.w3.org/2000/svg}"
    assert root.tag == f"{ns}svg" and root.get("viewBox") == f"0 0 {WIDTH} {HEIGHT}"
    assert root.get("role") == "img"
    assert root.find(f"{ns}title") is not None and root.find(f"{ns}desc") is not None
    desc = root.find(f"{ns}desc")
    assert desc is not None and desc.text and "Weekly closing price of AAPL" in desc.text
    assert {p.get("class") for p in root.findall(f"{ns}path")} == {"px", "s50", "s200"}
    assert "<script" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"\b(nan|inf)\b", svg, re.IGNORECASE)
    for x_text, y_text in re.findall(r"(-?\d+\.\d),(-?\d+\.\d)", svg):
        assert 0 <= float(x_text) <= WIDTH and 0 <= float(y_text) <= HEIGHT
    assert svg == str(price_chart_svg(chart))  # deterministic


def test_the_svg_shades_deep_drawdowns_only() -> None:
    rising = synthetic("X", date(2023, 1, 2), date(2026, 6, 30), wave=0.0)  # only goes up
    chart_up = build_chart(rising)
    assert chart_up is not None and "<polygon" not in price_chart_svg(chart_up)
    # up for two years, then down 40 %
    closes = [100 + i for i in range(500)] + [599 - 3 * i for i in range(1, 101)]
    crash = build_chart(daily(closes, start=date(2024, 1, 1)))
    assert crash is not None
    shaded = str(price_chart_svg(crash))
    assert shaded.count('class="dd"') >= 2  # the polygon and the legend swatch
    assert "<polygon" in shaded
    parse(shaded)


def test_the_svg_escapes_the_ticker() -> None:
    chart = build_chart(long_series())
    assert chart is not None
    hostile = chart.model_copy(update={"ticker": '<script>alert("x")</script>'})
    svg = str(price_chart_svg(hostile))
    assert "<script" not in svg and "&lt;script&gt;" in svg
    parse(svg)


def test_a_flat_price_does_not_divide_by_zero() -> None:
    flat = build_chart(daily([100] * 600, start=date(2024, 1, 1)))
    assert flat is not None
    parse(str(price_chart_svg(flat)))


def test_chart_model_roundtrips() -> None:
    chart = build_chart(long_series())
    assert chart is not None
    assert PriceChart.model_validate_json(chart.model_dump_json()) == chart
