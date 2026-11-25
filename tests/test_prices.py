"""Price providers: the series model, yfinance (stubbed, no network), Alpha Vantage (mock HTTP)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from stock_forensics.providers import prices as prices_module
from stock_forensics.providers.alphavantage import AlphaVantageProvider
from stock_forensics.providers.prices import (
    ALPHAVANTAGE_KEY_ENV,
    PriceError,
    PriceUnavailableError,
    Split,
    YFinanceProvider,
    select_provider,
)
from tests.price_fixtures import bars, series

SECRET = "TESTKEY-do-not-leak"


# --- the series model ----------------------------------------------------------------------


def test_on_or_before_and_window() -> None:
    s = series("X", bars(10, 11, 12, 13, start=date(2024, 3, 1)))  # Mar 1 to Mar 4
    assert s.on_or_before(date(2024, 2, 29)) is None
    found = s.on_or_before(date(2024, 3, 3))
    assert found is not None and found.close == 12
    last = s.on_or_before(date(2030, 1, 1))
    assert last is not None and last.close == 13
    assert [b.close for b in s.window(date(2024, 3, 2), date(2024, 3, 3))] == [11, 12]
    assert (s.first_day, s.last_bar and s.last_bar.day) == (date(2024, 3, 1), date(2024, 3, 4))


def test_split_factor_multiplies_later_splits_only() -> None:
    splits = (
        Split(day=date(2020, 8, 31), ratio=Decimal(4)),
        Split(day=date(2024, 6, 10), ratio=Decimal(10)),
    )
    adjusted = series("X", bars(1), splits=splits)
    assert adjusted.split_factor_after(date(2019, 12, 31)) == 40
    assert (
        adjusted.split_factor_after(date(2020, 8, 31)) == 10
    )  # the split day itself is not "after"
    assert adjusted.split_factor_after(date(2024, 6, 10)) == 1
    raw = series("X", bars(1), split_adjusted=False, splits=splits)
    assert raw.split_factor_after(date(2019, 12, 31)) == 1


# --- yfinance (stub module, real pandas) ---------------------------------------------------


class FakeTicker:
    def __init__(self, frame: Any, calls: list[dict[str, Any]], error: Exception | None) -> None:
        self._frame, self._calls, self._error = frame, calls, error

    def history(self, **kwargs: Any) -> Any:
        self._calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._frame


def fake_yfinance(frame: Any, error: Exception | None = None) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    symbols: list[str] = []

    def ticker(symbol: str) -> FakeTicker:
        symbols.append(symbol)
        return FakeTicker(frame, calls, error)

    module = SimpleNamespace(Ticker=ticker, symbols=symbols)
    return module, calls


def frame() -> Any:
    pd = pytest.importorskip("pandas")
    index = pd.DatetimeIndex(
        ["2024-06-07", "2024-06-10", "2024-06-11", "2024-06-12"], tz="America/New_York"
    )
    return pd.DataFrame(
        {
            "Close": [1200.30000305175781, float("nan"), 121.5, 123.0],
            "Stock Splits": [0.0, 10.0, 0.0, 0.0],
        },
        index=index,
    )


def test_yfinance_parses_closes_and_splits() -> None:
    module, calls = fake_yfinance(frame())
    result = YFinanceProvider(module).daily_prices("aapl", date(2024, 6, 7), date(2024, 6, 12))
    assert [(b.day, b.close) for b in result.bars] == [
        (date(2024, 6, 7), Decimal("1200.3000")),  # float32 noise removed
        (date(2024, 6, 11), Decimal("121.5000")),  # the NaN day is skipped
        (date(2024, 6, 12), Decimal("123.0000")),
    ]
    assert result.split_adjusted is True
    assert result.splits == (Split(day=date(2024, 6, 10), ratio=Decimal("10.0000")),)
    assert result.source.provider == "yfinance" and result.ticker == "AAPL"
    assert result.source.retrieved_at.tzinfo is not None
    # the call: unadjusted close, end is exclusive in yfinance so one day is added
    assert calls == [
        {
            "start": "2024-06-07",
            "end": "2024-06-13",
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
        }
    ]


def test_yfinance_symbols() -> None:
    module, _ = fake_yfinance(frame())
    provider = YFinanceProvider(module)
    provider.daily_prices("brk.b", date(2024, 6, 7), date(2024, 6, 12))
    provider.daily_prices("^GSPC", date(2024, 6, 7), date(2024, 6, 12))
    assert module.symbols == ["BRK-B", "^GSPC"]


def test_yfinance_without_a_splits_column() -> None:
    f = frame().drop(columns=["Stock Splits"])
    module, _ = fake_yfinance(f)
    result = YFinanceProvider(module).daily_prices("X", date(2024, 6, 7), date(2024, 6, 12))
    assert result.splits == ()


@pytest.mark.parametrize("failure", ["raises", "empty", "all nan"])
def test_yfinance_failures_are_price_errors(failure: str) -> None:
    pd = pytest.importorskip("pandas")
    if failure == "raises":
        module, _ = fake_yfinance(None, RuntimeError("HTTP 429"))
    elif failure == "empty":
        module, _ = fake_yfinance(pd.DataFrame({"Close": []}))
    else:
        f = pd.DataFrame(
            {"Close": [float("nan")]}, index=pd.DatetimeIndex(["2024-06-07"], tz="UTC")
        )
        module, _ = fake_yfinance(f)
    with pytest.raises(PriceError):
        YFinanceProvider(module).daily_prices("X", date(2024, 6, 7), date(2024, 6, 12))


def test_yfinance_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_module(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(prices_module, "importlib", SimpleNamespace(import_module=no_module))
    with pytest.raises(PriceUnavailableError, match=r"uv sync --extra prices"):
        YFinanceProvider()


# --- Alpha Vantage -------------------------------------------------------------------------


def av_body(days: dict[str, str]) -> dict[str, Any]:
    return {
        "Meta Data": {"2. Symbol": "AAPL"},
        "Time Series (Daily)": {
            day: {"1. open": close, "4. close": close, "5. volume": "1000"}
            for day, close in days.items()
        },
    }


DAYS = {
    "2026-11-12": "190.2500",
    "2026-11-11": "189.0000",
    "2026-11-10": "188.5000",
    "2026-10-01": "170.0000",
}


class AvServer:
    def __init__(self, body: Any = None, status: int = 200) -> None:
        self.body = av_body(DAYS) if body is None else body
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


def av(server: AvServer, tmp_path: Path, **kwargs: Any) -> AlphaVantageProvider:
    kwargs.setdefault("sleep", lambda _: None)  # tests never wait for the 1 request per second
    return AlphaVantageProvider(
        SECRET, transport=httpx.MockTransport(server), cache_dir=tmp_path, **kwargs
    )


def test_alpha_vantage_parses_and_filters_by_date(tmp_path: Path) -> None:
    server = AvServer()
    result = av(server, tmp_path).daily_prices("AAPL", date(2026, 11, 1), date(2026, 11, 12))
    assert [(b.day.isoformat(), str(b.close)) for b in result.bars] == [
        ("2026-11-10", "188.5000"),
        ("2026-11-11", "189.0000"),
        ("2026-11-12", "190.2500"),
    ]  # ascending, and 2026-10-01 is outside the requested period
    assert result.split_adjusted is False and result.splits == ()
    assert result.source.provider == "alphavantage"
    params = dict(server.requests[0].url.params)
    assert params["function"] == "TIME_SERIES_DAILY"
    assert params["symbol"] == "AAPL" and params["outputsize"] == "full"  # auto tries full first
    assert params["apikey"] == SECRET


class Plan:
    """Answers "full" with the whole history, or with a refusal like a limited key gets."""

    def __init__(self, *, full_allowed: bool) -> None:
        self.full_allowed = full_allowed
        self.sizes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        size = request.url.params["outputsize"]
        self.sizes.append(size)
        if size == "full" and not self.full_allowed:
            return httpx.Response(200, json={"Information": "full output is a premium feature"})
        return httpx.Response(200, json=av_body(DAYS))


def test_alpha_vantage_auto_uses_the_full_history_when_the_key_allows_it(tmp_path: Path) -> None:
    plan = Plan(full_allowed=True)
    provider = AlphaVantageProvider(
        SECRET, transport=httpx.MockTransport(plan), cache_dir=tmp_path, sleep=lambda _: None
    )
    result = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert plan.sizes == ["full"]
    assert "outputsize=full" in (result.source.endpoint or "")


def test_alpha_vantage_falls_back_to_compact_and_remembers_it(tmp_path: Path) -> None:
    plan = Plan(full_allowed=False)
    provider = AlphaVantageProvider(
        SECRET, transport=httpx.MockTransport(plan), cache_dir=tmp_path, sleep=lambda _: None
    )
    first = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert plan.sizes == ["full", "compact"]
    assert "outputsize=compact" in (first.source.endpoint or "")
    provider.daily_prices("SPY", date(2026, 10, 1), date(2026, 11, 12))
    assert plan.sizes == ["full", "compact", "compact"]  # no second try of full for this key


class Scripted:
    """Answers each request with the next canned JSON body and records the sizes asked for."""

    def __init__(self, *bodies: Any) -> None:
        self.bodies = list(bodies)
        self.sizes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sizes.append(request.url.params["outputsize"])
        return httpx.Response(200, json=self.bodies.pop(0))


RATE = {"Information": "Please consider spreading out your free API requests more sparingly"}


def test_a_rate_limit_waits_and_retries_instead_of_downgrading(tmp_path: Path) -> None:
    script = Scripted(RATE, av_body(DAYS), av_body(DAYS))
    pauses: list[float] = []
    provider = AlphaVantageProvider(
        SECRET,
        transport=httpx.MockTransport(script),
        cache_dir=tmp_path,
        clock=lambda: 0.0,
        sleep=pauses.append,
        min_interval=1.1,
    )
    result = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert script.sizes == ["full", "full"]  # the same size again, not "compact"
    assert 2.2 in pauses  # waited twice the minimum interval before the retry
    assert "outputsize=full" in (result.source.endpoint or "")
    provider.daily_prices("SPY", date(2026, 10, 1), date(2026, 11, 12))
    assert script.sizes == ["full", "full", "full"]  # full is still tried for the next symbol


def test_a_rate_limit_that_stays_is_reported_not_hidden(tmp_path: Path) -> None:
    script = Scripted(RATE, RATE, av_body(DAYS))
    provider = AlphaVantageProvider(
        SECRET,
        transport=httpx.MockTransport(script),
        cache_dir=tmp_path,
        sleep=lambda _: None,
    )
    # two rate-limit answers for "full", then "compact" works for this call only
    result = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert script.sizes == ["full", "full", "compact"]
    assert "outputsize=compact" in (result.source.endpoint or "")
    assert provider._full_refused is False  # a transient problem is not a verdict on the key


def test_an_unknown_message_falls_back_once_without_giving_up_on_full(tmp_path: Path) -> None:
    script = Scripted({"Information": "Something new happened"}, av_body(DAYS), av_body(DAYS))
    provider = AlphaVantageProvider(
        SECRET,
        transport=httpx.MockTransport(script),
        cache_dir=tmp_path,
        sleep=lambda _: None,
    )
    provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert script.sizes == ["full", "compact"]
    provider.daily_prices("SPY", date(2026, 10, 1), date(2026, 11, 12))
    assert script.sizes == ["full", "compact", "full"]


def test_alpha_vantage_fixed_outputsize_never_falls_back(tmp_path: Path) -> None:
    plan = Plan(full_allowed=False)
    provider = AlphaVantageProvider(
        SECRET,
        outputsize="full",
        transport=httpx.MockTransport(plan),
        cache_dir=tmp_path,
        sleep=lambda _: None,
    )
    with pytest.raises(PriceError, match="premium"):
        provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert plan.sizes == ["full"]
    compact = AlphaVantageProvider(
        SECRET,
        outputsize="compact",
        transport=httpx.MockTransport(plan),
        cache_dir=tmp_path / "c",
        sleep=lambda _: None,
    )
    compact.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert plan.sizes == ["full", "compact"]


def test_alpha_vantage_outputsize_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALPHAVANTAGE_OUTPUTSIZE", "compact")
    plan = Plan(full_allowed=True)
    provider = AlphaVantageProvider(
        SECRET, transport=httpx.MockTransport(plan), cache_dir=tmp_path, sleep=lambda _: None
    )
    provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert plan.sizes == ["compact"]
    with pytest.raises(ValueError, match="outputsize must be one of"):
        AlphaVantageProvider(SECRET, outputsize="everything")


def test_alpha_vantage_keeps_a_pause_between_requests(tmp_path: Path) -> None:
    # The service allows one request per second: the second network request has to wait.
    now = [100.0]
    pauses: list[float] = []

    def sleep(seconds: float) -> None:
        pauses.append(seconds)
        now[0] += seconds

    provider = AlphaVantageProvider(
        SECRET,
        transport=httpx.MockTransport(AvServer()),
        cache_dir=tmp_path,
        clock=lambda: now[0],
        sleep=sleep,
        min_interval=1.1,
    )
    provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert pauses == []  # the first request goes out at once
    now[0] += 0.3  # 0.3 s later the next one is wanted
    provider.daily_prices("SPY", date(2026, 10, 1), date(2026, 11, 12))
    assert len(pauses) == 1 and abs(pauses[0] - 0.8) < 1e-9
    provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))  # cached: no request
    assert len(pauses) == 1


def test_alpha_vantage_key_never_reaches_cache_or_provenance(tmp_path: Path) -> None:
    result = av(AvServer(), tmp_path).daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert SECRET not in (result.source.endpoint or "")
    assert SECRET not in result.model_dump_json()
    for path in tmp_path.iterdir():
        assert SECRET.encode() not in path.read_bytes()


def test_alpha_vantage_uses_the_cache_to_save_the_daily_quota(tmp_path: Path) -> None:
    server = AvServer()
    provider = av(server, tmp_path)
    first = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    second = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert len(server.requests) == 1
    assert first.source.retrieved_at == second.source.retrieved_at
    fresh = av(server, tmp_path, use_cache=False)
    fresh.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"Error Message": "Invalid API call."}, "Invalid API call."),
        ({"Information": "rate limit is 25 requests per day"}, "rate limit is 25"),
        ({"Note": "Thank you for using Alpha Vantage"}, "Thank you"),
        ({"something": "else"}, "unexpected response"),
    ],
)
def test_alpha_vantage_error_responses_are_not_cached(
    tmp_path: Path, body: dict[str, str], message: str
) -> None:
    server = AvServer(body)
    provider = av(server, tmp_path)
    with pytest.raises(PriceError, match=message):
        provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert list(tmp_path.iterdir()) == []  # an error must not be served again for 12 hours


def test_alpha_vantage_http_errors_do_not_leak_the_key(tmp_path: Path) -> None:
    with pytest.raises(PriceError) as excinfo:
        av(AvServer(status=500), tmp_path).daily_prices(
            "AAPL", date(2026, 10, 1), date(2026, 11, 12)
        )
    assert SECRET not in str(excinfo.value)
    assert "HTTPStatusError" in str(excinfo.value)


def test_alpha_vantage_skips_broken_rows_and_reports_an_empty_period(tmp_path: Path) -> None:
    body = av_body({"2026-11-12": "190.0", "not-a-date": "1", "2026-11-11": "oops"})
    provider = av(AvServer(body), tmp_path)
    result = provider.daily_prices("AAPL", date(2026, 10, 1), date(2026, 11, 12))
    assert [b.day for b in result.bars] == [date(2026, 11, 12)]
    with pytest.raises(PriceError, match="no prices"):
        provider.daily_prices("AAPL", date(2020, 1, 1), date(2020, 2, 1))


def test_alpha_vantage_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALPHAVANTAGE_KEY_ENV, raising=False)
    with pytest.raises(PriceUnavailableError, match=ALPHAVANTAGE_KEY_ENV):
        AlphaVantageProvider()
    monkeypatch.setenv(ALPHAVANTAGE_KEY_ENV, "  ")
    with pytest.raises(PriceUnavailableError):
        AlphaVantageProvider()


# --- choosing a provider -------------------------------------------------------------------


def yfinance_present(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    def import_module(name: str) -> Any:
        if present and name == "yfinance":
            return SimpleNamespace()
        raise ImportError(name)

    monkeypatch.setattr(prices_module, "importlib", SimpleNamespace(import_module=import_module))


def test_select_none_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, note = select_provider("none")
    assert provider is None and note is not None and "switched off" in note
    with pytest.raises(ValueError, match="unknown price provider"):
        select_provider("bloomberg")


def test_auto_prefers_yfinance_then_alpha_vantage_then_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ALPHAVANTAGE_KEY_ENV, raising=False)
    yfinance_present(monkeypatch, True)
    monkeypatch.setenv(ALPHAVANTAGE_KEY_ENV, SECRET)
    provider, note = select_provider("auto")
    assert isinstance(provider, YFinanceProvider) and note is None

    yfinance_present(monkeypatch, False)
    provider, note = select_provider("auto")
    assert isinstance(provider, AlphaVantageProvider) and note is None

    monkeypatch.delenv(ALPHAVANTAGE_KEY_ENV)
    provider, note = select_provider("auto")
    assert provider is None
    assert note is not None and "uv sync --extra prices" in note and ALPHAVANTAGE_KEY_ENV in note


def test_an_explicit_choice_that_cannot_be_used_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yfinance_present(monkeypatch, False)
    monkeypatch.delenv(ALPHAVANTAGE_KEY_ENV, raising=False)
    with pytest.raises(PriceUnavailableError):
        select_provider("yfinance")
    with pytest.raises(PriceUnavailableError):
        select_provider("alphavantage")


def test_json_helper_is_valid() -> None:
    # guards the fixture itself: the Alpha Vantage test bodies must be real JSON
    assert json.loads(json.dumps(av_body(DAYS)))["Meta Data"]
