# CPI and employment breakdowns

Atlas exposes US price and labor detail through the shared SDK, CLI, REST and MCP research service. The source checkout has 176 BLS selectors and four separately published Cleveland Fed inflation selections. These are registered definitions, not a claim that every selection has been retrieved live. [Verification](verification.md#us-cpi-and-employment-breakdowns) separates fictional tests, retained BLS replay and the observed Cleveland connection failure.

## Discover a category

```sh
atlas research catalog --topic cpi --breakdown housing --json
atlas research catalog --topic employment --breakdown occupation --json
atlas research catalog --topic employment --search US-CA --json
atlas research catalog --search トリム --json
```

Catalog requests are offline. `topic` is `cpi` or `employment`; `breakdown` matches a level after the topic in `category_path`. `series_search.groups` returns category paths and counts before the 100 full / 20 compact result limit. For example, `["employment", "region", "state"]` describes state estimates, and `search=US-CA` selects California's two series. Invalid or unknown combinations do not trigger upstream discovery or fabricate cross-tabulations.

## CPI definitions and changes

All BLS selections below are CPI-U for the US city average. The base selector is unadjusted (NSA); append `_sa` for the separately published seasonally adjusted (SA) index. `CUUR0000` / `CUSR0000` plus the suffix below are the exact source IDs.

| Selector                     | BLS suffix | Meaning                                                             |
| ---------------------------- | ---------- | ------------------------------------------------------------------- |
| `cpi`                        | `SA0`      | All items                                                           |
| `core_cpi`                   | `SA0L1E`   | All items less food and energy                                      |
| `goods_cpi`                  | `SAC`      | All commodities, including food and energy commodities              |
| `core_goods_cpi`             | `SACL1E`   | Commodities less food and energy commodities                        |
| `services_cpi`               | `SAS`      | Services, including shelter and energy services                     |
| `core_services_cpi`          | `SASLE`    | Services less energy services; includes shelter                     |
| `shelter_cpi`                | `SAH1`     | Shelter aggregate, including rent, OER and other shelter components |
| `rent_cpi`                   | `SEHA`     | Rent of primary residence                                           |
| `owners_equivalent_rent_cpi` | `SEHC`     | Owners' equivalent rent of residences                               |
| `food_cpi`                   | `SAF1`     | Food                                                                |
| `energy_cpi`                 | `SA0E`     | Energy                                                              |
| `durables_cpi`               | `SAD`      | Durable commodities                                                 |
| `nondurables_cpi`            | `SAN`      | Nondurable commodities                                              |

OER measures the rental service of owner-occupied housing, not house purchase prices or mortgage payments. Its base is **December 1982=100** (`index_1982_12_100`, `base_period="1982-12"`); other listed indexes use 1982–1984=100. See [BLS definitions](https://www.bls.gov/cpi/factsheets/owners-equivalent-rent-and-rent.htm) and the exact `definition_url` returned with each added selection.

For every base selector above, `macro_analytics` accepts `_yoy` (exact same month one year earlier, NSA) and `_mom` (exact preceding calendar month, SA). Both return decimal **ratios**, with formula `(current - baseline) / baseline`, both original inputs and 28-digit division precision. Multiply a ratio by 100 only for percentage display. Missing, zero or negative index baselines remain unavailable; M13 annual averages are not monthly inputs. Components overlap and are not additive. The separate [CPI effects service](cpi-effects.md) supplies reported all-items effects and relative importance, with conditional core NSA calculations and explicit stacking sets. It does not reconstruct a services-ex-shelter “supercore” index.

## Median and trimmed inflation

`inflation_measures` reads the Cleveland Fed's [two published CSVs](https://www.clevelandfed.org/indicators-and-data/median-cpi), without a key:

| Selector                      | Source CSV column      | Unit      | Annualized |
| ----------------------------- | ---------------------- | --------- | ---------- |
| `trimmed_mean_cpi_mom`        | `trim_monthly_chg`     | `ratio`   | false      |
| `trimmed_mean_cpi_annualized` | `trim_ann_monthly_chg` | `percent` | true       |
| `median_cpi_mom`              | `mcpi_monthly_chg`     | `ratio`   | false      |
| `median_cpi_annualized`       | `mcpi_ann_monthly_chg` | `percent` | true       |

All four are SA. The 16% trim excludes **8% of expenditure weight at each tail** of the monthly component price-change distribution. Median CPI uses its weighted 50th percentile. These are distinct from fixed food/energy exclusions and Dallas Fed trimmed-mean **PCE**. Atlas preserves the published annualized column instead of recalculating it from rounded monthly values.

CSV date labels such as `3/1/2026` identify a month; output uses `2026-03-31` with `date_basis="observation_period_end"`, retaining `source_period`. The original CSV row, column, header, cell spelling, URL, byte hash and retrieval time accompany each value. Missing cells stay null. Changed headers, duplicate months, nonfinite values and invalid periods fail closed. Each file is bounded to 2 MiB / 3,000 data rows and shares the normal six-hour cache and TLS/proxy transport. Paired observation-date filters and the common 30-calendar-year query limit apply; there is no release-time vintage or inferred publication date.

The [Cleveland Fed reuse permission](https://www.clevelandfed.org/privacy) covers attributed noncommercial, personal or educational use. Commercial redistribution rights have not been established. These series are excluded from the BLS seller catalog.

## Employment breakdowns

Each group offers an **employment level** and an **unemployment rate**, with independent source series and evidence.

| Breakdown     | Selectors                                                                                                    | Coverage and unit                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| State         | `employment_state_ca`, `unemployment_state_ca` (replace `ca` with any lower-case state abbreviation or `dc`) | All 50 states plus DC; LAUS, SA; levels in **persons**, rates in percent      |
| Census region | `employment_region_northeast`, `unemployment_region_northeast`                                               | `northeast`, `midwest`, `south`, `west`; LAUS, SA; persons / percent          |
| Age           | `employment_age_25_54`, `unemployment_age_25_54`                                                             | `16_19`, `20_24`, `25_54`, `55_plus`; CPS, SA; **thousand_persons** / percent |
| Occupation    | `employment_occupation_service`, `unemployment_occupation_service`                                           | Five broad CPS groups; **NSA**; thousand_persons / percent                    |

Occupation keys are `management_professional`, `service`, `sales_office`, `natural_resources_construction` (including maintenance), and `production_transportation` (including material moving). Employment uses current occupation; unemployment uses **last occupation** and the experienced labor force, excluding new entrants without previous work. These are occupations, not CES industries or job vacancies. Age groups cover all sexes; `age_min` and `age_max` are inclusive, with null maximum for 55+. Population-control and occupation-classification changes are not harmonized across history.

LAUS is residence-based and distinct from establishment payroll jobs. `region_code` uses `US-CA` for California and `US-CENSUS-NE` for the Northeast (distinct from Nebraska `US-NE`). `survey`, `measure`, `count_basis`, scope, seasonal adjustment and footnotes survive compact projection. Rates use their source-defined labor-force denominators. Do not sum rates or assume independently adjusted levels sum to national totals. Geography, occupation and age are **separate marginal series**; this implementation does not offer state × occupation × age cross-tabulations, counties, cities, detailed occupations or every age band.

Definitions and identifiers follow [LAUS series files](https://download.bls.gov/pub/time.series/la/la.series), [LAUS area codes](https://download.bls.gov/pub/time.series/la/la.area), [BLS series formats](https://www.bls.gov/help/hlpforma.htm) and [CPS definitions](https://www.bls.gov/cps/definitions.htm). The registry does not fetch all states when selecting one series. Existing BLS dated-request chunks, six-hour cache and 25-attempt shared daily budget remain in force.

## Shared calls and Web integration

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    rent = atlas.get_research({"dataset": "economics", "series": "rent_cpi_sa", "limit": 3})
    age = atlas.get_research(
        {"dataset": "economics", "series": "unemployment_age_25_54", "limit": 3}
    )
    trim = atlas.get_research(
        {"dataset": "inflation_measures", "series": "trimmed_mean_cpi_mom", "limit": 3}
    )
```

REST: `POST /v1/research` with those same query objects. MCP: `atlas_get_research` with `{"query": {...}}`. CLI: `atlas research get economics --series unemployment_state_ca --provider public --json`. Set `provider=fixture` for explicitly fictional offline examples. BLS and Cleveland require no API key. The optional Cleveland source is excluded from default source probes; explicitly select `cleveland_fed` for its two [operator checks](source-checks.md#cleveland-fed-inflation-probes).

Six bounded presets are `us_cpi_detail` (ten NSA indexes), `us_cpi_detail_yoy` (their ten annual changes), `us_inflation_trend` (headline/core/trimmed/median monthly ratios), `us_employment_age` (eight items), `us_employment_occupation` (ten) and `us_employment_region` (eight). Use `atlas research preset NAME --preview --json` to inspect the exact queries before fetching. Items may have different latest observation months; the result does not silently align them.

Web clients should refresh the exported [research schema](../schemas/research.schema.json) and [HTTP schema](../schemas/openapi.json), accept `inflation_measures` and the new registered selectors, and expose optional catalog `topic` / `breakdown` filters. Render `category_path`, groups, scope, units, adjustment, null reasons and source evidence from the response. Multiply only `ratio` values by 100 for percentage display; published `percent` values are already percentages. Do not label annualized monthly changes as YoY. The BLS seller query permits registered selectors up to 80 characters so the longer occupation names remain usable; Cleveland measures remain excluded. No scoring or economic formula needs to be reimplemented in the browser.
