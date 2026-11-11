# Metrics

Code: `src/stock_forensics/metrics/` (`engine.py` evaluation, one module per group). Input is a `Statements` object (see [normalization](normalization.md)), output is a `Metrics` object with one series per metric and one `Value` per fiscal year.

```python
from stock_forensics.metrics import compute_metrics

metrics = compute_metrics(statements)
roe = metrics.annual("roe", 2025)
print(roe.value, roe.note)  # 1.714... and the formula
for used in roe.inputs:  # the statement values it was computed from
    print(used.period.fiscal_year, used.value, used.source.accession_number)
metrics.latest("net_margin")  # newest fiscal year
metrics.of_group(Group.GROWTH)  # all growth metrics
```

Every metric is a `Value`: the number, the formula in `note`, the statement values it was computed from in `inputs` (each with its own accession number and concept). `Source.provider` is `stock_forensics`; `retrieved_at` is that of the input data, so results are deterministic.

## The 25 metrics

Ratios have unit `ratio` (0.25 = 25 %), amounts are `USD`.

| Group | Metric | Formula |
|---|---|---|
| Profitability | `gross_margin`, `operating_margin`, `net_margin` | gross profit, operating income or net income / revenue |
| | `roe` | net income / average(total equity, prior-year total equity) |
| | `roa` | net income / average(total assets, prior-year total assets) |
| | `roic` | operating income x (1 - tax rate) / average invested capital. Tax rate = income tax expense / income before tax. Invested capital = short-term debt + long-term debt + equity incl. noncontrolling interests (parent equity if no noncontrolling interests are reported; the note says so). Cash is not netted out. |
| Liquidity and leverage | `current_ratio` | current assets / current liabilities |
| | `quick_ratio` | (current assets - inventory) / current liabilities |
| | `debt_to_equity` | (short-term debt + long-term debt) / total equity |
| | `interest_coverage` | operating income / interest expense (operating income stands in for EBIT) |
| | `net_cash` (USD) | cash + short-term investments - short-term debt - long-term debt; negative means net debt |
| Cash flow quality | `free_cash_flow` (USD) | operating cash flow - capital expenditures |
| | `fcf_margin` | free cash flow / revenue |
| | `cash_conversion` | operating cash flow / net income |
| Growth | `revenue_growth_yoy`, `net_income_growth_yoy` | value / prior-year value - 1 |
| | `revenue_cagr_3y`, `net_income_cagr_3y` | (value / value three years earlier) ^ (1/3) - 1 |
| | `revenue_growth_consistency`, `net_income_growth_consistency` | share of the latest up to 5 year-over-year changes that are positive (at least 3 changes needed). The note lists them, for example `4 of 5 ... (2021+, 2022-, ...)`. |
| Capital allocation | `payout_ratio` | dividends paid / net income |
| | `buyback_ratio` | share repurchases / net income |
| | `total_payout_ratio` | (dividends paid + share repurchases) / net income |
| | `share_count_change_yoy` | diluted weighted shares / prior-year diluted weighted shares - 1 (positive = dilution) |
| | `rnd_ratio` | research and development / revenue |

"Earnings" means net income. Average-based metrics use the beginning and the end of the fiscal year, so they need the prior year too.

## Gaps are explicit

A metric is a `Value` with `value=None` and the reason in `note` (never a zero or a guess) when:

- **An input is missing:** `cannot compute net_cash: short_term_investments FY2026: not reported for this period`. This applies even when a line is missing only because the company has none (Walmart has no short-term investments, so its net cash is n/a). The inputs that were available are still attached.
- **A needed year is outside the data:** `needs fiscal year 2019 (statements cover 2020-2025)`.
- **The number would not be meaningful:** equity not positive (ROE, debt to equity), net income not positive (cash conversion, payout ratios), interest expense not positive, prior-year base not positive (growth), pre-tax income not positive or a tax rate outside 0-100 % (ROIC), a zero denominator.

## Flags from the inputs

If an input is restated, derived or carries a caveat in the statements, the metric's note names it: `net_income / revenue; inputs flagged: net_income FY2025 (restated)`. A restated profit therefore shows up in every ratio that uses it.

## Limitations

- Annual metrics only. TTM series exist in the statements; TTM metrics will follow when a report needs them.
- Definitions are standard textbook ones. The old `Stock-Forensic` notebook was not available, so its exact definitions (especially growth consistency) were not compared.
- Banks and insurers have other statement structures; many metrics are gaps for them, which is intended (the forensic scores mark them "not applicable" in step 5).
- Apple stopped tagging interest expense in 2024, so its interest coverage is a gap from fiscal 2024 on.

## Tests and verification

- `tests/test_metrics.py`: a fictional company with numbers chosen so that every metric can be checked by hand; the calculation is next to each assertion. Also all gap paths, the provenance of inputs and the JSON round trip.
- `tests/test_metrics_real.py`: real, trimmed statements of AAPL, MSFT and WMT with hand recalculation from 10-K figures, identities between metrics, plausible ranges and the explicit gaps.
- Live check when this was written (2026-11-11): all 32 distinct input values behind Apple's fiscal 2025 metrics appear in the 10-K documents, and six metrics recalculated by hand from the printed numbers (gross margin, net margin, current ratio, FCF margin, cash conversion, ROE) match the engine exactly.
