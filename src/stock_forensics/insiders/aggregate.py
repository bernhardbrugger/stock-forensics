"""From parsed Form 4 documents to insider transactions, totals, people and cluster buys.

Rules (see ``docs/insiders.md``):

* Only open-market purchases (code P) and sales (code S) of the non-derivative table feed the
  totals. Grants (A), option exercises (M, X, C, O), tax withholding (F), gifts (G) and the rest
  are listed and counted, but never mixed into "buying" or "selling".
* A filing with several reporting persons (a joint filing) reports each transaction once. It is
  counted once and attributed to the first reporting person.
* A 4/A replaces the earlier filings of the same reporting person for the same period of report.
  The same transaction reported in two filings is counted once.
* A sale counts as made under a Rule 10b5-1 plan if one of its footnotes says so, or else if the
  form's plan check box is set. "No indication" is not the same as "discretionary".
"""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from stock_forensics.insiders.form4 import Form4Document, Owner, RawTransaction
from stock_forensics.insiders.models import (
    Category,
    ClusterBuy,
    Coverage,
    InsiderActivity,
    InsiderTransaction,
    PersonSummary,
    Role,
    Tally,
    Totals,
)
from stock_forensics.models import Period, Source, Unit, Value, missing
from stock_forensics.providers.sec_models import Filing

CODE_MEANINGS = {
    "P": "Purchase (open market or private)",
    "S": "Sale (open market or private)",
    "A": "Grant, award or other acquisition from the issuer",
    "D": "Disposition to the issuer",
    "F": "Payment of exercise price or tax liability by withholding securities",
    "M": "Exercise or conversion of a derivative security (Rule 16b-3)",
    "X": "Exercise of an in-the-money or at-the-money derivative security",
    "C": "Conversion of a derivative security",
    "O": "Exercise of an out-of-the-money derivative security",
    "G": "Bona fide gift",
    "I": "Discretionary transaction",
    "J": "Other acquisition or disposition",
    "K": "Equity swap or similar hedging transaction",
    "L": "Small acquisition (Rule 16a-6)",
    "U": "Disposition pursuant to a tender offer",
    "V": "Transaction voluntarily reported earlier than required",
    "W": "Acquisition or disposition by will or the laws of descent",
    "Z": "Deposit into or withdrawal from a voting trust",
    "E": "Expiration of a short derivative position",
    "H": "Expiration or cancellation of a long derivative position",
}
_CATEGORIES = {
    "P": Category.PURCHASE,
    "S": Category.SALE,
    "A": Category.GRANT,
    "M": Category.EXERCISE,
    "X": Category.EXERCISE,
    "C": Category.EXERCISE,
    "O": Category.EXERCISE,
    "F": Category.TAX_WITHHOLDING,
    "G": Category.GIFT,
}
_ROLE_ORDER = [
    Role.CEO,
    Role.CFO,
    Role.OFFICER,
    Role.DIRECTOR,
    Role.TEN_PERCENT_OWNER,
    Role.OTHER,
]
_CEO = re.compile(r"\bceo\b|chief executive|principal executive", re.I)
_CFO = re.compile(r"\bcfo\b|chief financial|principal financial", re.I)

DEFAULT_CLUSTER_INSIDERS = 3
DEFAULT_CLUSTER_DAYS = 30


def category_of(code: str | None) -> Category:
    return _CATEGORIES.get(code or "", Category.OTHER)


def role_of(owner: Owner) -> Role:
    title = owner.officer_title or ""
    if owner.is_officer or title:
        if _CEO.search(title):
            return Role.CEO
        if _CFO.search(title):
            return Role.CFO
        return Role.OFFICER
    if owner.is_director:
        return Role.DIRECTOR
    if owner.is_ten_percent_owner:
        return Role.TEN_PERCENT_OWNER
    return Role.OTHER


def months_before(day: date, months: int) -> date:
    """The same day ``months`` months earlier (clamped to the end of a shorter month)."""
    year, month_index = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _owner_key(cik: int | None, name: str) -> str:
    return f"cik:{cik}" if cik is not None else f"name:{name}"


# --- one filing to transactions ------------------------------------------------------------


def _point_period(day: date) -> Period:
    return Period(fiscal_year=day.year, start=None, end=day)


def _value(
    number: Decimal,
    unit: Unit,
    day: date,
    source: Source,
    note: str | None = None,
    inputs: tuple[Value, ...] = (),
) -> Value:
    return Value(
        value=number, unit=unit, period=_point_period(day), source=source, note=note, inputs=inputs
    )


