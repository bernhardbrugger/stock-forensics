"""Result models for insider activity (SEC Form 4).

Every transaction keeps its filing (accession number, link to the Form 4) and its numbers as
``Value`` objects with a source. Only open-market purchases and sales (transaction codes P and
S in the non-derivative table) feed the totals; grants, option exercises, tax withholding and
gifts are listed but kept apart, because they say little about what insiders think of the stock.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from stock_forensics.models import Source, Value


class Category(StrEnum):
    PURCHASE = "purchase"  # code P
    SALE = "sale"  # code S
    GRANT = "grant"  # code A
    EXERCISE = "exercise"  # codes M, X, C, O
    TAX_WITHHOLDING = "tax_withholding"  # code F
    GIFT = "gift"  # code G
    OTHER = "other"  # everything else


class Role(StrEnum):
    CEO = "ceo"
    CFO = "cfo"
    OFFICER = "officer"
    DIRECTOR = "director"
    TEN_PERCENT_OWNER = "ten_percent_owner"
    OTHER = "other"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InsiderTransaction(_Frozen):
    owner_cik: int | None
    owner_name: str
    role: Role
    officer_title: str | None = None
    joint_owners: tuple[str, ...] = ()  # other reporting persons of the same filing
    security: str | None = None
    derivative: bool = False
    transaction_date: date
    code: str  # transaction code of the Form 4, "?" if missing
    code_meaning: str
    category: Category
    acquired_disposed: str | None = None  # "A" or "D"
    shares: Value | None = None
    price: Value | None = None
    amount: Value | None = None  # shares x price in USD; a gap if no price is reported
    shares_after: Decimal | None = None
    ownership: str | None = None  # "direct" or "indirect"
    nature_of_ownership: str | None = None
    under_10b5_1: bool = False
    plan_basis: str | None = None  # "footnote" or "form checkbox"
    footnotes: tuple[str, ...] = ()
    accession_number: str
    form: str
    filing_date: date
    document_url: str | None = None  # link to the Form 4 as published
    source: Source


class Tally(_Frozen):
    """Count, shares and USD amount of a group of transactions."""

    count: int
    shares: Value
    amount: Value


class PersonSummary(_Frozen):
    owner_cik: int | None
    name: str
    role: Role
    officer_title: str | None = None
    purchases: Tally
    sales: Tally
    planned_sales: Tally  # the part of the sales made under a Rule 10b5-1 plan
    net_amount: Value  # purchases minus sales in USD
    grants: int = 0
    option_exercises: int = 0
    tax_withholdings: int = 0
    gifts: int = 0
    other: int = 0


class ClusterBuy(_Frozen):
    """Several insiders buying within a short period."""

    start: date
    end: date
    insiders: tuple[str, ...]
    shares: Value
    amount: Value
    transactions: tuple[InsiderTransaction, ...]


class Totals(_Frozen):
    purchases: Tally
    sales: Tally
    planned_sales: Tally
    net_amount: Value  # purchases minus sales in USD
    buyers: int  # distinct insiders with an open-market purchase
    sellers: int
    not_counted: dict[Category, int]  # transactions kept out of the totals, by category


class Coverage(_Frozen):
    filings_listed: int  # Form 4 and 4/A filings in the period
    filings_read: int  # Form 4 filings about this company that were parsed
    other_issuer_filings: int = 0  # listed in the index, but the issuer is another company
    unreadable: tuple[str, ...] = ()  # accession numbers that could not be read or parsed
    superseded_filings: int = 0  # replaced by an amendment (4/A)
    duplicate_transactions: int = 0  # the same transaction reported in two filings
    outside_period: int = 0  # transactions before the start of the period
    notes: tuple[str, ...] = ()


class InsiderActivity(_Frozen):
    cik: int
    company_name: str
    ticker: str | None = None
    as_of: date
    since: date
    transactions: tuple[InsiderTransaction, ...]  # newest first
    people: tuple[PersonSummary, ...]  # by size of the open-market amount
    totals: Totals
    clusters: tuple[ClusterBuy, ...] = ()
    coverage: Coverage
    source: Source

    def of_category(self, category: Category) -> tuple[InsiderTransaction, ...]:
        return tuple(t for t in self.transactions if t.category is category)
