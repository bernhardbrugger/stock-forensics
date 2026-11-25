# Market data: valuation and technicals

Market data is optional. `forensics AAPL` works from SEC filings alone; with a price provider the
report gains a **Market data** section (valuation and technicals), and manufacturers get the
original Altman Z instead of the book-value Z', because the market value of equity is then known.

```bash
uv sync --extra prices                     # yfinance, no key needed
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
| Alpha Vantage | `ALPHAVANTAGE_API_KEY` | `full` (20+ years) if the key allows it, else the latest 100 trading days | `TIME_SERIES_DAILY`. Close as traded (not split-adjusted). One request per second is kept as a pause; every response is cached for 12 hours, which also saves the daily quota of a free key (25 requests per day). Index proxy: `SPY`. |

`ALPHAVANTAGE_OUTPUTSIZE=auto|full|compact` (default `auto`) decides how much history is asked
for. `auto` tries `full` and falls back to `compact` if the service answers that `full` is a
premium feature (and then stops trying for the rest of the run). A rate-limit answer is something
else: the provider waits and tries the same size once more, and never concludes from it that the
key is limited.

With only 100 days of prices (a free key), everything that needs more is a gap with the reason
("needs prices from ..., the history starts ..."): the 200-day average and the cross, weekly RSI
and MACD, the chart, 12-month momentum and relative strength, volatility, drawdowns, beta, and the
market value of older fiscal years. The latest market value, P/E, EV/EBITDA, FCF yield, the 50-day
average and 3 and 6 month momentum still work.

The close of `TIME_SERIES_DAILY` is the raw, as-traded price, so the series is treated as *not*
split-adjusted. This was checked live: Apple closed at USD 499.23 on 2020-08-28 and USD 129.04 on
2020-08-31, the day of the 4-for-1 split. The API key is only sent as the query parameter the
service requires and never appears in the cache key, in an error message or in the report.

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

## Trend indicators

For long-term investors, not for day trading: weekly and monthly horizons, computed from the daily
closes already loaded (no extra requests). They **look back**: they describe what the price did
and do not predict what it will do. Every reading is worded that way ("the long-term trend points
up", never a buy or sell word).

| Indicator | Formula | Plain-words reading |
| --- | --- | --- |
| Price vs. 50-day / 200-day average | close / simple average of the last 50 (200) closes - 1 | within 2 %: "right at its average"; otherwise "N % above (below) ...: the trend points up (down)" |
| 50-day vs. 200-day average | average of 50 closes / average of 200 closes - 1 | the shorter trend is stronger or weaker than the longer one |
| Last cross | days since the 50-day average last moved above (golden) or below (death) the 200-day average | names the date and says it looks back; no cross since both averages exist is a gap that says where they stand |
| RSI (14 weeks) | Wilder RSI on weekly closes (last close of each calendar week, the newest possibly unfinished week included) | 70 or more "stretched upward", 30 or less "stretched downward", else "middle range" (common rules of thumb) |
| MACD (weekly, 12/26/9) | EMA(12) - EMA(26) of weekly closes, signal EMA(9), histogram = MACD - signal | momentum up / improving / fading / down, from the histogram and the sign of the MACD |
| Relative strength vs. S&P 500, 6 and 12 months | (1 + stock return) / (1 + index return) - 1 | "N % better (worse) than the S&P 500", within 1 % "about in line" |

Details: the EMAs start with the first close (`alpha = 2 / (n + 1)`), RSI starts with the simple
average of the first 14 changes, and all loaded history is used as warm-up. At least 52 weekly
closes are required, otherwise RSI and MACD are gaps ("needs 52 weekly closes (about a year) to be
warmed up; have 40"). The 200-day average needs 200 daily closes. A series that is not
split-adjusted refuses to run across a jump that looks like a split.

The report shows the indicators in a **Trend** table with a plain-words column, and a **price
chart** as inline SVG (weekly closes of the last three years, 50 and 200-day averages, and the
stretches in which the price was at least 10 % below its highest weekly close of the period
shaded). The chart has a title and a text description for screen readers and needs no scripts or
files. Its points (weekly close and both averages) are part of the report data with their source,
so the picture can be checked. The Markdown report has the table, not the picture.

## Verification

* Every technical is tested against a hand-calculated reference (the working is in the test):
  momentum on a linear series, volatility of alternating moves, drawdown 120 to 60 = -50 %,
  beta of exactly 2 for a series that moves twice as much as the index.
* The trend indicators are tested on series small enough to follow on paper: weekly RSI with
  Wilder smoothing on closes 10, 11, 10, 12, 11, 13 with period 3 is exactly 75 (RS = 3), the MACD
  of 10, 12, 11, 13 with periods 2/3/2 has line 10/27, signal 8/27 and histogram 2/27, and a stock
  that gains 20 % against an index that gains 10 % has a relative strength of 9.09 %.
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

Checks of the trend indicators and the price sources (November 2026):

* **Indicators against an independent implementation.** For AAPL, MSFT, NVDA and MU the values were
  recomputed with pandas (`rolling`, `ewm`, `resample`) on the same yfinance closes. Moving
  average distances and relative strength agree to machine precision (about 1e-16), the weekly
  MACD to 4e-5 (our rounding to four places), the weekly RSI to 4e-3 (rounding to two places and
  a slightly different start of the smoothing).
* **A second price source.** Alpha Vantage and Yahoo give the same closes for AAPL on all 97
  common days (July to November 2026), none differs by more than one cent.
* **Alpha Vantage live.** `full` history works with the key that was used (6,802 trading days
  back to 1999-11-01), `compact` returns 100 days, the as-traded close was confirmed at Apple's
  2020 split, and a burst of requests triggers the one-per-second limit, which the provider now
  respects.
* The chart's geometry was checked in a browser (size, legend outside the plot area, shaded
  stretches). A picture of it could not be taken in the analysis environment, so the visual
  impression has not been checked by a person yet.
