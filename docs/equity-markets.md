# US, Japanese, Korean and British fundamental risk

The native cross-sectional estimator supports one country and local currency per model. It estimates market, industry and style returns from a broad supplied stock universe, then uses the same portfolio-risk service as the time-series estimator. All four markets require 30–200 common stocks, fixed industry membership over the fit window and complete adjusted total returns. See [model methodology](risk-model.md) and [input connections](data-connections.md).

| Manifest `country` | `currency` | `industry_classification`                  | Fundamental industry field                          | Methodology               |
| ------------------ | ---------- | ------------------------------------------ | --------------------------------------------------- | ------------------------- |
| `US`               | `USD`      | `sic_division`                             | Four-digit `sic`                                    | `atlas-us-fundamental-v1` |
| `JP`               | `JPY`      | `jpx33`                                    | Four-digit `industry_code`, including leading zeros | `atlas-jp-fundamental-v1` |
| `KR`               | `KRW`      | `ksic10_section` or `ksic11_section`       | One uppercase `industry_code` from `A` through `U`  | `atlas-kr-fundamental-v1` |
| `GB`               | `GBP`      | `uksic2007_section` or `uksic2026_section` | Uppercase `industry_code`: A–U (2007) or A–V (2026) | `atlas-gb-fundamental-v1` |

No price feed, issuer-to-industry database, verified filing-vintage archive or external financial framework is bundled. The extension is experimental. Supplied historical inputs can be used, but source rights and historical availability remain operator assertions. These models do not establish numerical or forecast equivalence with a proprietary vendor model.

## Run a complete local example

```sh
uv run --offline atlas risk demo --country JP --json
uv run --offline atlas risk demo --country KR --json
uv run --offline atlas risk demo --country GB --json
```

Each fictional example reads through a native JSON connection, estimates 60 daily cross sections on 60 assets, analyzes a portfolio/benchmark, applies a market shock and evaluates 20 subsequent sessions. Calendars and company observations are simulated, including the monetary amounts; they are not actual market data. Japan uses JPY/JPX33, Korea uses KRW/KSIC 11 and Britain uses GBP/UK SIC 2007. The `--country` option selects a fundamental demo, not a live-data source. The original US demo and observed-factor time-series demo remain available.

For actual inputs, add named connections to [the template](../examples/risk/connections.template.json), then adapt [the Japanese](../examples/risk/run-japan.json), [Korean](../examples/risk/run-korea.json) or [British request](../examples/risk/run-britain.json). A connection's manifest determines the market; request-side ticker spelling does not change it. Britain uses the ISO country code `GB`.

## Declare the market and currency

Set `country`, `currency` and `industry_classification` explicitly in the `manifest`. Every universe asset must declare the same country/currency and `security_type="common_stock"`. Mixed-country or mixed-currency panels fail. Old US panels can omit these fields and retain US/USD/SIC defaults; Japanese, Korean and British panels must override them.

Every Japanese/Korean/British fundamental vintage must also declare `currency`, matching the manifest. All book equity, total assets, TTM net income, total debt and market capitalizations must be full units of the same local currency, with decimal strings. Normalize thousands/millions explicitly before ingestion; no unit scale is inferred. US fundamental currency can be omitted for compatibility and means USD. A uniform monetary rescaling changes neither dimensionless styles nor portfolio-return risk when applied consistently.

Asset IDs are opaque strings. Alphanumeric Japanese issue identifiers and zero-prefixed Korean identifiers remain strings; Atlas does not infer country, exchange, share class or corporate identity from them. Use explicit column and asset mappings for provider-specific identifiers. No CUSIP/ISIN/ticker master is inferred.

The shared descriptors retain their definitions: log capitalization, equity/capitalization, TTM income/assets, debt/assets, momentum, historical market-proxy beta and residual volatility. The historical proxy is the equal-weight supplied **local** universe. Operators must align consolidation scope, common-equity basis, TTM coverage and accounting definitions across issuers. Atlas does not convert US GAAP, Japanese GAAP, IFRS or Korean accounting reports into comparable statements automatically.

## Supply industry assignments with their availability

Use exactly one fundamental industry field: `sic` for the US or `industry_code` for Japan/Korea/Britain. Blank optional CSV fields are omitted; SQLite NULL is accepted for omitted optional fields. An unknown code, a US SIC in another market or both industry fields fails.

