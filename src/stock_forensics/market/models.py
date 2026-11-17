"""Result models for market data: valuation metrics and technicals.

Every number is a :class:`~stock_forensics.models.Value` with its inputs (the prices and
statement values it was computed from) and a gap carries its reason, exactly as in the metrics
and scores. There is no traffic light on these numbers: whether a price is high or low is not a
forensic finding, and the report must not read like a buy or sell call.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Source, Value


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketMetric(_Frozen):
    name: str
    label: str
    group: Literal["valuation", "technicals", "trend"]
    formula: str
    value: Value
    reading: str | None = None  # the number in plain words, built by fixed rules


class ChartPoint(_Frozen):
    day: date
    close: Decimal
    sma_50: Decimal | None = None
    sma_200: Decimal | None = None


class PriceChart(_Frozen):
    """Weekly points for the price chart: the data behind the picture, with its source."""

    ticker: str
    source: Source
    note: str
    points: tuple[ChartPoint, ...]


class MarketData(_Frozen):
    provider: str
    ticker: str
    benchmark: str | None  # ticker of the index series used for beta, if it could be loaded
    price_date: date  # date of the latest close
    price: Value  # latest close
    market_cap: dict[int, Value]  # fiscal year -> market value of equity (feeds Altman Z)
    metrics: tuple[MarketMetric, ...]
    chart: PriceChart | None = None
    notes: tuple[str, ...] = ()

    @property
    def metrics_by_name(self) -> dict[str, MarketMetric]:
        return {m.name: m for m in self.metrics}

    def get(self, name: str) -> Value:
        for metric in self.metrics:
            if metric.name == name:
                return metric.value
        raise KeyError(name)

    def of_group(self, group: str) -> tuple[MarketMetric, ...]:
        return tuple(m for m in self.metrics if m.group == group)
