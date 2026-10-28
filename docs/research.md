# Free public-source research

`atlas research`, `Atlas.get_research`, MCP `atlas_get_research` and HTTP `POST /v1/research` use the same service. Sources are free public primary sources by default. The optional [international Yahoo Tokyo prices/actions](yahoo-finance.md) are a secondary-source exception for operator-enabled personal use, without a redistribution grant. No data subscription, model or wallet is required. BEA national accounts require a free registered `BEA_API_KEY`; other sources need no data API key. SEC alone requires a private `SEC_USER_AGENT` with the operator's actual contact details.

Queries can include optional `response` settings for compact evidence references, field selection and snapshot-bound pages. The default response is unchanged. See [agent contracts](agents.md) for typed MCP schemas, evidence retrieval and structured recovery hints.

Results include missing-data status, periods, coverage and source terms. Discover the API with `atlas research catalog --json` or `GET /v1/research/catalog`.

## Datasets

| ID                         | Content                                                             | Query fields                                                 | Output                                |
| -------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------- |
| `companies`                | Search the current SEC issuer/ticker directory                      | Optional `identifier`, `limit`                               | Current reference data                |
| `profile`                  | Name, SIC, exchanges and fiscal year end                            | `identifier`                                                 | Submission metadata                   |
| `statements`               | 37 financial concepts                                               | `identifier`, `metrics`, `period`, `count`, `allow_partial`  | Normalized reported facts             |
| `ratios`                   | Six ratios with formulas and inputs                                 | `identifier`, `period`, `count`, `allow_partial`             | Derived values                        |
| `financial_analytics`      | Five YoY growth rates, free cash flow and FCF margin                | `identifier`, `metrics`, `period`, `count`, `allow_partial`  | Derived values with reported inputs   |
| `company_facts`            | Concept inventory or raw XBRL observations                          | `identifier`, `taxonomy`, `concept`, `unit`, `limit`         | Original accounting contexts          |
| `filings`                  | All-form filing discovery                                           | `identifier`, `form`, `limit`                                | Filing references                     |
| `insider_transactions`     | Form 3/4/5 transactions and positions                               | `identifier`, `accession`, `limit`                           | XML records from one filing           |
| `institutional_holdings`   | A manager's 13F information table                                   | `identifier`, `accession`, `limit`                           | XML records from one filing           |
| `ownership_filings`        | 13D/13G beneficial ownership reports                                | `identifier`, `limit`                                        | Filing references                     |
| `fund_filings`             | N-PORT, N-PX, N-CSR and related reports                             | `identifier`, `limit`                                        | Filing references                     |
| `corporate_action_filings` | Current reports, proxies, offerings and tender-related filings      | `identifier`, `limit`                                        | Filing references                     |
| `economics`                | Twenty-two BLS price/labour/cost/productivity series                | `series`, `limit`, optional paired dates                     | Economic observations                 |
| `equity_prices`            | Optional international Yahoo Tokyo daily OHLC/volume/adjusted close | Explicit `.T` `identifier`, `limit`, optional paired dates   | Secondary-source prices, personal use |
| `equity_actions`           | Optional international Yahoo reported Tokyo dividends/splits        | Explicit `.T` `identifier`, `limit`, optional paired dates   | Secondary-source events, personal use |
| `treasury_yields`          | Daily Treasury par yields                                           | `year`, `limit`                                              | Observations by date and maturity     |
| `fx_rates`                 | ECB daily reference FX, with inverse/cross calculations             | Required `base_currency`, `quote_currency`; optional `limit` | Dated quote units per base unit       |
| `reference_rates`          | New York Fed overnight EFFR/SOFR                                    | Required `series`; optional `limit`                          | Effective daily observations          |
| `treasury_real_yields`     | Treasury TIPS-based real par yield curve                            | `year`, `limit`                                              | Observations by date and maturity     |
| `macro_analytics`          | CPI/core CPI YoY, payroll change and 10Y–2Y nominal spread          | Required `series`; optional `year`, `limit`                  | Derived values with original inputs   |
| `federal_debt`             | Debt to the Penny                                                   | `limit`                                                      | Daily debt observations               |
| `policy_news`              | Federal Reserve releases                                            | `limit`                                                      | Headlines, dates and source links     |
| `europe_economics`         | Eurostat GDP, HICP, labour, industry and retail                     | `series`, `limit`, optional paired dates                     | European economic observations        |
| `uk_economics`             | ONS CPI, GDP and rolling unemployment                               | `series`, `limit`, optional paired dates                     | UK economic observations              |
| `compare`                  | Common reported period for up to ten specified issuers              | `identifiers`, `metrics`, `period`, `allow_partial`          | Aligned reported/derived financials   |

