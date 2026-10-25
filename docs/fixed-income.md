# Government bond research across four regions

Retrieve government curves, inspect their shape and changes, and resolve the original observations through one Atlas MCP connection. `jgb_yields` adds Ministry of Finance Japan data to US nominal/real Treasury curves, overnight rates, inflation and reference FX. CLI, SDK, MCP and HTTP share these services. These curves require no subscription, wallet or API key.

## Try fictional observations

From a source checkout with locked dependencies installed:

```sh
uv run --offline atlas research preset us_bond_context --year 2025 --provider fixture --json
uv run --offline atlas research preset japan_bond_context --year 2026 --provider fixture --json
uv run --offline atlas research preset us_japan_bond_context --year 2026 --preview --provider fixture --json
uv run --offline atlas research get jgb_yields --year 2026 --limit 15 --provider fixture --json
```

Every fixture observation is fictional. The 2025 US fixture has multiple dated nominal and real curves. Japanese fixtures include negative/zero yields, missing tenors and a revised overlapping date. Offline success establishes parser/calculation behavior, not live source compatibility.

For actual data, omit `--provider fixture` and `--offline`. The host must permit HTTPS to `home.treasury.gov` and `www.mof.go.jp`; funding, CPI and FX use their separately listed sources. Live Treasury/MOF retrieval for this expansion remains unverified in the development environment.

## Curves and original evidence

| Dataset                | Interpretation                                                    | Selection                                             |
| ---------------------- | ----------------------------------------------------------------- | ----------------------------------------------------- |
| `treasury_yields`      | US nominal par yields in `percent_per_annum`                      | Selected `year`; one date/maturity per record         |
| `treasury_real_yields` | US TIPS-based real par yields, 5/7/10/20/30 years                 | Selected `year` from 2003                             |
| `jgb_yields`           | Japan nominal constant-maturity yields, 1–10/15/20/25/30/40 years | Optional `year` or paired inclusive observation dates |

The MOF adapter reads fixed English CSV files for [history](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv) and the [current month](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv). Dates become ISO dates; `10Y` becomes `10YEAR`. Decimal percent values, including negative and zero yields, remain unchanged. `-`, blanks and absent supported tenors remain unavailable.

The complete current-month row takes precedence on overlapping dates. Its missing values are not filled from older rows. Coverage reports overlap counts. Duplicate dates within a file, duplicate/unknown columns, inconsistent rows and malformed dates/numbers fail explicitly. Parsing accepts UTF-8 with an optional BOM or CP932, without replacement decoding.

Evidence retains URL, original byte hash, retrieval time and one-based `csv_row`/`csv_column`. Rows count logical CSV records, including title and header. `csv_header` identifies the source tenor. Compact evidence IDs resolve through `atlas_get_evidence`. Keep MOF attribution, Atlas modification notices and the [source conditions](../DATA_SOURCES.md).

Limits count records, not complete curves. `limit=15` returns the latest Japanese date across all supported tenors, including unavailable ones; smaller limits explicitly truncate that curve. Publication times remain unknown. No holidays are filled, and revised files do not establish historical information availability.

## Calculations in basis points

Use `macro_analytics` and an exact registered `series`. Full catalog output includes `curve_series` with terms and interpretation.

| Series                                                                                | Calculation                                                                               |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `yield_spread_10y_2y`, `yield_spread_10y_3m`, `yield_spread_30y_5y`                   | US long nominal yield minus short nominal yield, multiplied by 100                        |
| `jgb_spread_10y_2y`, `jgb_spread_20y_10y`, `jgb_spread_30y_10y`, `jgb_spread_40y_30y` | Japanese long yield minus short yield, multiplied by 100                                  |
| `yield_butterfly_2y_5y_10y`, `jgb_butterfly_2y_5y_10y`                                | `(2 × 5-year − 2-year − 10-year) × 100`; positive means the belly exceeds the wings' mean |
| `par_inflation_compensation_5y`, `_7y`, `_10y`, `_20y`, `_30y`                        | Same-date US nominal minus real par yield at the same maturity, multiplied by 100         |
| `yield_change_2y`, `_5y`, `_10y`, `_30y`                                              | US nominal yield minus its immediately preceding source observation, multiplied by 100    |
| `real_yield_change_5y`, `_10y`, `_30y`                                                | US real yield change on the same basis                                                    |
| `jgb_yield_change_2y`, `_5y`, `_10y`, `_20y`, `_30y`, `_40y`                          | Japanese yield change on the same basis                                                   |

