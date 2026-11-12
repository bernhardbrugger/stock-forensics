# ROADMAP: stock-forensics

> Work plan for Claude Code. **Exactly one step per session.** Every step has a goal, todos to tick off, a "Done when" check and a kickoff prompt to paste in.
>
> Flow per step: paste the prompt → Claude Code proposes a plan → you approve → implementation + tests → verification against real data → you confirm → commit with ticked todos.

**Progress:** Phase 1 (MVP) = steps 0–8 · Phase 2 (Extensions) = steps 9–12 · Phase 3 (Launch) = step 13

---

## Phase 1: MVP (runs entirely without API keys)

### Step 0: Project setup
**Goal:** A clean, empty Python project with quality gates that everything else builds on.

- [x] Repo `stock-forensics` created (public, MIT license)
- [x] `uv init` with `src/` layout, Python 3.12, package name `stock_forensics`
- [x] Dev dependencies: `pytest`, `ruff`, `mypy`, `pytest-recording`
- [x] `ruff` and `mypy --strict` configuration in `pyproject.toml`
- [x] Folder structure as described in `CLAUDE.md`, with empty `__init__.py` files
- [x] `.gitignore` (incl. `.env`, cache), `.env.example` with `SEC_USER_AGENT=`
- [x] GitHub Actions: lint + typecheck + tests on every push
- [x] Minimal README (one-line description, "work in progress", not-financial-advice note)
- [x] One dummy test so CI is green

**Done when:** `uv run pytest`, `ruff` and `mypy` pass locally and in GitHub Actions.

```text
Read CLAUDE.md and ROADMAP.md. We start with step 0 (project setup).
First present a plan (files, configuration, CI workflow) and wait for my approval.
```

---

### Step 1: Data model with provenance
**Goal:** The foundation for "every number can be verified".

- [x] Pydantic model `Source` (provider, URL/endpoint, form type, accession number, retrieval timestamp)
- [x] Pydantic model `Value` (value or `None`, unit, period, `Source`, `note` explaining missing data)
- [x] Models for `Company` (ticker, CIK, name, SIC code, industry) and `Period` (fiscal year, quarter, start/end)
- [x] Helper `missing(reason)` for clearly explained gaps
- [x] Tests: JSON serialization, missing values, units

**Done when:** A `Value` can be saved to JSON and loaded back without loss, including its source.

```text
Step 1: data model with provenance (see ROADMAP.md).
Plan first: which models, which fields, which tests. Then wait for approval.
```

---

### Step 2: SEC EDGAR provider
**Goal:** Reliable, polite access to SEC data with caching.

- [x] HTTP client with `User-Agent` from `SEC_USER_AGENT` (clear error if not set)
- [x] Rate limiter (max. 10 requests/second) and retry with backoff on 429/5xx
- [x] Disk cache with configurable TTL
- [x] `ticker_to_cik()` via `company_tickers.json`
- [x] `get_submissions(cik)` for the filing index incl. 8-K items
- [x] `get_company_facts(cik)` for all XBRL facts
- [x] Recorded responses as test fixtures (no live calls in CI)
- [x] Tests: unknown ticker, rate-limit behavior, cache hit

**Done when:** A short script returns CIK, name and the list of recent 10-Ks for AAPL, and the second call is served from cache.

```text
Step 2: SEC EDGAR provider (see ROADMAP.md and the SEC rules in CLAUDE.md).
Plan first, including how you handle rate limiting, caching and test fixtures.
```

---

### Step 3: Financial statement normalization
**Goal:** Turn raw XBRL facts into comparable balance sheet, income statement and cash flow statement.

- [x] Mapping table: standard field → list of possible US-GAAP concepts in priority order (e.g. revenue: `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`)
- [x] Cleanly separate annual (10-K, `FY`) and quarterly (10-Q) values; handle duplicates and later corrections (amendments) correctly
- [x] TTM calculation (last 4 quarters) where it makes sense
- [x] Result: a `Statements` object with a time series per field, each value with concept and accession number as source
- [x] Tests with fixtures from 3 companies that use different concepts
- [x] Reconciliation: revenue, net income and total assets for AAPL and MSFT checked against the actual 10-K

**Done when:** The core figures of the last 3 fiscal years for AAPL and MSFT match the 10-Ks exactly.

```text
Step 3: financial statement normalization (see ROADMAP.md).
Plan first: mapping strategy, handling of amendments and duplicates, TTM. At the end, show me the reconciliation against the actual 10-Ks.
```

---

