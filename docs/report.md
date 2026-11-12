# Report and CLI

`forensics AAPL` runs every check on one US stock and writes one report: a single HTML file with
traffic lights on top, then forensic scores, red flags in filings, insider activity, key figures
and the list of sources. The same report is available as Markdown and JSON.

```bash
export SEC_USER_AGENT="stock-forensics you@example.com"   # the SEC blocks anonymous clients
uv run forensics AAPL                       # writes ./AAPL-forensics.html
uv run forensics MSFT --format md           # ./MSFT-forensics.md
uv run forensics NVDA --format json --out nvda.json
uv run forensics AAPL --format md --out -   # to stdout, progress stays on stderr
uv run forensics AAPL --no-cache            # ignore cached SEC responses (cache is still refreshed)
```

`SEC_USER_AGENT` can also live in a `.env` file in the current folder (`SEC_USER_AGENT="name you@example.com"`).
`.env` is gitignored; variables that are already set in the environment win over the file.

A cold run (empty cache) takes roughly 10 to 30 seconds, a repeated run a few seconds, because
filings of past periods never change and are cached on disk.

Exit codes: `0` report written; `1` the analysis failed (unknown ticker, SEC not reachable,
output not writable); `2` usage problem (no `SEC_USER_AGENT`, empty ticker).

## From Python

```python
from stock_forensics.providers.sec_edgar import SecEdgarClient
from stock_forensics.report import analyze, render

with SecEdgarClient() as client:  # reads SEC_USER_AGENT
    report = analyze("AAPL", client=client)
print(report.summary.headline)
open("aapl.html", "w", encoding="utf-8").write(render(report, "html"))
```

`analyze` returns a `ForensicReport` (frozen pydantic model) with the full result of every
section: `statements`, `metrics`, `scores`, `red_flags`, `insiders`, plus `summary`, `sources`,
`errors` and the `disclaimer`. The JSON export is that object, so every value keeps its `unit`,
`period`, `source` (provider, endpoint, form, accession number, XBRL concept) and `inputs`.

## Pipeline

Seven steps, shown as progress on stderr: look up the ticker, load the filing index, build the
statements, compute metrics and scores, check filings for red flags, read the Form 4s, build the
report. The filing index is loaded once and shared by red flags and insiders.

A section that fails does not stop the others. It is `None` in the report, `errors` says why, and
the HTML shows a notice at the top. Only an unknown ticker or an unreadable filing index stops the
run, because nothing meaningful can be said without them.

## Traffic lights

All thresholds are constants in `src/stock_forensics/report/traffic.py`.

| Area | Green | Yellow | Red | Gray |
| --- | --- | --- | --- | --- |
| Beneish M-Score | unlikely manipulator | | likely manipulator | not applicable or not computable |
| Altman Z | safe | grey zone | distress | not applicable or not computable |
| Piotroski F | neutral or strong | | weak | not applicable or not computable |
| Accruals ratio | normal | elevated | high | not applicable or not computable |
| Red flags | no flag, only low flags, or older medium flags | medium flag in the last 3 years, or older high flag | high flag in the last 3 years | section failed |

* The **scores area** shows the worst light of the four scores. If no score applies (banks,
  insurers, REITs), it is gray with the reason, not green.
* The **red-flags area** uses a three-year recency window, measured from the filing date and
  counted inclusive (a flag filed exactly three years ago is still recent). Low flags are shown
  but never change the light.
* **Insider activity** is information only (blue). Most sales are planned diversification, often
  under Rule 10b5-1, so selling is not treated as a warning. Cluster buys are listed as context.
* **Metrics** have no light. A cut-off such as "current ratio below 1" would be an invention; the
  numbers are shown with formula and inputs instead.
* The **overall light** is the worst of scores and red flags. Gray never makes it worse, so a
  company whose scores do not apply gets the light of its red flags, and the headline says
  that the scores do not apply.

Every light is a colored dot plus a text label ("Clear", "Caution", "Warning", "Not available",
"Information"), so colour is never the only signal.

## What the HTML contains

* Header with company, CIK, industry (SIC), analysis date and the date the SEC data was retrieved.
* Overall result, the not-financial-advice note, one card per area, and the reasons behind
  every yellow or red light with links to the details.
* Forensic scores: total, zone, interpretation; expandable formula, inputs with weights and
  contributions, thresholds and known limitations.
* Red flags: each with date, form, link to the original filing and the quoted sentences; a
  table of what each check examined, so "no flag" can be told apart from "not checked".
* Insider activity: totals, per person, open-market transactions (newest 30) with links to the
  Form 4s; the rest is in the JSON export.
* Key figures: ten key financials and the 25 metrics for up to five fiscal years. Hover over a
  figure for its formula, inputs, filing and XBRL concept; "n/a" explains itself the same way.
* Sources: every filing the statement figures and red flags come from, with links.

The file is self-contained: inline CSS, no JavaScript, no external fonts, images or requests. It
follows the light or dark mode of the system and can be printed, archived or emailed.

## Known limitations

* The overall light is a screening result, not a verdict. A grey-zone Altman Z or a high accruals
  ratio is a reason to read the filing.
* Without market data (planned for step 9) manufacturers get the book-value Altman Z'.
* Form 4 transactions beyond the newest 30 open-market rows, and people beyond the top 10, are only
  in the JSON export.
* Form 4 sources are not repeated in the source list; each transaction links to its own filing.

## Verification

The offline end-to-end test (`tests/test_cli.py`, `tests/test_report_pipeline.py`) runs the CLI
against Apple's recorded data: company facts, filing index and 54 raw Form 4 files. Revenue and
net income of fiscal 2026 are asserted against the 10-K (USD 478,834 million and 132,175 million).

Live checks on real filings, with the expected outcome:

| Ticker | Why it was chosen | Result |
| --- | --- | --- |
| AAPL | large, clean reference | Caution: Altman Z' in the grey zone, other scores clear, no flags; 47 sales, 44 under 10b5-1 plans |
| MSFT | second clean reference | Clear, all four scores clear |
| JPM | bank | scores gray ("not meaningful for a bank"), overall from red flags only, headline says so |
| SMCI | known accounting problems | Warning: auditor resignation (8-K, 2024-10-24), late 10-K (NT 10-K, 2024-08-30), accruals ratio 41% |