All non-change terms must share an observation date. Nominal/real gaps use the union of source dates and expose a missing side; no different date replaces it. Each result retains the formula, exact inputs, country, currency and interpretation. A difference of `0.25` percentage points equals `25` basis points. Inverted spreads and negative butterflies are valid.

**The nominal/real par gap is an inflation-compensation proxy**, not a zero-coupon breakeven, pure expected inflation or an inflation-swap quote. Curve construction, inflation risk and liquidity affect the gap. No Japanese inflation-linked curve is inferred.

Changes retain `comparison_date` and `elapsed_calendar_days`. Friday-to-Monday spans three days; it is not a one-calendar-day return. A null preceding yield is not skipped. US calculations use the selected calendar year(s); a predecessor before that window is unavailable. Japanese calculations can use a preceding historical-file row. Output filters and limits apply after matching inputs.

```sh
uv run --offline atlas research get macro_analytics --series yield_spread_10y_3m --year 2025 --provider fixture --json
uv run --offline atlas research get macro_analytics --series par_inflation_compensation_10y --year 2025 --provider fixture --json
uv run --offline atlas research get macro_analytics --series jgb_yield_change_10y --start-date 2026-10-19 --end-date 2026-10-19 --provider fixture --json
```

Date ranges require both ordered bounds, at most 30 calendar years, and no `year`. US nominal analytics start in 1990; real-input calculations start in 2003. [Macro panels](international-macro.md) support these single-valued series through explicit `last`/`mean` aggregation with original dates.

## Euro area and Australia

`euro_area_yields` requires `series=aaa_spot` or `series=all_spot`. ECB YC publishes separate fitted, continuously compounded zero-coupon spot curves for AAA-rated and all-rated euro area central-government bonds. These are composite euro area curves, not Germany, France, Italy, the UK or all European sovereigns. Atlas uses `country=EA` as a region label and retains ECB reference area `U2`; currency stays EUR.

The 11 selected tenors are 3/6 months and 1/2/3/5/7/10/15/20/30 years. Fixed requests select the exact rating universe, spot-rate codes and one calendar year per file. The parser checks full series keys, conflicting dimension fields, `UNIT=PCPA`, zero unit multiplier, dates and observation status. Unknown/missing statuses do not become observed rates; duplicate tenor/date rows fail. No par-to-spot, compounding or currency conversion is performed.

`australia_yields` reads the RBA F2 CSV and maps the exact daily nominal series `FCMYGBAG2D`, `FCMYGBAG3D`, `FCMYGBAG5D`, `FCMYGBAG10D`, regardless of column position. Daily-frequency and percent-unit metadata are required. RBA's published interpolation is identified in `rate_type`; Atlas performs no extra interpolation. Indexed bonds in the same table are excluded, and no unsupported 30-year or Australian inflation-compensation curve is invented. The source compounding convention is preserved without claiming that it has been normalized to ECB continuous compounding.

Both datasets accept an optional `year` or paired inclusive observation dates. ECB coverage starts in 2004 and RBA F2 in 2013. The standard 30-calendar-year bound applies. ECB changes use selected source years and expose a missing predecessor before that window; RBA can use a preceding row from its published full-history file. Null preceding values are not skipped. Limits apply after date filtering; the latest complete selected tenor set takes 11 ECB records or four RBA records.

