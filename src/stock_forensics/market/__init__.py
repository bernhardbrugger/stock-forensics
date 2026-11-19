"""Market data: valuation metrics and technicals from a price provider (optional).

The core check works without any of this. With a price provider (``yfinance`` extra or an
Alpha Vantage key) the report gains the market value of equity per fiscal year (which lets
manufacturers use the original Altman Z), valuation metrics and technicals. See
``docs/market.md``.
"""

from __future__ import annotations

from datetime import date, timedelta

from stock_forensics.flags import years_before
from stock_forensics.market.common import price_value
from stock_forensics.market.indicators import build_chart, compute_trend
from stock_forensics.market.models import MarketData, MarketMetric
from stock_forensics.market.technicals import compute_technicals
from stock_forensics.market.valuation import compute_valuation, market_caps
from stock_forensics.metrics.engine import Metrics
from stock_forensics.normalize.statements import Statements
from stock_forensics.providers.prices import PriceError, PriceProvider, PriceSeries

HISTORY_YEARS = 3  # the longest technical window (3-year drawdown)
HISTORY_MARGIN_DAYS = 45


def history_start(statements: Statements | None, as_of: date) -> date:
    """How far back prices are needed: the oldest fiscal year end, and 3 years for technicals."""
    start = years_before(as_of, HISTORY_YEARS) - timedelta(days=HISTORY_MARGIN_DAYS)
    if statements is not None and statements.fiscal_years:
        oldest = statements.annual("shares_outstanding", statements.fiscal_years[0]).period.end
        start = min(start, oldest - timedelta(days=10))
    return start


def analyze_market(
    ticker: str,
    provider: PriceProvider,
    *,
    statements: Statements | None,
    metrics: Metrics | None,
    as_of: date,
) -> MarketData:
    """Load prices and compute market value, valuation and technicals.

    Raises :class:`~stock_forensics.providers.prices.PriceError` if the prices of the company
    itself cannot be loaded. A missing index series only turns the beta into a gap.
    """
    start = history_start(statements, as_of)
    series = provider.daily_prices(ticker, start, as_of)
    last = series.last_bar
    if last is None:
        raise PriceError(f"{provider.name} returned no prices for {ticker}")
    notes: list[str] = []
    benchmark: PriceSeries | None = None
    try:
        benchmark = provider.daily_prices(provider.benchmark, start, as_of)
    except PriceError as error:
        notes.append(f"index series {provider.benchmark} not loaded, beta is a gap: {error}")
    if series.first_day is not None and series.first_day > start + timedelta(days=7):
        notes.append(
            f"the price history starts {series.first_day}; periods before it are gaps "
            f"(the provider returned fewer days than the {start} start asked for)"
        )
    if not series.split_adjusted:
        notes.append(
            "closes are as traded (not adjusted for splits); metrics across a split-like jump "
            "are gaps"
        )
    caps = market_caps(statements, series) if statements is not None else {}
    age = (as_of - last.day).days
    metrics_out: tuple[MarketMetric, ...] = (
        *compute_valuation(statements, metrics, series, as_of_days_old=age),
        *compute_technicals(series, benchmark, as_of=as_of),
        *compute_trend(series, benchmark, as_of=as_of),
    )
    chart = build_chart(series)
    if chart is None:
        notes.append("no price chart: fewer than 26 weekly closes in the last 3 years")
    return MarketData(
        provider=provider.name,
        ticker=series.ticker,
        benchmark=benchmark.ticker if benchmark is not None else None,
        price_date=last.day,
        price=price_value(series, last),
        market_cap=caps,
        metrics=metrics_out,
        chart=chart,
        notes=tuple(notes),
    )


__all__ = ["MarketData", "MarketMetric", "analyze_market", "history_start"]
