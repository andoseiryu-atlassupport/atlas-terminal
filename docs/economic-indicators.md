# Economic indicators through one connection

Atlas retrieves current/revised official economic observations through the same CLI, Python SDK, six research-only MCP tools and HTTP API. Registered series can be searched offline by country, frequency, unit and source. New indicators use the existing observation-date filters, compact evidence, pagination, quality requirements, batches and calendar panels.

These additions are available in the latest source checkout. They are not included in the immutable `v0.7.0` release. All packaged examples are fictional; live API compatibility remains unverified.

## Coverage

| Dataset             | Registered series | Coverage                                                                                                                                                               |
| ------------------- | ----------------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `economics`         |                16 | US BLS consumer/producer prices, payrolls, manufacturing employment, wages, weekly hours, participation, unemployment duration, job openings, hires, quits and layoffs |
| `national_accounts` |                16 | US BEA GDP, real expenditure components, PCE prices, nominal/real consumption, personal/disposable income and saving rate                                              |
| `japan_economics`   |                22 | BOJ rates/FX/Tankan/money/credit plus government GDP, prices, employment, production/shipments/inventories, retail/household consumption, real wages and housing       |
| `europe_economics`  |                48 | Eight measures for each of the euro area, EU, Germany, France, Italy and Spain                                                                                         |
| `uk_economics`      |                 5 | ONS CPI index, CPI annual inflation, real GDP level, nonannualized GDP growth and rolling unemployment                                                                 |

Series counts include different national or regional selections. They do not count every available source code, or imply that all combinations have a current observation. IMF/OECD metadata discovery remains available for other international series; it is not presented as a fully curated feed for every country.

## Japanese additions

Use `dataset="japan_economics"` and one `series`:

| Series                                                                    | Original survey                                               | Frequency / interpretation                                                          |
| ------------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `gdp_nominal`, `gdp_real`                                                 | Cabinet Office national accounts (`00100409`)                 | Quarterly, seasonally adjusted annual-rate expenditure GDP                          |
| `gdp_deflator`                                                            | Cabinet Office national accounts (`00100409`)                 | Quarterly chain-linked deflator; index, not annualized growth                       |
| `core_cpi`                                                                | Consumer Price Index (`00200573`)                             | Monthly index excluding fresh food; not US core CPI's food/energy definition        |
| `industrial_production`, `industrial_shipments`, `industrial_inventories` | METI industrial production/shipments/inventories (`00550300`) | Monthly seasonally adjusted indexes                                                 |
| `retail_sales`                                                            | METI commercial sales (`00550030`)                            | Monthly nominal retail sales, unadjusted                                            |
| `household_consumption_real`                                              | Family Income and Expenditure Survey (`00200561`)             | Monthly seasonally adjusted real spending index, households with two or more people |
| `job_offers_ratio`                                                        | Employment referrals (`00450222`)                             | Monthly seasonally adjusted active openings-to-applicants ratio                     |
| `real_wages`                                                              | Monthly Labour Survey (`00450071`)                            | Monthly real total cash earnings index, unadjusted                                  |
| `housing_starts`                                                          | Housing starts (`00600120`)                                   | Monthly new dwelling count, unadjusted                                              |

The registration-free Statistical Dashboard API resolves each exact curated indicator label to one active national indicator from its original government survey. Zero or multiple matches return `INCOMPLETE_COVERAGE`. Atlas does not guess a 19-digit identifier, switch to a private-provider indicator or merge differently based historical indexes. Actual availability of the new selectors is unverified against live metadata.

Quarterly Dashboard requests use `Cycle=2` and source periods such as `20263Q00`; Atlas maps this to `2026-09-30`, `period="Q3"`, `frequency="quarterly"`. Monthly periods retain their existing behavior. Source titles, units, base-year labels, adjustment, provisional flags, survey codes and data/metadata locators remain explicit. Observation dates are not release dates.