def build_transactions(
    document: Form4Document, filing: Filing, source: Source
) -> tuple[list[InsiderTransaction], int]:
    """The transactions of one Form 4, and how many had no transaction date (and are skipped)."""
    primary = document.owners[0] if document.owners else None
    owner_name = primary.name if primary else "unknown"
    owner_cik = primary.cik if primary else None
    role = role_of(primary) if primary else Role.OTHER
    title = primary.officer_title if primary else None
    joint = tuple(o.name for o in document.owners[1:])

    built: list[InsiderTransaction] = []
    skipped = 0
    for raw in document.transactions:
        if raw.date is None:
            skipped += 1
            continue
        built.append(
            _transaction(raw, document, filing, source, owner_name, owner_cik, role, title, joint)
        )
    return built, skipped


def _transaction(
    raw: RawTransaction,
    document: Form4Document,
    filing: Filing,
    source: Source,
    owner_name: str,
    owner_cik: int | None,
    role: Role,
    title: str | None,
    joint: tuple[str, ...],
) -> InsiderTransaction:
    assert raw.date is not None
    day = raw.date
    shares = _value(raw.shares, Unit.SHARES, day, source) if raw.shares is not None else None
    price = _value(raw.price, Unit.USD_PER_SHARE, day, source) if raw.price is not None else None
    amount: Value | None = None
    if shares is not None and shares.value is not None:
        if price is not None and price.value is not None:
            amount = _value(
                shares.value * price.value,
                Unit.USD,
                day,
                source,
                note="shares x price per share, as reported in the Form 4",
                inputs=(shares, price),
            )
        else:
            amount = missing(
                "the Form 4 reports no price per share (see its footnotes)",
                unit=Unit.USD,
                period=_point_period(day),
                source=source,
                inputs=(shares,),
            )
    code = raw.code or "?"
    if raw.mentions_plan:
        plan, basis = True, "footnote"
    elif document.plan_checkbox:
        plan, basis = True, "form checkbox"
    else:
        plan, basis = False, None
    ownership = {"D": "direct", "I": "indirect"}.get(raw.ownership or "")
    return InsiderTransaction(
        owner_cik=owner_cik,
        owner_name=owner_name,
        role=role,
        officer_title=title,
        joint_owners=joint,
        security=raw.security,
        derivative=raw.derivative,
        transaction_date=day,
        code=code,
        code_meaning=CODE_MEANINGS.get(
            code, "No transaction code" if code == "?" else "Unknown code"
        ),
        category=category_of(raw.code),
        acquired_disposed=raw.acquired_disposed,
        shares=shares,
        price=price,
        amount=amount,
        shares_after=raw.shares_after,
        ownership=ownership,
        nature_of_ownership=raw.nature_of_ownership,
        under_10b5_1=plan,
        plan_basis=basis,
        footnotes=raw.footnotes,
        accession_number=filing.accession_number,
        form=filing.form,
        filing_date=filing.filing_date,
        document_url=filing.document_url,
        source=source,
    )


# --- amendments and duplicates -------------------------------------------------------------


def apply_amendments(
    items: list[tuple[Filing, Form4Document]],
) -> tuple[list[tuple[Filing, Form4Document]], int]:
    """Drop the filings that a later 4/A of the same person and period replaces."""

    def key(item: tuple[Filing, Form4Document]) -> tuple[date, str]:
        return (item[0].filing_date, item[0].accession_number)

    def person(item: tuple[Filing, Form4Document]) -> str | None:
        owners = item[1].owners
        return _owner_key(owners[0].cik, owners[0].name) if owners else None

    superseded: set[str] = set()
    amendments = sorted((i for i in items if i[0].form == "4/A"), key=key)
    for amendment in amendments:
        period = amendment[1].period_of_report
        who = person(amendment)
        if period is None or who is None:
            continue
        for other in items:
            if (
                other[0].accession_number != amendment[0].accession_number
                and person(other) == who
                and other[1].period_of_report == period
                and key(other) < key(amendment)
            ):
                superseded.add(other[0].accession_number)
    kept = [i for i in items if i[0].accession_number not in superseded]
    return kept, len(superseded)


