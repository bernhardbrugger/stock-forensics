"""Market value and valuation metrics, checked against the recorded Apple statements.

With a flat price of 100 every expected number is simple: market value = 100 x shares, and the
multiples follow from the statement values that the result lists as its inputs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stock_forensics.market.valuation import compute_valuation, market_caps
from stock_forensics.metrics.engine import Metrics
from stock_forensics.normalize.statements import Statements
from stock_forensics.providers.prices import PriceSeries, Split
from stock_forensics.report import ForensicReport
from tests.price_fixtures import PriceBar, business_days, series

END = date(2026, 11, 12)


def flat(
    price: int = 100,
    start: date = date(2022, 1, 3),
    *,
    split_adjusted: bool = True,
    splits: tuple[Split, ...] = (),
) -> PriceSeries:
    prices = tuple(PriceBar(day=d, close=Decimal(price)) for d in business_days(start, END))
    return series("AAPL", prices, split_adjusted=split_adjusted, splits=splits)


@pytest.fixture(scope="module")
def statements(aapl_report: ForensicReport) -> Statements:
    assert aapl_report.statements is not None
    return aapl_report.statements


@pytest.fixture(scope="module")
def metrics(aapl_report: ForensicReport) -> Metrics:
    assert aapl_report.metrics is not None
    return aapl_report.metrics


def valuation(
    statements: Statements | None,
    metrics: Metrics | None,
    prices: PriceSeries,
    days_old: int = 0,
) -> dict[str, object]:
    return {
        m.name: m.value
        for m in compute_valuation(statements, metrics, prices, as_of_days_old=days_old)
    }


def test_market_value_per_fiscal_year_is_price_times_shares(statements: Statements) -> None:
    caps = market_caps(statements, flat())
    assert set(caps) == set(statements.fiscal_years)
    for fiscal_year, cap in caps.items():
        shares = statements.annual("shares_outstanding", fiscal_year)
        assert shares.value is not None
        assert cap.value == Decimal(100) * shares.value
        assert cap.period == shares.period
        assert [i.value for i in cap.inputs] == [Decimal(100), shares.value]
        assert cap.note and "close on" in cap.note and f"FY{fiscal_year}" in cap.note


def test_market_value_is_a_gap_before_the_price_history(statements: Statements) -> None:
    caps = market_caps(statements, flat(start=date(2025, 6, 2)))
    assert caps[2023].value is None
    assert "the price history starts 2025-06-02, after the fiscal year end" in (
        caps[2023].note or ""
    )
    assert caps[2026].value is not None  # the covered years still work


def test_market_value_is_a_gap_without_shares(statements: Statements) -> None:
    shares = statements.series["shares_outstanding"]
    gap = shares.annual[2025].model_copy(update={"value": None, "note": "concept not reported"})
    patched = statements.model_copy(
        update={
            "series": {
                **statements.series,
                "shares_outstanding": shares.model_copy(
                    update={"annual": {**shares.annual, 2025: gap}}
                ),
            }
        }
    )
    cap = market_caps(patched, flat())[2025]
    assert cap.value is None and "shares outstanding FY2025: concept not reported" in (
        cap.note or ""
    )


def with_filing_date(statements: Statements, fiscal_year: int, filed: date) -> Statements:
    """The statements with the filing date of one fiscal year's share count replaced."""
    shares = statements.series["shares_outstanding"]
    old = shares.annual[fiscal_year]
    moved = old.model_copy(update={"source": old.source.model_copy(update={"filed": filed})})
    patched = shares.model_copy(update={"annual": {**shares.annual, fiscal_year: moved}})
    return statements.model_copy(
        update={"series": {**statements.series, "shares_outstanding": patched}}
    )


def test_statement_values_know_their_filing_date(statements: Statements) -> None:
    shares = statements.annual("shares_outstanding", 2026)
    assert shares.source.filed == date(2026, 11, 3)  # the 10-K for fiscal 2026


