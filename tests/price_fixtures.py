"""A fake price provider for the tests: deterministic series, never a network call.

``FakePrices`` answers like a provider: business days only, closes between ``start`` and
``end``. ``synthetic`` builds a smooth, reproducible series; the unit tests of the technicals
use tiny hand-made series (``bars``) so that every expected number can be calculated by hand.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from stock_forensics.models import Source
from stock_forensics.providers.prices import (
    PriceBar,
    PriceError,
    PriceProvider,
    PriceSeries,
    Split,
)

RETRIEVED = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def source(provider: str = "fakeprices") -> Source:
    return Source(provider=provider, endpoint="fake://prices", retrieved_at=RETRIEVED)


def bars(*closes: str | int | float, start: date = date(2024, 1, 1)) -> tuple[PriceBar, ...]:
    """Closes on consecutive calendar days from ``start`` (a series of exact decimals)."""
    return tuple(
        PriceBar(day=start + timedelta(days=i), close=Decimal(str(c))) for i, c in enumerate(closes)
    )


def series(
    ticker: str,
    prices: tuple[PriceBar, ...],
    *,
    split_adjusted: bool = True,
    splits: tuple[Split, ...] = (),
) -> PriceSeries:
    return PriceSeries(
        ticker=ticker,
        bars=prices,
        split_adjusted=split_adjusted,
        splits=splits,
        source=source(),
    )


def business_days(start: date, end: date) -> list[date]:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [d for d in days if d.weekday() < 5]


def synthetic(
    ticker: str,
    start: date,
    end: date,
    *,
    base: float = 100.0,
    drift: float = 0.0004,
    wave: float = 0.02,
    period: float = 23.0,
    split_adjusted: bool = True,
    splits: tuple[Split, ...] = (),
) -> PriceSeries:
    """A smooth upward series with a regular wave: reproducible, no randomness."""
    prices = tuple(
        PriceBar(
            day=day,
            close=Decimal(f"{base * (1 + drift) ** i * (1 + wave * math.sin(i / period)):.4f}"),
        )
        for i, day in enumerate(business_days(start, end))
    )
    return series(ticker, prices, split_adjusted=split_adjusted, splits=splits)


class FakePrices:
    """Implements ``PriceProvider`` with canned series per ticker; unknown tickers fail."""

    name = "fakeprices"
    benchmark = "^FAKE"

    def __init__(self, *, with_benchmark: bool = True, limit_days: int | None = None) -> None:
        self.with_benchmark = with_benchmark
        self.limit_days = limit_days  # like a free tier that only returns the latest N days
        self.calls: list[tuple[str, date, date]] = []

    def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
        self.calls.append((ticker, start, end))
        if ticker == self.benchmark:
            if not self.with_benchmark:
                raise PriceError("the index is not available")
            built = synthetic(ticker, start, end, base=4000, drift=0.0003, wave=0.01, period=31)
        elif ticker == "AAPL":
            built = synthetic(ticker, start, end, base=80, drift=0.0006, wave=0.03, period=19)
        else:
            raise PriceError(f"no prices for {ticker}")
        if self.limit_days is not None:
            built = built.model_copy(update={"bars": built.bars[-self.limit_days :]})
        return built


def as_provider(provider: FakePrices) -> PriceProvider:
    return provider
