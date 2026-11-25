"""Price data: a small provider interface and the yfinance implementation.

Prices are optional. The core check works from SEC filings alone; a price provider only adds
market value (needed for the original Altman Z), valuation metrics and technicals.

A provider returns a :class:`PriceSeries`: daily closes in ascending order, the splits it knows,
and whether the closes are adjusted for those splits. That flag matters: SEC share counts are
reported *as of the filing date*, so a price adjusted for a later split must be paired with a
share count adjusted the same way (see ``PriceSeries.split_factor_after``).
"""

from __future__ import annotations

import importlib
import os
from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Source

ALPHAVANTAGE_KEY_ENV = "ALPHAVANTAGE_API_KEY"
PRICE_CHOICES = ("auto", "yfinance", "alphavantage", "none")
# The package is not on PyPI, so the hint names the command that works in a checkout.
INSTALL_PRICES = "uv sync --extra prices"
YFINANCE_HINT = f"yfinance is not installed ({INSTALL_PRICES})"


class PriceError(Exception):
    """The prices could not be loaded."""


class PriceUnavailableError(PriceError):
    """The provider cannot be used at all (not installed, no API key)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PriceBar(_Frozen):
    day: date
    close: Decimal


class Split(_Frozen):
    day: date  # first trading day at the new share count
    ratio: Decimal  # 4 for a 4-for-1 split, 0.5 for a 1-for-2 reverse split


class PriceSeries(_Frozen):
    ticker: str
    bars: tuple[PriceBar, ...]  # ascending by day
    split_adjusted: bool  # True: earlier closes are divided by later splits (as Yahoo does)
    splits: tuple[Split, ...] = ()  # only known when the provider reports them
    source: Source

    @property
    def first_day(self) -> date | None:
        return self.bars[0].day if self.bars else None

    @property
    def last_bar(self) -> PriceBar | None:
        return self.bars[-1] if self.bars else None

    def on_or_before(self, day: date) -> PriceBar | None:
        """The last close on or before ``day``; ``None`` if the series starts later."""
        index = bisect_right([b.day for b in self.bars], day)
        return self.bars[index - 1] if index else None

    def window(self, start: date, end: date) -> tuple[PriceBar, ...]:
        return tuple(b for b in self.bars if start <= b.day <= end)

    def split_factor_after(self, day: date) -> Decimal:
        """Product of the split ratios after ``day``.

        Multiply a share count reported as of ``day`` by this to match a split-adjusted close.
        It is 1 for a series that is not split-adjusted.
        """
        if not self.split_adjusted:
            return Decimal(1)
        factor = Decimal(1)
        for split in self.splits:
            if split.day > day:
                factor *= split.ratio
        return factor


class PriceProvider(Protocol):
    name: str
    benchmark: str  # ticker of the S&P 500 proxy this provider can deliver

    def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
        """Daily closes from ``start`` to ``end`` (inclusive). Raises :class:`PriceError`."""
        ...


class YFinanceProvider:
    """Prices from Yahoo Finance through the optional ``yfinance`` package (no key needed).

    ``Close`` is used with ``auto_adjust=False``: adjusted for splits, not for dividends, so
    returns are price returns. yfinance is an unofficial client of Yahoo's website; its
    availability and terms are outside this project's control.
    """

    name = "yfinance"
    benchmark = "^GSPC"

    def __init__(self, module: Any | None = None) -> None:
        if module is None:
            try:
                module = importlib.import_module("yfinance")
            except ImportError:
                raise PriceUnavailableError(YFINANCE_HINT) from None
        self._yf = module

    def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
        symbol = ticker.upper().replace(".", "-")
        if ticker.startswith("^"):
            symbol = ticker
        try:
            frame = self._yf.Ticker(symbol).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),  # yfinance's end is exclusive
                interval="1d",
                auto_adjust=False,
                actions=True,
            )
        except Exception as error:  # noqa: BLE001 - yfinance raises many kinds of errors
            raise PriceError(f"yfinance could not load {symbol}: {error}") from None
        if frame is None or frame.empty:
            raise PriceError(f"yfinance returned no prices for {symbol}")
        bars = _bars(frame["Close"].items())
        if not bars:
            raise PriceError(f"yfinance returned no usable closes for {symbol}")
        splits: tuple[Split, ...] = ()
        if "Stock Splits" in frame.columns:
            splits = tuple(
                Split(day=day, ratio=ratio)
                for day, ratio in _numbers(frame["Stock Splits"].items())
                if ratio > 0
            )
        return PriceSeries(
            ticker=symbol,
            bars=tuple(bars),
            split_adjusted=True,
            splits=splits,
            source=Source(
                provider=self.name,
                endpoint=f"yfinance Ticker({symbol}).history(1d, {start} to {end})",
                retrieved_at=datetime.now(UTC),
            ),
        )


def _numbers(items: Any) -> list[tuple[date, Decimal]]:
    out: list[tuple[date, Decimal]] = []
    for stamp, raw in items:
        number = float(raw)
        if number != number or number in (float("inf"), float("-inf")):  # NaN or infinite
            continue
        # Yahoo delivers float32 noise (189.30000305...): four decimals are the real precision.
        out.append((stamp.date(), Decimal(f"{number:.4f}")))
    return out


def _bars(items: Any) -> list[PriceBar]:
    bars = [PriceBar(day=day, close=close) for day, close in _numbers(items) if close > 0]
    bars.sort(key=lambda b: b.day)
    unique: dict[date, PriceBar] = {b.day: b for b in bars}
    return list(unique.values())


def select_provider(choice: str = "auto") -> tuple[PriceProvider | None, str | None]:
    """The price provider for a choice, plus a note when there is none.

    ``auto`` takes yfinance if it is installed, else Alpha Vantage if a key is set, else no
    provider. An explicit choice that cannot be used raises :class:`PriceUnavailableError`.
    """
    # Imported here: alphavantage imports this module for the shared types.
    from stock_forensics.providers.alphavantage import AlphaVantageProvider

    if choice not in PRICE_CHOICES:
        raise ValueError(f"unknown price provider {choice!r}; choose from {PRICE_CHOICES}")
    if choice == "none":
        return None, "price data was switched off (--prices none)"
    if choice == "yfinance":
        return YFinanceProvider(), None
    if choice == "alphavantage":
        return AlphaVantageProvider(), None
    try:
        return YFinanceProvider(), None
    except PriceUnavailableError:
        pass
    if os.environ.get(ALPHAVANTAGE_KEY_ENV, "").strip():
        return AlphaVantageProvider(), None
    return None, (
        f"no price provider: install the extra ({INSTALL_PRICES}) or set {ALPHAVANTAGE_KEY_ENV}"
    )
