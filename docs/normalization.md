# Financial statement normalization

Code: `src/stock_forensics/normalize/` (`concepts.py` field mapping, `periods.py` fiscal calendar, `statements.py` the builder). Input is the `CompanyFacts` of the SEC provider, output is a `Statements` object.

```python
from stock_forensics.normalize.statements import build_statements
from stock_forensics.providers.sec_edgar import SecEdgarClient

with SecEdgarClient() as client:
    facts = client.get_company_facts(client.ticker_to_cik("AAPL").cik)

statements = build_statements(facts, years=6, quarters=8)
revenue = statements.annual("revenue", 2025)  # a Value: number, unit, period, source, note
print(revenue.value, revenue.source.accession_number, revenue.source.xbrl_concept)
ttm = statements.get("revenue").ttm
```

`Statements` has one `Series` per standard field (37 fields across income statement, balance sheet and cash flow). A series has `annual` (by fiscal year), `quarterly` (keys like `"2025Q3"`) and `ttm`. Every entry is a `Value`; a gap is a `Value` with `value=None` and a reason in `note`, never a zero. The whole object serializes to JSON.

## Why periods are matched by dates

In the SEC data, `fy` and `fp` describe the **filing's** fiscal period, not the period of the number. A 10-K for fiscal 2023 also holds the 2021 and 2022 comparatives, all tagged `fy=2023`. So a period is identified by its exact `(start, end)` dates. Its fiscal label (`fy`, `fp`) is taken from the **earliest** filing that reports it, which is the filing of that period itself. The label is the one the company uses: Walmart's fiscal 2024 ends in January 2024. 52/53-week years (Apple: 364 or 371 days) are recognised by duration (340 to 380 days for a year, up to 105 days for a quarter).

## Rules

| Topic | Rule |
|---|---|
| Which filings count | 10-K, 10-K/A, 10-KT, 10-Q, 10-Q/A, 10-QT. Facts from other forms (for example 8-K) are ignored. |
| Which concept | Per field a priority list of US-GAAP concepts (`concepts.py`). **Per period** the first concept with a value wins, so a series survives a tag change. The concept used is in `Value.source.xbrl_concept`. |
| Duplicates | Several filings report the same period (original, next year's comparative, amendment). The latest filing wins. If it differs from the original, the note says so: `restated: originally X in <accession> (filed ...), now Y in <accession>`. |
| Fiscal-year values | Cite an annual filing (10-K family). The next 10-Q repeats the year-end balance sheet as a comparative, but is not cited for the year-end figure. |
| Quarters | A 10-Q reports Q1 as a quarter, but Q2 and Q3 as year-to-date, and cash flows only year-to-date. Missing quarters are derived by subtraction (Q2 = 6M - Q1, Q3 = 9M - 6M, **Q4 = fiscal year - 9M**) and marked in the note: `derived: FY 1000 (accession) - 9M YTD 700 (accession)`. A reported discrete quarter always wins over a derived one. |
| Per-share and average figures | EPS and weighted shares are never derived or summed (subtraction would be wrong). Q4 stays a gap with that reason. |
| TTM | Sum of the last four consecutive quarters, only for additive flow items (not for balance sheet items, EPS, share counts). If the newest period is a fiscal year end, TTM equals that year. If any quarter is missing, TTM is a gap that names it. |
| Gaps | Every field has a value for every year and quarter in the range. If a field is not reported, `note` says `not reported for this period (tried: us-gaap:A, us-gaap:B ...)`. Concepts that exist only in another unit (for example EUR) are named. Only USD, per-share and share units are used. |

### Labelled derivations (kept to a minimum)

| Field | Fallback when no concept is reported |
|---|---|
| `gross_profit` | `revenue - cost_of_revenue` |
| `total_liabilities` | `total_liabilities_and_equity - equity_incl_noncontrolling` |
| `selling_general_admin` | Selling and marketing + general and administrative, only if **both** are reported (Microsoft) |
| `short_term_debt` | Sum of the reported components (short-term borrowings, commercial paper, current portion of long-term debt). Missing components are named in the note, because a partial sum can understate. |

Totals like `CashCashEquivalentsAndShortTermInvestments` are deliberately not mapped to a component field (they would double count). A few concepts carry a caveat in the note, for example `ProfitLoss` ("includes noncontrolling interests") and `Depreciation` ("may exclude amortization").

## Limitations

- `shares_outstanding` only uses the balance sheet concept `CommonStockSharesOutstanding`. Walmart no longer tags it, so it is a gap there; use `shares_diluted_weighted` for dilution.
- Derived values and TTM have a single accession in `source`; the other inputs are listed in the note. A structured list of inputs may follow with the metrics engine.
- Foreign filers (20-F, 40-F, non-USD) are not supported and give gaps or `NoFinancialDataError`.
- Banks and insurers have their own statement structure: revenue, cost of revenue and others are mostly gaps (JPMorgan: no gross profit, no SG&A). That is intended; forensic scores mark them "not applicable" (step 5).
- The default history is 6 fiscal years and 8 quarters (`years=`, `quarters=`).

## Reconciliation with the 10-K

`scripts/reconcile_statements.py` downloads the 10-K documents and compares the normalized revenue, net income, total assets and operating cash flow with the numbers the documents print (read from their inline-XBRL tags, with scale and sign), not with the API:

```bash
SEC_USER_AGENT="stock-forensics you@example.com" uv run python scripts/reconcile_statements.py AAPL MSFT WMT
```

Result when this was written (2026-11-11): all figures of the last three fiscal years match for Apple, Microsoft and Walmart (36 of 36). The expected values in `tests/test_statements_real.py` come from that run.

## Tests and fixtures

- `tests/test_statements_synthetic.py`: a fictional company with hand-built facts; every expected number has its calculation next to it (restatement, amendment, concept switch, derived quarters, TTM, composites, gaps).
- `tests/test_periods.py`: fiscal calendar (52/53 weeks, January year end, label from earliest filing).
- `tests/test_statements_real.py`: real, trimmed company facts of AAPL, MSFT, WMT (`tests/fixtures/sec_edgar/statements/`, recorded by `scripts/record_statement_fixtures.py`). Checks the 10-K figures, that four quarters add up to the year, that TTM rolls forward correctly, and that the balance sheet balances.
