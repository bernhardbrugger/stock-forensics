import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from stock_forensics.providers._cache import DiskCache
from stock_forensics.providers._ratelimit import RateLimiter
from stock_forensics.providers.sec_edgar import (
    USER_AGENT_ENV,
    MissingUserAgentError,
    NotFoundError,
    RequestFailedError,
    SecEdgarClient,
    SecEdgarError,
    TickerNotFoundError,
)

UA = "stock-forensics-tests test@example.com"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}

SUBMISSIONS = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "tickers": ["AAPL"],
    "fiscalYearEnd": "0930",
    "unknownNewField": {"ignored": True},
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-23-000106",
                "0000320193-23-000077",
                "0000320193-22-000108",
                "0000320193-23-000110",
                "0000320193-21-000105",
                "0000320193-23-000064",
            ],
            "filingDate": [
                "2023-11-03",
                "2023-08-04",
                "2022-10-28",
                "2023-09-01",
                "2021-10-29",
                "2023-05-05",
            ],
            "reportDate": [
                "2023-09-30",
                "2023-07-01",
                "2022-09-24",
                "",
                "2021-09-25",
                "2023-04-01",
            ],
            "form": ["10-K", "10-Q", "10-K", "8-K", "10-K/A", "8-K"],
            "items": ["", "", "", "4.01,9.01", "", "2.02, 9.01"],
            "primaryDocument": [
                "aapl-20230930.htm",
                "aapl-20230701.htm",
                "aapl-20220924.htm",
                "a8-k.htm",
                "",
                "a8-k2.htm",
            ],
        },
        "files": [{"name": "CIK0000320193-submissions-001.json", "filingCount": 100}],
    },
}

FACTS_JSON = """{
  "cik": 320193,
  "entityName": "Apple Inc.",
  "facts": {
    "us-gaap": {
      "Revenues": {
        "label": "Revenues",
        "description": "Total revenue",
        "units": {
          "USD": [
            {"start": "2022-09-25", "end": "2023-09-30", "val": 383285000000,
             "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K",
             "filed": "2023-11-03", "frame": "CY2023", "extra": 1}
          ]
        }
      },
      "EarningsPerShareBasic": {
        "label": "EPS basic",
        "units": {
          "USD/shares": [
            {"start": "2022-09-25", "end": "2023-09-30", "val": 6.16,
             "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY", "form": "10-K",
             "filed": "2023-11-03"}
          ]
        }
      }
    },
    "dei": {}
  }
}"""


