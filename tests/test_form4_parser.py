"""The Form 4 XML parser, on real filings and on hand-made variants."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stock_forensics.insiders.form4 import (
    Form4ParseError,
    parse_form4,
    raw_xml_url,
)
from stock_forensics.providers.sec_models import Filing
from tests.insider_fixtures import FIXTURES, xml

# --- real filings -----------------------------------------------------------------------

APPLE_PLANNED_SALE = "0001140361-26-042149"  # Jennifer Newstead, sale under a 10b5-1 plan


def test_real_planned_sale_by_an_apple_officer() -> None:
    doc = parse_form4(xml("aapl", APPLE_PLANNED_SALE))
    assert doc.document_type == "4"
    assert doc.period_of_report == date(2026, 11, 3)
    assert (doc.issuer_cik, doc.issuer_name, doc.issuer_symbol) == (320193, "Apple Inc.", "AAPL")
    (owner,) = doc.owners
    assert owner.name == "Newstead Jennifer"
    assert owner.cik == 1780525
    assert (owner.is_officer, owner.is_director, owner.is_ten_percent_owner) == (True, False, False)
    assert owner.officer_title == "SVP, GC and Government Affairs"
    assert doc.plan_checkbox is True  # <aff10b5One>true</aff10b5One>

    (t,) = doc.transactions
    assert t.derivative is False
    assert t.security == "Common Stock"
    assert t.date == date(2026, 11, 3)
    assert t.code == "S"
    assert t.shares == Decimal("2399")
    assert t.price == Decimal("325.75")
    assert t.acquired_disposed == "D"
    assert t.shares_after == Decimal("29997")
    assert t.ownership == "D"
    assert t.mentions_plan  # the footnote names the Rule 10b5-1 trading plan
    assert "adopted by the reporting person on May 5, 2026" in t.footnotes[0]


def test_real_ceo_purchase_at_intel() -> None:
    doc = parse_form4(xml("intc", "0001127602-24-021794"))
    buys = [t for t in doc.transactions if t.code == "P"]
    assert [(t.shares, t.price) for t in buys] == [
        (Decimal("7500"), Decimal("20.3137")),
        (Decimal("5000"), Decimal("19.9186")),
    ]
    assert doc.owners[0].name == "GELSINGER PATRICK P"
    assert doc.owners[0].officer_title == "CEO"
    assert doc.plan_checkbox is None or doc.plan_checkbox is False  # before the check box era
    assert not any(t.mentions_plan for t in buys)


def test_real_joint_filing_of_a_ten_percent_owner() -> None:
    # Occidental / Berkshire: one filing, several reporting persons, indirect ownership
    joint = [
        doc
        for doc in (parse_form4(p.read_bytes()) for p in sorted((FIXTURES / "oxy").glob("*.xml")))
        if doc.issuer_cik == 797468 and len(doc.owners) > 1
    ]
    assert joint
    doc = joint[0]
    assert len(doc.owners) >= 2
    assert doc.owners[0].name == "BERKSHIRE HATHAWAY INC"
    assert doc.owners[0].is_ten_percent_owner
    assert any(t.ownership == "I" for t in doc.transactions)
    assert any(t.nature_of_ownership for t in doc.transactions)


def test_real_amendment_carries_the_date_of_the_original() -> None:
    doc = parse_form4(xml("msft_amendment", "0001062993-24-019893"))
    assert doc.document_type == "4/A"
    assert doc.date_of_original_submission == date(2024, 12, 2)
    assert doc.period_of_report == date(2024, 12, 2)
    (t,) = doc.transactions
    assert (t.code, t.shares, t.price) == ("F", Decimal("430.839"), Decimal("423.46"))


def test_real_filings_have_both_tables() -> None:
    kinds = set()
    for path in (FIXTURES / "aapl").glob("*.xml"):
        kinds |= {t.derivative for t in parse_form4(path.read_bytes()).transactions}
    assert kinds == {True, False}  # option exercises have a derivative leg


def test_every_recorded_filing_parses() -> None:
    paths = list(FIXTURES.glob("*/*.xml"))
    assert len(paths) >= 80
    for path in paths:
        doc = parse_form4(path.read_bytes())
        assert doc.owners, path.name
        assert doc.issuer_cik is not None, path.name


# --- hand-made variants -----------------------------------------------------------------


def document(body: str, extra: str = "") -> str:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2025-03-03</periodOfReport>
  <issuer><issuerCik>0000000123</issuerCik><issuerName>Example Corp</issuerName>
    <issuerTradingSymbol>EXM</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000000999</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>0</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship>
  </reportingOwner>
  {extra}
  {body}
</ownershipDocument>"""


def nd(code: str = "P", shares: str = "100", price: str | None = "10.50", foot: str = "") -> str:
    price_xml = (
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        if price is not None
        else f"<transactionPricePerShare>{foot}</transactionPricePerShare>"
    )
    return f"""<nonDerivativeTable><nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2025-03-03</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
        {price_xml}
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature></nonDerivativeTransaction></nonDerivativeTable>"""