def drop_duplicates(
    transactions: list[InsiderTransaction],
) -> tuple[list[InsiderTransaction], int]:
    """Count a transaction once if two filings report it; keep the latest filing's version."""
    groups: dict[tuple[object, ...], dict[str, list[InsiderTransaction]]] = defaultdict(dict)
    for t in transactions:
        k = (
            _owner_key(t.owner_cik, t.owner_name),
            t.security,
            t.derivative,
            t.transaction_date,
            t.code,
            t.shares.value if t.shares else None,
            t.price.value if t.price else None,
            t.ownership,
            t.acquired_disposed,
        )
        groups[k].setdefault(t.accession_number, []).append(t)
    kept: list[InsiderTransaction] = []
    for by_filing in groups.values():
        latest = max(by_filing.values(), key=lambda ts: (ts[0].filing_date, ts[0].accession_number))
        kept.extend(latest)
    return kept, len(transactions) - len(kept)


# --- aggregation ---------------------------------------------------------------------------


def _is_market(t: InsiderTransaction, category: Category) -> bool:
    """A purchase or sale of the non-derivative table: the only kind that feeds the totals."""
    return t.category is category and not t.derivative


def _sum(
    values: list[Value],
    unit: Unit,
    period: Period,
    source: Source,
    label: str,
    expected: int | None = None,
) -> Value:
    known = [v for v in values if v.value is not None]
    total = sum((v.value for v in known if v.value is not None), Decimal(0))
    note = f"sum of {len(known)} {label}"
    if not values and expected == 0:
        note = f"no {label}"
    if expected is not None and len(known) < expected:
        note += f"; {expected - len(known)} without a price are not included"
    return Value(
        value=total, unit=unit, period=period, source=source, note=note, inputs=tuple(known)
    )


def make_tally(
    transactions: list[InsiderTransaction], period: Period, source: Source, label: str
) -> Tally:
    shares = [t.shares for t in transactions if t.shares is not None]
    amounts = [t.amount for t in transactions if t.amount is not None]
    return Tally(
        count=len(transactions),
        shares=_sum(shares, Unit.SHARES, period, source, f"{label} (shares)"),
        amount=_sum(
            amounts, Unit.USD, period, source, f"{label} (USD)", expected=len(transactions)
        ),
    )


def _difference(a: Tally, b: Tally, period: Period, source: Source) -> Value:
    a_amount = a.amount.value or Decimal(0)
    b_amount = b.amount.value or Decimal(0)
    return Value(
        value=a_amount - b_amount,
        unit=Unit.USD,
        period=period,
        source=source,
        note="open-market purchases minus open-market sales in USD (positive = net buying)",
        inputs=(a.amount, b.amount),
    )


def _best_role(roles: list[Role]) -> Role:
    return min(roles, key=_ROLE_ORDER.index)


def summarize_people(
    transactions: list[InsiderTransaction], period: Period, source: Source
) -> list[PersonSummary]:
    by_person: dict[str, list[InsiderTransaction]] = defaultdict(list)
    for t in transactions:
        by_person[_owner_key(t.owner_cik, t.owner_name)].append(t)
    people: list[PersonSummary] = []
    for group in by_person.values():
        purchases = [t for t in group if _is_market(t, Category.PURCHASE)]
        sales = [t for t in group if _is_market(t, Category.SALE)]
        planned = [t for t in sales if t.under_10b5_1]
        buy, sell, plan = (
            make_tally(purchases, period, source, "purchases"),
            make_tally(sales, period, source, "sales"),
            make_tally(planned, period, source, "sales under a 10b5-1 plan"),
        )

        def count(category: Category, rows: list[InsiderTransaction] = group) -> int:
            return sum(1 for t in rows if t.category is category)

        people.append(
            PersonSummary(
                owner_cik=group[0].owner_cik,
                name=group[0].owner_name,
                role=_best_role([t.role for t in group]),
                officer_title=next((t.officer_title for t in group if t.officer_title), None),
                purchases=buy,
                sales=sell,
                planned_sales=plan,
                net_amount=_difference(buy, sell, period, source),
                grants=count(Category.GRANT),
                option_exercises=count(Category.EXERCISE),
                tax_withholdings=count(Category.TAX_WITHHOLDING),
                gifts=count(Category.GIFT),
                other=count(Category.OTHER),
            )
        )
    people.sort(
        key=lambda p: (
            -((p.purchases.amount.value or Decimal(0)) + (p.sales.amount.value or Decimal(0))),
            p.name,
        )
    )
    return people


