# Forensic scores

Code: `src/stock_forensics/scores/` (`beneish.py`, `altman.py`, `piotroski.py`, `accruals.py`, `industry.py`, `base.py`). Input is a `Statements` object (see [normalization](normalization.md)) plus the SIC code from the SEC submissions; output is a `Scores` object with one `ScoreResult` per score and fiscal year.

```python
from stock_forensics.scores import compute_scores

submissions = client.get_submissions(cik)
scores = compute_scores(statements, sic_code=submissions.sic_code, industry=submissions.industry)
m = scores.latest("beneish_m")
print(m.total.value, m.zone)  # -2.29 unlikely_manipulator
print(m.interpretation)  # plain-language reading, incl. the largest contributions
for c in m.components:  # DSRI, GMI, ... each with weight, contribution and inputs
    print(c.name, c.value.value, c.contribution)
print(m.thresholds, m.limitations, m.reference)
```

A `ScoreResult` has: `total` (a `Value`, unit `score`, whose `inputs` are the component values and, one level further, the statement values with accession numbers), `components`, `constant` (intercept), `thresholds`, `zone`, `interpretation`, `limitations`, `formula` and `reference`. Names: `beneish_m`, `altman_z`, `piotroski_f`, `accruals_ratio`.

If a score cannot be computed, `total.value` is `None` with the reason in `total.note`, and every component that could be computed stays visible. Nothing is filled with zeros.

## The scores

| Score | Formula | Thresholds | Reference |
|---|---|---|---|
| **Beneish M** | M = -4.84 + 0.920 DSRI + 0.528 GMI + 0.404 AQI + 0.892 SGI + 0.115 DEPI - 0.172 SGAI + 4.679 TATA - 0.327 LVGI | above -1.78: `likely_manipulator`, otherwise `unlikely_manipulator` | Beneish (1999), Financial Analysts Journal 55(5) |
| **Altman Z** | variants below | per variant, zones `safe`, `grey`, `distress` | Altman (1968, 1983, 1995) |
| **Piotroski F** | sum of nine 0/1 signals | 8 to 9 `strong`, 3 to 7 `neutral`, 0 to 2 `weak` | Piotroski (2000), Journal of Accounting Research 38 |
| **Accruals ratio** | (net income - operating cash flow) / average total assets | absolute value up to 10 % `normal`, up to 25 % `elevated`, above `high` | Sloan (1996), The Accounting Review 71(3) |

### Beneish indices (year t against t-1)

| Index | Definition |
|---|---|
| DSRI | (receivables / revenue) / (prior-year receivables / prior-year revenue) |
| GMI | prior-year gross margin / gross margin |
| AQI | (1 - (current assets + net PP&E) / total assets) / same ratio of the prior year |
| SGI | revenue / prior-year revenue |
| DEPI | prior-year D&A rate / D&A rate, with rate = D&A / (D&A + net PP&E) |
| SGAI | (SG&A / revenue) / (prior-year SG&A / prior-year revenue) |
| TATA | (net income - operating cash flow) / total assets |
| LVGI | ((current liabilities + long-term debt) / total assets) / same ratio of the prior year |

Simplifications: TATA is the cash-flow version (the original balance sheet version needs income tax payable, which is not in the statements); AQI uses current assets plus net PP&E without a separate securities line; D&A comes from the cash flow statement.

### Altman variants

X1 = working capital / total assets, X2 = retained earnings / total assets, X3 = EBIT / total assets (operating income stands in for EBIT), X5 = revenue / total assets.

| Variant | Used for | Formula | Distress below / safe above |
|---|---|---|---|
| **Z** | manufacturers (SIC 2000-3999) when a market value of equity is supplied | 1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4 + 1.0 X5, X4 = market equity / total liabilities | 1.81 / 2.99 |
| **Z'** | manufacturers without market data (book-value version) | 0.717 X1 + 0.847 X2 + 3.107 X3 + 0.420 X4 + 0.998 X5, X4 = book equity / total liabilities | 1.23 / 2.90 |
| **Z''** | everything else (non-manufacturers) | 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4, X4 = book equity / total liabilities | 1.10 / 2.60 |

The result states which variant was used and why. `compute_scores(..., market_cap={fiscal_year: Value})` lets manufacturers use the original Z; market data arrives with step 9. A value exactly on a threshold belongs to the grey zone. With an unknown SIC code Z'' is used and the result says so.

### Piotroski signals

Each signal scores 1 if it is good: `F_ROA` (ROA > 0), `F_CFO` (operating cash flow > 0), `F_DELTA_ROA` (ROA up), `F_ACCRUAL` (operating cash flow > net income), `F_DELTA_LEVER` (long-term debt / average assets did not rise), `F_DELTA_LIQUID` (current ratio up), `F_EQ_OFFER` (share count did not rise), `F_DELTA_MARGIN` (gross margin up), `F_DELTA_TURN` (asset turnover up). ROA, CFO and turnover use total assets at the beginning of the year, so the score for year t needs balance sheets for t-2, t-1 and t. The total is only given if all nine can be evaluated; otherwise it is a gap with the reason and "x of y evaluable criteria passed", and each criterion is still listed with its result.

