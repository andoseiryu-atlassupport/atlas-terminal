# Macro data and reference FX

The CLI, Python SDK, MCP and HTTP API share the same macro datasets, date rules and source evidence. All upstream data are free. BEA national accounts require a free registered `BEA_API_KEY`; the other macro sources require no registration, wallet or SEC contact setting.

| Dataset                | Required query fields             | Source window                                                              | Units                                           |
| ---------------------- | --------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------- |
| `economics`            | BLS `series`                      | Latest three years by default; dated requests in ten-year chunks           | Published index, percentage or employment units |
| `national_accounts`    | BEA `series`                      | Current and prior two years by default; dated requests in five-year chunks | Source `CL_UNIT`, `UNIT_MULT` and `Metric_Name` |
| `japan_economics`      | Japanese `series`                 | Current and prior two years by default; optional dates                     | Source units and adjustment metadata            |
| `fx_rates`             | `base_currency`, `quote_currency` | Full published ECB history; optional dates                                 | Quote currency per one base currency            |
| `reference_rates`      | `series`: `effr` or `sofr`        | New York Fed latest 90 effective observations                              | Percent per annum                               |
| `treasury_real_yields` | None; optional `year`             | Treasury selected calendar year, from 2003                                 | Percent per annum                               |
| `macro_analytics`      | `series`                          | BLS date window or Treasury selected year                                  | Ratio, thousand persons or basis points         |

All accept `limit` from 1–500 and shared `response` options. A limit selects the newest records after filtering; response pagination traverses that selection, not unlimited source history. Split long daily histories into smaller date queries to retrieve more than 500 records. Source coverage can start later or end earlier than the requested window; dates and limits never invent missing observations.

For `economics`, `national_accounts`, `japan_economics`, `fx_rates` and BLS-based `macro_analytics`, provide **both** `start_date` and `end_date` as inclusive ISO dates spanning at most 30 calendar years. Monthly/quarterly dates select the observation's calendar period end; daily dates select its effective/reference date. These are observation filters, not historical information-availability dates. `as_of` and PIT requests remain unsupported. Do not combine dates with `year`, or use dates for the Treasury spread.

BLS history uses the official V1 read-only POST with `seriesid`, `startyear` and `endyear`. Provenance records the actual method, endpoint and safe body as well as the logical source/cache identity. The private counter enforces 25 attempts per UTC day across requests and retries; the upstream per-IP quota can be exhausted independently. Monthly calculations fetch the prior calendar year for exact lag inputs before applying the output date filter. Optional `year` on BLS calculations now requests that year and its prior-year baseline. Treasury queries retain their single-year behavior.

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

Supported currencies are EUR, USD, JPY, GBP, CHF, AUD, CAD, NZD, CNY, CZK, DKK, HUF, NOK, PLN, SEK, ISK, TRY, BRL, HKD, IDR, ILS, INR, KRW, MXN, MYR, PHP, SGD, THB, ZAR, RON and BGN. Historical coverage and discontinuations vary by currency; inclusion in the catalog does not promise a current fixing. The two query currencies must differ. Each record states `base_currency`, `quote_currency`, `quote_convention=quote_currency_per_one_base_currency` and its unit. A USD/JPY value of `"120"` with `unit="JPY_per_USD"` means one USD equals 120 JPY.

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

Government-curve analytics now cover US/Japan slopes, butterflies, preceding-observation yield changes and same-date US nominal/real par gaps. Calculations use full selected source windows before output filtering and retain negative yields and explicit missing inputs. Par gaps are inflation-compensation proxies, not pure expected inflation or zero-coupon breakevens. [Series, formulas, Japanese CSV evidence and bond presets](fixed-income.md).

Every derived record retains `formula`, `inputs`, `unit`, `status` and a missing-input reason where needed. Unavailable calculations remain explicit rows; they do not require `allow_partial`, a financial-dataset option. Data comes from current/revised snapshots and does not establish what was known at a historical time. `as_of`/`pit` requests are rejected.

## US national accounts

Use `national_accounts` with one of these curated series:

| Series                       | Official BEA NIPA table | Frequency and interpretation                                  |
| ---------------------------- | ----------------------- | ------------------------------------------------------------- |
| `gdp_nominal`                | T10105                  | Quarterly, nominal GDP at seasonally adjusted annual rates    |
| `gdp_real`                   | T10106                  | Quarterly, real GDP at seasonally adjusted annual rates       |
| `gdp_growth`                 | T10101                  | Quarterly, published annualized percentage change in real GDP |
| `pce_price_index`            | T20804                  | Monthly, PCE price index level                                |
| `core_pce_price_index`       | T20804                  | Monthly, PCE price index excluding food and energy            |
| `personal_income`            | T20600                  | Monthly, seasonally adjusted annual-rate flow                 |
| `disposable_personal_income` | T20600                  | Monthly, seasonally adjusted annual-rate flow                 |
| `personal_consumption`       | T20600                  | Monthly, seasonally adjusted annual-rate flow                 |

