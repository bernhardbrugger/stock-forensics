"""Small helpers shared by the valuation and technicals modules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from stock_forensics.models import Period, Source, Unit, Value, missing
from stock_forensics.providers.prices import PriceBar, PriceSeries

PROVIDER = "stock_forensics"


def period(end: date, start: date | None = None) -> Period:
    return Period(fiscal_year=end.year, start=start, end=end)


def computed_source(series: PriceSeries) -> Source:
    # The timestamp is that of the price data, so the result stays deterministic.
    return Source(provider=PROVIDER, retrieved_at=series.source.retrieved_at)


def price_value(series: PriceSeries, bar: PriceBar, note: str | None = None) -> Value:
    adjusted = " (split-adjusted)" if series.split_adjusted else " (as traded)"
    return Value(
        value=bar.close,
        unit=Unit.USD_PER_SHARE,
        period=period(bar.day),
        source=series.source,
        note=note or f"close on {bar.day}{adjusted}",
    )


def number(
    series: PriceSeries,
    amount: Decimal,
    unit: Unit,
    end: date,
    note: str,
    inputs: tuple[Value, ...],
    start: date | None = None,
) -> Value:
    return Value(
        value=amount,
        unit=unit,
        period=period(end, start),
        source=computed_source(series),
        note=note,
        inputs=inputs,
    )


def gap(
    series: PriceSeries,
    reason: str,
    unit: Unit,
    end: date,
    inputs: tuple[Value, ...] = (),
    start: date | None = None,
) -> Value:
    return missing(
        reason,
        unit=unit,
        period=period(end, start),
        source=computed_source(series),
        inputs=inputs,
    )