```sh
uv run --offline atlas research get japan_economics --series gdp_real --provider fixture --json
uv run --offline atlas research preset japan_activity_overview --provider fixture --json
```

## US additions

New BLS selectors are `ppi_final_demand`, `manufacturing_payrolls`, `average_weekly_hours`, `employment_population_ratio`, `job_quits`, `job_hires`, `job_layoffs` and `long_term_unemployment`. The catalog gives exact official series IDs and units. `ppi_final_demand` is an index level based on November 2009; a value is not itself an inflation percentage. Employment and JOLTS levels retain their thousand-person/job scales. Existing BLS V1 quotas and dated ten-year request chunks still apply.

New BEA selectors are `gdp_consumption_real`, `business_investment_real`, `residential_investment_real`, `government_consumption_investment_real`, `exports_real`, `imports_real`, `personal_saving_rate` and `pce_real`. Real GDP components use NIPA table `T10106`; real monthly PCE uses `T20806`; saving uses `T20600`. Annual-rate flows and the nonannualized saving percentage remain distinct. Reported `CL_UNIT`, `UNIT_MULT`, measurement and source line identities are preserved without changing scale or chaining bases.

BEA alone requires a privately configured, free registered `BEA_API_KEY`. Missing configuration fails BEA items independently in a batch or preset.

## Europe

Use `dataset="europe_economics"`. Combine a region prefix (`ea`, `eu`, `de`, `fr`, `it`, `es`) with one measure below, for example `de_industrial_production` or `ea_hicp_yoy`.

| Suffix                  | Eurostat table / selection                       | Meaning                                                                   |
| ----------------------- | ------------------------------------------------ | ------------------------------------------------------------------------- |
| `gdp_nominal`           | `namq_10_gdp`, `CP_MEUR`, `SCA`, `B1GQ`          | Quarterly current-price GDP, million EUR; not annualized                  |
| `gdp_real`              | `namq_10_gdp`, `CLV20_MEUR`, `SCA`, `B1GQ`       | Quarterly chain-linked GDP, million EUR at 2020 reference; not annualized |
| `gdp_growth`            | `namq_10_gdp`, `CLV_PCH_PRE`, `SCA`, `B1GQ`      | Real quarter-on-quarter percent change; not an annualized US growth rate  |
| `hicp`                  | `prc_hicp_minr`, `I25`, `coicop18=TOTAL`         | Monthly all-items HICP, 2025=100, ECOICOP version 2                       |
| `hicp_yoy`              | `prc_hicp_minr`, `RCH_A`, `coicop18=TOTAL`       | Published annual percent change, not a decimal ratio                      |
| `unemployment`          | `une_rt_m`, `PC_ACT`, `SA`, `sex=T`, `age=TOTAL` | Monthly unemployment percentage of the labour force                       |
| `industrial_production` | `sts_inpr_m`, `I21`, `SCA`, `PRD`, `B-D`         | Monthly industrial output index, 2021=100                                 |
| `retail_sales`          | `sts_trtu_m`, `I21`, `SCA`, `VOL_SLS`, `G47`     | Monthly retail volume index, 2021=100                                     |

`EA` means Eurostat's **changing euro-area composition**, not one fixed membership across history. EU uses `EU27_2020`. The original `reference_area`, dimensions and geographic labels accompany every observation. Atlas does not harmonize membership or convert an ECB government-curve regional label into a statistical aggregate.

