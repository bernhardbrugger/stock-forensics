# Insider activity (Form 4)

Code: `src/stock_forensics/insiders/` (`form4.py` parser, `aggregate.py` classification and aggregation, `models.py` results). It answers: are the people with the most information buying or selling?

```python
from stock_forensics.insiders import get_insider_activity
from stock_forensics.providers.sec_edgar import SecEdgarClient

with SecEdgarClient() as client:
    activity = get_insider_activity(client, cik, months=12)

t = activity.totals
print(t.purchases.count, t.purchases.amount.value)  # open-market purchases
print(t.sales.count, t.sales.amount.value)  # open-market sales
print(t.planned_sales.count, t.net_amount.value)  # sales under a 10b5-1 plan; net in USD
for cluster in activity.clusters:  # several insiders buying within 30 days
    print(cluster.start, cluster.end, cluster.insiders, cluster.amount.value)
for person in activity.people:  # by size of the open-market amount
    print(person.name, person.role, person.net_amount.value)
for x in activity.transactions:  # every transaction, newest first
    print(x.transaction_date, x.owner_name, x.code, x.category, x.document_url)
```

`analyze_filings(submissions, read_xml, as_of=..., months=...)` does the same on an already loaded filing history with an injectable reader (used by the tests).

## Real purchases are kept apart from everything else

Only **open-market purchases (code P) and sales (code S) of the non-derivative table** count as buying and selling. Every other transaction is listed, but kept out of the totals, because it says little about what insiders think of the stock:

| Category | Codes | Meaning |
|---|---|---|
| `purchase` | P | purchase (the SEC code covers open market and private purchases) |
| `sale` | S | sale (open market or private) |
| `grant` | A | stock or options awarded by the company |
| `exercise` | M, X, C, O | exercise or conversion of options and similar |
| `tax_withholding` | F | shares withheld to pay exercise price or taxes |
| `gift` | G | bona fide gift |
| `other` | D, J, K, I, L, U, V, W, Z, E, H | everything else |

Purchases or sales of derivative securities are listed but not counted (a note in `coverage` says how many). `totals.not_counted` shows how many transactions of each category were kept out.

## What a result contains

- **`InsiderTransaction`**: person, role (`ceo`, `cfo`, `officer`, `director`, `ten_percent_owner`, `other`; read from the flags and the free-text title, "President & CFO" is a CFO), date, code and its meaning, category, `shares`, `price` and `amount` (each a `Value`; `amount` = shares x price and lists both as `inputs`), holdings after the transaction, direct or indirect, the plan indication, the footnotes, and **the accession number and a link to the Form 4**.
- **`Totals`**: purchases, sales, sales under a plan, net amount (positive = net buying), the number of distinct buyers and sellers. Each tally has count, shares and USD amount, and each is a `Value` whose `inputs` are the transaction amounts it sums up.
- **`PersonSummary`** per person, and **`ClusterBuy`** for several insiders buying close together: at least **3 distinct insiders** with a purchase within **30 days** (`cluster_min_insiders`, `cluster_days`). A window starts at a purchase; the window of day 0 includes day 30 but not day 31.
- **`Coverage`**: filings listed and read, unreadable ones (by accession number), filings about another issuer, replaced filings, duplicate transactions, transactions outside the period.

The result states facts ("net 3.2 million USD sold, 80 % of the sales under a plan"), not a recommendation.

## Rules

- **Period:** the last `months` (default 12) by **transaction date**. Filings are those filed in the period.
- **Joint filings:** a filing with several reporting persons (for example Berkshire Hathaway and its subsidiaries) reports every transaction once. It is counted **once** and attributed to the first reporting person; the others are in `joint_owners`.
- **Amendments:** a 4/A replaces the earlier filings of the same first reporting person with the same period of report. The same transaction in two filings is counted once (the later filing wins); identical lines inside one filing are both kept.
- **Rule 10b5-1 plans:** a transaction counts as made under a plan if one of **its own footnotes** says so (`plan_basis="footnote"`), or else if the filing's plan check box (`aff10b5One`, in use since April 2023) is set (`"form checkbox"`). **"No indication" is not the same as "discretionary":** older filings have no check box, and not every plan is mentioned.
- **Gaps:** if a purchase or sale reports no price, `amount.value` is `None` with the reason, and the transaction is counted but not included in USD sums (the note of the sum says how many were left out). Filings that cannot be read are listed in the coverage, never skipped silently.
- **Only filings about the company:** the SEC index also lists Form 4s that the company itself filed as an insider of another issuer (Occidental about Western Midstream). They are recognised by the issuer in the XML and left out.

## How the data is read

The filing index lists the rendered view (`.../xslF345X06/form4.xml`). The parser reads the **raw XML** in the same folder without the `xsl...` part, which has the reporting owners (flags and title), the non-derivative and derivative tables, the footnotes and the plan check box. A transaction's footnotes are those that its elements refer to. Documents that are not Form 4 XML are reported as unreadable.

## Limitations

- No verdict. Sales are often diversification and tax planning; purchases are the stronger signal, but one purchase says little about one company.
- Derivative transactions are not part of the totals. Form 3 (initial holdings) and Form 5 (annual statements) are not read, and holdings without a transaction are ignored.
- A joint filing is attributed to its first reporting person.
- The role comes from the filing; one person can hold several roles (the highest counts: CEO, CFO, officer, director, 10% owner).
- A 4/A is matched to its original by person and period of report, because the XML does not name the original filing.
- Companies with very many insiders (hundreds of Form 4s a year) need one document request per filing at first; they are cached afterwards.

## Tests and fixtures

- `tests/test_form4_parser.py`: the parser on real filings and hand-made variants (missing price, price only in a footnote, every spelling of the check box, joint filings, derivative table, holdings only, broken XML, the raw XML address).
- `tests/test_insiders_aggregate.py`: a fully hand-calculated dataset (the calculation is at the top of the file), exact cluster windows (30 against 31 days), the plan basis, gaps, derivative purchases, joint filings, amendments, duplicates, the window, roles and codes.
- `tests/test_insiders_cases.py`: real cases with an **independent recalculation** of the totals straight from the raw XML, the Intel purchase by hand (7,500 x 20.3137 + 5,000 x 19.9186 = 251,945.75 USD), the UnitedHealth cluster, Apple's planned sales, Berkshire's joint filings, the Microsoft amendment, and the whole path through the client with a fake SEC server.
- `tests/fixtures/insiders/`: real, raw Form 4 XML and trimmed filing indexes (about 0.6 MB), recorded by `scripts/record_insider_fixtures.py`, which also checks that the result from the fixtures equals a live run.

Live check when this was written (2026-11-12): Apple's last 12 months (54 Form 4 filings, 145 transactions): no purchase, 47 sales by 7 insiders for about 208.8 million USD, 44 of them under a 10b5-1 plan; 15 tax withholdings, 5 gifts, 20 grants and 58 option exercises are kept out of the totals. The links of the rendered Form 4s open the filings.
