from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from stock_forensics.providers.ixbrl import inline_xbrl_values
from stock_forensics.providers.sec_edgar import NotFoundError
from tests.test_sec_edgar import T0, Env, Server

DOC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"

HTML = """
<html><body>
<table>
<tr><td>Total net sales</td>
  <td><ix:nonFraction unitRef="usd" name="us-gaap:Revenues" scale="6" decimals="-6"
      contextRef="c1" format="ixt:num-dot-decimal">383,285</ix:nonFraction></td></tr>
<tr><td>Total assets</td>
  <td><ix:nonFraction name="us-gaap:Assets" scale="6" contextRef="c2">352,583</ix:nonFraction></td>
</tr>
<tr><td>Other loss</td>
  <td>(<ix:nonFraction name="us-gaap:OtherNonoperatingIncomeExpense" scale="6" sign="-"
      contextRef="c1">565</ix:nonFraction>)</td></tr>
<tr><td>EPS</td>
  <td><ix:nonFraction name="us-gaap:EarningsPerShareBasic" scale="0"
      contextRef="c1"><span>6.16</span></ix:nonFraction></td></tr>
<tr><td>Dash</td>
  <td><ix:nonFraction name="us-gaap:Goodwill" contextRef="c1">&#8212;</ix:nonFraction></td></tr>
<tr><td>again</td>
  <td><ix:nonFraction name="us-gaap:Assets" scale="6" contextRef="c3">352,755</ix:nonFraction></td>
</tr>
</table></body></html>
"""


def test_inline_xbrl_values_apply_scale_sign_and_nested_markup() -> None:
    values = inline_xbrl_values(HTML)
    assert values["us-gaap:Revenues"] == {Decimal(383_285_000_000)}
    assert values["us-gaap:Assets"] == {Decimal(352_583_000_000), Decimal(352_755_000_000)}
    assert values["us-gaap:OtherNonoperatingIncomeExpense"] == {Decimal(-565_000_000)}
    assert values["us-gaap:EarningsPerShareBasic"] == {Decimal("6.16")}


def test_inline_xbrl_ignores_dashes_and_plain_html() -> None:
    assert "us-gaap:Goodwill" not in inline_xbrl_values(HTML)
    assert inline_xbrl_values("<html><body>no tags, 383,285</body></html>") == {}


def test_a_wrong_number_is_not_found_in_the_document() -> None:
    values = inline_xbrl_values(HTML)
    assert Decimal(383_286_000_000) not in values["us-gaap:Revenues"]


# --- SecEdgarClient.get_document --------------------------------------------------------


def test_get_document_returns_bytes_and_caches_forever(tmp_path: Path) -> None:
    server = Server(httpx.Response(200, content=HTML.encode()))
    env = Env(tmp_path, server)
    client = env.client(ttl=timedelta(hours=1))
    assert client.get_document(DOC_URL) == HTML.encode()
    env.clock.now = T0 + timedelta(days=3650)  # far beyond the normal TTL
    assert client.get_document(DOC_URL) == HTML.encode()
    assert len(server.requests) == 1


def test_get_document_only_accepts_sec_archive_urls(tmp_path: Path) -> None:
    server = Server(httpx.Response(200, content=b"x"))
    env = Env(tmp_path, server)
    with pytest.raises(ValueError, match="Not an SEC filing document URL"):
        env.client().get_document("https://example.com/evil.htm")
    assert server.requests == []


def test_get_document_404(tmp_path: Path) -> None:
    env = Env(tmp_path, Server(httpx.Response(404)))
    with pytest.raises(NotFoundError):
        env.client().get_document(DOC_URL)