Register a free key at [BEA API signup](https://apps.bea.gov/api/signup/) and configure `BEA_API_KEY` privately in the process running Atlas. Missing configuration returns `CONFIG_REQUIRED` without an HTTP request. Keys are attached only to the outbound BEA request and excluded from returned URLs, evidence and logs. BEA may echo its key in the original response: the unmodified source bytes remain in the **private** cache and must not be published. Source hashes always describe those original bytes.

Values are decimal strings with grouping commas removed. Atlas preserves source `CL_UNIT`, `UNIT_MULT`, `Metric_Name`, line identity and period; it does not silently multiply a value, change a reference year or substitute percentage growth for an index level. `annualized` and `seasonally_adjusted` describe the selected table. A PCE index is not a PCE inflation percentage. Current revised tables do not identify what was known at an earlier release date.

```sh
atlas research get national_accounts --series gdp_real --start-date 2010-01-01 --end-date 2026-12-31 --limit 100 --json
atlas research get national_accounts --series core_pce_price_index --provider fixture --json
```

## Japanese macro data

`japan_economics` uses the BOJ Time-Series Data Search API and the separate registration-free Statistical Dashboard API. It does not require an application ID for the main e-Stat API.

| Series                         | Publisher                               | Frequency                                                           |
| ------------------------------ | --------------------------------------- | ------------------------------------------------------------------- |
| `cpi`                          | Statistics Bureau, Consumer Price Index | Monthly, national all-items index, not seasonally adjusted          |
| `unemployment`                 | Statistics Bureau, Labour Force Survey  | Monthly, national total, seasonally adjusted                        |
| `call_rate`                    | BOJ                                     | Daily, uncollateralized overnight call rate                         |
| `usd_jpy_tokyo`                | BOJ                                     | Daily, Tokyo USD/JPY at 17:00 JST                                   |
| `tankan_manufacturing`         | BOJ                                     | Quarterly, actual business conditions DI for large manufacturers    |
| `tankan_nonmanufacturing`      | BOJ                                     | Quarterly, actual business conditions DI for large nonmanufacturers |
| `monetary_base`                | BOJ                                     | Monthly, average monetary base                                      |
| `money_stock_m2`               | BOJ                                     | Monthly, average M2 stock                                           |
| `bank_loans`                   | BOJ                                     | Monthly, domestically licensed banks' loans                         |
| `real_effective_exchange_rate` | BOJ publication                         | Monthly, real effective exchange-rate index                         |

The catalog includes exact BOJ database/series identifiers. Source units and titles are retained, including monetary scales and index reference years. BOJ `source_updated_on` is `LAST_UPDATE`, which can also change when metadata change; it is not a publication timestamp. Null observations stay unavailable. Tokyo USD/JPY and the ECB reference fixing remain separate series with different observation conventions.

Dashboard indicators are resolved from official metadata using the government survey, exact curated label, national geography, monthly frequency and seasonal adjustment. Atlas requires one active match, refuses ambiguity and does not join differently based historical indexes. Every observation retains its government survey, unit, provisional flag, annotations and both value/metadata provenance. An older request can return fewer rows where the selected active index lacks history.

```sh
atlas research get japan_economics --series cpi --start-date 2010-01-01 --end-date 2026-12-31 --limit 500 --json
atlas research get japan_economics --series tankan_manufacturing --json
atlas research preset japan_macro_overview --json
atlas research preset us_japan_overview --start-date 2025-01-01 --end-date 2026-12-31 --json
```

The Japan preset needs no registration. The US/Japan preset includes BEA items and reports their configuration errors independently if no key is configured. Both accept paired dates and return the latest selected observation per item, not aligned publication-time snapshots. Use ordinary dataset queries for full selected histories. See [presets](presets.md).

Keep the BOJ and Statistical Dashboard attribution/non-guarantee notices with redistributed results. The BOJ API notice asks publishers of API-backed services to notify the BOJ; operators are responsible for that publication step. No notification is sent by Atlas. See [source conditions](../DATA_SOURCES.md).

## Fixtures and validation limits

The packaged ECB, New York Fed, Treasury, BEA, BOJ and Statistical Dashboard examples are fictional, marked synthetic and linked under `.invalid`. Existing BLS fixtures intentionally remain byte-exact and lack a same-month prior-year baseline: fixture `cpi_yoy` and `core_cpi_yoy` therefore return unavailable rows. Tests inject separate fictional source bytes to verify successful CPI calculations and missing/zero/ambiguous baselines. Payroll changes and yield spreads have working packaged examples.

Official ECB documentation/conditions, New York Fed reference-rate terms and Treasury XML documentation were inspected. Live API response compatibility and runtime availability remain unverified; the NY Fed API documentation page could not be read fully. Fixture and mock-transport tests do not establish live coverage or latency. BEA, BOJ, Statistical Dashboard and BLS API documentation was also inspected. The execution environment blocks direct official-source hosts, and no BEA key was configured; actual new-source responses remain unverified. Release calendars, historical value-revision feeds, dedicated forecast/consensus feeds, real-time market rates, term SOFR and historical release vintages are unavailable.

[Query guide](research.md) · [Agent contracts](agents.md) · [Sources and terms](../DATA_SOURCES.md) · [Validation evidence](verification.md)

## International series and aligned panels

Use `sdmx_catalog` and `international_economics` for metadata-selected IMF/OECD series. Use `macro_panel` to align explicit macro/FX queries into monthly, quarterly or annual periods, retaining original inputs and missingness. Registered-series search is available through the existing catalog. [Discovery, aggregation rules and runnable examples](international-macro.md).