Eurostat [migrated HICP to ECOICOP version 2 in 2026](https://ec.europa.eu/eurostat/web/hicp/information-data). Atlas explicitly selects `prc_hicp_minr` and `coicop18=TOTAL`; the archived `prc_hicp_midx`/`prc_hicp_manr` series are not silently appended.

The JSON-stat 2.0 adapter validates dataset/agency identity, all dimension codes, singleton non-time dimensions, category positions, periods and cell bounds. Dense and sparse arrays and different time-dimension positions are supported. Missing cells remain null; zeros and negative growth stay valid. Source forecast flags make the output unavailable while preserving the original numeric cell separately. Other original flags, including provisional/break/estimate notices, remain available for interpretation. API update time is not a publication timestamp.

```sh
uv run --offline atlas research catalog --source-id eurostat --country DE --json
uv run --offline atlas research get europe_economics --series ea_hicp_yoy --provider fixture --json
uv run --offline atlas research preset euro_area_macro_overview --provider fixture --json
```

## United Kingdom

`uk_economics` reads exact ONS website JSON time series:

| Series         | CDID / dataset  | Meaning                                                                   |
| -------------- | --------------- | ------------------------------------------------------------------------- |
| `cpi`          | `D7BT` / `MM23` | Monthly CPI all-items index; source title retains its reference year      |
| `cpi_yoy`      | `D7G7` / `MM23` | Monthly published annual inflation percentage                             |
| `gdp_real`     | `ABMI` / `PN2`  | Quarterly chain-linked GDP, seasonally adjusted, source million-GBP scale |
| `gdp_growth`   | `IHYQ` / `PN2`  | Quarterly real GDP growth, seasonally adjusted, not annualized            |
| `unemployment` | `MGSX` / `LMS`  | Age 16+, seasonally adjusted, **three-month rolling estimate**            |

ONS monthly, quarterly and annual arrays are kept separate. Dataset/CDID identity, source units, dates, duplicate periods and numeric values are validated. Units/titles stay source-reported; `base_period=null` does not invent a machine-readable index reference year from the retrieval date. Source release-date text is retained with `published_at=null`.

ONS labels each MGSX rolling estimate by its **middle month**. A source label `2026 SEP` means August–October: Atlas retains `source_period` and `source_label_date="2026-09-30"` but returns `period_start="2026-08-01"`, `period_end=date="2026-10-31"` and `reference_period_kind="three_month_rolling"`. Date filters apply to the actual window end. These are monthly observations of overlapping windows, not three independent monthly unemployment measurements. Calendar panels preserve that definition.

```sh
uv run --offline atlas research get uk_economics --series gdp_growth --provider fixture --json
uv run --offline atlas research preset uk_macro_overview --provider fixture --json
```

## Batches, presets and aligned panels

Five additional presets expand into inspectable ordinary queries: `japan_activity_overview` (10 items), `us_activity_overview` (10), `euro_area_macro_overview` (9), `uk_macro_overview` (6) and `global_macro_overview` (10). They accept paired optional dates, item selection, preview, compact view and quality requirements. Dates and source definitions stay independent; a preset is not an atomic global snapshot or a release-surprise feed.

MCP arguments wrap the query:

```json
{
  "query": {
    "preset": "global_macro_overview",
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "view": "compact"
  }
}
```

Submit this to `atlas_get_research_preset`. Python `Atlas.get_research_preset` and HTTP `POST /v1/research/preset` receive the inner query directly. Use `macro_panel` with explicit `last`/`mean` aggregation for calendar alignment; it preserves source units, annualization, bases, rolling-period definitions, inputs, missingness and independent errors. Calendar alignment does not establish comparability.

## Remaining coverage gaps

Dedicated US Census retail/housing/durable-goods/trade connectors, Federal Reserve industrial production, US weekly claims, Japanese machinery orders/trade/current-account connectors and corporate statistics remain gaps. Generic IMF/OECD access can sometimes supply corresponding official measures, but requires explicit metadata selection and does not guarantee identical definitions or timing. Dedicated Australian, Chinese, Canadian and other overseas high-frequency macro feeds are not supplied by this expansion. Existing Australian government yields are a separate dataset.

Consensus expectations, release surprises, private PMI/ISM/conference-board indexes, forecasts and historical release vintages are not inferred or substituted. A free article or chart does not establish redistribution rights to its underlying private series.

[Source conditions](../DATA_SOURCES.md) · [History and macro conventions](macro.md) · [Presets](presets.md) · [Panels](international-macro.md) · [Verification](verification.md)