def test_a_split_after_the_filing_is_applied_to_the_share_count(statements: Statements) -> None:
    # The 10-K was filed on 2026-11-03. A 4-for-1 split on 2026-12-15 came after it: the share
    # count is still the old one, but the close is adjusted, so the count is multiplied by 4.
    split = (Split(day=date(2026, 12, 15), ratio=Decimal(4)),)
    adjusted = market_caps(statements, flat(splits=split))
    plain = market_caps(statements, flat())
    assert adjusted[2026].value == plain[2026].value * 4  # type: ignore[operator]
    assert "x 4 (splits after 2026-11-03" in (adjusted[2026].note or "")
    # a series that is not split-adjusted needs no such factor
    as_traded = market_caps(statements, flat(split_adjusted=False, splits=split))
    assert as_traded[2026].value == plain[2026].value


def test_a_split_before_the_filing_is_already_in_the_share_count(statements: Statements) -> None:
    # A split on 2026-10-15 is after the balance sheet (2026-09-26) but before the 10-K was filed
    # (2026-11-03): the company reports the count after the split, so no factor.
    split = (Split(day=date(2026, 10, 15), ratio=Decimal(4)),)
    caps = market_caps(statements, flat(splits=split))
    assert caps[2026].value == market_caps(statements, flat())[2026].value


def test_a_restated_share_count_is_not_multiplied_twice(statements: Statements) -> None:
    # NVIDIA: the balance sheet of January 2024 (2.47 billion shares) was restated to 24.7 billion
    # in the next year's 10-K, filed 2025-02, after the 10-for-1 split of 2024-06-10. The latest
    # filing wins in the statements, so the count is already split-adjusted: factor 1.
    split = (Split(day=date(2024, 6, 10), ratio=Decimal(10)),)
    restated = with_filing_date(statements, 2024, date(2025, 2, 26))
    original = with_filing_date(statements, 2024, date(2024, 2, 21))
    cap_restated = market_caps(restated, flat(splits=split))[2024]
    cap_original = market_caps(original, flat(splits=split))[2024]
    assert cap_original.value == cap_restated.value * 10  # type: ignore[operator]
    assert "x 10 (splits after 2024-02-21" in (cap_original.note or "")
    assert "splits after" not in (cap_restated.note or "")


def test_without_a_filing_date_the_period_end_is_the_fallback(statements: Statements) -> None:
    shares = statements.series["shares_outstanding"]
    old = shares.annual[2026]
    undated = old.model_copy(update={"source": old.source.model_copy(update={"filed": None})})
    patched = statements.model_copy(
        update={
            "series": {
                **statements.series,
                "shares_outstanding": shares.model_copy(
                    update={"annual": {**shares.annual, 2026: undated}}
                ),
            }
        }
    )
    split = (Split(day=date(2026, 10, 15), ratio=Decimal(4)),)  # after the balance sheet date
    cap = market_caps(patched, flat(splits=split))[2026]
    assert cap.value == market_caps(patched, flat())[2026].value * 4  # type: ignore[operator]


def test_split_before_the_balance_sheet_date_is_not_applied(statements: Statements) -> None:
    split = (Split(day=date(2022, 6, 1), ratio=Decimal(2)),)  # before every fiscal year end shown
    caps = market_caps(statements, flat(splits=split))
    assert caps[2026].value == market_caps(statements, flat())[2026].value