`identifier` accepts a ticker, CIK or company name. For 13F, use the **reporting manager's CIK**. An issuer CIK does not reverse-search every manager holding that issuer. Insider reports are likewise limited to the resolved filing history. Omitting `accession` selects the latest matching filing. Original filings and amendments are not merged automatically.

`limit` ranges from 1–500; financial `count` ranges from 1–20; comparisons accept at most ten identifiers. Unknown fields, fields unrelated to the selected dataset and booleans used as counts are rejected. The filing `form` filter is an exact match. Original form names, including `SCHEDULE 13D` and legacy `SC 13D`, are preserved.

Government-bond research adds `jgb_yields` with an optional `year` or paired observation dates and `limit`, plus US/Japan curve analytics through `macro_analytics`. Three bond presets combine curves, local changes, funding and FX using the same interfaces. [Datasets, exact series names and CSV evidence](fixed-income.md).

Euro area and Australian government research uses `euro_area_yields` (required `series` of `aaa_spot` or `all_spot`) and `australia_yields`. Both accept `year` or paired observation dates and `limit`. ECB spot rates remain distinct from nominal par/benchmark yields. [Regional contracts and presets](fixed-income.md#euro-area-and-australia).

## Examples

These examples use fictional data without network access. For public sources, use `--provider public` and replace `ATLASDEMO` with a real ticker or CIK.

```sh
uv run --offline atlas research get profile --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period ytd --metrics operating_cash_flow,capital_expenditure --count 2 --provider fixture --json
uv run --offline atlas research get ratios --identifier ATLASDEMO --count 1 --provider fixture --json
uv run --offline atlas research get financial_analytics --identifier ATLASDEMO --metrics revenue_growth_yoy,free_cash_flow,free_cash_flow_margin --count 1 --provider fixture --json
uv run --offline atlas research get compare --identifiers ATLASDEMO --metrics revenue,revenue_growth_yoy,free_cash_flow --provider fixture --json
uv run --offline atlas research get company_facts --identifier ATLASDEMO --concept EarningsPerShareBasic --unit USD/shares --limit 5 --provider fixture --json
uv run --offline atlas research get filings --identifier ATLASDEMO --form 8-K --provider fixture --json
uv run --offline atlas research get insider_transactions --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get institutional_holdings --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get economics --series unemployment --provider fixture --json
uv run --offline atlas research get treasury_yields --year 2026 --provider fixture --json
uv run --offline atlas research get fx_rates --base-currency USD --quote-currency JPY --provider fixture --json
uv run --offline atlas research get reference_rates --series sofr --provider fixture --json
uv run --offline atlas research get treasury_real_yields --year 2026 --provider fixture --json
uv run --offline atlas research get macro_analytics --series yield_spread_10y_2y --year 2026 --provider fixture --json
uv run --offline atlas research get federal_debt --provider fixture --json
uv run --offline atlas research get policy_news --provider fixture --json
```

Python:

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    data = atlas.get_research(
        {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "period": "quarterly",
            "metrics": ["revenue", "eps_basic"],
            "count": 3,
        }
    )
    print(data["records"])
```

For MCP, call `atlas_research_catalog`, then pass `{"query":{"dataset":"economics","series":"cpi"}}` to `atlas_get_research`. With `--provider public`, macro-only queries need no SEC contact setting. SEC configuration is checked on the first SEC request. The `hosted` provider does not proxy research.

HTTP:

```sh
uv run --offline atlas serve --provider fixture --payments off --port 8000
curl -sS http://127.0.0.1:8000/v1/research/catalog
curl -sS http://127.0.0.1:8000/v1/research -H 'Content-Type: application/json' -d '{"dataset":"economics","series":"cpi"}'
```

The reference API supports self-hosted, single-process loopback use. Research endpoints never require quotes or payments. Optional annual snapshot purchases use a separate contract.

## Financial concepts and periods

The catalog's `statement_metrics` lists metric names, tags and units. USD, shares and USD/share values remain distinct. Normalized statements do not support IFRS or custom extension concepts; `company_facts` can inspect an explicitly requested taxonomy when it exists in the source.

- `annual`: Primary-year 10-K / 10-K/A facts spanning 330–400 days.
- `quarterly`: Explicit 70–110-day durations, including independently reported quarters in 10-K filings. Atlas never calculates Q4 as annual minus nine-month YTD.
- `ytd`: The longest eligible reported 70–310-day duration in the selected 10-Q / 10-Q/A filing. Discrete quarters and cumulative values remain distinct.

Selection joins accession, form, filed date and submissions reportDate to actual start/end dates. It never infers periods from `fy` or `frame` alone. Balance-sheet instant values require a duration anchor at the same report date. Transition periods, short years and periods without duration anchors may be absent. Different metrics may use original and amended filings; each retains its own provenance.

Missing or equally ranked conflicting facts return `null` with a reason. Incomplete coverage produces `INCOMPLETE_COVERAGE` by default. `allow_partial=true` / `--allow-partial` permits partial results; no eligible reported periods remains an error.

Ratios cover net margin, gross margin, operating margin, current ratio, total liabilities / stockholders' equity, and operating cash flow / net income. Output units are `ratio`, with Decimal precision 28. Inputs must cover the same period; missing inputs, mismatched periods and zero denominators prevent calculation. Price/earnings, price/book and forecast yields remain unavailable: the optional Tokyo daily-price reader does not provide aligned historical equity/share/earnings inputs or analyst estimates.

## Growth and cash-flow analytics

`financial_analytics` returns calculated records separately from `statements`. Its `metrics` filter accepts the seven entries in the catalog's `analytics_metrics`; omitting it requests all seven.

| Metric                        | Formula                                                                                | Unit    |
| ----------------------------- | -------------------------------------------------------------------------------------- | ------- |
| `revenue_growth_yoy`          | (Current revenue − prior-year revenue) / prior-year revenue                            | `ratio` |
| `operating_income_growth_yoy` | (Current operating income − prior-year operating income) / prior-year operating income | `ratio` |
| `net_income_growth_yoy`       | (Current net income − prior-year net income) / prior-year net income                   | `ratio` |
| `eps_basic_growth_yoy`        | (Current basic EPS − prior-year basic EPS) / prior-year basic EPS                      | `ratio` |
| `eps_diluted_growth_yoy`      | (Current diluted EPS − prior-year diluted EPS) / prior-year diluted EPS                | `ratio` |
| `free_cash_flow`              | Operating cash flow − capital expenditure                                              | `USD`   |
| `free_cash_flow_margin`       | (Operating cash flow − capital expenditure) / revenue                                  | `ratio` |

Ratios are decimal strings: `"0.25"` means 25%, not 0.25%. Division uses Decimal precision 28 with round-half-even, independently of the caller's Decimal context; subtraction preserves the input digits without rounding. Every record has `derived=true`, `status`, `formula`, `period`, `period_end` and original reported `inputs`, including units, periods, filing metadata, hashes and locators. Growth inputs also identify their `current` and `prior_year` roles and expose `comparison_period`.

Growth uses actual primary-filing dates, not fiscal labels or a fixed row offset. Both start dates and both end dates must be 358–372 days apart, and the two duration lengths must differ by at most seven days. This permits normal 52/53-week years while rejecting a quarter-to-YTD comparison. There must be one eligible prior-year input with the same taxonomy, tag and unit. A missing or ambiguous input, a concept change, or a zero/negative prior-year value returns `value=null` with a reason. Negative current values are allowed when the baseline is positive. Historical inputs may be older than the requested `count`; only the requested current periods are returned. Current-source amendments follow the statement selection policy, without reconstructing historical information availability.

FCF uses `NetCashProvidedByUsedInOperatingActivities` minus `PaymentsToAcquirePropertyPlantAndEquipment`, with the latter treated as a nonnegative reported cash outflow. This is a defined calculation, not a company's adjusted or non-GAAP FCF. Negative capex is rejected without guessing a sign convention; zero capex and negative operating CF remain valid. FCF inputs, and revenue for FCF margin, must have identical actual periods and USD units. Missing or ambiguous inputs, period mismatches and a zero revenue denominator remain unavailable. Quarterly cash-flow data must be explicitly reported; YTD cash flows are not converted into discrete quarters.

Coverage counts `requested_periods`, `available_periods`, `requested_cells` and `derived_cells`. Missing requested cells or truncated filing history require `allow_partial=true`, even if some calculations succeed. No missing quarter, Q4, currency, or reported financial value is inferred.

## Comparing financials at a common period

`compare` accepts both `statement_metrics` and `analytics_metrics` in one `metrics` list. Its default metrics remain revenue, net income, assets, liabilities and operating cash flow. The shared service exposes the same behavior through CLI, Python, MCP and HTTP:

```python
with Atlas("fixture") as atlas:
    comparison = atlas.get_research(
        {
            "dataset": "compare",
            "identifiers": ["ATLASDEMO"],
            "metrics": ["revenue", "revenue_growth_yoy", "free_cash_flow"],
            "period": "annual",
        }
    )
```

The latest reported period end shared by the successfully retrieved issuer histories is selected, within the existing maximum of 20 submission archives per issuer. This can be older than an issuer's latest filing. The service does not compare each issuer's independently selected latest period. For each metric, available records must have identical actual start/end dates and units across issuers. Growth requires identical prior-year comparison dates too; the within-issuer seven-day tolerance does not relax cross-issuer date alignment.

`alignment.period_end` identifies the chosen end. `alignment.metrics` reports each metric's alignment status and comparable issuer count; each cell has `comparable`. A mismatch suppresses that metric's values while preserving periods and provenance. Missing issuers or inputs remain explicit gaps. With no common end, all returned cells are unavailable, `period_ends` is empty, and each issuer's `available_period_ends` explains the available history. Fiscal years ending on different dates may therefore be uncomparable. Partial, unaligned or truncated results require `allow_partial=true`. Comparison coverage distinguishes reported, derived and available cells; it does not treat calculated values as reported facts.

## Ownership records

Form 3/4/5 preserves transaction codes, acquisition/disposition codes, direct/indirect ownership, derivatives, missing prices and footnote IDs. A reported transaction is not automatically interpreted as a purchase or sale. At most 200 footnotes are returned, each limited to 2,000 characters, with explicit truncation fields. Price currency is not inferred; its unit is `as_reported_in_filing`.

13F reports selected eligible quarter-end positions with a reporting lag. It does not establish all current holdings, shorts or trade flows. Because value conventions differ across form versions, Atlas preserves `reported_value_unit=see_filing_instructions` without guessing dollars versus thousands or multiplying values. It does not infer ticker mappings from CUSIPs or query a paid identifier database.

XML discovery uses filing history and safe directory filenames, examining at most six candidates. Unsupported XML forms do not count as successful extraction. The service does not redistribute whole documents, images or attachments.

## Economics and policy

BLS `series` accepts twenty-two registered selectors or their official IDs. The original selectors are `cpi`, `cpi_sa`, `core_cpi`, `unemployment`, `participation`, `payrolls`, `hourly_earnings` and `job_openings`; [eight additional price/labour selectors](economic-indicators.md#us-additions) cover PPI, manufacturing, hours, JOLTS flows and long-term unemployment. [Six cost/productivity/trade-price selectors](economic-indicators.md#us-employment-costs-productivity-and-trade-prices) add four quarterly and two monthly series. Unregistered V1 retrieval defaults to the latest three years; paired `start_date`/`end_date` select up to 30 calendar years in ten-year POST requests. Units, seasonal adjustment, footnotes and M13 annual averages are preserved. Quarterly series use Q01–Q04 at calendar quarter ends; Q05 annual summaries remain in the source but are explicitly excluded from these selections. ECI index levels and published productivity/unit-labor-cost changes at annual rates retain separate measurement metadata. The raw `economics` dataset does not calculate changes or reconstruct release-time vintages. `macro_analytics` calculates changes from exact monthly inputs in the selected current/revised source history. A shared cache and private database enforce 25 HTTP attempts per UTC day. Other programs and users sharing an IP may consume the upstream allowance first.

Treasury yields use the selected year's nominal par curve. Each record is one date/maturity pair, so a record limit can truncate a day's curve; coverage identifies truncation. Fiscal Data returns one page of at most 500 recent records. Federal Reserve output contains retained official headlines and links, not general company news or article bodies.

`fx_rates` requires distinct supported currencies: EUR, USD, JPY, GBP, CHF, AUD, CAD, NZD or CNY. `reference_rates` accepts `effr` or `sofr`. `macro_analytics` accepts `cpi_yoy`, `core_cpi_yoy`, `payrolls_change_mom` or `yield_spread_10y_2y`. `treasury_real_yields` accepts one year from 2003 onward. See [macro and FX methodology](macro.md) for windows, formulas, units, missing inputs and attribution requirements.

## Provenance, validation and unavailable data

Raw bytes stay in a private cache. Values carry `raw_sha256`, `retrieved_at` and a JSON pointer or XML local path. XML local paths omit namespaces and use one-based positions. JSON pointers escape `/` as `~1` under RFC 6901. Source names, search results, footnotes and headlines are untrusted text, never instructions.

Retrieving an observation for an earlier year does not establish what was available then. `as_of` / `pit` requests are rejected. Comparisons require common actual periods; issuers with different fiscal calendars may have no comparable values.

Consolidated prices, daily prices and adjustment factors, order books, options, consensus estimates, complete news/transcript coverage, a historical security master and PIT are unavailable. Private APIs or scraping with unclear terms do not fill those gaps. See [DATA_SOURCES](../DATA_SOURCES.md) for source conditions and [validation scope](verification.md) for test evidence. Catalog `live_verified=false` distinguishes offline validation from live access.

## National accounts and Japanese macro

`national_accounts` adds sixteen BEA series: nominal/real GDP, annualized GDP growth, PCE/core PCE indexes, personal/disposable income and personal consumption. Public retrieval requires a free registered `BEA_API_KEY`. `japan_economics` adds twenty-nine BOJ/Statistical Dashboard series without registration: prices, unemployment, short rates, Tokyo FX, Tankan and money/credit statistics. Both support paired observation dates, exact units and original byte evidence. `europe_economics` and `uk_economics` add Eurostat/ONS GDP, prices, employment and real activity through the same interfaces. [Expanded economic indicators](economic-indicators.md). See the [series catalog, history rules and examples](macro.md).

## International discovery and macro panels

Use catalog search to find registered macro/FX queries. `sdmx_catalog` discovers IMF/OECD/ABS dataflows and codes; `international_economics` validates exact dimensions before retrieval. `macro_panel` aligns explicit queries with last/mean aggregation and preserved inputs. [Query fields, limits and examples](international-macro.md).

`activity_economics` supplies twenty-two named US, Australian, Canadian and Chinese selections, including US retail/housing/durable goods/industrial activity and goods/services trade, weekly DOL claims via FRED, and selected GDP/CPI/unemployment observations. It requires `series` and accepts paired `start_date`/`end_date`, `limit`, presentation and quality requirements. Use `atlas research get activity_economics --series ca_cpi --provider fixture --json` for a fictional example. Census and FRED require separate free keys for public reads; [exact selectors, frequencies and source requirements](economic-indicators.md#us-activity-and-weekly-claims) explain the limits.
