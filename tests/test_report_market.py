"""Market data in the report: pipeline, scores, rendering, CLI, and the paths without prices."""

from __future__ import annotations

import functools
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stock_forensics import cli
from stock_forensics.market import analyze_market
from stock_forensics.providers.prices import PriceError, PriceSeries, PriceUnavailableError
from stock_forensics.report import (
    ForensicReport,
    analyze,
    render_html,
    render_json,
    render_markdown,
)
from stock_forensics.report.models import Light
from stock_forensics.report.pipeline import TOTAL_STEPS
from tests.price_fixtures import FakePrices
from tests.report_fixtures import AS_OF, FakeSec, make_client

NAMES = {
    "valuation": ["market_cap", "enterprise_value", "pe", "ev_ebitda", "fcf_yield"],
    "technicals": [
        "momentum_3m",
        "momentum_6m",
        "momentum_12m",
        "momentum_12_1",
        "volatility_1y",
        "max_drawdown_1y",
        "max_drawdown_3y",
        "beta_2y",
        "dist_52w_high",
        "dist_52w_low",
    ],
}


def test_market_data_is_in_the_report(aapl_report_market: ForensicReport) -> None:
    market = aapl_report_market.market
    assert market is not None and aapl_report_market.errors == {}
    assert (market.provider, market.ticker, market.benchmark) == ("fakeprices", "AAPL", "^FAKE")
    assert market.price_date == date(2026, 11, 12)
    assert market.price.value is not None
    for group, names in NAMES.items():
        assert [m.name for m in market.of_group(group)] == names
    for metric in market.metrics:
        assert metric.value.value is not None, metric.name  # the fake history covers everything
        assert metric.value.inputs and metric.formula and metric.label


def test_market_value_makes_manufacturers_use_the_original_altman_z(
    aapl_report: ForensicReport, aapl_report_market: ForensicReport
) -> None:
    assert aapl_report.scores is not None and aapl_report_market.scores is not None
    without = aapl_report.scores.latest("altman_z")
    with_prices = aapl_report_market.scores.latest("altman_z")
    assert without.variant == "Z'"
    assert with_prices.variant == "Z"
    assert with_prices.total.value is not None and with_prices.total.value != without.total.value
    # the market value is an input of the score, with the price behind it
    x4 = next(c for c in with_prices.components if c.name == "X4")
    providers = {v.source.provider for v in (x4.value, *x4.value.inputs)}
    assert "fakeprices" in {i.source.provider for item in x4.value.inputs for i in item.inputs} | (
        providers
    )


def test_without_a_provider_the_report_says_so(aapl_report: ForensicReport) -> None:
    assert aapl_report.market is None
    assert aapl_report.market_note == "no price provider configured"
    html = render_html(aapl_report)
    assert "Not available: no price provider configured." in html
    assert "no price provider configured" in render_markdown(aapl_report)
    assert aapl_report.scores is not None
    assert aapl_report.scores.latest("altman_z").variant == "Z'"  # unchanged without prices


def test_the_reason_for_no_provider_is_passed_on(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeSec()) as client:
        report = analyze(
            "AAPL",
            client=client,
            as_of=AS_OF,
            prices=None,
            prices_note="price data was switched off",
        )
    assert report.market_note == "price data was switched off"


class BrokenPrices(FakePrices):
    def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
        raise PriceError("HTTP 429 from the price service")


def test_a_failing_provider_only_removes_the_market_section(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeSec()) as client:
        report = analyze("AAPL", client=client, as_of=AS_OF, prices=BrokenPrices())
    assert report.market is None
    assert "HTTP 429" in report.errors["market"]
    assert report.scores is not None and report.scores.latest("altman_z").variant == "Z'"
    assert report.red_flags is not None and report.insiders is not None
    html = render_html(report)
    assert "Not available: PriceError: HTTP 429 from the price service." in html
    assert "Some parts of this report are missing" in html


def test_a_missing_index_only_removes_the_beta(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeSec()) as client:
        report = analyze(
            "AAPL", client=client, as_of=AS_OF, prices=FakePrices(with_benchmark=False)
        )
    assert report.market is not None and report.market.benchmark is None
    beta = report.market.get("beta_2y")
    assert beta.value is None and "could not be loaded" in (beta.note or "")
    assert any("index series" in n for n in report.market.notes)
    assert report.market.get("momentum_12m").value is not None


def test_a_free_tier_history_gives_gaps_with_reasons_not_wrong_numbers(tmp_path: Path) -> None:
    # Alpha Vantage free keys return 100 trading days: no year of history, no old fiscal years.
    with make_client(tmp_path, FakeSec()) as client:
        report = analyze("AAPL", client=client, as_of=AS_OF, prices=FakePrices(limit_days=100))
    market = report.market
    assert market is not None
    assert market.get("momentum_3m").value is not None
    for name in ("momentum_12m", "volatility_1y", "max_drawdown_3y", "beta_2y"):
        assert market.get(name).value is None, name
        assert market.get(name).note, name
    assert market.get("pe").value is not None  # the latest valuation only needs the latest price
    assert market.market_cap[2023].value is None
    assert "the price history starts" in (market.market_cap[2023].note or "")
    assert any("price history starts" in n for n in market.notes)
    # Apple's last fiscal year ended 2026-09-26, inside the last 100 trading days
    assert market.market_cap[2026].value is not None