### Step 4: Metrics engine
**Goal:** The metrics from my old `Stock-Forensic` notebook, rebuilt cleanly, tested and with sources.

- [x] **Profitability:** gross, operating and net margin, ROE, ROA, ROIC
- [x] **Liquidity & leverage:** current ratio, quick ratio, debt/equity, interest coverage, net cash
- [x] **Cash flow quality:** free cash flow, FCF margin, cash conversion (operating cash flow ÷ net income)
- [x] **Growth:** revenue and earnings growth (YoY, 3-year CAGR), growth consistency
- [x] **Capital allocation:** payout ratio, buybacks, dilution (share count YoY), R&D ratio
- [x] Every metric returns a `Value` with the formula in `note` and its input values as source
- [x] Tests with hand-calculated reference values

**Done when:** All metrics are computed for AAPL and spot checks recalculated by hand match.

```text
Step 4: metrics engine (see ROADMAP.md).
Use my old Stock-Forensic notebook as a reference (function names like compute_profitability_metrics, compute_liquidity_metrics, compute_growth_consistency ...), but rebuild it cleanly and with provenance.
Plan first.
```

---

### Step 5: Forensic scores
**Goal:** The core of the project: a systematic search for warning signs in the numbers.

- [x] **Beneish M-Score** (8 variables: DSRI, GMI, AQI, SGI, DEPI, SGAI, TATA, LVGI; threshold −1.78)
- [x] **Altman Z-Score** (original formula for manufacturers, Z'' variant for non-manufacturers; output the zones clearly)
- [x] **Piotroski F-Score** (9 criteria, each reported individually)
- [x] **Accruals ratio** (earnings quality)
- [x] Every score returns: total, all components, thresholds, interpretation, limitations
- [x] Industry exclusions: banks, insurers, REITs → "not applicable" with reason (via SIC code)
- [x] Tests: one fully hand-calculated example per score, with the calculation documented as a comment in the test

**Done when:** All three scores are plausible for AAPL and MSFT, all components are visible in the output, and a bank is correctly marked "not applicable".

```text
Step 5: forensic scores (see ROADMAP.md).
Plan first. For each score: formula, source of the formula, thresholds, limitations. Tests with hand-calculated reference values.
```

---

### Step 6: Red flags from SEC filings
**Goal:** Warning signs that are not in the numbers but in the filings.

- [x] **Auditor change:** 8-K with Item 4.01
- [x] **Restatements:** 8-K with Item 4.02 (previously issued financial statements no longer reliable)
- [x] **Late filings:** NT 10-K / NT 10-Q
- [x] **Going-concern warnings** in the 10-K (via EDGAR full-text search or text search in the document; check feasibility first)
- [x] **Frequent 10-K/A amendments**
- [x] Every flag with date, form type and link to the original filing
- [x] Tests with fixtures (at least one real case per flag type)

**Done when:** For a company with a known auditor change or restatement, the flag appears including a link to the filing.

```text
Step 6: red flags from SEC filings (see ROADMAP.md).
First check the most reliable way to detect going-concern warnings, and suggest test companies with real cases. Then present the plan.
```

---

### Step 7: Insider activity (Form 4)
**Goal:** Are the people with the most information buying or selling?

- [x] Fetch Form 4 filings for the last 12 months and parse the XML
- [x] Distinguish transaction codes: open-market purchase (P), sale (S), grant (A), option exercise (M), tax withholding (F)
- [x] Account for 10b5-1 plans (pre-scheduled sales)
- [x] Aggregation: net buys/sells by person and role (CEO, CFO, director), cluster buys (several insiders within a short period)
- [x] Tests with fixtures

**Done when:** For AAPL, the insider overview for the last 12 months appears with links to the Form 4 filings.

```text
Step 7: insider activity from Form 4 (see ROADMAP.md).
Plan first. Important: clearly separate real open-market purchases from grants and tax withholding.
```

---

### Step 8: Report + CLI (MVP done 🎉)
**Goal:** One command, one report that people actually enjoy reading.

- [x] Pipeline `analyze(ticker) -> ForensicReport` (one JSON object with everything)
- [x] HTML report as a single file: summary with traffic lights (green/yellow/red) at the top, then scores, metrics, red flags, insiders, list of sources
- [x] Every number in the report links to or names its source
- [x] Markdown and JSON export
- [x] CLI: `forensics AAPL`, `--format html|md|json`, `--out`, `--no-cache`
- [x] Readable progress output with `rich`
- [x] Not-financial-advice note in the report
- [x] End-to-end test with fixtures

**Done when:** `uv run forensics AAPL` produces an HTML report in under a minute that you understand without any explanation.

