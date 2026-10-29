# Market research and company discovery

The source checkout adds price indicators and comparisons, explicit-universe financial screening, classified SEC filing events, selected fund portfolio disclosures and arbitrary FRED series. Queries use the same research service through CLI, Python SDK, MCP and HTTP. These additions do not change the existing `v0.7.0` release assets.

| Dataset                            | Question it answers                                                    | Important boundary                                                     |
| ---------------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `equity_prices` / `equity_actions` | What daily prices and security actions did Yahoo report?               | Optional personal-use secondary source; current revised data           |
| `price_analytics`                  | What trailing indicators follow from those adjusted prices?            | Explicit formulas, selected-window warm-up and missing inputs          |
| `price_comparison`                 | How did selected stocks or ETFs perform together?                      | Two to ten instruments, one market/currency, exact date alignment      |
| `company_screen`                   | Which selected issuers meet reported-financial thresholds?             | One to ten explicit issuers at a common reported end                   |
| `filing_events`                    | Which historical filings disclosed results or other classified events? | SEC filing dates, without future earnings dates                        |
| `fund_holdings`                    | What positions did one selected N-PORT filing report?                  | Dated filing values and series identity, without current ETF mapping   |
| `fred_series`                      | What does a chosen FRED series report in its native units?             | Private API key, series-specific producer rights, no vintage selection |

Run `atlas research catalog --json` for current metrics, selectors and validated query fields. A JSON object shown below can also be submitted to `Atlas.get_research`, the `atlas_get_research` MCP tool or `POST /v1/research`. Save it privately and use `atlas research run --input query.json --provider fixture --json` for a supported fictional example, or select `--provider public` with the source's required configuration. Source failures never switch to fictional data.

## Daily market prices and trailing indicators

The [optional Yahoo reader](yahoo-finance.md) supports US/Tokyo securities plus an explicit list of reference indexes, FX pairs, cryptocurrencies and futures. Source identity, currency, timezone, instrument type and daily frequency must agree. Exact supported symbols appear in the catalog; the list does not establish permission to redistribute the data.

