# Market data: valuation and technicals

Market data is optional. `forensics AAPL` works from SEC filings alone; with a price provider the
report gains a **Market data** section (valuation and technicals), and manufacturers get the
original Altman Z instead of the book-value Z', because the market value of equity is then known.

```bash
pip install 'stock-forensics[prices]'      # yfinance, no key needed
uv run forensics AAPL                      # --prices auto: yfinance if installed
uv run forensics AAPL --prices none        # SEC data only
export ALPHAVANTAGE_API_KEY=...            # Alpha Vantage instead (see limits below)
uv run forensics AAPL --prices alphavantage
```

`--prices auto` (the default) takes yfinance if it is installed, else Alpha Vantage if
`ALPHAVANTAGE_API_KEY` is set, else runs without prices and says so in the report. An explicit
`--prices yfinance` or `--prices alphavantage` that cannot be used (package missing, no key) stops
with exit code 2 instead of silently doing something else. Only the ticker symbol is sent to the
price provider.

There is **no traffic light** on valuation or technicals. Whether a price is high or low is not a
forensic finding, and a light on it would read like a buy or sell call. The numbers are shown with
formula, inputs and basis, and the overall light is unchanged by them (except that Altman Z may
move from the Z' to the Z variant, which is a statement about the score, not about the price).

## Providers

| Provider | Key | History | Notes |
| --- | --- | --- | --- |
| `yfinance` (extra `prices`) | none | years | Unofficial client of Yahoo Finance. Its availability and terms are outside this project's control. Close adjusted for splits, not for dividends. Index: `^GSPC`. |
| Alpha Vantage | `ALPHAVANTAGE_API_KEY` | free key: last 100 trading days | `TIME_SERIES_DAILY`. Per the Alpha Vantage documentation only `outputsize=compact` (100 data points) is free; `full` and the adjusted endpoint are premium. The free quota is small (25 requests per day according to the provider's published limits at the time of writing), so responses are cached for 12 hours. Index proxy: `SPY`. |

With a free Alpha Vantage key, everything that needs more than about five months of prices is a
gap with the reason ("needs prices from ..., the history starts ..."): 12-month momentum,
volatility, drawdowns, beta, and the market value of older fiscal years. The latest market value,
P/E, EV/EBITDA, FCF yield, 3 and 6 month momentum still work.

The Alpha Vantage close is documented as the raw, as-traded price, so it is treated as *not*
split-adjusted. The provider has not been tried live by the project (no key was available); it is
covered by tests with recorded-style responses only. The API key is only sent as the query
parameter the service requires and never appears in the cache key, in an error message or in the
report.

The interface is `stock_forensics.providers.prices.PriceProvider`: one method,
`daily_prices(ticker, start, end) -> PriceSeries`, plus the `benchmark` ticker. A new provider
(for example FMP) only has to implement that.

## Splits

SEC share counts are reported as of the balance sheet date. A close that Yahoo adjusts for a
*later* split is not comparable with such a count: Nvidia's January 2024 balance sheet counts
shares before the June 2024 10-for-1 split, the adjusted price is a tenth of what was paid.
`PriceSeries.split_adjusted` and `PriceSeries.splits` therefore travel with the prices, and the
market value multiplies the share count by the splits that came after the balance sheet date
(the note says so: `x 10 (splits after ...)`). A series that is not split-adjusted needs no
factor, but its technicals refuse to run across a jump that looks like a split (a daily change of
-30 % or less, or +40 % or more) and say why.

## Valuation

| Metric | Formula |
| --- | --- |
| Market value of equity | latest close x latest reported shares outstanding (balance sheet of the newest quarter or year) |
| Enterprise value | market value - net cash (the `net_cash` metric of the latest fiscal year) |
| P/E | market value / net income |
| EV/EBITDA | enterprise value / (operating income + depreciation and amortization) |
| FCF yield | (operating cash flow - capital expenditures) / market value |

Flows (net income, EBITDA parts, cash flows) are trailing twelve months when all their inputs have
one, otherwise the latest fiscal year; the basis is written next to the number. A loss or a
non-positive EBITDA gives a gap ("net income is not positive"), never a negative multiple.

Known limitations: the net cash of the EV is from the last fiscal year end, not the newest
quarter. Companies with several share classes report one total or one class, and the market value
can then be off; the note shows the share count that was used. The market value by fiscal year
uses the shares of that balance sheet, not the cover page count of the 10-K.

## Technicals

All based on closes (not intraday highs and lows), price returns without dividends. Calendar
windows are measured back from the latest close; a window the history does not cover is a gap.

| Metric | Formula |
| --- | --- |
| Return 3, 6, 12 months | close / close N months earlier - 1 |
| Return 12-1 | close 1 month ago / close 12 months ago - 1 |
| Volatility, 1 year | sample standard deviation of the last 252 daily log returns x sqrt(252) |
| Maximum drawdown, 1 and 3 years | lowest value of close / highest earlier close in the window - 1 |
| Beta, 2 years | covariance of weekly returns with the index / variance of the index weekly returns (at least 52 shared weeks) |
| Distance to 52-week high / low | close / highest (lowest) close of 365 days - 1 |

If the latest close is more than 10 days older than the analysis date, every market number is a
gap ("the latest close is from ..., N days before ...").

## Verification

* Every technical is tested against a hand-calculated reference (the working is in the test):
  momentum on a linear series, volatility of alternating moves, drawdown 120 to 60 = -50 %,
  beta of exactly 2 for a series that moves twice as much as the index.
* Market values are checked against the recorded Apple statements (price x shares), including a
  4-for-1 split after the balance sheet date.
* All price tests are offline: yfinance is stubbed with a real `pandas` frame, Alpha Vantage runs
  against a mock transport. CI never calls a price service.

Live checks with yfinance on real filings (November 2026), by order of magnitude against
what was publicly known at the time:

| Ticker | What was checked | Result |
| --- | --- | --- |
| AAPL | latest market value = close x shares | USD 5.2 trillion = USD 356.27 x about 14.6 billion shares; Altman moves from Z' (grey) to Z (safe) |
| MSFT | market value at fiscal year ends | June 2024 USD 3.32 trillion, June 2025 USD 3.70 trillion (close x about 7.43 billion shares) |
| NVDA | **split handling** (10-for-1 in June 2024, 4-for-1 in July 2021) | January 2021 USD 322 billion, January 2022 USD 572 billion, January 2023 USD 502 billion, January 2024 USD 1.50 trillion |
| JPM | bank | P/E works; EV/EBITDA and FCF yield are gaps with the reason (no capex, no net cash components) |

The NVDA check found a real bug in the first version: the statements take the newest filing for
every period, and the 10-K for fiscal 2025 reports the January 2024 share count already
multiplied by the June 2024 split. Multiplying it again made the market value ten times too
high (USD 15 trillion). The rule is now "only splits after the filing the share count comes
from", which is why `Source` has a `filed` date. The tests contain this case.

Not verified: a second, independent price source could not be reached from the analysis
environment, so the prices themselves are Yahoo's. Alpha Vantage was not tried live (no key).