`F_EQ_OFFER` is a proxy: year-end shares outstanding, or diluted weighted shares if those are missing for either year (never mixed), because the cash raised from share issues is not in the statements.

## Industry exclusions

For banks, other lenders, brokers, insurers, holding companies and REITs the statement structure differs (deposits, premiums, no gross profit), so all four scores are `not applicable`: `applicable=False`, `not_applicable_reason` names the industry, `total.value` is `None`. Excluded SIC codes:

| SIC | Industry |
|---|---|
| 6000-6099 | banks and other depository institutions |
| 6100-6199 | non-depository credit institutions |
| 6200-6299 | securities and commodity brokers, dealers, exchanges |
| 6300-6399 / 6400-6499 | insurance carriers / insurance agents and brokers |
| 6700-6797, 6799 | holding companies and investment offices |
| 6798 | real estate investment trusts (REITs) |

Real estate operators and developers (6500-6599) are not excluded. If the SIC code is unknown the scores are computed and every limitation list says that the exclusion could not be checked.

## Limitations (also delivered with every result)

- **Beneish:** calibrated on US manufacturers from 1982 to 1992; high false-positive rate (many legitimate fast-growing companies score above the cutoff); a screen, not evidence.
- **Altman:** built on US manufacturers; book values; equity can be small or negative after buybacks (Apple), which holds Z' in the grey zone without real distress; operating income stands in for EBIT.
- **Piotroski:** designed for high book-to-market stocks; binary signals; `F_EQ_OFFER` is a proxy.
- **Accruals:** the 10 % and 25 % bands are practitioner rules of thumb (sources disagree; Sloan ranked firms against each other); acquisitions and growth distort accruals.

## Verification status of the formulas

Coefficients and thresholds were checked against several independent secondary sources, not the original papers, because the papers were not accessible in this session:

- Beneish: the eight coefficients and the -1.78 cutoff agree across [Wikipedia](https://en.wikipedia.org/wiki/Beneish_M-score), [stockalarm.io](https://pro.stockalarm.io/blog/beneish-m-score-guide) and [daytrading.com](https://daytrading.com/beneish-m-score). One mirror words the interpretation the other way round; the common reading (above -1.78 is flagged) is used.
- Altman: Z'' coefficients and the 2.60 / 1.10 zones agree across [metricgate](https://metricgate.com/docs/altman-z-score/), [strategiccfo](https://strategiccfo.com/z-score-model/) and [calculate.co.nz](https://www.calculate.co.nz/altman-z-score-calculator.php). For Z' the cutoffs are 2.90 and 1.23 in Altman's private-firm model; some calculators quote 2.99 for the upper one. **Open point:** the Z' coefficients themselves were not found in these sources and come from Altman (1983) as known; they should be checked against the paper.
- Piotroski: the nine signals and their pass conditions agree across [quant-investing.com](https://www.quant-investing.com/blog/piotroski-f-score-complete-guide/) and others. **Open point:** the asset base of each ratio (beginning of year or average) could not be confirmed there; the implementation follows the paper as described (beginning of year for ROA, CFO and turnover, average for leverage) and this was not re-checked against the paper's text.
- Accruals: formula as on [investing.com](https://investing.com/academy/analysis/sloan-ratio-definition); thresholds are conventions, not from Sloan.

## Tests and verification

- `tests/test_scores.py`: one fully worked example per score with the calculation in a comment (Beneish M = -1.74088 from indices 1.2, 1.25, 1.2, 1.25, 1.2, 1.3, 0.04, 1.12; Altman Z'' = 4.674, Z' = 2.86525, Z = 4.595; Piotroski F = 9; accruals ratio 110 / 1100 = 0.10), exact threshold boundaries, all gap paths, industry exclusions with real SIC examples, provenance and the JSON round trip.
- `tests/test_scores_real.py`: real, trimmed statements of AAPL, MSFT and WMT: all components visible, plausible totals, an independent recalculation of Apple's fiscal 2025 M-Score from the raw statement values, banks not applicable.
- Live check when this was written (2026-11-11): all 30 distinct raw inputs behind Apple's four scores for fiscal 2025 and all 28 behind Microsoft's appear in the 10-K documents. Results for fiscal 2025: Apple M -2.29 (unlikely manipulator), Z' 2.35 (grey), F 8 (strong), accruals 0.1 %; Microsoft M -2.54, Z'' 4.49 (safe), F 6 (neutral), accruals -6.1 %. JPMorgan (SIC 6021) is not applicable for all four scores.