All values keep original CSV row/column evidence, publisher attribution, `rate_type`, `compounding`, `curve_universe`, country and currency. Compact field selection retains these interpretation fields. Source estimations are publisher observations; Atlas's own slopes/butterflies/changes are separately marked derived.

Registered `macro_analytics` series add:

- `euro_aaa_spread_10y_2y`, `_30y_5y`, `_30y_10y`, `euro_aaa_butterfly_2y_5y_10y` and `euro_aaa_yield_change_2y`, `_5y`, `_10y`, `_30y`.
- The same eight names with the `euro_all` prefix use only the all-rating curve.
- `australia_spread_10y_2y`, `_10y_3y`, `australia_butterfly_2y_5y_10y` and `australia_yield_change_2y`, `_3y`, `_5y`, `_10y`.

They use the existing exact basis-point formulas within one source convention. The AAA/all-rating distinction is not an individual sovereign credit spread. Cross-country yields are not made economically equivalent by sharing a unit label.

```sh
uv run --offline atlas research get euro_area_yields --series aaa_spot --year 2026 --limit 11 --provider fixture --json
uv run --offline atlas research get australia_yields --year 2026 --limit 4 --provider fixture --json
uv run --offline atlas research preset euro_area_bond_context --year 2026 --provider fixture --json
uv run --offline atlas research preset australia_bond_context --year 2026 --provider fixture --json
uv run --offline atlas research preset global_bond_context --year 2026 --provider fixture --json
```

The regional presets add curves, local shapes/changes and reference EUR/USD or AUD/USD. The global preset retrieves US nominal/real, Japanese, both euro area and Australian curves, three reference FX pairs and SOFR in ten independent items. It uses the same six research MCP tools, with preview, per-item quality controls and resolved evidence. It does not imply synchronized observations, a currency-hedged return or a global bond risk model.

Public execution requires HTTPS access to `data-api.ecb.europa.eu` and `www.rba.gov.au`. ECB/RBA wire contracts were inspected through public code references; direct live retrieval and the current source-terms pages remain unverified under development-host restrictions. Preserve [ECB conditions](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html), [RBA copyright/third-party exceptions](https://www.rba.gov.au/copyright/) and original provider notices. Fixture values are fictional.

## Bond presets through one connection

| Preset                  | Included context                                                                                         |
| ----------------------- | -------------------------------------------------------------------------------------------------------- |
| `us_bond_context`       | Nominal/real curves, 2s10s/3m10y/5s30s, butterfly, 10-year par gap, nominal/real 10-year changes, SOFR   |
| `japan_bond_context`    | JGB curve, 2s10s/10s30s/30s40s, butterfly, 10-year change, BOJ call rate, Japanese CPI level             |
| `us_japan_bond_context` | Both curves, local 2s10s and 10-year changes, US 10-year par gap, SOFR, BOJ call rate, reference USD/JPY |

An optional `year` applies to curve items only. Omission resolves the current UTC year in the inspectable plan; funding, CPI and FX retain independent available dates. The CPI item is an index level. This is not an atomic snapshot or currency-hedged relative-value calculation. Selection, preview and per-item quality requirements use the existing [preset contract](presets.md).

For MCP, call `atlas_get_research_preset` with `{"query":{"preset":"japan_bond_context","year":2026}}`. SDK `Atlas.get_research_preset` and HTTP `POST /v1/research/preset` take the inner query object directly. Direct reads use `atlas_get_research`, `Atlas.get_research` and `POST /v1/research`. No additional MCP tool or connection is needed.

Security-specific prices, coupon schedules, accrued interest, duration/DV01/convexity, key-rate risk, FX hedge costs, auction supply, repo quotes and credit/OAS are not supplied. Curve yields alone do not determine security total returns or portfolio P&L.

[Source checks](source-checks.md) · [Macro methodology](macro.md) · [Citation rules](citations.md)