class Server:
    """A scripted fake SEC server that records every request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def json_response(data: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Env:
    def __init__(self, tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.sleeps: list[float] = []
        self.clock = Clock()
        self.tmp_path = tmp_path
        self.handler = handler

    def client(self, **overrides: object) -> SecEdgarClient:
        kwargs: dict[str, object] = {
            "user_agent": UA,
            "cache": DiskCache(self.tmp_path, clock=self.clock),
            "rate_limiter": RateLimiter(1_000_000),
            "transport": httpx.MockTransport(self.handler),
            "sleep": self.sleeps.append,
            "jitter": lambda: 0.0,
        }
        kwargs.update(overrides)
        return SecEdgarClient(**kwargs)  # type: ignore[arg-type]


def env_for(tmp_path: Path, server: Server) -> Env:
    return Env(tmp_path, server)


# --- user agent -------------------------------------------------------------------------


def test_missing_user_agent_gives_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(USER_AGENT_ENV, raising=False)
    with pytest.raises(MissingUserAgentError, match=USER_AGENT_ENV):
        SecEdgarClient(cache_dir=tmp_path)


@pytest.mark.parametrize("value", ["", "   ", "just-a-name"])
def test_user_agent_needs_a_contact_address(value: str, tmp_path: Path) -> None:
    with pytest.raises(MissingUserAgentError):
        SecEdgarClient(user_agent=value, cache_dir=tmp_path)


def test_user_agent_is_read_from_env_and_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(USER_AGENT_ENV, UA)
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    with env.client(user_agent=None) as client:
        client.ticker_to_cik("AAPL")
    assert server.requests[0].headers["User-Agent"] == UA
    assert server.requests[0].url.host == "www.sec.gov"


# --- ticker_to_cik ----------------------------------------------------------------------


@pytest.mark.parametrize("ticker", ["AAPL", "aapl", " Aapl "])
def test_ticker_to_cik_known_ticker(ticker: str, tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(TICKERS)))
    company = env.client().ticker_to_cik(ticker)
    assert company.ticker == "AAPL"
    assert company.cik == 320193
    assert company.cik_padded == "0000320193"
    assert company.name == "Apple Inc."


def test_ticker_with_dot_matches_sec_dash_spelling(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(TICKERS)))
    assert env.client().ticker_to_cik("BRK.B").cik == 1067983


def test_unknown_ticker_raises(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(TICKERS)))
    with pytest.raises(TickerNotFoundError, match="NOPE"):
        env.client().ticker_to_cik("NOPE")


# --- retry and errors -------------------------------------------------------------------


def test_429_is_retried_with_backoff(tmp_path: Path) -> None:
    server = Server(httpx.Response(429), httpx.Response(429), json_response(TICKERS))
    env = env_for(tmp_path, server)
    assert env.client().ticker_to_cik("AAPL").cik == 320193
    assert len(server.requests) == 3
    assert env.sleeps == [1.0, 2.0]


def test_retry_after_header_is_honoured_and_capped(tmp_path: Path) -> None:
    server = Server(
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(503, headers={"Retry-After": "9999"}),
        json_response(TICKERS),
    )
    env = env_for(tmp_path, server)
    env.client().ticker_to_cik("AAPL")
    assert env.sleeps == [3.0, 60.0]


def test_server_errors_give_up_after_max_attempts(tmp_path: Path) -> None:
    server = Server(httpx.Response(503))
    env = env_for(tmp_path, server)
    with pytest.raises(RequestFailedError, match="4 attempts"):
        env.client().ticker_to_cik("AAPL")
    assert len(server.requests) == 4
    assert env.sleeps == [1.0, 2.0, 4.0]  # no sleep after the last attempt


def test_network_errors_are_retried(tmp_path: Path) -> None:
    server = Server(httpx.ConnectError("boom"), json_response(TICKERS))
    env = env_for(tmp_path, server)
    assert env.client().ticker_to_cik("MSFT").cik == 789019
    assert len(server.requests) == 2


def test_404_is_not_retried(tmp_path: Path) -> None:
    server = Server(httpx.Response(404))
    env = env_for(tmp_path, server)
    with pytest.raises(NotFoundError):
        env.client().get_submissions(999999999)
    assert len(server.requests) == 1
    assert env.sleeps == []


def test_403_points_to_the_user_agent(tmp_path: Path) -> None:
    server = Server(httpx.Response(403))
    env = env_for(tmp_path, server)
    with pytest.raises(RequestFailedError, match="SEC_USER_AGENT"):
        env.client().ticker_to_cik("AAPL")
    assert len(server.requests) == 1


def test_invalid_json_from_server_raises(tmp_path: Path) -> None:
    server = Server(httpx.Response(200, content=b"<html>not json</html>"))
    env = env_for(tmp_path, server)
    with pytest.raises(SecEdgarError, match="valid JSON"):
        env.client().ticker_to_cik("AAPL")


def test_invalid_cik_is_rejected(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response({})))
    with pytest.raises(ValueError):
        env.client().get_submissions(0)


def test_rate_limiter_is_used_for_every_attempt(tmp_path: Path) -> None:
    class CountingLimiter(RateLimiter):
        calls = 0

        def acquire(self) -> None:
            type(self).calls += 1

    server = Server(httpx.Response(500), json_response(TICKERS))
    env = env_for(tmp_path, server)
    env.client(rate_limiter=CountingLimiter(10)).ticker_to_cik("AAPL")
    assert CountingLimiter.calls == 2


def test_clients_share_one_rate_limiter_by_default(tmp_path: Path) -> None:
    from stock_forensics.providers._ratelimit import shared_limiter

    env = env_for(tmp_path, Server(json_response(TICKERS)))
    a = env.client(rate_limiter=None)
    b = env.client(rate_limiter=None)
    assert a._limiter is b._limiter is shared_limiter()  # noqa: SLF001


# --- cache behaviour --------------------------------------------------------------------


def test_second_call_is_served_from_cache(tmp_path: Path) -> None:
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    client = env.client()
    client.ticker_to_cik("AAPL")
    client.ticker_to_cik("MSFT")
    assert len(server.requests) == 1


def test_cache_is_shared_between_client_instances(tmp_path: Path) -> None:
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    env.client().ticker_to_cik("AAPL")
    env.client().ticker_to_cik("AAPL")
    assert len(server.requests) == 1


def test_cache_expires_after_ttl(tmp_path: Path) -> None:
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    client = env.client(ttl=timedelta(hours=24))
    client.ticker_to_cik("AAPL")
    env.clock.now = T0 + timedelta(hours=23)
    client.ticker_to_cik("AAPL")
    assert len(server.requests) == 1
    env.clock.now = T0 + timedelta(hours=25)
    client.ticker_to_cik("AAPL")
    assert len(server.requests) == 2


def test_ttl_none_keeps_cache_forever(tmp_path: Path) -> None:
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    client = env.client(ttl=None)
    client.ticker_to_cik("AAPL")
    env.clock.now = T0 + timedelta(days=5000)
    client.ticker_to_cik("AAPL")
    assert len(server.requests) == 1


def test_use_cache_false_refetches_but_refreshes_the_cache(tmp_path: Path) -> None:
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    env.client(use_cache=False).ticker_to_cik("AAPL")
    env.client(use_cache=False).ticker_to_cik("AAPL")
    assert len(server.requests) == 2
    env.client().ticker_to_cik("AAPL")  # a normal client now finds the refreshed entry
    assert len(server.requests) == 2


def test_source_keeps_original_retrieval_time_on_cache_hit(tmp_path: Path) -> None:
    server = Server(json_response(SUBMISSIONS))
    env = env_for(tmp_path, server)
    client = env.client()
    first = client.get_submissions(320193)
    env.clock.now = T0 + timedelta(hours=1)
    second = client.get_submissions(320193)
    assert len(server.requests) == 1
    assert first.source.retrieved_at == T0
    assert second.source.retrieved_at == T0
    assert second.source.provider == "sec_edgar"
    assert second.source.endpoint == "https://data.sec.gov/submissions/CIK0000320193.json"


def test_damaged_cache_entry_is_refetched(tmp_path: Path) -> None:
    server = Server(json_response(TICKERS))
    env = env_for(tmp_path, server)
    env.client().ticker_to_cik("AAPL")
    (next(tmp_path.glob("*.cache"))).write_bytes(
        b'{"url": "https://www.sec.gov/files/company_tickers.json", '
        b'"fetched_at": "2026-11-11T12:00:00+00:00"}\n<<corrupt>>'
    )
    assert env.client().ticker_to_cik("AAPL").cik == 320193
    assert len(server.requests) == 2


# --- submissions ------------------------------------------------------------------------


def test_get_submissions_requests_padded_cik_url(tmp_path: Path) -> None:
    server = Server(json_response(SUBMISSIONS))
    env = env_for(tmp_path, server)
    env.client().get_submissions(320193)
    assert str(server.requests[0].url) == "https://data.sec.gov/submissions/CIK0000320193.json"


def test_submissions_company_info(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(SUBMISSIONS)))
    sub = env.client().get_submissions(320193)
    assert sub.cik == 320193
    assert sub.name == "Apple Inc."
    assert sub.sic_code == "3571"
    assert sub.industry == "Electronic Computers"
    assert sub.fiscal_year_end == "0930"
    assert sub.tickers == ("AAPL",)
    assert sub.older_files == ("CIK0000320193-submissions-001.json",)
    company = sub.to_company("aapl")
    assert (company.ticker, company.cik, company.sic_code) == ("AAPL", 320193, "3571")


def test_submissions_recent_10ks(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(SUBMISSIONS)))
    sub = env.client().get_submissions(320193)
    tenks = sub.filings_of_form("10-K")
    assert [f.accession_number for f in tenks] == ["0000320193-23-000106", "0000320193-22-000108"]
    assert tenks[0].filing_date == date(2023, 11, 3)
    assert tenks[0].report_date == date(2023, 9, 30)
    assert tenks[0].document_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
    )


def test_submissions_amendments_are_opt_in(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(SUBMISSIONS)))
    sub = env.client().get_submissions(320193)
    forms = [f.form for f in sub.filings_of_form("10-K", include_amendments=True)]
    assert forms == ["10-K", "10-K", "10-K/A"]


def test_submissions_8k_items_and_missing_fields(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response(SUBMISSIONS)))
    sub = env.client().get_submissions(320193)
    eight_ks = sub.filings_of_form("8-K")
    assert eight_ks[0].items == ("4.01", "9.01")
    assert eight_ks[0].report_date is None  # empty string in the SEC data
    assert eight_ks[1].items == ("2.02", "9.01")
    tenk_a = sub.filings_of_form("10-K/A")[0]
    assert tenk_a.primary_document is None
    assert tenk_a.document_url is None
    assert sub.filings_of_form("10-K")[0].items == ()


def test_submissions_without_filings_section(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(json_response({"cik": "0000000001", "name": "Tiny Co"})))
    sub = env.client().get_submissions(1)
    assert sub.filings == ()
    assert sub.sic_code is None
    assert sub.filings_of_form("10-K") == ()


# --- company facts ----------------------------------------------------------------------


def test_company_facts_parse_exact_decimals(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(httpx.Response(200, content=FACTS_JSON.encode())))
    facts = env.client().get_company_facts(320193)
    assert facts.cik == 320193
    assert facts.entity_name == "Apple Inc."

    revenue = facts.concept("us-gaap", "Revenues")
    assert revenue is not None
    fact = revenue.units["USD"][0]
    assert fact.val == Decimal("383285000000")
    assert fact.accn == "0000320193-23-000106"
    assert (fact.fy, fact.fp, fact.form) == (2023, "FY", "10-K")
    assert fact.start == date(2022, 9, 25)
    assert fact.end == date(2023, 9, 30)
    assert fact.filed == date(2023, 11, 3)
    assert fact.frame == "CY2023"

    eps = facts.concept("us-gaap", "EarningsPerShareBasic")
    assert eps is not None
    assert eps.units["USD/shares"][0].val == Decimal("6.16")  # exact, no float rounding
    assert eps.units["USD/shares"][0].frame is None


def test_company_facts_unknown_concept_is_none(tmp_path: Path) -> None:
    env = env_for(tmp_path, Server(httpx.Response(200, content=FACTS_JSON.encode())))
    facts = env.client().get_company_facts(320193)
    assert facts.concept("us-gaap", "DoesNotExist") is None
    assert facts.concept("ifrs-full", "Revenues") is None


def test_company_facts_source_points_at_the_endpoint(tmp_path: Path) -> None:
    server = Server(httpx.Response(200, content=FACTS_JSON.encode()))
    env = env_for(tmp_path, server)
    facts = env.client().get_company_facts(320193)
    assert facts.source.endpoint == (
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    )
    assert facts.source.retrieved_at == T0
    assert json.loads(facts.source.model_dump_json())["provider"] == "sec_edgar"