```text
Step 8: report + CLI (see ROADMAP.md). This completes the MVP.
Plan first, including a sketch of the report layout (sections, traffic-light logic). Then wait for approval.
```

---

## Phase 2: Extensions

### Step 9: Market data & technicals (optional)
**Goal:** Add price context without breaking the core principle "works without keys".

- [ ] Pluggable price provider (interface), implementations: `yfinance` (optional extra), Alpha Vantage (with key)
- [ ] Market cap for Altman Z (original) and valuation metrics (P/E, EV/EBITDA, FCF yield)
- [ ] Technicals: momentum, volatility, max drawdown, beta vs. S&P 500, distance to 52-week high/low
- [ ] Without a price provider: these sections show "n/a" with a note

**Done when:** With the extra installed, the valuation and technicals sections are filled, and without it everything still works.

```text
Step 9: market data & technicals (see ROADMAP.md). Plan first, including the provider interface.
```

---

### Step 10: MCP server
**Goal:** Agents like Claude can use the analysis directly. This is also a distribution channel through MCP directories.

- [ ] MCP server with FastMCP: tools `analyze_stock`, `get_forensic_scores`, `get_red_flags`, `get_insider_activity`, `get_metrics`
- [ ] Compact tool responses (not the whole JSON at once), with sources
- [ ] Start command `forensics-mcp`, configuration examples for Claude Desktop / Claude Code in the README
- [ ] Test with the MCP Inspector

**Done when:** Asked "Run a forensic analysis of NVDA", Claude in Claude Desktop calls the tools and answers with sources.

```text
Step 10: MCP server (see ROADMAP.md). Use the official MCP Python SDK (FastMCP). Plan first.
```

---

### Step 11: LLM summary with number guard (optional)
**Goal:** A readable interpretation, without invented numbers.

- [ ] Optional extra for Anthropic or OpenAI, key from the environment
- [ ] The LLM only receives the finished report JSON and writes the summary, the strongest warning signs and open questions
- [ ] **Number guard:** every number in the LLM text is automatically checked against the JSON; unknown numbers → text is rejected and regenerated, or the passage is flagged
- [ ] Without a key: the section is omitted, everything else unchanged
- [ ] Tests for the number guard (incl. deliberately wrong numbers)

**Done when:** The guard reliably catches a deliberately manipulated text.

```text
Step 11: LLM summary with number guard (see ROADMAP.md). Plan first, especially for the number guard.
```

---

### Step 12: News & earnings calls (optional)
**Goal:** The logic from my `DeepStockResearch` notebook as a clean module.

- [ ] News sentiment via Alpha Vantage (with key), aggregated over 30/90 days
- [ ] Earnings call transcripts (FMP with key): tone, evasive answers, guidance changes
- [ ] Earnings history: surprises vs. expectations, price reaction
- [ ] Everything clearly marked "optional, requires key"

**Done when:** With keys, the sections appear; without keys, everything runs as before.

```text
Step 12: news & earnings calls (see ROADMAP.md). Use my DeepStockResearch_FINAL notebook as a reference (fetch_news_sentiment, fetch_transcript, analyze_transcript ...). Plan first.
```

---

## Phase 3: Launch

### Step 13: Docs, demo & release
**Goal:** The repo convinces within 10 seconds.

- [ ] README: one-line pitch, demo GIF at the top, quickstart in 3 commands, sample report, methodology of the scores with sources, limitations, not financial advice
- [ ] Sample reports for 2–3 well-known companies in `examples/` (or as a GitHub Pages demo)
- [ ] `docs/methodology.md`: every formula, every threshold, every source
- [ ] PyPI release (`uv build`, `uv publish`) so that `pipx install stock-forensics` works
- [ ] GitHub: topics (`sec-edgar`, `financial-analysis`, `forensic-accounting`, `mcp-server`, `beneish`, `altman-z-score`, `piotroski`), social preview image, description
- [ ] Listings: MCP registry & directories, relevant awesome lists (e.g. quant/finance, MCP)
- [ ] Add the project to my GitHub profile README

**Done when:** A stranger gets from the README to their first own report in under 2 minutes.

```text
Step 13: docs, demo & release (see ROADMAP.md). Plan first, including the README structure.
```

---

## Ideas for later (not part of the plan)
- Comparison with industry peers (same SIC code)
- Watchlist mode: several tickers, changes since the last run
- Historical review: would the scores have caught past scandals? (as a blog post / notebook)
- Connection to `deepstock-bot` (insider alerts) and `capitol-street-bets`
