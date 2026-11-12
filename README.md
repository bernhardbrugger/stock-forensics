# stock-forensics

Forensic check for US stocks: accounting red flags, forensic scores (Beneish, Altman, Piotroski), SEC filing flags and insider activity. Every number carries its source.

> **Work in progress.** Nothing here is usable yet. See [ROADMAP.md](ROADMAP.md) for the plan.

## Docs

- [Data model](docs/data-model.md): every number is a `Value` with unit, period and source
- [SEC EDGAR provider](docs/sec-edgar.md): setup, usage, rate limit, cache
- [Statement normalization](docs/normalization.md): standard fields, quarters, TTM, reconciliation with the 10-K
- [Metrics](docs/metrics.md): 25 ratios with formulas, inputs and explicit gaps
- [Forensic scores](docs/scores.md): Beneish, Altman, Piotroski, accruals ratio, with thresholds and limitations
- [Red flags from SEC filings](docs/red-flags.md): auditor changes, restatements, late filings, going concern, amendments
- [Insider activity](docs/insiders.md): Form 4 purchases and sales, 10b5-1 plans, cluster buys

## Not financial advice

This project flags risks in public data. It does not issue buy or sell recommendations and is not financial advice. Use it as a starting point for your own research.

## License

[MIT](LICENSE)