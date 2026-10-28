# Optional Yahoo Finance Japanese equities

The latest source checkout adds `equity_prices` and `equity_actions` through the same CLI, Python SDK, MCP and HTTP research service. `japan_equity_history` combines both reads against one cached source response. These additions are separate from the existing `v0.7.0` release assets.

This is an **optional secondary source for personal use**, disabled by default. It uses the international `query1.finance.yahoo.com` chart endpoint, not Yahoo! JAPAN pages. Yahoo does not endorse Atlas or guarantee this unofficial API. The software license grants no license to republish or commercially redistribute Yahoo/provider data. Review the [Yahoo terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) and the [yfinance project's data-use notice](https://github.com/ranaroussi/yfinance#legal-stuff) for the intended use and separate data rights. Installing open-source software does not establish those rights.

## Enable and retrieve

Set `ATLAS_ENABLE_YAHOO_FINANCE=1` in the operator's process environment for personal use under the source terms. It cannot be enabled by an MCP/HTTP query. Default diagnostics and source probes exclude this optional source; selecting `yahoo_finance` explicitly reports its configuration and can run the opt-in probe. Do not enable it on a shared public data-redistribution service.

```sh
# No network or source setting required; the results are fictional.
uv run --offline atlas research preset japan_equity_history --identifier 7203.T --provider fixture --json

# Actual source access requires normal outbound HTTPS to query1.finance.yahoo.com.
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_prices --identifier 7203.T --start-date 2025-01-01 --end-date 2026-10-26 --json
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_actions --identifier 7203.T --start-date 2025-01-01 --end-date 2026-10-26 --json
```

```python
from atlas_terminal.client import Atlas

# Enable the optional source in this process's environment before a public read.
with Atlas("public") as atlas:
    result = atlas.get_research_preset(
        {
            "preset": "japan_equity_history",
            "identifier": "7203.T",
            "start_date": "2025-01-01",
            "end_date": "2026-10-26",
            "view": "compact",
        }
    )
```

Use an explicit four-character Tokyo Yahoo symbol starting with a digit, such as `7203.T`, `6758.T` or `130A.T`. Atlas does not resolve a bare Japanese code, infer an issuer identity or map a Yahoo industry label to JPX33. The returned source symbol, JPY currency, Tokyo timezone/exchange, instrument type and daily frequency must agree. Other countries, exchanges, currencies, intraday intervals and arbitrary URLs are rejected. Tokyo equities and ETFs are supported when identified as such by Yahoo; an `EQUITY` label is not a verified common-stock classification for risk modeling.

Without dates, the request selects the latest month. Supply both inclusive dates for up to ten calendar years. They become Yahoo Unix timestamp boundaries at Tokyo midnight, with the upper boundary moved to the next day because Yahoo uses an exclusive end. Dates are observation filters, not historical information-availability dates. `limit` is 1–500; inspect truncation when selecting a longer history. The preset selects at most 100 price rows and 100 actions per item.

## Values, dates and adjustment conventions

| Dataset          | Returned facts                                 | Interpretation                                                                                    |
| ---------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `equity_prices`  | Open, high, low, close, volume, adjusted close | Daily JPY/share prices; whole-share volume, including a true zero                                 |
| `equity_actions` | Reported dividends and stock-split terms       | Dividend amount in JPY/share as reported; numerator/denominator identify new shares per old share |

Yahoo's normal OHLC values are **already split-adjusted**. They are not a verified unadjusted trade-price history. The separate adjusted close also reflects dividends. Records preserve `price_basis="yahoo_split_adjusted"` and `adjusted_close_basis="yahoo_split_and_dividend_adjusted"`, including after compact field projection. Atlas does not re-adjust or repair prices. The [yfinance adjustment guide](https://ranaroussi.github.io/yfinance/advanced/price_adjustment.html) explains the distinction; Atlas preserves raw chart JSON rather than using a DataFrame conversion or adding a yfinance runtime dependency.

Numeric JSON tokens are parsed with Decimal and returned as decimal strings without binary-float conversion or rounding to a presumed price tick. Missing fields remain null; absent adjusted close is never replaced by close. Invalid identity, array lengths, duplicates, nonpositive prices, negative/fractional volume and conflicting split terms fail explicitly. Missing source rows are not replaced by holiday or forward-filled prices. An empty action history is a successful empty result with original source evidence.

`date` is the timestamp's **Tokyo session/event date**. `observation_at` retains the source timestamp; it does not establish a market-close or first-publication timestamp. `published_at` stays null. The current-session daily bar is excluded until the source's same-day regular-session end has elapsed at retrieval; when that end is absent or unconfirmed, the current bar is excluded. `coverage.excluded_unconfirmed_session_rows` records this separately. A completed session is not a guarantee that the publisher will never revise the bar. Future event dates are excluded from historical action results.

Reported dividend amounts are not rescaled or reconstructed as original pre-split cash amounts. Their currency follows the chart's JPY quote metadata (`currency_basis="chart_quote_currency"`); it does not independently establish a payment currency. Event dates do not establish dividend payment dates. Split coefficients can be fractional and remain exact; a supplied ratio label must be mathematically consistent with those coefficients. Missing action records do not establish that an issuer never split or paid dividends. Unknown event types remain identified in coverage and in the raw source, without being reclassified as dividends or splits.

## Evidence and shared access

Every result retains the original source-body SHA-256, retrieval time and source URL. Price rows include `field_sources` with a JSON pointer for each OHLC/volume/adjusted-close scalar and the source timestamp. A row's main `source` resolves to its close cell; adjusted-close provenance remains separate. Action pointers identify their original event objects, and instrument metadata has its own pointer. Compact evidence IDs resolve through the existing `Atlas.get_evidence` / `atlas_get_evidence` contracts.

Prices and actions with the same symbol/date window use one canonical cache key and the same original bytes. Public reads use the shared bounded HTTP transport, 15-minute cache, one-request-per-second limiter and finite retries. Authentication failures do not collect cookies/crumbs, change hosts, use a paid fallback or return fictional data. Upstream rate limits or endpoint changes can make retrieval unavailable.

No fundamentals, analyst forecasts, real-time or intraday feed, delisting-complete universe, exchange calendar or point-in-time vintages are supplied. Historical observations are currently revised publisher data. Existing source-age, missingness, truncation and observation-date requirements remain available.

## Risk-model inputs

The research reader does not automatically construct a risk panel. The [existing local connections](data-connections.md) can consume appropriately prepared split-and-dividend-adjusted prices, with explicitly supplied session boundaries, availability times and the remaining required inputs. Research chart timestamps must not be substituted for verified session-close/availability times.

For fundamental cross-sectional risk, supply actual historical market capitalization or a separate verified **unadjusted** close/share-count basis. Do not multiply Yahoo's split-adjusted close by raw historical shares. Current market capitalization, share counts, financial statements and industry labels are not historical vintages and are not inferred by this reader.

## Verification

Default tests use two clearly fictional Yahoo-shaped source files. They verify exact decimals and original scalar locators; JPY/Tokyo/source identity; inclusive boundaries and exclusive upstream timestamps; completed-session filtering; zeros/nulls; empty actions; dividends and reverse splits; malformed arrays/values; endpoint allowlisting and operator-only enablement; shared cache/preset execution; compact evidence/pagination/quality; CLI/SDK/HTTP and an actual official stdio MCP session. Such tests establish no live Yahoo availability.

For an explicitly requested private live verification:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_yahoo_live.py --allow-network --output build/yahoo-live
```

The script checks normal and dated price reads for `7203.T` and `6758.T`, reuses each response for actions and compact evidence, and executes the dated preset through SDK and actual stdio MCP. It retains source bytes, hashes, queries, results and resolved evidence under the ignored output directory. It stops after the first source failure. No real Yahoo data is bundled in the repository or published by this workflow. See [the verification record](verification.md) for the actual execution outcome and limitations.

## Connection to time-series risk

The source checkout includes a named personal-use Yahoo connector with a separate ETF factor. See [configuration, timing, exact returns and evidence](data-connections.md#optional-yahoo-time-series-connection). Real split windows, ETF reads, one observed null adjusted close and a 120-session two-stock risk execution were checked privately on 2026-10-29 JST; zero volume was not observed. [Verification scope](verification.md#data-integrity-and-japanese-company-extensions).
