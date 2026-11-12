"""Hand-calculated tests for the insider aggregation.

Dataset (fiscal year 2025, window 2025-01-01 to 2025-12-31):

  A  CEO       2025-03-03  P  1000 @ 10.00 = 10,000
               2025-03-04  P   500 @ 12.00 =  6,000
               2025-06-02  S   200 @ 20.00 =  4,000   (footnote: Rule 10b5-1 plan)
               2025-06-03  S   100 @ 21.00 =  2,100   (no plan)
  B  CFO       2025-03-10  P   300 @ 11.00 =  3,300
  C  director  2025-03-25  P   100 @  9.50 =    950
  D  director  2025-04-20  P    50 @ 10.00 =    500
  E  officer   2025-05-05  A  2000 @  0     grant
               2025-05-06  F   400 @ 15     tax withholding
               2025-05-07  M  1000          option exercise
               2025-05-08  G    50 @  0     gift

  purchases: 5 transactions, 1000+500+300+100+50 = 1,950 shares, 10,000+6,000+3,300+950+500
             = 20,750 USD
  sales:     2 transactions, 200+100 = 300 shares, 4,000+2,100 = 6,100 USD;
             under a plan: 1 transaction, 200 shares, 4,000 USD
  net:       20,750 - 6,100 = 14,650 USD (net buying); buyers A, B, C, D = 4; sellers A = 1
  not counted: grant 1, exercise 1, tax withholding 1, gift 1 (E's four transactions)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_forensics.insiders import InsiderActivity, analyze_filings
from stock_forensics.insiders.aggregate import (
    analyze_form4s,
    category_of,
    months_before,
    role_of,
)
from stock_forensics.insiders.form4 import Form4Document, Owner, RawTransaction
from stock_forensics.insiders.models import Category, Role
from stock_forensics.providers.sec_models import Filing, parse_submissions
from tests.insider_fixtures import SOURCE

AS_OF = date(2025, 12, 31)
SINCE = date(2025, 1, 1)
PLAN_FOOTNOTE = "Sold pursuant to a Rule 10b5-1 trading plan adopted on January 2, 2025."


def owner(cik: int, name: str, *, title: str | None = None, director: bool = False) -> Owner:
    return Owner(
        cik=cik,
        name=name,
        is_director=director,
        is_officer=title is not None,
        is_ten_percent_owner=False,
        is_other=False,
        officer_title=title,
    )


def tx(
    day: str,
    code: str,
    shares: str | None,
    price: str | None,
    *,
    derivative: bool = False,
    footnotes: tuple[str, ...] = (),
    ownership: str = "D",
) -> RawTransaction:
    return RawTransaction(
        derivative=derivative,
        security="Common Stock",
        date=date.fromisoformat(day),
        code=code,
        shares=Decimal(shares) if shares is not None else None,
        price=Decimal(price) if price is not None else None,
        acquired_disposed="A" if code in "PAM" else "D",
        shares_after=None,
        ownership=ownership,
        nature_of_ownership=None,
        footnotes=footnotes,
    )


def doc(
    owners: tuple[Owner, ...],
    transactions: list[RawTransaction],
    *,
    form: str = "4",
    period: str | None = None,
    plan_box: bool | None = None,
    issuer_cik: int = 123,
) -> Form4Document:
    return Form4Document(
        document_type=form,
        period_of_report=date.fromisoformat(period) if period else None,
        date_of_original_submission=None,
        issuer_cik=issuer_cik,
        issuer_name="Example Corp",
        issuer_symbol="EXM",
        owners=owners,
        plan_checkbox=plan_box,
        transactions=tuple(transactions),
    )


_counter = 0


def filing(filed: str, form: str = "4") -> Filing:
    global _counter
    _counter += 1
    accession = f"0000000001-25-{_counter:06d}"
    return Filing(
        accession_number=accession,
        form=form,
        filing_date=date.fromisoformat(filed),
        primary_document="xslF345X05/form4.xml",
        document_url=f"https://www.sec.gov/Archives/edgar/data/123/{accession}/xslF345X05/form4.xml",
    )


A = owner(1, "A Alpha", title="Chief Executive Officer")
B = owner(2, "B Beta", title="Chief Financial Officer")
C = owner(3, "C Gamma", director=True)
D = owner(4, "D Delta", director=True)
E = owner(5, "E Epsilon", title="SVP, Operations")


def dataset() -> list[tuple[Filing, Form4Document]]:
    return [
        (
            filing("2025-03-05"),
            doc(
                (A,),
                [
                    tx("2025-03-03", "P", "1000", "10.00"),
                    tx("2025-03-04", "P", "500", "12.00"),
                ],
            ),
        ),
        (
            filing("2025-06-04"),
            doc(
                (A,),
                [
                    tx("2025-06-02", "S", "200", "20.00", footnotes=(PLAN_FOOTNOTE,)),
                    tx("2025-06-03", "S", "100", "21.00"),
                ],
            ),
        ),
        (filing("2025-03-12"), doc((B,), [tx("2025-03-10", "P", "300", "11.00")])),
        (filing("2025-03-27"), doc((C,), [tx("2025-03-25", "P", "100", "9.50")])),
        (filing("2025-04-22"), doc((D,), [tx("2025-04-20", "P", "50", "10.00")])),
        (
            filing("2025-05-09"),
            doc(
                (E,),
                [
                    tx("2025-05-05", "A", "2000", "0"),
                    tx("2025-05-06", "F", "400", "15"),
                    tx("2025-05-07", "M", "1000", None),
                    tx("2025-05-08", "G", "50", "0"),
                ],
            ),
        ),
    ]


def analyze(
    items: list[tuple[Filing, Form4Document]] | None = None, **kwargs: object
) -> InsiderActivity:
    return analyze_form4s(
        items if items is not None else dataset(),
        cik=123,
        company_name="Example Corp",
        ticker="EXM",
        as_of=AS_OF,
        since=SINCE,
        source=SOURCE,
        **kwargs,  # type: ignore[arg-type]
    )


# --- totals -----------------------------------------------------------------------------


def test_totals_by_hand() -> None:
    totals = analyze().totals
    assert totals.purchases.count == 5
    assert totals.purchases.shares.value == Decimal(1950)
    assert totals.purchases.amount.value == Decimal(20750)
    assert totals.sales.count == 2
    assert totals.sales.shares.value == Decimal(300)
    assert totals.sales.amount.value == Decimal(6100)
    assert totals.planned_sales.count == 1
    assert totals.planned_sales.shares.value == Decimal(200)
    assert totals.planned_sales.amount.value == Decimal(4000)
    assert totals.net_amount.value == Decimal(14650)
    assert (totals.buyers, totals.sellers) == (4, 1)


def test_grants_exercises_tax_withholding_and_gifts_are_kept_apart() -> None:
    activity = analyze()
    assert activity.totals.not_counted == {
        Category.GRANT: 1,
        Category.EXERCISE: 1,
        Category.TAX_WITHHOLDING: 1,
        Category.GIFT: 1,
    }
    # they are listed ...
    assert len(activity.transactions) == 11  # 7 purchases and sales plus E's 4
    assert {t.category for t in activity.of_category(Category.GRANT)} == {Category.GRANT}
    # ... but E, who only has such transactions, adds nothing to the totals
    e = next(p for p in activity.people if p.name == "E Epsilon")
    assert (e.purchases.count, e.sales.count) == (0, 0)
    assert (e.grants, e.option_exercises, e.tax_withholdings, e.gifts) == (1, 1, 1, 1)
    assert e.net_amount.value == Decimal(0)


def test_every_total_is_traceable_to_its_transactions() -> None:
    totals = analyze().totals
    amount = totals.purchases.amount
    assert len(amount.inputs) == 5
    assert sum((i.value or 0 for i in amount.inputs), Decimal(0)) == amount.value
    assert {i.source.form_type for i in amount.inputs} == {"4"}
    assert all(i.source.accession_number for i in amount.inputs)
    assert totals.net_amount.inputs == (totals.purchases.amount, totals.sales.amount)
    assert "net buying" in (totals.net_amount.note or "")
    assert totals.purchases.amount.source.provider == "stock_forensics"


# --- people -----------------------------------------------------------------------------


def test_people_are_summarized_and_ordered_by_market_amount() -> None:
    people = analyze().people
    assert [p.name for p in people] == [
        "A Alpha",  # 16,000 + 6,100
        "B Beta",  # 3,300
        "C Gamma",  # 950
        "D Delta",  # 500
        "E Epsilon",  # 0
    ]
    a = people[0]
    assert a.role is Role.CEO
    assert a.officer_title == "Chief Executive Officer"
    assert (a.purchases.count, a.purchases.shares.value, a.purchases.amount.value) == (
        2,
        Decimal(1500),
        Decimal(16000),
    )
    assert (a.sales.count, a.sales.shares.value, a.sales.amount.value) == (
        2,
        Decimal(300),
        Decimal(6100),
    )
    assert (a.planned_sales.count, a.planned_sales.amount.value) == (1, Decimal(4000))
    assert a.net_amount.value == Decimal(9900)  # 16,000 - 6,100
    assert [p.role for p in people[1:]] == [Role.CFO, Role.DIRECTOR, Role.DIRECTOR, Role.OFFICER]


# --- cluster buys -----------------------------------------------------------------------


def test_cluster_by_hand() -> None:
    (cluster,) = analyze().clusters
    # window of 30 days from 2025-03-03: A (twice), B and C buy; D buys 48 days later
    assert (cluster.start, cluster.end) == (date(2025, 3, 3), date(2025, 3, 25))
    assert cluster.insiders == ("A Alpha", "B Beta", "C Gamma")
    assert cluster.shares.value == Decimal(1900)  # 1000 + 500 + 300 + 100
    assert cluster.amount.value == Decimal(20250)  # 10,000 + 6,000 + 3,300 + 950
    assert len(cluster.transactions) == 4
    assert all(t.category is Category.PURCHASE for t in cluster.transactions)


def three_buyers(days_between: list[int]) -> list[tuple[Filing, Form4Document]]:
    start = date(2025, 5, 1)
    people = [A, B, C]
    return [
        (
            filing((start + timedelta(days=d)).isoformat()),
            doc((p,), [tx((start + timedelta(days=d)).isoformat(), "P", "10", "5.00")]),
        )
        for p, d in zip(people, days_between, strict=True)
    ]


@pytest.mark.parametrize(
    ("offsets", "clusters"),
    [
        ([0, 15, 30], 1),  # exactly 30 days apart: inside the window
        ([0, 15, 31], 0),  # 31 days: the third buyer falls outside
        ([0, 0, 0], 1),  # all on the same day
    ],
)
def test_cluster_window_is_exactly_30_days(offsets: list[int], clusters: int) -> None:
    assert len(analyze(three_buyers(offsets)).clusters) == clusters


def test_two_insiders_are_not_a_cluster_unless_asked() -> None:
    items = three_buyers([0, 5, 10])[:2]
    assert analyze(items).clusters == ()
    assert len(analyze(items, cluster_min_insiders=2).clusters) == 1


def test_the_same_insider_buying_often_is_not_a_cluster() -> None:
    items = [
        (filing("2025-05-01"), doc((A,), [tx("2025-05-01", "P", "10", "5.00")] * 4)),
    ]
    assert analyze(items).clusters == ()


def test_a_cluster_window_can_be_changed() -> None:
    items = three_buyers([0, 40, 80])
    assert analyze(items).clusters == ()
    assert len(analyze(items, cluster_days=90).clusters) == 1


def test_sales_do_not_make_a_cluster() -> None:
    start = date(2025, 5, 1)
    items = [
        (filing(start.isoformat()), doc((p,), [tx(start.isoformat(), "S", "10", "5.00")]))
        for p in (A, B, C)
    ]
    assert analyze(items).clusters == ()


# --- the 10b5-1 plan --------------------------------------------------------------------


def sale(*, footnotes: tuple[str, ...] = (), box: bool | None = None) -> InsiderActivity:
    return analyze(
        [
            (
                filing("2025-06-04"),
                doc((A,), [tx("2025-06-02", "S", "10", "5.00", footnotes=footnotes)], plan_box=box),
            )
        ]
    )


def test_plan_from_a_footnote() -> None:
    (t,) = sale(footnotes=(PLAN_FOOTNOTE,)).transactions
    assert (t.under_10b5_1, t.plan_basis) == (True, "footnote")


def test_plan_from_the_form_check_box_if_there_is_no_footnote() -> None:
    (t,) = sale(box=True).transactions
    assert (t.under_10b5_1, t.plan_basis) == (True, "form checkbox")


def test_the_footnote_is_the_better_basis_if_both_exist() -> None:
    (t,) = sale(footnotes=(PLAN_FOOTNOTE,), box=True).transactions
    assert t.plan_basis == "footnote"


@pytest.mark.parametrize("box", [None, False])
def test_no_indication_of_a_plan_is_not_called_discretionary(box: bool | None) -> None:
    activity = sale(box=box)
    (t,) = activity.transactions
    assert (t.under_10b5_1, t.plan_basis) == (False, None)
    assert activity.totals.planned_sales.count == 0
    assert activity.totals.sales.count == 1


# --- gaps -------------------------------------------------------------------------------


def test_a_purchase_without_a_price_counts_but_has_no_amount() -> None:
    items = [
        (
            filing("2025-05-02"),
            doc((A,), [tx("2025-05-01", "P", "100", "10.00"), tx("2025-05-01", "P", "50", None)]),
        )
    ]
    activity = analyze(items)
    unpriced = next(t for t in activity.transactions if t.price is None)
    assert unpriced.amount is not None
    assert unpriced.amount.value is None
    assert "reports no price per share" in (unpriced.amount.note or "")
    purchases = activity.totals.purchases
    assert purchases.count == 2
    assert purchases.shares.value == Decimal(150)
    assert purchases.amount.value == Decimal(1000)  # only the priced one
    assert "1 without a price are not included" in (purchases.amount.note or "")


def test_a_derivative_purchase_is_listed_but_not_counted() -> None:
    items = [
        (
            filing("2025-05-02"),
            doc((A,), [tx("2025-05-01", "P", "100", "10.00", derivative=True)]),
        )
    ]
    activity = analyze(items)
    assert len(activity.transactions) == 1
    assert activity.totals.purchases.count == 0
    assert activity.totals.buyers == 0
    assert any(
        "derivative securities are listed but not counted" in n for n in activity.coverage.notes
    )


def test_no_transactions_gives_zero_tallies_with_an_explanation() -> None:
    activity = analyze([])
    assert activity.transactions == ()
    assert activity.people == ()
    assert activity.totals.purchases.count == 0
    assert activity.totals.purchases.amount.value == Decimal(0)
    assert activity.totals.purchases.amount.note == "no open-market purchases (USD)"
    assert activity.totals.net_amount.value == Decimal(0)


# --- joint filings, amendments, duplicates ----------------------------------------------


def test_a_joint_filing_counts_each_transaction_once() -> None:
    fund = owner(10, "Big Fund LP")
    parent = owner(11, "Big Holdings Inc")
    items = [
        (filing("2025-05-02"), doc((fund, parent), [tx("2025-05-01", "P", "1000", "10.00")])),
    ]
    activity = analyze(items)
    assert activity.totals.purchases.count == 1
    assert activity.totals.purchases.shares.value == Decimal(1000)  # not 2,000
    (t,) = activity.transactions
    assert (t.owner_name, t.joint_owners) == ("Big Fund LP", ("Big Holdings Inc",))
    assert [p.name for p in activity.people] == ["Big Fund LP"]
    assert activity.totals.buyers == 1


def test_an_amendment_replaces_the_earlier_filing_of_the_same_person_and_period() -> None:
    original = (
        filing("2025-05-02"),
        doc((A,), [tx("2025-05-01", "P", "100", "10.00")], period="2025-05-01"),
    )
    amendment = (
        filing("2025-05-06", "4/A"),
        doc((A,), [tx("2025-05-01", "P", "100", "10.50")], form="4/A", period="2025-05-01"),
    )
    other_person = (
        filing("2025-05-03"),
        doc((B,), [tx("2025-05-01", "P", "10", "10.00")], period="2025-05-01"),
    )
    other_period = (
        filing("2025-05-20"),
        doc((A,), [tx("2025-05-19", "P", "5", "10.00")], period="2025-05-19"),
    )
    activity = analyze([original, amendment, other_person, other_period])
    assert activity.coverage.superseded_filings == 1
    prices = sorted(t.price.value for t in activity.transactions if t.price and t.price.value)
    assert prices == [Decimal("10.00"), Decimal("10.00"), Decimal("10.50")]  # 10.00 of A is gone
    a_purchase = next(
        t
        for t in activity.transactions
        if t.owner_name == "A Alpha" and t.shares and t.shares.value == 100
    )
    assert (a_purchase.form, a_purchase.price.value if a_purchase.price else None) == (
        "4/A",
        Decimal("10.50"),
    )
    assert activity.totals.purchases.count == 3


def test_an_amendment_does_not_replace_a_later_filing() -> None:
    later = (
        filing("2025-05-20"),
        doc((A,), [tx("2025-05-01", "P", "7", "10.00")], period="2025-05-01"),
    )
    amendment = (
        filing("2025-05-06", "4/A"),
        doc((A,), [tx("2025-05-01", "P", "100", "10.50")], form="4/A", period="2025-05-01"),
    )
    assert analyze([later, amendment]).coverage.superseded_filings == 0


def test_the_same_transaction_in_two_filings_is_counted_once() -> None:
    first = (filing("2025-05-02"), doc((A,), [tx("2025-05-01", "P", "100", "10.00")]))
    second = (filing("2025-05-04"), doc((A,), [tx("2025-05-01", "P", "100", "10.00")]))
    activity = analyze([first, second])
    assert activity.coverage.duplicate_transactions == 1
    (t,) = activity.transactions
    assert t.accession_number == second[0].accession_number  # the later filing wins


def test_identical_lines_within_one_filing_are_both_kept() -> None:
    one = (
        filing("2025-05-02"),
        doc((A,), [tx("2025-05-01", "P", "100", "10.00"), tx("2025-05-01", "P", "100", "10.00")]),
    )
    activity = analyze([one])
    assert activity.coverage.duplicate_transactions == 0
    assert activity.totals.purchases.count == 2
    assert activity.totals.purchases.shares.value == Decimal(200)


# --- the window -------------------------------------------------------------------------


def test_only_transactions_inside_the_window_count() -> None:
    items = [
        (
            filing("2025-01-05"),
            doc(
                (A,),
                [
                    tx("2024-12-31", "P", "10", "1.00"),  # one day before the window
                    tx("2025-01-01", "P", "20", "1.00"),  # first day
                    tx("2025-12-31", "P", "30", "1.00"),  # last day
                    tx("2026-01-02", "P", "40", "1.00"),  # after the window
                ],
            ),
        )
    ]
    activity = analyze(items)
    assert activity.totals.purchases.shares.value == Decimal(50)
    assert activity.coverage.outside_period == 2


# --- roles and codes --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "director", "role"),
    [
        ("Chief Executive Officer", False, Role.CEO),
        ("CEO, UHG", False, Role.CEO),
        ("Chairman and CEO", True, Role.CEO),
        ("Principal Executive Officer", False, Role.CEO),
        ("President & CFO", False, Role.CFO),
        ("EVP and Chief Financial Officer", False, Role.CFO),
        ("SVP, GC and Government Affairs", False, Role.OFFICER),
        ("Executive Chairman", True, Role.OFFICER),
        (None, True, Role.DIRECTOR),
    ],
)
def test_roles_are_read_from_the_flags_and_the_title(
    title: str | None, director: bool, role: Role
) -> None:
    person = Owner(1, "X", director, title is not None, False, False, title)
    assert role_of(person) is role


def test_other_roles() -> None:
    assert role_of(Owner(1, "X", False, False, True, False, None)) is Role.TEN_PERCENT_OWNER
    assert role_of(Owner(1, "X", False, False, False, True, None)) is Role.OTHER


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("P", Category.PURCHASE),
        ("S", Category.SALE),
        ("A", Category.GRANT),
        ("M", Category.EXERCISE),
        ("X", Category.EXERCISE),
        ("C", Category.EXERCISE),
        ("O", Category.EXERCISE),
        ("F", Category.TAX_WITHHOLDING),
        ("G", Category.GIFT),
        ("D", Category.OTHER),
        ("J", Category.OTHER),
        ("W", Category.OTHER),
        (None, Category.OTHER),
    ],
)
def test_transaction_codes_map_to_categories(code: str | None, category: Category) -> None:
    assert category_of(code) is category


def test_a_transaction_carries_its_filing_and_a_link() -> None:
    items = dataset()
    activity = analyze(items)
    t = next(t for t in activity.transactions if t.code == "F")
    assert t.code_meaning.startswith("Payment of exercise price or tax liability")
    assert t.accession_number.startswith("0000000001-25-")
    assert t.form == "4"
    assert t.document_url is not None
    assert t.document_url.endswith("/xslF345X05/form4.xml")
    assert t.source.provider == "sec_edgar"
    assert t.source.accession_number == t.accession_number
    assert t.shares is not None
    assert t.amount is not None
    assert t.amount.value == Decimal(6000)  # 400 x 15
    assert t.amount.inputs == (t.shares, t.price)
    assert t.ownership == "direct"


# --- months, analyze_filings, JSON ------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 11, 12), 12, date(2025, 11, 12)),
        (date(2025, 6, 20), 2, date(2025, 4, 20)),
        (date(2025, 1, 15), 2, date(2024, 11, 15)),
        (date(2024, 2, 29), 12, date(2023, 2, 28)),  # no February 29 a year earlier
        (date(2025, 3, 31), 1, date(2025, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 2, 29)),
    ],
)
def test_months_before(day: date, months: int, expected: date) -> None:
    assert months_before(day, months) == expected


def index_for(filings: list[Filing]) -> dict[str, object]:
    cols = {
        "accessionNumber": [f.accession_number for f in filings],
        "filingDate": [f.filing_date.isoformat() for f in filings],
        "reportDate": ["" for _ in filings],
        "form": [f.form for f in filings],
        "items": ["" for _ in filings],
        "primaryDocument": [f.primary_document for f in filings],
    }
    return {
        "cik": "0000000123",
        "name": "Example Corp",
        "tickers": ["EXM"],
        "filings": {"recent": cols},
    }


def xml_for(issuer: int, code: str = "P") -> str:
    return f"""<ownershipDocument><documentType>4</documentType>
    <periodOfReport>2025-05-01</periodOfReport>
    <issuer><issuerCik>{issuer:010d}</issuerCik></issuer>
    <reportingOwner><reportingOwnerId><rptOwnerCik>0000000009</rptOwnerCik>
    <rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
    </reportingOwner><nonDerivativeTable><nonDerivativeTransaction>
    <transactionDate><value>2025-05-01</value></transactionDate>
    <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>10</value></transactionShares>
    <transactionPricePerShare><value>5</value></transactionPricePerShare></transactionAmounts>
    </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


