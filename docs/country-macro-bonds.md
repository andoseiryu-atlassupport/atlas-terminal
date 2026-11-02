# Selected country macro and government-rate data

`global_economics` covers Malaysia, Thailand, Singapore and New Zealand alongside the existing nineteen G20 countries and EU aggregate: **23 countries plus the EU, 336 annual country/indicator templates**. Fourteen WDI indicators include GDP, annual inflation, modeled unemployment, external accounts, population, central-government debt and annual average FX. Definitions and gaps follow the [WDI guide](g20-macro.md). Missing central-government debt is not replaced with general-government debt.

Two additional datasets provide **29 named IMF/OECD selections** for these seven countries. They share CLI, SDK, MCP, HTTP, compact evidence, quality requirements and private local history. No additional API key or paid source is required.

| Country      | Code | CPI index / YoY | Policy-related rate | Government-bond rate | Treasury-bill yield | OECD long-term yield |
| ------------ | ---- | --------------- | ------------------- | -------------------- | ------------------- | -------------------- |
| South Africa | `ZA` | Monthly         | Monthly             | Monthly              | Monthly             | Monthly              |
| Türkiye      | `TR` | Monthly         | Monthly             | Unavailable          | Unavailable         | Unavailable          |
| Malaysia     | `MY` | Monthly         | Monthly             | Monthly              | Unavailable         | Unavailable          |
| Thailand     | `TH` | Monthly         | Monthly             | Monthly              | Monthly             | Unavailable          |
| Singapore    | `SG` | Monthly         | Unavailable         | Unavailable          | Unavailable         | Unavailable          |
| New Zealand  | `NZ` | **Quarterly**   | Monthly             | Monthly              | Monthly             | Monthly              |
| China        | `CN` | Monthly         | Unavailable         | Unavailable          | Unavailable         | Monthly              |

“Unavailable” means no registered selection in this reader. It does not mean that the country has no bonds, publishes no statistics or has a zero rate. Unsupported combinations return `INCOMPLETE_COVERAGE` before a source request. No alternate rate, maturity, country or provider is substituted. Reporting lag and gaps vary by observation window.

## Queries

Annual data:

```sh
atlas research get global_economics --country MY --series gdp_growth --start-date 2020-01-01 --end-date 2025-12-31 --provider public --json
```

`country_economics` requires `country` and one of `cpi_index`, `cpi_inflation_yoy`, `monetary_policy_related_rate`:

```sh
atlas research get country_economics --country NZ --series cpi_index --start-date 2025-01-01 --end-date 2026-09-30 --provider public --json
```

`country_bond_yields` requires `country` and one of `government_bond_rate`, `treasury_bill_yield`, `long_term_government_yield`:

```sh
atlas research get country_bond_yields --country CN --series long_term_government_yield --start-date 2025-01-01 --end-date 2026-09-30 --provider public --json
```

Both accept paired observation-date bounds and `limit`. Defaults request the current year and three prior years, subject to source coverage and explicit truncation. Dates select inclusive source period ends, not historical information availability. Current/revised values retain original periods, decimal precision, zero/null distinctions, source attributes, XML paths and separate metadata hashes. Projection/forecast flags and future reference periods produce unavailable values while preserving their source values.

Use `atlas research catalog --country ZA --json` for searchable queries. The full catalog's `country_series` lists each binding, source, flow, dimensions, unit, frequency and maturity basis. The generic `international_economics` reader remains available for other explicitly discovered SDMX series.

The new `country_macro_overview` preset supplies the same ten annual WDI items as `g20_country_overview`, while accepting all 24 registered geographies. The G20 preset retains its original nineteen-country/EU scope; the four additional countries are not labeled G20 members.

## Definitions and limits

IMF CPI selects `INDEX_TYPE=CPI`, all items, and the publisher's original index or YoY transformation. `REFERENCE_PERIOD` and `COMMON_REFERENCE_PERIOD` remain distinct. An index-base change is not silently normalized. New Zealand keeps native quarters; no monthly CPI or nowcast is manufactured. Published CPI indices, YoY rates and annual WDI inflation are separate series. [Panels](international-macro.md) require explicit aggregation and reject upsampling.

IMF MFS interest rates follow **national definitions**. `monetary_policy_related_rate` is not a verified current central-bank target, target range or announcement history. Singapore's exchange-rate policy is not represented by an invented interest-rate target. Published series can lag central-bank decisions.

IMF government/bill rates do not establish a uniform maturity, local-currency denomination, month-end fixing or monthly-average convention when that information is absent. `source_maturity` and `source_currency` remain null if omitted. No unmatched two- versus ten-year slope is calculated.

OECD `IRLT` follows its approximately ten-year government-bond, monthly-average definition with national methodology. `benchmark_tenor_years=10` describes that indicator, **not an individual bond's maturity or expiry**. Percentage-per-annum units and zero source multiplier are checked before normalization. No conversion to basis points or second annualization occurs.

These are statistical rates. Individual bond prices, coupons, issuance inventories, executable quotes, complete daily curves and publisher release-time vintages are unavailable. Country/period alignment does not establish comparable credit risk or market conventions.

## Sources and verification

- [IMF CPI](https://data.imf.org/en/datasets/IMF.STA:CPI) and [MFS interest rates](https://data.imf.org/en/datasets/IMF.STA:MFS_IR).
- [OECD long-term interest-rate definition](https://www.oecd.org/en/data/indicators/long-term-interest-rates.html).
- [Singapore government-security benchmarks](https://eservices.mas.gov.sg/statistics/fdanet/BenchmarkPricesAndYields.aspx) and [Türkiye central-bank rates](https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB+EN/Main+Menu/Statistics/Interest+Rates) are reference links, not integrated feeds. Normal requests were blocked by the runtime proxy with 403; no direct-source retrieval was verified. The selected recent Turkish OECD IRLT window returned HTTP 404. This does not establish a national-source outage or absence of those markets.

Original producer terms and [source licenses](../DATA_SOURCES.md) apply. The upstream source-family count remains 24. Actual captures stay in private ignored output; added packaged fixtures are fictional. Explicit verification:

```sh
uv run --offline python scripts/verify_country_macro_live.py --allow-network --output build/country-macro-live
```

See [validation scope](verification.md) for dated source-cell checks and remaining gaps.