def test_minimal_document() -> None:
    doc = parse_form4(document(nd()))
    (t,) = doc.transactions
    assert (t.code, t.shares, t.price, t.date) == (
        "P",
        Decimal(100),
        Decimal("10.50"),
        date(2025, 3, 3),
    )
    assert doc.owners[0].is_director
    assert doc.plan_checkbox is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("1", True), ("TRUE", True), ("false", False), ("0", False)],
)
def test_the_plan_check_box_accepts_every_boolean_spelling(text: str, expected: bool) -> None:
    doc = parse_form4(document(nd(), extra=f"<aff10b5One>{text}</aff10b5One>"))
    assert doc.plan_checkbox is expected


def test_a_missing_price_is_none_not_zero() -> None:
    (t,) = parse_form4(document(nd(price=None))).transactions
    assert t.price is None
    assert t.shares == Decimal(100)


def test_the_price_may_be_explained_only_in_a_footnote() -> None:
    body = nd(price=None, foot='<footnoteId id="F1"/>') + (
        '<footnotes><footnote id="F1">Weighted average price of purchases at prices '
        "ranging from $10.00 to $11.00.</footnote></footnotes>"
    )
    (t,) = parse_form4(document(body)).transactions
    assert t.price is None
    assert t.footnotes == (
        "Weighted average price of purchases at prices ranging from $10.00 to $11.00.",
    )


def test_footnotes_are_linked_to_their_transaction() -> None:
    linked = nd().replace(
        "<securityTitle><value>Common Stock</value></securityTitle>",
        '<securityTitle><value>Common Stock</value><footnoteId id="F2"/></securityTitle>',
    )
    body = linked + (
        '<footnotes><footnote id="F1">Not about this one under Rule 10b5-1.</footnote>'
        '<footnote id="F2">Bought in the open market.</footnote></footnotes>'
    )
    (t,) = parse_form4(document(body)).transactions
    assert t.footnotes == ("Bought in the open market.",)
    assert not t.mentions_plan  # the 10b5-1 text belongs to another footnote


@pytest.mark.parametrize(
    "text",
    ["Rule 10b5-1 trading plan", "pursuant to a 10b5-1(c) plan", "RULE 10B5-1", "10b51 plan"],
)
def test_plan_wording_is_recognised(text: str) -> None:
    body = (
        nd().replace(
            "<transactionDate>",
            '<transactionDate><footnoteId id="F1"/>',
        )
        + f'<footnotes><footnote id="F1">{text}</footnote></footnotes>'
    )
    (t,) = parse_form4(document(body)).transactions
    assert t.mentions_plan


def test_derivative_table_transactions_are_marked() -> None:
    derivative = """<derivativeTable><derivativeTransaction>
      <securityTitle><value>Stock Option</value></securityTitle>
      <transactionDate><value>2025-03-03</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts></derivativeTransaction></derivativeTable>"""
    doc = parse_form4(document(nd() + derivative))
    assert [(t.derivative, t.code) for t in doc.transactions] == [(False, "P"), (True, "M")]


def test_a_filing_with_only_holdings_has_no_transactions() -> None:
    holdings = (
        "<nonDerivativeTable><nonDerivativeHolding></nonDerivativeHolding></nonDerivativeTable>"
    )
    assert parse_form4(document(holdings)).transactions == ()


def test_several_reporting_owners() -> None:
    second = """<reportingOwner><reportingOwnerId><rptOwnerCik>0000000888</rptOwnerCik>
      <rptOwnerName>Big Holder LLC</rptOwnerName></reportingOwnerId>
      <reportingOwnerRelationship><isTenPercentOwner>true</isTenPercentOwner>
      </reportingOwnerRelationship></reportingOwner>"""
    doc = parse_form4(document(nd(), extra=second))
    assert [o.name for o in doc.owners] == ["Doe Jane", "Big Holder LLC"]
    assert doc.owners[1].is_ten_percent_owner


def test_unparseable_numbers_and_dates_become_none() -> None:
    body = nd(shares="n/a", price="tbd").replace("2025-03-03", "soon")
    (t,) = parse_form4(document(body)).transactions
    assert (t.shares, t.price, t.date) == (None, None, None)


@pytest.mark.parametrize(
    "bad",
    ["", "not xml at all", "<html><body>Rate limit</body></html>", "<ownershipDocument><unclosed>"],
)
def test_documents_that_are_not_form_4_raise(bad: str) -> None:
    with pytest.raises(Form4ParseError):
        parse_form4(bad)


# --- the raw XML address ----------------------------------------------------------------


def filing(primary: str | None, url: str | None) -> Filing:
    return Filing(
        accession_number="0001140361-26-042149",
        form="4",
        filing_date=date(2026, 11, 5),
        primary_document=primary,
        document_url=url,
    )


def test_raw_xml_url_strips_the_rendered_view_folder() -> None:
    f = filing(
        "xslF345X06/form4.xml",
        "https://www.sec.gov/Archives/edgar/data/320193/000114036126042149/xslF345X06/form4.xml",
    )
    assert raw_xml_url(f) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000114036126042149/form4.xml"
    )


def test_raw_xml_url_for_a_plain_document_and_missing_data() -> None:
    plain = filing(
        "form4.xml", "https://www.sec.gov/Archives/edgar/data/320193/000114036126042149/form4.xml"
    )
    assert raw_xml_url(plain) == plain.document_url
    assert raw_xml_url(filing(None, None)) is None
