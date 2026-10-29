# Optional Yahoo Finance market observations

The source checkout supplies `equity_prices` and `equity_actions` through the shared CLI, Python SDK, MCP and HTTP research service. Daily prices cover US/Tokyo securities and the explicitly listed reference indexes, FX pairs, cryptocurrencies and futures below. `equity_actions` is restricted to securities. `japan_equity_history` combines prices and actions against one cached source response. These capabilities do not change the existing `v0.7.0` release assets.

This is an **optional secondary source for personal use**, disabled by default. It uses the international `query1.finance.yahoo.com` chart endpoint, not Yahoo! JAPAN pages. Yahoo does not endorse Atlas or guarantee this unofficial API. The software license grants no license to republish or commercially redistribute Yahoo/provider data. Review the [Yahoo terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) and the [yfinance project's data-use notice](https://github.com/ranaroussi/yfinance#legal-stuff) for intended use and separate data rights.

## Enable and retrieve

Set `ATLAS_ENABLE_YAHOO_FINANCE=1` in the operator's process environment for personal use under the source terms. An MCP/HTTP query cannot enable it. Default diagnostics and source probes exclude this optional source; explicitly selecting `yahoo_finance` reports its configuration and permits the opt-in probe. Do not enable it on a shared public data-redistribution service.

```sh
# Fictional observations; no network or source setting required.
uv run --offline atlas research get equity_prices --identifier AAPL --provider fixture --json
uv run --offline atlas research preset japan_equity_history --identifier 7203.T --provider fixture --json

# Actual source access uses normal outbound HTTPS.
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_prices --identifier AAPL --start-date 2026-09-01 --end-date 2026-10-23 --json
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_prices --identifier '^GSPC' --start-date 2026-09-01 --end-date 2026-10-23 --json
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_actions --identifier 7203.T --start-date 2024-09-01 --end-date 2024-10-31 --json
```

```python
from atlas_terminal.client import Atlas

# Enable the optional source in this process's environment before a public read.
with Atlas("public") as atlas:
    result = atlas.get_research(
        {
            "dataset": "equity_prices",
            "identifier": "SPY",
            "start_date": "2026-09-01",
            "end_date": "2026-10-23",
            "response": {"view": "compact"},
        }
    )
```

## Supported identities

| Instruments         | Explicit symbols                                                                            | Identity and price units                                                              |
| ------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Tokyo equities/ETFs | Four-character `.T` codes beginning with a digit, such as `7203.T`, `6758.T`, `130A.T`      | JPY, Tokyo, JPX/TYO; JPY per share                                                    |
| US equities/ETFs    | US ticker symbols, including class suffixes such as `BRK-B`; examples `AAPL`, `MSFT`, `SPY` | USD, New York; supported Nasdaq/NYSE/Arca/American/BATS exchange codes; USD per share |
| Indexes             | `^GSPC`, `^DJI`, `^IXIC`, `^RUT`, `^VIX`, `^N225`                                           | Explicit source index/exchange/timezone; index points                                 |
| FX                  | `EURUSD=X`, `GBPUSD=X`, `AUDUSD=X`, `NZDUSD=X`, `USDJPY=X`, `USDCHF=X`, `USDCAD=X`          | London source dates; explicit quote currency per base currency                        |
| Crypto              | `BTC-USD`, `ETH-USD`                                                                        | UTC source dates; USD per BTC/ETH                                                     |
| Commodity futures   | `GC=F`, `SI=F`, `CL=F`, `NG=F`                                                              | New York source dates; USD per source quote unit                                      |
| Index futures       | `ES=F`, `NQ=F`                                                                              | New York source dates; index points                                                   |

The returned source symbol, currency, exchange/timezone, instrument type and daily frequency must agree with the fixed instrument policy. `USDCAD=X` additionally accepts Yahoo's explicit `CAD=X` alias; `instrument.symbol` preserves the requested identifier and `instrument.source_symbol` preserves the original metadata identity. No other symbol rewriting occurs. Bare Japanese codes, unsupported markets, arbitrary URLs and intraday intervals are rejected. A Yahoo `EQUITY` label does not establish common-share class. Current security classifications and historical market capitalization are not inferred.

Without dates, requests select the latest month. Supply both inclusive dates for up to ten calendar years. They become Unix timestamp boundaries at midnight in the declared source timezone, accounting for daylight saving, with the upper boundary moved to the following day because Yahoo uses an exclusive end. These are observation filters, not historical availability dates. `limit` is 1–500; inspect truncation for longer histories. The Japanese preset selects at most 100 prices and 100 actions per item.

## Values, dates and adjustment conventions

Security OHLC values are **already split-adjusted**. They are not a verified unadjusted trade-price history. Adjusted close additionally reflects dividends. Records preserve `price_basis="yahoo_split_adjusted"` and `adjusted_close_basis="yahoo_split_and_dividend_adjusted"`, including under field projection. The [yfinance adjustment guide](https://ranaroussi.github.io/yfinance/advanced/price_adjustment.html) explains this distinction; Atlas preserves chart JSON without adding a yfinance runtime dependency.

Reference instruments instead declare `price_basis="yahoo_reported_daily"` and `adjusted_close_basis="yahoo_reported_adjusted_close"`. Their adjustment semantics are not inferred. Futures represent Yahoo's changing front-contract quotations: no roll adjustment, continuous total-return series, contract multiplier, settlement status or expiry is inferred. Futures can have zero or negative prices and retain them exactly. Percentage-return calculations require a positive price basis and cannot treat a contract roll as an economic investment return.

Security volume is in shares and futures volume is in contracts. Index/FX/crypto volume is labeled `source_reported_volume`; it is not reinterpreted as traded shares, currency units or coins. Source zero volume remains zero and is distinct from null.

Numeric JSON tokens are parsed with Decimal and returned as decimal strings without binary-float conversion or presumed price-tick rounding. Missing fields remain null; absent adjusted close is never replaced by close. Invalid identity, array lengths, duplicates, nonpositive non-futures prices, negative/fractional volume and conflicting split terms fail. Missing dates are not replaced by holiday or forward-filled prices. An empty security action history succeeds with its original evidence.

Some Yahoo FX bars report an open or close outside their reported low/high. Atlas retains every original number and marks those rows `status="ambiguous"`, `ohlc_consistent=false` and `quality_flags=["ohlc_bounds_conflict"]`. Coverage reports `inconsistent_ohlc_records` and `partial=true`. `requirements.allow_missing=false` rejects these ambiguous results. No floating-point tolerance or price repair is used. Other instrument classes continue to reject inconsistent OHLC bounds.

`date` is the timestamp's **declared source-local date**. `observation_at` retains the source timestamp, not a verified market-close or publication time. `published_at` stays null. For securities/indexes, a current-day bar is excluded until the source's same-day regular-session end has elapsed at retrieval. Without that evidence the bar is excluded. FX/crypto/futures current-day bars are always excluded until their source-local day has ended, since overnight/continuous sessions need different close conventions. `coverage.excluded_unconfirmed_session_rows` records exclusions. Source dates do not establish a verified exchange calendar, and completed bars may still be revised.

Reported security dividend amounts are not rescaled or reconstructed as original pre-split cash amounts. Their currency follows the chart quote metadata (`currency_basis="chart_quote_currency"`), not independently verified payment currency. Event dates are not payment dates. Future events are excluded. Split coefficients can be fractional; supplied ratio labels must be exactly consistent. Missing events do not establish that an issuer never split or paid dividends. Unknown event types remain listed in coverage and the raw source.

## Evidence and access

Every result retains the original source-body SHA-256, retrieval time and URL. Price `field_sources` contain JSON pointers for each OHLC/volume/adjusted-close scalar and timestamp. A row's main `source` resolves to close; adjusted-close provenance remains separate. Actions identify original event objects and instrument metadata has its own pointer. Compact evidence resolves through `Atlas.get_evidence` / `atlas_get_evidence`.

Matching prices/actions use one canonical cache key and the same bytes. Public reads use the bounded HTTP transport, 15-minute cache, one-request-per-second limiter and finite retries. Authentication failures do not collect cookies/crumbs, change hosts, use paid fallbacks or return fictional data. Upstream rate limits and endpoint changes can make retrieval unavailable. No intraday/real-time feed, order book, option chain, delisting-complete universe or publisher PIT vintages are supplied.

## Time-series risk

The named Yahoo connector accepts 1–9 securities and a separate source-identified ETF factor, all from one supported market/currency: US/USD or Tokyo/JPY. It requires personal-use acknowledgement, source enablement and `allow_network=true`. Index, FX, crypto and futures symbols are excluded, as are mixed markets/currencies. An ETF factor is a proxy, not an official market factor. Yahoo `EQUITY` observations are labeled `other` in the time-series universe because the source does not verify common-share class.

The connector uses 61–500 exact common price dates with no gap filling. Adjusted-close ratios are rounded half-even to 18 decimal places. Return boundaries follow the end of each declared source-local calendar day, not verified exchange closing times; the selected day must have ended. Availability follows the latest retrieval among all input sources, and the fit's `as_of` must be selected after capture. Per-price evidence remains in the connection receipt. See [configuration and timing](data-connections.md#optional-yahoo-time-series-connection).

For cross-sectional risk, supply actual historical capitalization or a separate verified **unadjusted** price/share-count basis. Do not multiply Yahoo's split-adjusted close by raw historical shares. Current share counts and industry labels do not establish historical values.

## Verification

Five bundled Yahoo-shaped files are fictional: two Tokyo and three US examples (`AAPL`, `MSFT`, `SPY`). Offline tests cover decimals and locators; strict source identity; source-local inclusive dates and US daylight saving; completed-session filtering; zero/null prices and volume; empty actions and splits; FX ambiguity; signed futures; endpoint allowlisting; operator-only enablement; evidence/quality/shared interfaces; and same-market USD/JPY risk inputs. Fictional checks do not establish live availability.

The Tokyo live workflow remains available:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_yahoo_live.py --allow-network --output build/yahoo-live
```

Expanded private verification uses:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_market_prices_live.py --allow-network --output build/market-prices-live
```

On 2026-10-30 JST, expanded verification fetched 25 symbols for 2026-09-01–2026-10-23, plus the 2020-04-17–2020-04-23 `CL=F` window. All 990 normalized price rows and 6,930 numeric/timestamp locators matched the retained raw source. Six FX results contained ten conflicting OHLC bars, retained as ambiguous. The verification therefore reports 21 `PASS` and six `PARTIAL` checks and exits nonzero to expose these source-quality limits. One passing check was a USD time-series risk execution using 203 observed AAPL/MSFT/SPY returns and a 120-session estimation window. Another confirmed the negative 2020-04-20 crude-oil close without rounding or repair.

Source bytes, normalized observations and risk artifacts stay in ignored private output/cache directories. No real Yahoo data is bundled or published. Historical Tokyo split windows, ETF reads, one null adjusted close and a 120-session JPY risk execution were checked separately; zero Tokyo volume was not observed. [Verification scope](verification.md#data-integrity-and-japanese-company-extensions).

## Explicit intraday bars

The separate [`intraday_prices` dataset](intraday-prices.md) accepts 1/5/15/30/60-minute intervals and short explicit local-date windows. It retains timestamp/session evidence and an unverified adjustment basis, excludes unfinished or future bars and never substitutes intraday closes for daily adjusted prices. It uses the same source enablement, normal proxy/TLS, cache and rate limits.
