"""Technicals against hand-calculated references, plus every gap path.

Each test builds a tiny series whose expected number can be worked out on paper (the working is
in the test). Series use consecutive calendar days so that "N months earlier" is easy to find.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_forensics.market.technicals import SPECS, compute_technicals, first_jump
from stock_forensics.models import Value
from stock_forensics.providers.prices import PriceBar, PriceSeries
from tests.price_fixtures import bars, series

START = date(2024, 1, 1)


def run(
    stock: PriceSeries, benchmark: PriceSeries | None = None, as_of: date | None = None
) -> dict[str, Value]:
    last = stock.last_bar
    assert last is not None
    result = compute_technicals(stock, benchmark, as_of=as_of or last.day)
    return {m.name: m.value for m in result}


def linear(days: int = 400) -> PriceSeries:
    """Close = 100 + number of days since 2024-01-01, one bar per calendar day."""
    return series("X", bars(*[100 + i for i in range(days)], start=START))


def test_all_specs_are_returned_in_order() -> None:
    result = compute_technicals(linear(), None, as_of=START + timedelta(days=399))
    assert [m.name for m in result] == [name for name, _, _ in SPECS]
    assert {m.group for m in result} == {"technicals"}


def test_momentum_on_a_linear_series() -> None:
    values = run(linear())
    # 2024 is a leap year. Last day = 2024-01-01 + 399 days = 2025-02-03, close 499
    # 3 months earlier 2024-11-03 = day 307 -> 407; 6 months 2024-08-03 = day 215 -> 315
    # 12 months 2024-02-03 = day 33 -> 133; 1 month ago 2025-01-03 = day 368 -> 468
    assert values["momentum_3m"].value == Decimal(499) / Decimal(407) - 1
    assert values["momentum_6m"].value == Decimal(499) / Decimal(315) - 1
    assert values["momentum_12m"].value == Decimal(499) / Decimal(133) - 1
    assert values["momentum_12_1"].value == Decimal(468) / Decimal(133) - 1
    note = values["momentum_3m"].note or ""
    assert "close 2025-02-03 / close 2024-11-03" in note
    assert [v.value for v in values["momentum_3m"].inputs] == [Decimal(407), Decimal(499)]


def test_momentum_is_a_gap_when_the_history_is_too_short() -> None:
    values = run(series("X", bars(*range(100, 200), start=START)))  # 100 days
    assert values["momentum_3m"].value is not None  # 3 months fit into 100 days
    for name in ("momentum_6m", "momentum_12m", "momentum_12_1"):
        assert values[name].value is None
        assert "history starts 2024-01-01" in (values[name].note or "")


def test_volatility_of_alternating_moves() -> None:
    # Closes alternate x1.02 and /1.02, so the 252 log returns are +-ln(1.02), mean 0.
    # Sample variance = 252 * ln(1.02)^2 / 251; annualized = sqrt(variance) * sqrt(252).
    closes = [Decimal(100)]
    for i in range(300):
        closes.append(closes[-1] * (Decimal("1.02") if i % 2 == 0 else 1 / Decimal("1.02")))
    values = run(series("X", bars(*[f"{c:.10f}" for c in closes], start=START)))
    expected = math.sqrt(252 * math.log(1.02) ** 2 / 251) * math.sqrt(252)
    vol = values["volatility_1y"].value
    assert vol is not None and abs(float(vol) - expected) < 1e-5
    assert "252 daily log returns" in (values["volatility_1y"].note or "")


def test_volatility_of_a_constant_growth_series_is_zero() -> None:
    closes = [Decimal(100) * Decimal("1.01") ** i for i in range(300)]
    values = run(series("X", bars(*[f"{c:.12f}" for c in closes], start=START)))
    vol = values["volatility_1y"].value
    assert vol is not None and float(vol) < 1e-6


def test_volatility_needs_252_returns() -> None:
    values = run(series("X", bars(*range(100, 200), start=START)))
    assert values["volatility_1y"].value is None
    assert "needs 252 daily returns (253 closes); have 99" in (values["volatility_1y"].note or "")


def drawdown_series() -> PriceSeries:
    # 394 days at 100, then 100, 120, 90, 110, 60, 80. Highest earlier close before the low
    # of 60 is 120: drawdown 60 / 120 - 1 = -0.5. (The 3-year window is not covered.)
    return series("X", bars(*([100] * 394 + [100, 120, 90, 110, 60, 80]), start=START))


def test_maximum_drawdown_and_the_52_week_range() -> None:
    values = run(drawdown_series())
    assert values["max_drawdown_1y"].value == Decimal("-0.5")
    assert "high on 2025-01-30 to the low on 2025-02-02" in (values["max_drawdown_1y"].note or "")
    # 52 weeks: high 120, low 60, last close 80
    assert values["dist_52w_high"].value == Decimal(80) / Decimal(120) - 1  # -1/3
    assert values["dist_52w_low"].value == Decimal(80) / Decimal(60) - 1  # +1/3
    assert values["dist_52w_high"].value is not None and values["dist_52w_high"].value < 0
    assert values["dist_52w_low"].value is not None and values["dist_52w_low"].value > 0


def test_three_year_drawdown_needs_three_years_of_prices() -> None:
    values = run(drawdown_series())
    assert values["max_drawdown_3y"].value is None
    assert "needs prices from" in (values["max_drawdown_3y"].note or "")
    assert "the history starts 2024-01-01" in (values["max_drawdown_3y"].note or "")


def weekly(returns: list[float], start: date = date(2023, 1, 6)) -> PriceSeries:
    """One close per week (Fridays) following the given weekly returns, from 100."""
    closes = [Decimal(100)]
    for r in returns:
        closes.append(closes[-1] * (1 + Decimal(str(r))))
    return series(
        "X",
        tuple(
            PriceBar(day=start + timedelta(weeks=i), close=Decimal(f"{c:.10f}"))
            for i, c in enumerate(closes)
        ),
    )


def pattern(weeks: int) -> list[float]:
    return [0.01 if i % 3 else -0.02 for i in range(weeks)]


def test_beta_of_a_series_that_moves_twice_as_much_as_the_index_is_two() -> None:
    # Weekly stock return = 2 x weekly index return, so cov / var = 2 exactly.
    index_returns = pattern(160)
    index = weekly(index_returns)
    stock = weekly([2 * r for r in index_returns])
    values = run(stock, index)
    beta = values["beta_2y"].value
    assert beta is not None and abs(float(beta) - 2.0) < 1e-5
    assert "weekly returns against X" in (values["beta_2y"].note or "")


def test_beta_of_a_series_unrelated_to_the_index_is_zero() -> None:
    index = weekly(pattern(160))
    stock = weekly([0.0] * 160)  # never moves: covariance 0
    beta = run(stock, index)["beta_2y"].value
    assert beta is not None and abs(float(beta)) < 1e-9


def test_beta_gaps() -> None:
    stock = weekly(pattern(160))
    assert "could not be loaded" in (run(stock, None)["beta_2y"].note or "")
    flat_index = weekly([0.0] * 160)
    assert "did not move" in (run(stock, flat_index)["beta_2y"].note or "")
    short = run(weekly(pattern(40)), weekly(pattern(40)))["beta_2y"]
    assert short.value is None and "needs" in (short.note or "")
    # the window of two years is not covered by 60 weeks
    sixty = run(weekly(pattern(60)), weekly(pattern(60)))["beta_2y"]
    assert sixty.value is None and "needs prices from" in (sixty.note or "")


def test_a_stale_latest_close_makes_everything_a_gap() -> None:
    stock = linear()
    values = run(stock, as_of=START + timedelta(days=399 + 30))
    assert all(v.value is None for v in values.values())
    assert "30 days before" in (values["momentum_3m"].note or "")


def test_unadjusted_series_with_a_split_like_jump_is_not_trusted() -> None:
    # As traded: 100 for a year, a 2-for-1 split halves the price to 50.
    closes = [100] * 380 + [50] * 20
    raw = series("X", bars(*closes, start=START), split_adjusted=False)
    values = run(raw)
    for name in ("momentum_12m", "max_drawdown_1y", "dist_52w_high", "volatility_1y"):
        assert values[name].value is None, name
        assert "not split-adjusted" in (values[name].note or ""), name
        assert "-50 %" in (values[name].note or ""), name
    # ... a window after the jump is fine
    assert values["momentum_3m"].value is None or "possibly a split" not in (
        values["momentum_3m"].note or ""
    )
    # the same prices from a split-adjusted provider are used as they are
    adjusted = run(series("X", bars(*closes, start=START), split_adjusted=True))
    assert adjusted["momentum_12m"].value is not None


def test_first_jump_thresholds() -> None:
    def jump(*closes: int) -> object:
        return first_jump(bars(*closes, start=START), START)

    assert jump(100, 100, 100) is None
    assert jump(100, 71) is None  # -29 %: an ordinary bad day
    assert jump(100, 70) is not None  # -30 %: possibly a 3-for-2 split
    assert jump(100, 139) is None
    assert jump(100, 140) is not None  # +40 %: possibly a reverse split


def test_every_value_has_provenance_or_a_reason() -> None:
    for stock in (linear(), series("X", bars(*range(100, 130), start=START))):
        for value in run(stock).values():
            if value.value is None:
                assert value.note and value.note.strip()
            else:
                assert value.inputs and value.note
                assert value.source.provider == "stock_forensics"


@pytest.mark.parametrize("name", [n for n, _, _ in SPECS])
def test_every_spec_has_label_and_formula(name: str) -> None:
    spec = next(s for s in SPECS if s[0] == name)
    assert spec[1] and spec[2]