def find_clusters(
    purchases: list[InsiderTransaction],
    period: Period,
    source: Source,
    *,
    min_insiders: int,
    days: int,
) -> list[ClusterBuy]:
    """Windows of ``days`` days from a purchase on, with at least ``min_insiders`` buyers."""
    ordered = sorted(purchases, key=lambda t: (t.transaction_date, t.accession_number))
    clusters: list[ClusterBuy] = []
    i = 0
    while i < len(ordered):
        window_end = ordered[i].transaction_date + timedelta(days=days)
        group = [t for t in ordered[i:] if t.transaction_date <= window_end]
        insiders = {_owner_key(t.owner_cik, t.owner_name) for t in group}
        if len(insiders) >= min_insiders:
            names = tuple(dict.fromkeys(t.owner_name for t in group))
            tally = make_tally(group, period, source, "purchases in the cluster")
            clusters.append(
                ClusterBuy(
                    start=group[0].transaction_date,
                    end=group[-1].transaction_date,
                    insiders=names,
                    shares=tally.shares,
                    amount=tally.amount,
                    transactions=tuple(group),
                )
            )
            i += len(group)
        else:
            i += 1
    return clusters


def analyze_form4s(
    items: list[tuple[Filing, Form4Document]],
    *,
    cik: int,
    company_name: str,
    ticker: str | None,
    as_of: date,
    since: date,
    source: Source,
    unreadable: tuple[str, ...] = (),
    filings_listed: int | None = None,
    other_issuer_filings: int = 0,
    cluster_min_insiders: int = DEFAULT_CLUSTER_INSIDERS,
    cluster_days: int = DEFAULT_CLUSTER_DAYS,
) -> InsiderActivity:
    """Combine parsed Form 4 filings into the insider activity of a period."""
    kept, superseded = apply_amendments(items)
    all_transactions: list[InsiderTransaction] = []
    no_date = 0
    for filing, document in kept:
        tx_source = source.model_copy(
            update={"form_type": filing.form, "accession_number": filing.accession_number}
        )
        built, skipped = build_transactions(document, filing, tx_source)
        all_transactions.extend(built)
        no_date += skipped
    unique, duplicates = drop_duplicates(all_transactions)
    in_period = [t for t in unique if since <= t.transaction_date <= as_of]
    outside = len(unique) - len(in_period)
    in_period.sort(key=lambda t: (t.transaction_date, t.accession_number), reverse=True)

    period = Period(fiscal_year=as_of.year, start=since, end=as_of)
    aggregate_source = Source(provider="stock_forensics", retrieved_at=source.retrieved_at)
    purchases = [t for t in in_period if _is_market(t, Category.PURCHASE)]
    sales = [t for t in in_period if _is_market(t, Category.SALE)]
    planned = [t for t in sales if t.under_10b5_1]
    buy = make_tally(purchases, period, aggregate_source, "open-market purchases")
    sell = make_tally(sales, period, aggregate_source, "open-market sales")
    plan = make_tally(planned, period, aggregate_source, "sales under a 10b5-1 plan")
    not_counted: dict[Category, int] = {}
    for t in in_period:
        if t.category not in (Category.PURCHASE, Category.SALE):
            not_counted[t.category] = not_counted.get(t.category, 0) + 1
    derivative_market = sum(
        1 for t in in_period if t.derivative and t.category in (Category.PURCHASE, Category.SALE)
    )

    notes: list[str] = []
    if no_date:
        notes.append(f"{no_date} transaction(s) without a date were left out")
    if derivative_market:
        notes.append(
            f"{derivative_market} purchase or sale transaction(s) of derivative securities "
            "are listed but not counted in the totals"
        )
    return InsiderActivity(
        cik=cik,
        company_name=company_name,
        ticker=ticker,
        as_of=as_of,
        since=since,
        transactions=tuple(in_period),
        people=tuple(summarize_people(in_period, period, aggregate_source)),
        totals=Totals(
            purchases=buy,
            sales=sell,
            planned_sales=plan,
            net_amount=_difference(buy, sell, period, aggregate_source),
            buyers=len({_owner_key(t.owner_cik, t.owner_name) for t in purchases}),
            sellers=len({_owner_key(t.owner_cik, t.owner_name) for t in sales}),
            not_counted=not_counted,
        ),
        clusters=tuple(
            find_clusters(
                purchases,
                period,
                aggregate_source,
                min_insiders=cluster_min_insiders,
                days=cluster_days,
            )
        ),
        coverage=Coverage(
            filings_listed=filings_listed if filings_listed is not None else len(items),
            filings_read=len(items),
            other_issuer_filings=other_issuer_filings,
            unreadable=unreadable,
            superseded_filings=superseded,
            duplicate_transactions=duplicates,
            outside_period=outside,
            notes=tuple(notes),
        ),
        source=source,
    )