def test_prices_are_requested_for_the_longest_needed_period(tmp_path: Path) -> None:
    fake = FakePrices()
    with make_client(tmp_path, FakeSec()) as client:
        report = analyze("AAPL", client=client, as_of=AS_OF, prices=fake)
    assert report.statements is not None
    (ticker, start, end), (bench, bench_start, _) = fake.calls
    assert (ticker, bench, end) == ("AAPL", "^FAKE", AS_OF)
    assert start == bench_start
    oldest = report.statements.annual("shares_outstanding", report.statements.fiscal_years[0])
    assert start < oldest.period.end  # far enough back for the oldest fiscal year end


def test_progress_includes_the_market_step(tmp_path: Path) -> None:
    with_prices: list[str] = []
    without: list[str] = []
    with make_client(tmp_path, FakeSec()) as client:
        analyze(
            "AAPL",
            client=client,
            as_of=AS_OF,
            prices=FakePrices(),
            progress=lambda n, t, m: with_prices.append(m),
        )
        analyze("AAPL", client=client, as_of=AS_OF, progress=lambda n, t, m: without.append(m))
    assert len(with_prices) == len(without) == TOTAL_STEPS
    assert "Loading market data" in with_prices and "Skipping market data" in without


def test_traffic_lights_ignore_valuation_and_technicals(
    aapl_report: ForensicReport, aapl_report_market: ForensicReport
) -> None:
    assert all(a.area != "market" for a in aapl_report_market.summary.areas)
    assert all(r.area != "market" for r in aapl_report_market.summary.reasons)
    # only the Altman variant changed the light, never a price level
    assert aapl_report.summary.overall is Light.YELLOW
    assert aapl_report_market.summary.overall is Light.GREEN


def test_html_shows_valuation_and_technicals(aapl_report_market: ForensicReport) -> None:
    html = render_html(aapl_report_market)
    assert '<h2 id="market">Market data</h2>' in html
    for label in ("Valuation", "Technicals", "Price / earnings", "EV / EBITDA", "S&amp;P 500"):
        assert label in html or label.replace("&amp;", "&") in html, label
    assert "none of these numbers has a traffic light" in html
    assert "None" not in html and "Decimal(" not in html
    # formatted for people: multiples with x, returns as percent, beta as a plain number
    market = aapl_report_market.market
    assert market is not None
    pe, momentum, beta = (market.get(n).value for n in ("pe", "momentum_12m", "beta_2y"))
    assert pe is not None and momentum is not None and beta is not None
    assert f"{float(pe):.2f}x" in html
    assert f"{float(momentum) * 100:.1f}%" in html
    assert f"{float(beta):.2f}</td>" in html


def test_markdown_and_json_carry_the_market_data(aapl_report_market: ForensicReport) -> None:
    md = render_markdown(aapl_report_market)
    assert "## Market data" in md and "| Price / earnings |" in md
    assert "| Volatility, 1 year (annualized) |" in md
    restored = ForensicReport.model_validate_json(render_json(aapl_report_market))
    assert restored == aapl_report_market


runner = CliRunner()


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("FORCE_COLOR", "TTY_COMPATIBLE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(
        cli, "build_client", lambda *, no_cache: make_client(tmp_path / "c", FakeSec())
    )
    monkeypatch.setattr(cli, "analyze", functools.partial(analyze, as_of=AS_OF))


def test_cli_uses_the_selected_provider(
    monkeypatch: pytest.MonkeyPatch, cli_env: None, tmp_path: Path
) -> None:
    chosen: list[str] = []

    def select(choice: str) -> tuple[FakePrices, None]:
        chosen.append(choice)
        return FakePrices(), None

    monkeypatch.setattr(cli, "select_provider", select)
    result = runner.invoke(cli.app, ["AAPL", "--prices", "yfinance"])
    assert result.exit_code == 0, result.stderr
    assert chosen == ["yfinance"]
    assert "Market data: fakeprices, latest close 2026-11-12" in result.stderr
    assert "Loading market data" in result.stderr
    assert "Market data" in (tmp_path / "AAPL-forensics.html").read_text(encoding="utf-8")


def test_cli_without_prices_says_why(monkeypatch: pytest.MonkeyPatch, cli_env: None) -> None:
    monkeypatch.setattr(
        cli, "select_provider", lambda choice: (None, "no price provider: install the extra")
    )
    result = runner.invoke(cli.app, ["AAPL"])
    assert result.exit_code == 0, result.stderr
    assert "Market data: not used (no price provider: install the extra)" in result.stderr
    assert "Skipping market data" in result.stderr


def test_cli_explicit_provider_that_is_not_usable_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, cli_env: None
) -> None:
    def select(choice: str) -> tuple[None, None]:
        raise PriceUnavailableError("yfinance is not installed")

    monkeypatch.setattr(cli, "select_provider", select)
    result = runner.invoke(cli.app, ["AAPL", "--prices", "yfinance"])
    assert result.exit_code == 2
    assert "yfinance is not installed" in result.stderr


def test_cli_rejects_an_unknown_price_choice(cli_env: None) -> None:
    result = runner.invoke(cli.app, ["AAPL", "--prices", "bloomberg"])
    assert result.exit_code == 2


def test_analyze_market_requires_prices_of_the_company(aapl_report: ForensicReport) -> None:
    class OnlyIndex(FakePrices):
        def daily_prices(self, ticker: str, start: date, end: date) -> PriceSeries:
            if ticker != self.benchmark:
                raise PriceError("unknown ticker")
            return super().daily_prices(ticker, start, end)

    with pytest.raises(PriceError, match="unknown ticker"):
        analyze_market(
            "AAPL",
            OnlyIndex(),
            statements=aapl_report.statements,
            metrics=aapl_report.metrics,
            as_of=AS_OF,
        )