def test_analyze_filings_reports_unreadable_foreign_and_good_filings() -> None:
    good, broken, missing_doc, foreign, too_old, after = (
        filing("2025-05-02"),
        filing("2025-05-03"),
        filing("2025-05-04"),
        filing("2025-05-05"),
        filing("2024-05-05"),  # before the window
        filing("2026-02-01"),  # after the window
    )
    texts = {
        good.accession_number: xml_for(123),
        broken.accession_number: "<html>Too many requests</html>",
        foreign.accession_number: xml_for(999),  # filed by the company about another issuer
        too_old.accession_number: xml_for(123),
        after.accession_number: xml_for(123),
    }
    submissions = parse_submissions(
        index_for([good, broken, missing_doc, foreign, too_old, after]), SOURCE
    )
    activity = analyze_filings(
        submissions, lambda f: texts.get(f.accession_number), as_of=AS_OF, months=12
    )
    coverage = activity.coverage
    assert coverage.filings_listed == 4  # the two filings outside the period are not listed
    assert coverage.filings_read == 1
    assert coverage.other_issuer_filings == 1
    assert set(coverage.unreadable) == {broken.accession_number, missing_doc.accession_number}
    assert activity.totals.purchases.count == 1
    assert activity.ticker == "EXM"
    assert activity.company_name == "Example Corp"


def test_activity_roundtrips_through_json() -> None:
    activity = analyze()
    assert InsiderActivity.model_validate_json(activity.model_dump_json()) == activity
    data = json.loads(activity.model_dump_json())
    # Decimals are strings in the JSON (no float rounding); the scale of the price is kept
    assert Decimal(data["totals"]["purchases"]["amount"]["value"]) == Decimal(20750)