Japan accepts the 33 SICC sector codes, such as `"0050"`, `"3650"` and `"7050"`. It does not reinterpret them as US SIC or accept the `"9999"` catch-all. The code list is inspectable in `atlas risk catalog`. Factors have stable identifiers such as `industry:jpx33_3650`; the catalog supplies readable sector labels. [JPX/SICC classification reference](https://www.jpx.co.jp/english/sicc/sectors/01.html) and [sector-code table](https://www.jpx.co.jp/english/sicc/sectors/p6b22i0000002osw-att/sectors_01.pdf).

Korea accepts supplied KSIC top-level sections `A`–`U`, with the revision declared in the manifest. Factors are labeled, for example, `industry:ksic11_section_C`. These are broad industry controls; manufacturing remains one section. No numeric five-digit-code conversion, KRX sector conversion or fine vendor taxonomy is inferred. KSIC 11 metadata cannot assert availability before its July 1, 2024 KST effective date. The [official revision notice](https://mods.go.kr/board.es?act=view&bid=107&list_no=428660&mid=a10403040000) defines that date; the [official KSIC guidance](https://kosis.kr/civilComplaint/qnaDetail.do?boardIdx=18943) describes the earlier classification. The caller still supplies the issuer assignment valid at each cutoff.

Britain accepts explicit [ONS UK SIC 2007](https://www.ons.gov.uk/methodology/classificationsandstandards/ukstandardindustrialclassificationofeconomicactivities/uksic2007) sections A–U or [UK SIC 2026](https://www.ons.gov.uk/methodology/classificationsandstandards/ukstandardindustrialclassificationofeconomicactivities/uksic2026) sections A–V. These ONS references are available under the Open Government Licence v3.0 except where stated otherwise. The [2026 revision guidance](https://www.ons.gov.uk/methodology/classificationsandstandards/ukstandardindustrialclassificationofeconomicactivities/uksic2026revisionprocess) gives an April 14, 2026 effective date and a staged operational transition. Atlas rejects earlier asserted 2026 classification availability. Section letters from J onward have changed meaning; versions are not interchangeable, and factors/holdouts retain the selected version. A five-digit Companies House code must be mapped explicitly to a section. No ICB/GICS data or automatic issuer classification is supplied.

For relational input, an explicit operator `industry_map` may map source labels/codes to canonical local codes, after `columns.fundamentals` renames the source field to `industry_code`. For example, `{"industry_map":{"source_electronics":"3650"}}` is valid for Japan, and `{"industry_map":{"62020":"J"}}` explicitly maps that source code to a UK SIC 2007 section. Multiple source categories may intentionally map to one canonical industry. The target must pass market-specific validation; Atlas does not certify the supplied crosswalk. JSON input already uses canonical codes and rejects mappings. The mapping hash is saved in the ingestion receipt.

Each fundamental `available_at` must cover the complete financial/classification record. If an industry assignment became available later than its financial statements, use that later availability. Keep true historical assignment vintages; today's assignment is not automatically valid for the past. At least two represented industries and three stocks in each are required; a singleton sector fails rather than being silently merged. Rank/three-observations-per-parameter checks still apply when many of the 33 Japanese sectors are represented.

## Sterling prices quoted in pence

British canonical financials, capitalizations and asset currency are always full `GBP`, not `GBp` or `GBX`. For CSV/SQLite `return_input="adjusted_prices"` with a GBP manifest, every price row must declare `quote_currency="GBP"`, `"GBp"` or `"GBX"`. The last two mean pence: Atlas divides both raw `close` and `adjusted_close` by exactly 100 before capitalization or return ratios. This also handles a declared quote-unit change across sessions. A supplied `market_cap` is already GBP and is never divided by 100. Shares remain whole reported shares. The [LSE price explorer](https://www.londonstockexchange.com/live-markets/market-data-dashboard/price-explorer?categories=EQUITY&showonlylse=true) exposes quotation currencies; no exchange-price dataset is downloaded here.

For example, 250 GBX followed by 2.75 GBP is a 10% return; 275 GBX times 1,000 shares is GBP 2,750 capitalization. Missing, lowercased or foreign quote-currency labels fail for GBP input. No price-magnitude guess or FX conversion is performed. Other currencies may omit this field for compatibility; if present, it must match the manifest. JSON panels and supplied-return tables already contain normalized returns/capitalizations and do not take raw quote-unit fields. The normalization receipt records the conversion rule alongside original input hashes. See [price input contracts](data-connections.md#convert-supplied-prices-to-returns).

## Calendars, outputs and limits

Sessions use explicit time-zone-aware close-to-close boundaries supplied by the operator. Atlas does not infer local holidays, British summer time or insert returns for closed days. US/British bounds are seven calendar days per session and for usable capitalization age; Japan/Korea use fourteen to allow extended closures. These are explicit bounds, not a certified exchange calendar. Longer periods or older capitalization fail. Annualization still uses the fixed 252-session assumption; it is not calibrated separately to each exchange.

All models use the same constrained cross-sectional WLS and EWMA/shrinkage calculations. Summaries, selected exposure vintages, portfolio analysis and holdouts retain the country, currency and classification. Holdouts must match the saved market/currency/classification even when asset IDs are the same. Saved models retain their market-specific methodology version after restart.

The current scope is **separate local-market models**. Aggregating US, Japanese, Korean and British portfolios into one base-currency risk model would additionally require aligned calendars, country/FX exposures and cross-market covariance. Local volatility results alone cannot be combined into a valid global covariance. No such global model, actual-data calibration or exchange-data entitlement is supplied here.
