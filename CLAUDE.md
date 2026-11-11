# CLAUDE.md: stock-forensics

## What this project is

`stock-forensics` runs a full forensic check on a single US stock: fundamentals, accounting red flags, SEC filings, insider activity, market data and (optionally) news and earnings calls. The output is one report that a human can trust.

Ships as:
- a Python library (`stock_forensics`)
- a CLI (`forensics NVDA`)
- an MCP server (`forensics-mcp`) so AI agents can call the same engine

## Core principles (non-negotiable)

1. **Numbers come from code, never from an LLM.** Every metric is computed deterministically. The LLM layer (optional) only writes narrative from the already computed JSON and must not introduce any number that is not in that JSON.
2. **Every number has provenance.** Each value carries its source: provider, endpoint/filing, accession number or form type, fiscal period, XBRL concept. A user must be able to verify any figure.
3. **Works without paid API keys.** SEC EDGAR is the default data source (free, no key). Alpha Vantage, FMP, OpenAI/Anthropic are optional extras that only enrich the report.
4. **Missing data is explicit, not silent.** If a metric cannot be computed, return `None` with a reason ("concept not reported", "needs 2 fiscal years") and show it as "n/a" in the report. Never fill gaps with guesses or zeros.
5. **Statistics before storytelling.** Scores show their inputs, thresholds and known limitations (e.g. Altman Z is not meaningful for banks).
6. **Not financial advice.** The report and README say so clearly. The tool flags risks, it does not issue buy/sell calls.

## Tech stack

- Python 3.12, managed with `uv`
- `httpx` (HTTP), `pydantic` v2 (models), `pandas` (time series)
- `typer` + `rich` (CLI), `jinja2` (HTML/Markdown report, single self-contained HTML file)
- `mcp` Python SDK (FastMCP) for the MCP server
- `pytest`, `pytest-recording`/VCR-style fixtures, `ruff`, `mypy --strict` on `src/`
- Optional extras: `yfinance` (prices), `alpha-vantage` / FMP via plain `httpx`, `anthropic` / `openai` (narrative)

## Repo layout

```
src/stock_forensics/
  providers/      # data sources: sec_edgar.py, prices.py, alphavantage.py, fmp.py
  normalize/      # XBRL concept mapping -> standardized statements
  metrics/        # deterministic metrics (profitability, liquidity, growth, ...)
  scores/         # beneish.py, altman.py, piotroski.py, accruals.py
  flags/          # filing-based red flags (auditor change, late filing, restatement, going concern)
  insiders/       # Form 4 parsing and aggregation
  report/         # jinja2 templates + renderer
  narrative/      # optional LLM layer with number-guard
  models.py       # pydantic models incl. Value(value, unit, source, period, note)
  cli.py
  mcp_server.py
tests/
  fixtures/       # recorded API responses, never live calls in CI
docs/
```

## SEC EDGAR rules

- Always send a descriptive `User-Agent` with contact info (from env `SEC_USER_AGENT`, e.g. `stock-forensics hello.bernhard@proton.me`). Requests without it get blocked.
- Stay under 10 requests per second. Use a shared rate limiter.
- Cache responses on disk (default `~/.cache/stock-forensics`, TTL configurable). Filings for past periods never change.
- Key endpoints: `https://www.sec.gov/files/company_tickers.json` (ticker -> CIK), `https://data.sec.gov/submissions/CIK##########.json` (filing index), `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (all XBRL facts).

## How we work (workflow for every step in ROADMAP.md)

1. **Plan first.** Before writing code for a step, present a short plan (files, functions, tests) and wait for approval.
2. **Small steps.** One ROADMAP step per session/PR. Do not start the next step unasked.
3. **Tests with every change.** New logic needs unit tests. Scores need tests against hand-calculated reference values (document the calculation in the test).
4. **Verify against real data**, not just code review: run the CLI on 3 reference tickers (`AAPL`, `MSFT`, plus one company with known problems) and sanity-check the numbers against the actual 10-K.
5. **Quality gate before every commit:** `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest` must pass.
6. **Commit only after explicit confirmation.** Use conventional commits (`feat:`, `fix:`, `test:`, `docs:`).
7. **Update ROADMAP.md**: tick off finished todos in the same commit.
8. **Secrets** only from environment variables / `.env` (gitignored). Never hardcode keys, never print them, never commit `.env`.

## Definition of done for any feature

- Code + tests + type hints
- Provenance on every new value
- Missing-data path tested
- Docs updated (README section or `docs/`)
- ROADMAP checkbox ticked