def test_valuation_numbers_follow_from_their_inputs(
    statements: Statements, metrics: Metrics
) -> None:
    result = valuation(statements, metrics, flat())
    cap = result["market_cap"]
    assert cap.value is not None  # type: ignore[attr-defined]
    shares = cap.inputs[1]  # type: ignore[attr-defined]
    assert cap.value == Decimal(100) * shares.value  # type: ignore[attr-defined]
    assert shares.period.end >= date(2026, 9, 26)  # the newest reported share count

    net_cash = metrics.latest("net_cash")
    assert net_cash.value is not None
    ev = result["enterprise_value"]
    assert ev.value == cap.value - net_cash.value  # type: ignore[attr-defined, operator]

    pe = result["pe"]
    income = pe.inputs[1]  # type: ignore[attr-defined]
    assert income.value is not None and income.value > 0
    assert pe.value == cap.value / income.value  # type: ignore[attr-defined, operator]
    assert "trailing twelve months" in (pe.note or "")  # type: ignore[attr-defined]

    ev_ebitda = result["ev_ebitda"]
    operating_income, depreciation = ev_ebitda.inputs[1], ev_ebitda.inputs[2]  # type: ignore[attr-defined]
    assert ev_ebitda.value == ev.value / (operating_income.value + depreciation.value)  # type: ignore[attr-defined, operator]

    fcf = result["fcf_yield"]
    ocf, capex = fcf.inputs[1], fcf.inputs[2]  # type: ignore[attr-defined]
    assert fcf.value == (ocf.value - capex.value) / cap.value  # type: ignore[attr-defined, operator]


def test_loss_making_company_has_no_pe(statements: Statements, metrics: Metrics) -> None:
    income = statements.series["net_income"]
    assert income.ttm is not None
    patched = statements.model_copy(
        update={
            "series": {
                **statements.series,
                "net_income": income.model_copy(
                    update={"ttm": income.ttm.model_copy(update={"value": Decimal(-5)})}
                ),
            }
        }
    )
    pe = valuation(patched, metrics, flat())["pe"]
    assert pe.value is None  # type: ignore[attr-defined]
    assert "net income is not positive" in (pe.note or "")  # type: ignore[attr-defined]


def test_negative_ebitda_has_no_multiple(statements: Statements, metrics: Metrics) -> None:
    income = statements.series["operating_income"]
    assert income.ttm is not None
    patched = statements.model_copy(
        update={
            "series": {
                **statements.series,
                "operating_income": income.model_copy(
                    update={"ttm": income.ttm.model_copy(update={"value": Decimal(-(10**12))})}
                ),
            }
        }
    )
    result = valuation(patched, metrics, flat())["ev_ebitda"]
    assert result.value is None and "EBITDA is not positive" in (result.note or "")  # type: ignore[attr-defined]


def test_falls_back_to_the_fiscal_year_without_a_ttm(
    statements: Statements, metrics: Metrics
) -> None:
    income = statements.series["net_income"]
    patched = statements.model_copy(
        update={
            "series": {**statements.series, "net_income": income.model_copy(update={"ttm": None})}
        }
    )
    pe = valuation(patched, metrics, flat())["pe"]
    assert pe.value is not None  # type: ignore[attr-defined]
    assert "fiscal year 2026" in (pe.note or "")  # type: ignore[attr-defined]


def test_without_metrics_there_is_no_enterprise_value(statements: Statements) -> None:
    result = valuation(statements, None, flat())
    assert result["market_cap"].value is not None  # type: ignore[attr-defined]
    for name in ("enterprise_value", "ev_ebitda"):
        assert result[name].value is None  # type: ignore[attr-defined]
    assert "net cash is not available" in (result["enterprise_value"].note or "")  # type: ignore[attr-defined]
    assert result["pe"].value is not None  # type: ignore[attr-defined]


def test_without_statements_everything_is_a_gap() -> None:
    result = valuation(None, None, flat())
    assert len(result) == 5
    for value in result.values():
        assert value.value is None  # type: ignore[attr-defined]
        assert "needs the financial statements" in (value.note or "")  # type: ignore[attr-defined]


def test_a_stale_price_makes_everything_a_gap(statements: Statements, metrics: Metrics) -> None:
    result = valuation(statements, metrics, flat(), days_old=30)
    for value in result.values():
        assert value.value is None  # type: ignore[attr-defined]
        assert "30 days before the analysis date" in (value.note or "")  # type: ignore[attr-defined]


def test_every_number_names_its_inputs(statements: Statements, metrics: Metrics) -> None:
    for name, value in valuation(statements, metrics, flat()).items():
        assert value.value is not None, name  # type: ignore[attr-defined]
        assert value.inputs and value.note, name  # type: ignore[attr-defined]
        assert value.source.provider == "stock_forensics", name  # type: ignore[attr-defined]
