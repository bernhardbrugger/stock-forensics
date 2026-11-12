"""Insider activity of real companies, from recorded Form 4 filings.

Apple has planned sales, tax withholding, grants, gifts and option exercises but no purchase;
UnitedHealth has a cluster of five insiders buying within three days; Intel's CEO bought stock;
Occidental shows joint filings of a 10% owner (Berkshire Hathaway) and filings that the company
made about another issuer; Microsoft has an amendment (4/A).

The totals are recalculated by an independent routine that reads the raw XML with ElementTree and
knows nothing about the package, to catch mistakes in the parser or the aggregation.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from stock_forensics.insiders import InsiderActivity, get_insider_activity
from stock_forensics.insiders.aggregate import months_before
from stock_forensics.insiders.models import Category, Role
from stock_forensics.providers.sec_edgar import SecEdgarClient
from tests.insider_fixtures import FIXTURES, PERIODS, activity, submissions


def independent_totals(case: str) -> dict[str, Decimal]:
    """Sum open-market purchases and sales straight from the XML, one line per transaction.

    Only filings whose issuer is the company count; amendments are not in these cases.
    """
    as_of, months = PERIODS[case]
    cik = int(json.loads((FIXTURES / case / "submissions.json").read_text("utf-8"))["cik"])
    since = months_before(as_of, months)
    totals = {
        "P": Decimal(0),
        "S": Decimal(0),
        "P_n": Decimal(0),
        "S_n": Decimal(0),
        "P_sh": Decimal(0),
    }
    for path in sorted((FIXTURES / case).glob("*.xml")):
        root = ET.fromstring(path.read_bytes())
        issuer = root.findtext("issuer/issuerCik")
        if issuer is None or int(issuer) != cik:
            continue
        for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
            code = t.findtext("transactionCoding/transactionCode")
            day = date.fromisoformat(t.findtext("transactionDate/value") or "1900-01-01")
            if code not in ("P", "S") or not since <= day <= as_of:
                continue
            shares = Decimal(t.findtext("transactionAmounts/transactionShares/value") or "0")
            price = Decimal(t.findtext("transactionAmounts/transactionPricePerShare/value") or "0")
            totals[code] += shares * price
            totals[f"{code}_n"] += 1
            if code == "P":
                totals["P_sh"] += shares
    return totals


# --- the totals agree with an independent calculation -----------------------------------


@pytest.mark.parametrize("case", ["aapl", "unh", "intc", "oxy"])
def test_totals_match_an_independent_sum_of_the_raw_xml(case: str) -> None:
    mine = activity(case).totals
    other = independent_totals(case)
    assert mine.purchases.amount.value == other["P"]
    assert mine.sales.amount.value == other["S"]
    assert Decimal(mine.purchases.count) == other["P_n"]
    assert Decimal(mine.sales.count) == other["S_n"]
    assert mine.purchases.shares.value == other["P_sh"]
    assert mine.net_amount.value == other["P"] - other["S"]


# --- Intel: a CEO buys ------------------------------------------------------------------


def test_intel_ceo_purchase_by_hand() -> None:
    result = activity("intc")
    totals = result.totals
    # 7,500 x 20.3137 + 5,000 x 19.9186 = 152,352.75 + 99,593.00 = 251,945.75 USD
    assert totals.purchases.count == 2
    assert totals.purchases.shares.value == Decimal(12500)
    assert totals.purchases.amount.value == Decimal("251945.7500")
    assert (totals.buyers, totals.sellers) == (1, 0)
    (person,) = [p for p in result.people if p.purchases.count]
    assert person.name == "GELSINGER PATRICK P"
    assert person.role is Role.CEO
    # the same filings also hold option exercises and tax withholding: kept apart
    assert totals.not_counted[Category.EXERCISE] > 0
    assert totals.not_counted[Category.TAX_WITHHOLDING] > 0
    assert result.clusters == ()  # one buyer is not a cluster


# --- UnitedHealth: a cluster -----------------------------------------------------------


def test_unitedhealth_cluster_of_five_insiders() -> None:
    result = activity("unh")
    (cluster,) = result.clusters
    assert (cluster.start, cluster.end) == (date(2025, 5, 14), date(2025, 5, 16))
    assert set(cluster.insiders) == {
        "HEMSLEY STEPHEN J",
        "REX JOHN F",
        "FLYNN TIMOTHY PATRICK",
        "Gil Kristen",
        "Noseworthy John H",
    }
    assert cluster.shares.value == Decimal(109408)
    # Hemsley (CEO): 86,700 x 288.57 = 25,019,019.00
    ceo = next(p for p in result.people if p.name == "HEMSLEY STEPHEN J")
    assert ceo.role is Role.CEO
    assert ceo.purchases.amount.value == Decimal("25019019.00")
    cfo = next(p for p in result.people if p.name == "REX JOHN F")
    assert cfo.role is Role.CFO
    assert result.totals.buyers == 5
    assert cluster.amount.value == result.totals.purchases.amount.value  # all buys are in it
    # five distinct filings, each with a link
    links = {t.document_url for t in cluster.transactions}
    assert len(links) == 5
    assert all(link and "/Archives/edgar/data/731766/" in link for link in links)


# --- Apple: planned sales, no purchases -------------------------------------------------


def test_apple_has_sales_but_no_purchases() -> None:
    result = activity("aapl")
    t = result.totals
    assert t.purchases.count == 0
    assert t.buyers == 0
    assert t.sales.count == 47
    assert t.sellers == 7
    assert t.net_amount.value == -(t.sales.amount.value or 0)
    assert result.clusters == ()
    assert result.coverage.filings_listed == 54
    assert result.coverage.filings_read == 54
    assert result.coverage.unreadable == ()
    assert result.coverage.outside_period == 1  # a late-reported transaction before the window


def test_apple_planned_sales_are_separated() -> None:
    result = activity("aapl")
    t = result.totals
    assert t.planned_sales.count == 44  # sold under a Rule 10b5-1 plan
    assert t.planned_sales.amount.value is not None
    assert t.sales.amount.value is not None
    assert 0 < t.planned_sales.amount.value < t.sales.amount.value
    planned = [x for x in result.of_category(Category.SALE) if x.under_10b5_1]
    assert len(planned) == 44
    assert {x.plan_basis for x in planned} <= {"footnote", "form checkbox"}
    cook = next(p for p in result.people if p.name == "COOK TIMOTHY D")
    assert cook.role is Role.CEO
    assert (cook.sales.count, cook.planned_sales.count) == (10, 10)


def test_apple_grants_withholding_gifts_and_exercises_are_not_buying_or_selling() -> None:
    result = activity("aapl")
    not_counted = result.totals.not_counted
    assert not_counted[Category.TAX_WITHHOLDING] == 15
    assert not_counted[Category.GIFT] == 5
    assert not_counted[Category.GRANT] == 20
    assert not_counted[Category.EXERCISE] == 58  # 22 non-derivative and 36 derivative lines
    tax = result.of_category(Category.TAX_WITHHOLDING)
    assert all(x.code == "F" for x in tax)
    assert all(x.code_meaning.startswith("Payment of exercise price or tax") for x in tax)
    # a purchase or sale count never includes them
    assert result.totals.sales.count + result.totals.purchases.count == len(
        [x for x in result.transactions if x.category in (Category.SALE, Category.PURCHASE)]
    )


def test_every_apple_transaction_links_to_its_form_4() -> None:
    result = activity("aapl")
    accessions = {f.accession_number for f in submissions("aapl").filings}
    for x in result.transactions:
        assert x.accession_number in accessions
        assert x.document_url is not None
        assert re.search(rf"/{x.accession_number.replace('-', '')}/", x.document_url)
        assert x.source.accession_number == x.accession_number
        assert x.form == "4"


# --- Occidental: joint filings of a 10% owner ------------------------------------------


def test_berkshire_joint_filings_are_counted_once() -> None:
    result = activity("oxy")
    t = result.totals
    assert t.purchases.count == 14  # Berkshire Hathaway's purchases of Occidental shares
    assert t.buyers == 1
    berkshire = next(p for p in result.people if p.purchases.count)
    assert berkshire.name == "BERKSHIRE HATHAWAY INC"
    assert berkshire.role is Role.TEN_PERCENT_OWNER
    joint = [x for x in result.transactions if x.joint_owners]
    assert joint
    assert all(len(x.joint_owners) >= 1 for x in joint)
    # counting each reporting person would multiply the shares; the independent sum is the proof
    assert t.purchases.shares.value == independent_totals("oxy")["P_sh"]
    assert all(x.ownership == "indirect" for x in result.of_category(Category.PURCHASE))


def test_filings_made_about_another_issuer_are_left_out_and_counted() -> None:
    result = activity("oxy")
    # two of the seven filings were made by Occidental as an insider of Western Midstream
    assert result.coverage.filings_listed == 7
    assert result.coverage.filings_read == 5
    assert result.coverage.other_issuer_filings == 2
    assert not any(p.name.startswith("OCCIDENTAL") for p in result.people)
    assert result.totals.sales.count == 0  # Occidental's own sale of those units is not here


# --- Microsoft: an amendment ------------------------------------------------------------


def test_microsoft_amendment_replaces_the_original() -> None:
    result = activity("msft_amendment")
    assert result.coverage.filings_read == 3
    assert result.coverage.superseded_filings == 1
    forms = sorted((x.transaction_date.isoformat(), x.form, x.code) for x in result.transactions)
    assert forms == [("2024-12-02", "4/A", "F"), ("2024-12-16", "4", "F")]
    amended = next(x for x in result.transactions if x.form == "4/A")
    # 430.839 shares withheld at 423.46 USD
    assert amended.amount is not None
    assert amended.amount.value == Decimal("430.839") * Decimal("423.46")
    assert result.totals.not_counted == {Category.TAX_WITHHOLDING: 2}


# --- the whole thing through the client -------------------------------------------------


class FakeSec:
    def __init__(self, case: str) -> None:
        self.case = case
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        folder = FIXTURES / self.case
        if re.search(r"submissions/CIK\d{10}\.json$", url):
            return httpx.Response(200, content=(folder / "submissions.json").read_bytes())
        accession = re.search(r"/data/\d+/(\d{18})/", url)
        if accession:
            digits = accession.group(1)
            name = f"{digits[:10]}-{digits[10:12]}-{digits[12:]}.xml"
            path = folder / name
            if path.exists() and "/xsl" not in url:  # the raw XML, not the rendered view
                return httpx.Response(200, content=path.read_bytes())
        return httpx.Response(404)


def test_get_insider_activity_through_the_client(tmp_path: Path) -> None:
    fake = FakeSec("unh")
    client = SecEdgarClient(
        user_agent="stock-forensics-tests test@example.com",
        cache_dir=tmp_path,
        transport=httpx.MockTransport(fake),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    as_of, months = PERIODS["unh"]
    with client:
        result = get_insider_activity(client, 731766, as_of=as_of, months=months)
    assert isinstance(result, InsiderActivity)
    assert len(result.clusters) == 1
    assert result.totals.purchases.count == 5
    xml_requests = [u for u in fake.requests if u.endswith(".xml")]
    assert len(xml_requests) == 12  # one raw XML per Form 4
    assert not any("/xsl" in u for u in xml_requests)  # never the rendered view
    assert result.coverage.unreadable == ()


def test_a_missing_document_is_reported_not_skipped_silently(tmp_path: Path) -> None:
    class Gappy(FakeSec):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if "000073176625000145" in str(request.url) and str(request.url).endswith(".xml"):
                return httpx.Response(404)  # the CEO's purchase
            return super().__call__(request)

    client = SecEdgarClient(
        user_agent="stock-forensics-tests test@example.com",
        cache_dir=tmp_path,
        transport=httpx.MockTransport(Gappy("unh")),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    as_of, months = PERIODS["unh"]
    with client:
        result = get_insider_activity(client, 731766, as_of=as_of, months=months)
    assert result.coverage.unreadable == ("0000731766-25-000145",)
    assert result.coverage.filings_read == 11
    assert result.totals.purchases.count == 4  # the CEO's purchase is missing, and says so
    assert len(result.clusters) == 1  # four insiders still buy within three days


def test_the_activity_of_a_real_company_roundtrips_through_json() -> None:
    result = activity("unh")
    assert InsiderActivity.model_validate_json(result.model_dump_json()) == result
