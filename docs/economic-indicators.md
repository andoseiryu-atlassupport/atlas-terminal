# Economic indicators through one connection

Atlas retrieves current/revised official economic observations through the same CLI, Python SDK, six research-only MCP tools and HTTP API. Registered series can be searched offline by country, frequency, unit and source. New indicators use the existing observation-date filters, compact evidence, pagination, quality requirements, batches and calendar panels.

These additions are available in the latest source checkout. They are not included in the immutable `v0.7.0` release. Packaged examples are fictional. Dated private live checks, original-cell matches and remaining selector/key failures are recorded in the [validation guide](verification.md#data-integrity-and-japanese-company-extensions).

## Coverage

| Dataset              | Registered series | Coverage                                                                                                                                                                 |
| -------------------- | ----------------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `economics`          |                22 | US BLS price/labour statistics, quarterly employment costs and nonfarm productivity/unit labor cost growth, plus monthly import/export prices                            |
| `national_accounts`  |                16 | US BEA GDP, real expenditure components, PCE prices, nominal/real consumption, personal/disposable income and saving rate                                                |
| `japan_economics`    |                29 | BOJ rates/FX/Tankan/money/credit plus government GDP, prices, employment, production, consumption, housing, machinery orders, external accounts and corporate statistics |
| `europe_economics`   |                48 | Eight measures for each of the euro area, EU, Germany, France, Italy and Spain                                                                                           |
| `uk_economics`       |                 5 | ONS CPI index, CPI annual inflation, real GDP level, nonannualized GDP growth and rolling unemployment                                                                   |
| `activity_economics` |                22 | Eleven US activity/trade/claims selections, four Australian, three Canadian and four Chinese selections with original frequencies and definitions                        |

Series counts include different national or regional selections. They do not count every available source code, or imply that all combinations have a current observation. IMF/OECD/ABS metadata discovery remains available for other international series; it is not presented as a fully curated feed for every country.

## Japanese additions

Use `dataset="japan_economics"` and one `series`:

| Series                                                                    | Original survey                                                          | Frequency / interpretation                                                                                           |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `gdp_nominal`, `gdp_real`                                                 | Cabinet Office national accounts (`00100409`)                            | Quarterly, seasonally adjusted annual-rate expenditure GDP                                                           |
| `gdp_deflator`                                                            | Cabinet Office national accounts (`00100409`)                            | Quarterly chain-linked deflator; index, not annualized growth                                                        |
| `core_cpi`                                                                | Consumer Price Index (`00200573`)                                        | Monthly index excluding fresh food; not US core CPI's food/energy definition                                         |
| `industrial_production`, `industrial_shipments`, `industrial_inventories` | METI industrial production/shipments/inventories (`00550300`)            | Monthly seasonally adjusted indexes                                                                                  |
| `retail_sales`                                                            | METI commercial sales (`00550030`)                                       | Monthly nominal retail sales, unadjusted                                                                             |
| `household_consumption_real`                                              | Family Income and Expenditure Survey (`00200561`)                        | Monthly seasonally adjusted real spending index, households with two or more people                                  |
| `job_offers_ratio`                                                        | Employment referrals (`00450222`)                                        | Monthly seasonally adjusted active openings-to-applicants ratio                                                      |
| `real_wages`                                                              | Monthly Labour Survey (`00450071`)                                       | Monthly real total cash earnings index, unadjusted                                                                   |
| `housing_starts`                                                          | Housing starts (`00600120`)                                              | Monthly new dwelling count, unadjusted                                                                               |
| `machinery_orders`                                                        | Cabinet Office machinery orders (`00100401`)                             | Monthly private demand excluding ships and electric power, seasonally adjusted                                       |
| `trade_exports`, `trade_imports`, `trade_balance`                         | Ministry of Finance trade statistics (`00350300`)                        | Monthly nominal totals/balance, unadjusted; original monetary scale retained                                         |
| `current_account`                                                         | Balance of payments (`00350503`)                                         | Monthly current-account balance, unadjusted                                                                          |
| `corporate_profits`, `corporate_capex`                                    | Financial Statements Statistics of Corporations by Industry (`00350600`) | Quarterly ordinary profits and investment excluding software, all industries excluding finance/insurance; unadjusted |

The registration-free Statistical Dashboard API resolves each exact curated indicator label to one active national indicator from its original government survey. Zero or ambiguous matches return `INCOMPLETE_COVERAGE`. When the source retains several active base-year versions of the same index level, Atlas selects the newest explicitly named base as one complete series; it never combines bases. Atlas does not guess a 19-digit identifier, switch to a private-provider indicator or merge differently based historical indexes. Live checks resolved 16 of the 21 Dashboard selectors. The three monthly customs-trade and two corporate-statistics selectors remain unresolved; annual trade values and balance-of-payments trade are not substituted. Current account uses the Dashboard survey code `20160101`.

Quarterly Dashboard requests use `Cycle=2` and source periods such as `20263Q00`; Atlas maps this to `2026-09-30`, `period="Q3"`, `frequency="quarterly"`. Monthly periods retain their existing behavior. Source titles, units, base-year labels, adjustment, provisional flags, survey codes and data/metadata locators remain explicit. Observation dates are not release dates.

```sh
uv run --offline atlas research get japan_economics --series gdp_real --provider fixture --json
uv run --offline atlas research preset japan_activity_overview --provider fixture --json
```

## US additions

New BLS selectors are `ppi_final_demand`, `manufacturing_payrolls`, `average_weekly_hours`, `employment_population_ratio`, `job_quits`, `job_hires`, `job_layoffs` and `long_term_unemployment`. The catalog gives exact official series IDs and units. `ppi_final_demand` is an index level based on November 2009; a value is not itself an inflation percentage. Employment and JOLTS levels retain their thousand-person/job scales. Existing BLS V1 quotas and dated ten-year request chunks still apply.

New BEA selectors are `gdp_consumption_real`, `business_investment_real`, `residential_investment_real`, `government_consumption_investment_real`, `exports_real`, `imports_real`, `personal_saving_rate` and `pce_real`. Real GDP components use NIPA table `T10106`; real monthly PCE uses `T20806`; saving uses `T20600`. Annual-rate flows and the nonannualized saving percentage remain distinct. Reported `CL_UNIT`, `UNIT_MULT`, measurement and source line identities are preserved without changing scale or chaining bases.

BEA requires a privately configured, free registered `BEA_API_KEY`. The additional US activity sources below use separate free keys for Census and FRED. Missing configuration fails only the affected items in a batch or preset.

## US employment costs, productivity and trade prices

These six selections use the existing registration-free BLS `economics` dataset. No FRED or Census key is needed for them.

| Series                         | BLS ID              | Measurement                                                                                        |
| ------------------------------ | ------------------- | -------------------------------------------------------------------------------------------------- |
| `employment_cost_compensation` | `CIS1010000000000I` | Civilian-worker total compensation ECI, seasonally adjusted index, December 2005=100               |
| `employment_cost_wages`        | `CIS1020000000000I` | Civilian-worker wages/salaries ECI, same adjustment and base                                       |
| `nonfarm_productivity_growth`  | `PRS85006092`       | Nonfarm business labor productivity, published quarter-on-quarter percent change at an annual rate |
| `unit_labor_cost_growth`       | `PRS85006112`       | Nonfarm business unit labor costs, same published change convention                                |
| `import_prices`                | `EIUIR`             | Monthly all-commodities import price index, BEA end-use classification, unadjusted, 2000=100       |
| `export_prices`                | `EIUIQ`             | Monthly all-commodities export price index, same classification, adjustment and base               |

ECI and productivity/cost observations use `Q01`–`Q04`. Their normalized dates are March 31, June 30, September 30 and December 31. ECI describes the quarter-end reference month rather than a three-month average; its `reference_period_kind` records that distinction. The ECI selections return index levels, not the adjacent percentage-change columns in the release tables. The [ECI source format](https://download.bls.gov/pub/time.series/ci/ci.txt) and [compensation](https://www.bls.gov/news.release/eci.t01.htm)/[wages](https://www.bls.gov/news.release/eci.t02.htm) tables explain these definitions.

Productivity and unit labor cost growth have `unit="percent"`, `annualized=true` and `reference_period_kind="quarter_over_quarter_change"`. A value of `1.4` means a reported 1.4% annualized change. Atlas neither divides it by 100 nor annualizes it again. These are reported observations, not Atlas-derived growth estimates. The [nonfarm business table](https://www.bls.gov/news.release/prod2.t02.htm) distinguishes quarterly changes at annual rates from annual summaries and year-over-year changes. The [BLS series page](https://data.bls.gov/timeseries/PRS85006092) and [productivity format](https://download.bls.gov/pub/time.series/pr/pr.txt) define the selection and periods.

`Q05` is an annual summary, not a fifth quarter or a replacement for Q4. Quarterly selections exclude such rows and expose `coverage.excluded_annual_summary_rows`; the original rows remain accessible through source bytes/hashes. Missing quarters remain missing. Monthly price selections continue to distinguish `M13` annual averages from monthly observations. Unexpected frequencies fail explicitly. Calendar panels retain the measurement and annualization fields, exclude annual averages and reject upsampling quarterly observations into months.

The [import-price](https://www.bls.gov/news.release/ximpim.t01.htm) and [export-price](https://www.bls.gov/news.release/ximpim.t02.htm) tables publish index bases and separate index levels from percentage changes. These selections cover commodity trade prices, not Census goods/services trade values. The [international price format](https://download.bls.gov/pub/time.series/ei/ei.txt) documents periods and historical frequency changes; older quarterly observations are not silently relabeled monthly.

```sh
uv run --offline atlas research preset us_inflation_costs --preview --provider fixture --json
uv run --offline atlas research preset us_inflation_costs --provider fixture --json
uv run --offline atlas research get economics --series employment_cost_compensation --provider fixture --json
uv run --offline atlas research catalog --dataset economics --frequency quarterly --json
```

`us_inflation_costs` combines the six selections with CPI and PPI final demand in eight inspectable reads. It preserves independent dates and frequencies and accepts paired dates, item selection and quality requirements. Dated reads remain in ten-year chunks under the shared BLS V1 limit of 25 attempts per UTC day; a long multi-item request can consume most of that allowance. Preview is offline and cached reads avoid unnecessary requests. The commands above use fictional data. Separate [recorded live verification](evidence/bls-2026-10-27/README.md) covers GET/dated POST for all six selections and the SDK/MCP preset. V1 does not include their unit/base/annualization metadata; those definitions were checked against official documentation separately.

## US activity and weekly claims

Use `dataset="activity_economics"` with these curated `series` values:

| Series                     | Source and exact selection                       | Frequency / interpretation                                                                 |
| -------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `us_retail_sales`          | Census EITS `marts`, category `44X72`, type `SM` | Monthly advance retail/food-services sales, seasonally adjusted million USD                |
| `us_housing_starts`        | Census EITS `resconst`, `TOTAL`, `STARTS`        | Monthly housing starts, seasonally adjusted annual rate, thousand dwellings                |
| `us_housing_permits`       | Census EITS `resconst`, `TOTAL`, `PERMITS`       | Monthly permits, seasonally adjusted annual rate, thousand dwellings                       |
| `us_durable_goods_orders`  | Census EITS `advm3`, `MDM`, `NO`                 | Monthly durable-goods new orders, seasonally adjusted million USD                          |
| `us_industrial_production` | Federal Reserve G.17 `ip_sa.txt`, `B50001`       | Monthly total industrial production, seasonally adjusted, 2017=100                         |
| `us_capacity_utilization`  | Federal Reserve G.17 `utl_sa.txt`, `B50001`      | Monthly total-industry capacity utilization, seasonally adjusted percent                   |
| `us_initial_claims`        | US Department of Labor via FRED `ICSA`           | Seasonally adjusted count, week ending Saturday                                            |
| `us_continued_claims`      | US Department of Labor via FRED `CCSA`           | Seasonally adjusted insured-unemployment count, week ending Saturday                       |
| `us_trade_exports`         | Census EITS `ftd`, `BOPGS`, `EXP`                | Monthly goods/services exports, balance of payments basis, seasonally adjusted million USD |
| `us_trade_imports`         | Census EITS `ftd`, `BOPGS`, `IMP`                | Monthly goods/services imports, same basis and scale                                       |
| `us_trade_balance`         | Census EITS `ftd`, `BOPGS`, `BAL`                | Reported monthly trade balance, same basis; deficits retain their negative sign            |

The Census API requires a free privately configured `CENSUS_API_KEY`. DOL claims are retrieved through FRED and require a free `FRED_API_KEY`; this is not an ALFRED vintage request. The keys are appended only to outbound requests, excluded from canonical cache/evidence URLs and never accepted as tool arguments. G.17 direct text downloads require no key. Configure process environment privately; Atlas does not automatically load `.env` files.

US trade uses the combined goods-and-services BOP category, not goods-only customs trade or bilateral country totals. The balance is a reported `BAL` cell, never a subtraction across separately retrieved exports/imports. These monthly flows are not annualized. The [Census EITS guide](https://www2.census.gov/api-documentation/EITS_API_User_Guide_Dec2020.pdf) documents `BOPGS` with `EXP`, `IMP` and `BAL`; the [current variable list](https://api.census.gov/data/timeseries/eits/ftd/variables.html) defines the required fields. Use the current [API examples](https://api.census.gov/data/timeseries/eits/ftd/examples.html) for key requirements, which supersede the older guide's optional-key text.

Census queries request `program_code` and `time_slot_id` alongside measure, category, adjustment and error status. Responses preserve those identifiers, reject mixed programs or duplicate observation slots, distinguish national observations from error estimates and keep suppressed cells unavailable. G.17 evidence identifies the exact original text line and inclusive, one-based character columns. Claims retain their original week-ending dates and producer/disseminator attribution. Observation-period ends, source update text and retrieval times remain distinct; unavailable publication timestamps stay null.

```sh
uv run --offline atlas research get activity_economics --series us_industrial_production --provider fixture --json
uv run --offline atlas research catalog --frequency weekly --json
uv run --offline atlas research preset us_activity_extended --preview --provider fixture --json
uv run --offline atlas research preset us_trade_overview --provider fixture --json
```

## Australia, Canada and China

These also use `activity_economics`:

| Series                  | Source                                      | Frequency / interpretation                                                                                    |
| ----------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `au_gdp_real`           | ABS `ANA_AGG`                               | Quarterly seasonally adjusted chain-volume GDP; not annualized; source unit/base/multiplier retained          |
| `au_unemployment`       | ABS `LF`                                    | Monthly seasonally adjusted unemployment rate, persons age 15+                                                |
| `au_cpi_quarterly`      | ABS `CPI_Q`                                 | Quarterly all-groups index, weighted average of eight capital cities, unadjusted                              |
| `au_cpi_monthly`        | ABS `CPI` version `2.0.0`                   | Monthly all-groups CPI index, Australia (`REGION=50`), unadjusted; source base retained                       |
| `ca_cpi`                | StatCan vector `41690973`, table `18100004` | Monthly national all-items CPI, unadjusted, publisher index base                                              |
| `ca_unemployment`       | StatCan vector `2062815`, table `14100287`  | Monthly national unemployment rate, age 15+, seasonally adjusted percent                                      |
| `ca_gdp_real`           | StatCan vector `65201210`, table `36100434` | Monthly all-industry real GDP at basic prices, seasonally adjusted annual rate; source chained-CAD base/scale |
| `cn_cpi`, `cn_cpi_yoy`  | OECD `DSD_PRICES@DF_PRICES_ALL`             | Monthly national all-items CPI index or published year-on-year percent change                                 |
| `cn_gdp_growth_annual`  | World Bank WDI `NY.GDP.MKTP.KD.ZG`          | Annual real GDP growth percent                                                                                |
| `cn_gdp_nominal_annual` | World Bank WDI `NY.GDP.MKTP.CN`             | Annual nominal GDP in current CNY                                                                             |

These endpoints need no API key. ABS reads resolve the source dataflow structure before validating every curated dimension code. Monthly CPI selects `ABS,CPI,2.0.0`, key `1.10001.10.50.M`; quarterly CPI selects `ABS,CPI_Q,1.0.0`, with `FREQ=Q`. The [official ABS update notes](https://www.abs.gov.au/statistics/application-programming-interfaces-apis/data-api-user-guide/updates) describe monthly CPI 2.0.0 from November 2025, the September 2025=100 rebase and the separate quarterly `CPI_Q` flow restored in April 2026. The [current tutorial](https://www.abs.gov.au/statistics/application-programming-interfaces-apis/data-api-user-guide/tutorials-links) publishes the monthly key. Atlas retains source-reported base periods and never splices the two frequencies or substitutes a discontinued monthly indicator. If current metadata lacks the selected quarterly dimensions, the query fails with `INCOMPLETE_COVERAGE`. China GDP is annual WDI data, which can include national statistics, OECD inputs and World Bank estimates; original producer notes are retained. It is not a domestic quarterly GDP or release-time feed.

StatCan metadata, vector/table/coordinate, monthly frequency and unit code must agree. Numeric values retain their reported power-of-ten scale rather than being multiplied twice. Confidential, unavailable, rounded-to-zero, unreliable, not-applicable and below-detection cells remain null; a usable numeric zero remains zero. Original flags and local release-time text stay available. Unknown flags fail explicitly. World Bank pagination and indicator/country identity are validated; nonempty observation-status flags make a cell unavailable while preserving the source value and flag.

ABS/OECD forecast flags are preserved and forecast cells are unavailable as observed values in this curated dataset. Source units, reference years and seasonal definitions are not harmonized across countries. Dated live ABS GDP, monthly CPI and labour reads passed; the registered quarterly CPI key returned HTTP 404 `NoRecordsFound` despite resolving its official structure. Japanese corporate/trade gaps remain explicit. Missing data or changed metadata never selects a different definition or frequency.

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    plan = atlas.get_research_preset({"preset": "canada_macro_overview", "preview": True})
    data = atlas.get_research_preset({"preset": "canada_macro_overview"})
    print(plan["plan"])
    print(data["overview"])
```

Use `Atlas("public")` for actual source requests on a host that permits the required endpoints. Fixture values are fictional.

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

`ea_` GDP and HICP use Eurostat `EA`, its **changing euro-area composition**. The unemployment, industrial-production and retail selectors use explicit `EA21` (21 countries from 2026), because those tables do not expose `EA`. Their fixed-membership definitions remain distinct. EU uses `EU27_2020`. The original `reference_area`, dimensions and geographic labels accompany every observation. Atlas does not harmonize membership or convert an ECB government-curve regional label into a statistical aggregate.

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

The activity expansion adds `us_activity_extended` (8), `us_trade_overview` (3), `japan_external_corporate` (7), `australia_macro_overview` (4), `canada_macro_overview` (3) and `china_macro_overview` (4). Saved JSON examples are in `examples/research_presets/`. Weekly claims can enter monthly/quarterly/annual panels through explicit `last` or `mean` aggregation. Weekly coverage uses observed dates only: no missing-week or holiday calendar is inferred, and any returned null prevents a usable aggregate. Annual GDP cannot be upsampled into monthly observations.

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

Bilateral/detail trade and fuller Census categories, China's domestic quarterly GDP/monthly industrial output/retail/trade, and broader Australian/Canadian demand and labour details remain gaps. The curated registries do not represent every government release. Japanese Dashboard indicators depend on exact active source metadata; registration in the catalog does not establish live availability. Existing Australian government yields are a separate dataset.

Consensus expectations, release surprises, private PMI/ISM/conference-board indexes, forecasts and historical release vintages are not inferred or substituted. A free article or chart does not establish redistribution rights to its underlying private series.

[Source conditions](../DATA_SOURCES.md) · [History and macro conventions](macro.md) · [Presets](presets.md) · [Panels](international-macro.md) · [Verification](verification.md)

BOJ reads accept the official `RESULTSET` container and integer survey periods, preserving exact JSON pointers. Source-null calendar placeholders after retrieval are excluded and counted; past missing observations remain explicit. Dashboard lowercase `cycle`, optional `TO_NUMBER` and blank unit labels are supported. A blank source unit is reported as `source_unspecified` with its original code/label, rather than assigning a fabricated scale.
