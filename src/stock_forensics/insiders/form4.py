"""Parser for the XML of SEC Form 4 (statement of changes in beneficial ownership).

The XML (``ownershipDocument``) lists the issuer, one or more reporting owners with their
relationship to the company, the transactions of a non-derivative table (shares) and a
derivative table (options and similar), footnotes, and since April 2023 a check box for
transactions made under a Rule 10b5-1 trading plan (``aff10b5One``).

Parsing is lenient about missing optional parts (a missing price becomes ``None``) and strict
about the document: XML that is not an ownership document raises :class:`Form4ParseError`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from stock_forensics.providers.sec_models import Filing

PLAN = re.compile(r"10b5-?1", re.I)


class Form4ParseError(ValueError):
    """The document is not a readable Form 4 ownership document."""


@dataclass(frozen=True)
class Owner:
    cik: int | None
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    is_other: bool
    officer_title: str | None


@dataclass(frozen=True)
class RawTransaction:
    derivative: bool  # from the derivative table (options, warrants, ...)
    security: str | None
    date: date | None
    code: str | None
    shares: Decimal | None
    price: Decimal | None
    acquired_disposed: str | None  # "A" or "D"
    shares_after: Decimal | None
    ownership: str | None  # "D" direct or "I" indirect
    nature_of_ownership: str | None
    footnotes: tuple[str, ...]  # the texts of the footnotes this transaction refers to

    @property
    def mentions_plan(self) -> bool:
        return any(PLAN.search(text) for text in self.footnotes)


@dataclass(frozen=True)
class Form4Document:
    document_type: str  # "4" or "4/A"
    period_of_report: date | None
    date_of_original_submission: date | None  # only in amendments
    issuer_cik: int | None
    issuer_name: str | None
    issuer_symbol: str | None
    owners: tuple[Owner, ...]
    plan_checkbox: bool | None  # aff10b5One: None if the filing has no such field
    transactions: tuple[RawTransaction, ...]


def _text(element: ET.Element | None, path: str) -> str | None:
    if element is None:
        return None
    found = element.find(path)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def _flag(element: ET.Element | None, path: str) -> bool | None:
    text = _text(element, path)
    if text is None:
        return None
    return text.lower() in {"1", "true"}


def _decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _date(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _owner(element: ET.Element) -> Owner:
    relation = element.find("reportingOwnerRelationship")
    return Owner(
        cik=_int(_text(element, "reportingOwnerId/rptOwnerCik")),
        name=_text(element, "reportingOwnerId/rptOwnerName") or "unknown",
        is_director=bool(_flag(relation, "isDirector")),
        is_officer=bool(_flag(relation, "isOfficer")),
        is_ten_percent_owner=bool(_flag(relation, "isTenPercentOwner")),
        is_other=bool(_flag(relation, "isOther")),
        officer_title=_text(relation, "officerTitle"),
    )


def _transaction(
    element: ET.Element, *, derivative: bool, footnotes: dict[str, str]
) -> RawTransaction:
    ids = [e.get("id") for e in element.iter("footnoteId")]
    texts = tuple(footnotes[i] for i in dict.fromkeys(ids) if i is not None and i in footnotes)
    return RawTransaction(
        derivative=derivative,
        security=_text(element, "securityTitle/value"),
        date=_date(_text(element, "transactionDate/value")),
        code=_text(element, "transactionCoding/transactionCode"),
        shares=_decimal(_text(element, "transactionAmounts/transactionShares/value")),
        price=_decimal(_text(element, "transactionAmounts/transactionPricePerShare/value")),
        acquired_disposed=_text(
            element, "transactionAmounts/transactionAcquiredDisposedCode/value"
        ),
        shares_after=_decimal(
            _text(element, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        ),
        ownership=_text(element, "ownershipNature/directOrIndirectOwnership/value"),
        nature_of_ownership=_text(element, "ownershipNature/natureOfOwnership/value"),
        footnotes=texts,
    )


def parse_form4(xml: bytes | str) -> Form4Document:
    """Parse the raw XML of a Form 4 (or 4/A)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise Form4ParseError(f"not valid XML: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise Form4ParseError(f"not an ownership document (root element {root.tag!r})")

    footnotes = {
        f.get("id", ""): " ".join("".join(f.itertext()).split())
        for f in root.findall("footnotes/footnote")
    }
    transactions = [
        _transaction(t, derivative=False, footnotes=footnotes)
        for t in root.findall("nonDerivativeTable/nonDerivativeTransaction")
    ] + [
        _transaction(t, derivative=True, footnotes=footnotes)
        for t in root.findall("derivativeTable/derivativeTransaction")
    ]
    issuer = root.find("issuer")
    return Form4Document(
        document_type=_text(root, "documentType") or "4",
        period_of_report=_date(_text(root, "periodOfReport")),
        date_of_original_submission=_date(_text(root, "dateOfOriginalSubmission")),
        issuer_cik=_int(_text(issuer, "issuerCik")),
        issuer_name=_text(issuer, "issuerName"),
        issuer_symbol=_text(issuer, "issuerTradingSymbol"),
        owners=tuple(_owner(o) for o in root.findall("reportingOwner")),
        plan_checkbox=_flag(root, "aff10b5One"),
        transactions=tuple(transactions),
    )


def raw_xml_url(filing: Filing) -> str | None:
    """The URL of the raw XML of a Form 4 filing.

    The filing index points at the rendered HTML (``.../xslF345X06/form4.xml``); the raw XML has
    the same name in the folder of the filing itself.
    """
    if filing.document_url is None or filing.primary_document is None:
        return None
    folder = filing.document_url.rsplit("/", 1)[0]
    if "/" in filing.primary_document:  # strip the "xslF345X06" folder of the rendered view
        folder = folder.rsplit("/", 1)[0]
    return f"{folder}/{filing.primary_document.rsplit('/', 1)[-1]}"
