# Macro data and reference FX

Four research datasets share the CLI, Python SDK, MCP and HTTP service. Public retrieval needs no API key, registration, wallet or SEC contact setting. The catalog publishes each dataset's schema, supported currencies/series, source conditions and query limits.

| Dataset                | Required query fields             | Source window                                     | Units                                   |
| ---------------------- | --------------------------------- | ------------------------------------------------- | --------------------------------------- |
| `fx_rates`             | `base_currency`, `quote_currency` | ECB latest 90 calendar days                       | Quote currency per one base currency    |
| `reference_rates`      | `series`: `effr` or `sofr`        | New York Fed latest 90 effective observations     | Percent per annum                       |
| `treasury_real_yields` | None; optional `year`             | Treasury selected calendar year, from 2003        | Percent per annum                       |
| `macro_analytics`      | `series`                          | BLS latest three years, or Treasury selected year | Ratio, thousand persons or basis points |

All accept `limit` from 1–500 and the shared `response` options. Treasury queries default to the current calendar year; pass `year=2026` for packaged fixtures. For BLS calculations, optional `year` filters the current observation year within the source window, without changing the underlying API request. Increasing `limit` or paging cannot extend upstream history.

```sh
atlas research get fx_rates --base-currency USD --quote-currency JPY --provider fixture --json
atlas research get reference_rates --series sofr --provider fixture --json
atlas research get treasury_real_yields --year 2026 --provider fixture --json
atlas research get macro_analytics --series yield_spread_10y_2y --year 2026 --provider fixture --json
```

Use `--provider public` for official source retrieval. The following query works as an SDK argument, MCP `atlas_get_research`'s `query` argument, or the HTTP `POST /v1/research` body:

```json
{
  "dataset": "fx_rates",
  "base_currency": "USD",
  "quote_currency": "JPY",
  "response": { "view": "compact", "page_size": 5 }
}
```

## FX conventions and calculations

Supported currencies are EUR, USD, JPY, GBP, CHF, AUD, CAD, NZD and CNY. The two query currencies must differ. Each record states `base_currency`, `quote_currency`, `quote_convention=quote_currency_per_one_base_currency` and its unit. A USD/JPY value of `"120"` with `unit="JPY_per_USD"` means one USD equals 120 JPY.

ECB publishes reference rates as foreign currency units per EUR. EUR-base records preserve that reported value and source decimal places. Other records set `derived=true` and retain the same-date reported EUR inputs, roles, source hashes and XML attribute paths:

| Requested pair | Formula                       |
| -------------- | ----------------------------- |
| EUR / JPY      | Reported JPY per EUR          |
| USD / EUR      | 1 / USD per EUR               |
| USD / JPY      | (JPY per EUR) / (USD per EUR) |

Division uses Decimal precision 28 and round-half-even, independent of the caller's Decimal context. Derived records have their formula and `inputs`; each input has its original provenance. A derived record's own `source` is null because the output was not reported by the ECB. Compact responses replace input provenance with evidence IDs; retrieve them through `atlas_get_evidence` or `Atlas.get_evidence`.

Both inputs must belong to the same ECB date. Missing currencies return `value=null` with a reason. Nonpositive or malformed rates and duplicate dates/currencies fail source validation. Weekends, holidays and missing dates are not filled or interpolated. ECB reference rates are normally published around 16:00 CET on European working days for information purposes. They are not real-time, bid/ask or executable quotes. Atlas does not construct a publication timestamp from that schedule.

Retain the `attributions` entries identifying the ECB, its conditions and Atlas normalization/inverse/cross modifications when redistributing results.

## Overnight and real rates

`reference_rates` selects EFFR (overnight unsecured) or SOFR (overnight secured). It verifies the declared series identity and preserves the source's `percentRate` value as a decimal string. A value of `"1.30"` means 1.30% per annum. SOFR here is the overnight benchmark, distinct from forward-looking Term SOFR.

`date` is the source effective date. `revision_indicator` retains the source flag when present. `published_at` is null: neither effective date nor retrieval time is presented as the publication time. Current source corrections may change observations; no historical release vintage is reconstructed. Preserve the New York Fed copyright, required reference-rate notice, disclaimer, terms and normalization disclosure in `attributions`, including for compact output.

`treasury_real_yields` parses the TIPS-based real par curve for 5, 7, 10, 20 and 30 years. These are indicative par yields, not prices or yields for an individual security. Negative and zero values are valid. Missing fields and XML null markers produce unavailable records without converting them to zero. Each record is one date/maturity; a small `limit` can truncate a day's curve, as reported in coverage.

ECB and Treasury snapshots use a six-hour cache TTL; EFFR/SOFR snapshots use one hour. TTL is an Atlas refresh interval, not a freshness or publication guarantee. Results distinguish observation dates, source retrieval times and absent publication timestamps.

## Derived macro indicators

| `series`              | Inputs and formula                                                                          | Unit               |
| --------------------- | ------------------------------------------------------------------------------------------- | ------------------ |
| `cpi_yoy`             | BLS CPI-U, not seasonally adjusted: (current − same month last year) / same month last year | `ratio`            |
| `core_cpi_yoy`        | BLS CPI-U excluding food/energy, not seasonally adjusted; same YoY formula                  | `ratio`            |
| `payrolls_change_mom` | Seasonally adjusted total nonfarm payroll level − exact previous month's level              | `thousand_persons` |
| `yield_spread_10y_2y` | (Same-day nominal 10-year yield − nominal 2-year yield) × 100                               | `basis_points`     |

A CPI ratio of `"0.03"` means 3%. A payroll change of `"150"` means 150,000 persons. A yield spread of `"25"` means 25 basis points, or 0.25 percentage points. Negative payroll changes and inverted yield spreads are valid. CPI index levels must be positive; negative employment levels are unavailable.

Monthly calculations exclude BLS M13 annual averages. They match the exact calendar lag, including December/January for payrolls; a neighboring month never replaces a missing baseline. `comparison_period` identifies the required prior month even when it is absent. Output limits apply after input matching, so a baseline outside the returned rows can still be used. CPI division uses precision 28 and round-half-even; payroll subtraction preserves source digits.

The spread uses the full selected Treasury year's source before applying the output limit. Inputs must have the same observation date. It preserves negative yields and reports missing maturities as unavailable. It does not combine nominal and real yields or infer inflation expectations.

Every derived record retains `formula`, `inputs`, `unit`, `status` and a missing-input reason where needed. Unavailable calculations remain explicit rows; they do not require `allow_partial`, a financial-dataset option. Data comes from current/revised snapshots and does not establish what was known at a historical time. `as_of`/`pit` requests are rejected.

## Fixtures and validation limits

The packaged ECB, New York Fed and Treasury examples are fictional, marked synthetic and linked under `.invalid`. Existing BLS fixtures intentionally remain byte-exact and lack a same-month prior-year baseline: fixture `cpi_yoy` and `core_cpi_yoy` therefore return unavailable rows. Tests inject separate fictional source bytes to verify successful CPI calculations and missing/zero/ambiguous baselines. Payroll changes and yield spreads have working packaged examples.

Official ECB documentation/conditions, New York Fed reference-rate terms and Treasury XML documentation were inspected. Live API response compatibility and runtime availability remain unverified; the NY Fed API documentation page could not be read fully. Fixture and mock-transport tests do not establish live coverage or latency. GDP/PCE, release calendars, forecasts, full FX history, real-time market rates, term SOFR and historical release vintages are outside this expansion.

[Query guide](research.md) · [Agent contracts](agents.md) · [Sources and terms](../DATA_SOURCES.md) · [Validation evidence](verification.md)