Set `ATLAS_ENABLE_YAHOO_FINANCE=1` in the operator environment for personal use before a public Yahoo read. Query arguments cannot enable it. Ordinary CLI research reads perform network access through the configured public provider; diagnostic and live-verification scripts have their own explicit network flags.

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_prices --identifier AAPL --start-date 2025-01-01 --end-date 2025-12-31 --limit 500 --json
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get equity_prices --identifier '^GSPC' --start-date 2025-01-01 --end-date 2025-12-31 --limit 500 --json
```

For indicators, use `price_analytics` with one symbol and selected metrics:

```json
{
  "dataset": "price_analytics",
  "identifier": "AAPL",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "metrics": ["return_1d", "sma_20", "rsi_14", "macd", "drawdown"],
  "limit": 500
}
```

Available indicators include simple consecutive-observation returns, cumulative return, 20/50-observation SMA, 12/26-observation EMA, Wilder RSI 14, 20-return sample volatility, drawdown, MACD/signal/histogram and 20-observation Bollinger bands. Formulas, required observations, source input ranges and units are returned. Calculations use adjusted close with explicit half-even rounding to 18 decimal places.

Windows count returned observations, not verified exchange sessions. No hidden earlier history is fetched for warm-up. Null/nonpositive adjusted prices make affected calculations unavailable. Rolling and recursive indicators restart after those observations; cumulative return and drawdown remain unavailable after a gap because they start at the first selected row. Volatility is per observation and is not annualized. Index/FX/crypto/futures observations retain source-specific units and adjustment meaning; futures prices do not establish a tradable roll strategy or contract return.

## Comparing price histories

```json
{
  "dataset": "price_comparison",
  "identifiers": ["AAPL", "MSFT", "SPY"],
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "limit": 100
}
```

The comparison requires two to ten stocks/ETFs in one currency and market timezone, with 3–500 usable observations per instrument, at most 3,000 input observations in total and identical ordered dates. It rejects missing prices or different date vectors rather than selecting a common subset. Results include cumulative performance, sample return volatility, maximum drawdown and pairwise covariance/correlation, with original adjusted-price evidence. Constant return histories have unavailable correlation. Volatility and covariance are not annualized.

The result describes the selected interval. Shared dates do not prove simultaneous closes, exchange-calendar completeness or future risk. For estimated factor risk, use the separate [named Yahoo time-series connection](data-connections.md#optional-yahoo-time-series-connection) or [operator-supplied local histories](data-connections.md).

## Financial screening over an explicit universe

```json
{
  "dataset": "company_screen",
  "identifiers": ["ATLASDEMO"],
  "period": "annual",
  "metrics": ["revenue", "revenue_growth_yoy", "free_cash_flow"],
  "filters": [
    {
      "metric": "revenue_growth_yoy",
      "operator": "gte",
      "value": "0.1",
      "unit": "ratio"
    },
    {
      "metric": "free_cash_flow",
      "operator": "gt",
      "value": "0",
      "unit": "USD"
    }
  ],
  "sort_by": "revenue_growth_yoy",
  "sort_order": "desc"
}
```

This fictional example runs with `--provider fixture`. Public issuer reads require the operator's private `SEC_USER_AGENT` containing an application name and actual contact email. Select actual issuer tickers or CIKs when using the public provider; the screen does not automatically enumerate the market.

Provide one to ten distinct issuers and one to ten filters, with at most 40 distinct metrics across output, filters and sorting in one query. All 37 reported financial concepts, seven growth/FCF calculations and six ratios are available. The ratios are `net_margin`, `gross_margin`, `operating_margin`, `current_ratio`, `liabilities_to_equity` and `operating_cash_flow_to_net_income`; they reuse the same reported-input arithmetic as the `ratios` dataset.

Operators are `lt`, `lte`, `eq`, `gte` and `gt`. Thresholds are finite decimal strings in the catalog metric's exact unit: `"0.1"` with unit `ratio` means 10%, while a USD threshold uses full dollars. Every filter must be known and true for an issuer to match. Repeated aliases of the same CIK are rejected.

Screening reuses the financial comparison's latest common reported end. Actual start/end dates and growth baselines must align across issuers. It does not use each company's independently latest observation or manufacture a fourth quarter. Missing/unmatched inputs require `allow_partial=true` and remain unavailable. Every requested issuer appears with `screen_status` of `matched`, `excluded` or `unavailable`, its individual threshold decisions and original financial evidence. Sorting keeps unavailable sort values last and preserves requested order for ties.

This is a screen of a caller-selected universe, with current revised filing information. It does not provide a survivorship-free market universe, historical information availability, consensus forecasts, price/earnings multiples or current market capitalization. [Financial selection and arithmetic](methodology.md).

## Historical filing events

```sh
uv run --offline atlas research get filing_events --identifier ATLASDEMO --series earnings_results --start-date 2026-01-01 --end-date 2026-10-29 --provider fixture --json
```

Selectors are `all`, `earnings_results`, `periodic_reports`, `current_reports`, `offerings`, `shareholder_votes`, `ownership` and `fund_reports`. `earnings_results` requires an exact Item 2.02 in an 8-K or 8-K/A; `shareholder_votes` requires Item 5.07. A 6-K is a current report, without assuming that it contains earnings. Registration/prospectus filings are offering-related disclosures, not proof of an IPO or completed offering.

Date filters use **SEC filingDate**. Returned `report_date` and acceptance timestamp remain separate, with per-field JSON pointers and classification rules. A filing date is not an inferred earnings announcement, payment, meeting or future scheduled date. Amendments remain separate events, and archive caps remain explicit. Empty selections succeed without inventing events.

## Fund positions from one N-PORT filing

```json
{
  "dataset": "fund_holdings",
  "identifier": "ATLASDEMO",
  "accession": "9999999999-26-000008",
  "limit": 500
}
```

This is a fictional fixture example. For public access, provide an actual registrant identifier and a specific NPORT-P or NPORT-P/A accession. Use `fund_filings` to discover filing metadata first. The parser validates filing/registrant/report-date identity and retains the disclosed fund series. A registrant CIK can cover more than one fund; an accession is not a ticker-to-ETF mapping.

Positions preserve source security names and identifiers, balances and original unit codes, denomination currency, reported USD value, percentage of net assets, investment country and source category codes. Original XML field paths and the filing's valuation date remain available. Financial-year-end, valuation/report date and filing date are distinct. Reported categories are not automatically mapped to a vendor industry taxonomy.

When the complete portfolio fits the requested limit, all position values are available and reported net assets are positive, `allocations` groups original investment-country and asset-category codes. Each derived allocation is `100 * sum(reported USD position values) / reported USD net assets`, with exact inputs, denominator evidence and derivative-position counts. Otherwise allocations are unavailable with an explicit reason. Totals need not equal 100%; Atlas does not infer residual cash. Source conditional-currency and exchange-rate disclosures remain explicit without applying a conversion.

These are positions from a dated filing. They do not establish today's holdings, ETF creation basket, total economic exposure, historical adjusted shares, derivative look-through or a CUSIP-to-ticker join. An original percentage of net assets is not silently recalculated as a percentage of disclosed investments. Inspect position limits, missing values and any allocation coverage before interpreting totals. Source rights and SEC access conditions still apply. Direct official-source live retrieval is separate from fictional parser and interface tests; see the dated [verification record](verification.md).

## Generic FRED observations

```json
{
  "dataset": "fred_series",
  "series": "GDP",
  "start_date": "2020-01-01",
  "end_date": "2026-10-29",
  "limit": 100
}
```

Configure `FRED_API_KEY` privately for public access, including reads from cache. The key is sent only to validated FRED endpoints and is not included in canonical URLs, saved source identities or output. No key is needed for the fictional `DEMO_INDEX` fixture.

Series identifiers use uppercase letters, digits and underscores, with at most 80 characters. Supply both dates for an inclusive window of at most 30 calendar years; without dates the default is January 1 of the latest three-calendar-year span through today. Responses preserve source title, units, frequency, seasonal-adjustment labels, notes and realtime fields. `"."` remains unavailable and a true zero remains zero. Returned observations are newest first, with local truncation explicit.

FRED native dates often identify **period starts**. Atlas retains those labels rather than replacing them with period ends or information-availability timestamps. It performs no aggregation, unit conversion, forecast detection or vintage selection. The endpoint requests a bounded complete observation page; incomplete upstream pages fail instead of silently dropping history. Realtime fields alone do not establish ALFRED or point-in-time coverage.

FRED distributes government and third-party series. An API key does not grant a blanket license to republish every series; check the returned notes and original producer's conditions. Registered-key live availability is recorded separately from offline fixture checks in [verification](verification.md).

## A local chart from a saved response

Save a complete full-view result in a private directory, then render it without another source request:

```sh
uv run --offline atlas research chart --input prices.json --output prices.html --field adjusted_close
uv run --offline atlas research chart --input indicators.json --output rsi.html --field value --metric rsi_14
```

The output is a standalone local HTML chart with a data table and source evidence. It does not start a service, load third-party assets or publish the result. Charts require one series and unit, at most 4 MiB of saved input and at most 500 supplied records. Missing observations break the line. Keep source-data rights in mind before sharing an exported file; local visualization does not grant redistribution permission.

[Data sources and terms](../DATA_SOURCES.md) · [Research queries](research.md) · [Agent contracts](agents.md) · [Verification scope](verification.md)
