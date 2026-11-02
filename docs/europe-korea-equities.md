# European and Korean market observations

The latest source checkout extends the existing five optional Yahoo datasets to Korean KOSPI/KOSDAQ and selected venues in sixteen European countries. Use `equity_prices`, `equity_actions`, `intraday_prices`, `price_analytics` or `price_comparison` through the same CLI, SDK, MCP and HTTP research service. The catalog remains at 53 datasets; these are additional markets within existing datasets.

| Trading market          | Suffix | Quote denomination | Example     |
| ----------------------- | ------ | ------------------ | ----------- |
| Korea KOSPI             | `.KS`  | KRW                | `005930.KS` |
| Korea KOSDAQ            | `.KQ`  | KRW                | `247540.KQ` |
| United Kingdom / London | `.L`   | GBP, GBp or GBX    | `VOD.L`     |
| Germany / Xetra         | `.DE`  | EUR                | `SAP.DE`    |
| France / Paris          | `.PA`  | EUR                | `TTE.PA`    |
| Netherlands / Amsterdam | `.AS`  | EUR                | `ASML.AS`   |
| Italy / Milan           | `.MI`  | EUR                | `ENI.MI`    |
| Spain / Madrid          | `.MC`  | EUR                | `SAN.MC`    |
| Switzerland / SIX       | `.SW`  | CHF                | `NESN.SW`   |
| Sweden / Stockholm      | `.ST`  | SEK                | `VOLV-B.ST` |
| Denmark / Copenhagen    | `.CO`  | DKK                | `NOVO-B.CO` |
| Norway / Oslo           | `.OL`  | NOK                | `EQNR.OL`   |
| Finland / Helsinki      | `.HE`  | EUR                | `NOKIA.HE`  |
| Belgium / Brussels      | `.BR`  | EUR                | `ABI.BR`    |
| Austria / Vienna        | `.VI`  | EUR                | `OMV.VI`    |
| Ireland / Dublin        | `.IR`  | EUR                | `KRX.IR`    |
| Portugal / Lisbon       | `.LS`  | EUR                | `EDP.LS`    |
| Poland / Warsaw         | `.WA`  | PLN                | `PKN.WA`    |

Symbols must use the explicit source suffix and preserve Korean leading zeros. A spelling alone does not establish listing identity or availability. Source symbol, quote denomination, exchange, timezone, instrument type and interval must match the registry. Stocks and ETFs are accepted; unsupported venues and quote currencies fail explicitly. These selections do not cover every European country, exchange, listing or currency class. `market` identifies the trading venue's country, not an issuer's domicile.

## Access and examples

Yahoo is an optional secondary source for personal use, disabled by default. Set `ATLAS_ENABLE_YAHOO_FINANCE=1`; source conditions do not grant redistribution rights. No Yahoo! JAPAN access, cookie/crumb collection, alternative-host fallback or paid feed is used.

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 atlas research get equity_prices --identifier 005930.KS --start-date 2026-10-01 --end-date 2026-10-31 --provider public --json
ATLAS_ENABLE_YAHOO_FINANCE=1 atlas research get equity_actions --identifier NOVO-B.CO --start-date 2023-09-01 --end-date 2023-09-30 --provider public --json
atlas research get equity_prices --identifier VOD.L --provider fixture --json
atlas research get price_analytics --identifier SAP.DE --metrics return_1d,cumulative_return --provider fixture --json
atlas research get intraday_prices --identifier 005930.KS --start-date 2026-10-28 --end-date 2026-10-28 --interval 5m --provider fixture --json
```

The SDK uses `Atlas.get_research(query)` with the same dataset and identifier fields; MCP uses `atlas_get_research`. Public daily reads default to the latest month. Paired inclusive dates may span up to ten calendar years; returned results are bounded to 500 rows, with explicit truncation. Intraday reads retain the existing interval/retention limits. [Daily contracts](yahoo-finance.md) and [intraday contracts](intraday-prices.md).

## Quote units and time

British `GBp`/`GBX` prices are pence, while `GBP` prices are pounds. Original prices and dividend scalars remain unchanged. Records retain `currency`, `unit`, `denomination_currency` and `quote_unit_scale`: one GBp/GBX corresponds to `0.01` GBP; one GBP corresponds to `1` GBP. This scale is explicit metadata, not a conversion applied to returned prices. Dividend amounts retain the chart's quote-currency basis without independently asserting their actual payment currency or original pre-split cash amount.

Daily security OHLC is Yahoo's split-adjusted series; adjusted close also reflects dividends. Exact source cells, nulls, source volume zero and action terms remain distinct. No missing session is filled. Local date filters use the registered source timezone, including daylight saving; a London fall-transition day spans 25 UTC hours. The Vienna feed currently reports `Europe/Amsterdam`. Atlas retains that declared source timezone instead of asserting a verified Vienna exchange calendar.

Price comparisons require one national trading market, exact common dates and equal quote units. EUR alone does not authorize a Germany/France comparison, and mixed pound/pence price inputs are rejected instead of being silently converted.

## Time-series risk

The named Yahoo time-series connection accepts these markets with one explicitly selected ETF factor from the same national trading market. Limits remain 1–9 assets, one separate ETF factor and 61–500 complete common price dates. A source-identified ETF is a proxy, not an official market factor. [Connection setup](data-connections.md#optional-yahoo-time-series-connection).

The risk manifest uses the full denomination currency, including GBP for pence quotes. Within each asset's adjusted-close ratio its fixed quote-unit scale cancels; no price-to-market-cap calculation or FX conversion is performed. Receipts retain each source quote unit and scale. This permits a GBP-quoted ETF factor and GBp-quoted stock while keeping both source inputs inspectable. Missing prices, inconsistent units, date mismatches, stale last observations and availability before capture remain errors. Set `as_of` after obtaining the sources; do not backdate it to the price window.

Observed-factor time-series models do not require a local industry taxonomy. Fundamental cross-sectional models retain their separate US/Japan/Korea/UK requirements and supplied financial vintages; continental-European fundamental classifications and historical financials are not introduced by this price reader.

## Verification and remaining coverage

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_europe_korea_live.py --allow-network --output build/europe-korea-live
```

The dated verification checks original price/action cells, three 5-minute selections and three existing time-series fits through the normal proxy/TLS client and persistent cache. Actual data and receipts remain private under ignored `build/`; bundled examples are fictional. [Actual results and limits](verification.md).

This change supplies market observations and source instrument metadata. It does not add local standardized company financials, valuation multiples, analyst consensus or a complete security master. DART needs a private free registration key; that key was unset, and DART/KRX/ESMA/FCA destinations were blocked by the runtime's observed policy during verification. Those local access limits do not establish upstream outages. No official local financial-data live success is claimed.
