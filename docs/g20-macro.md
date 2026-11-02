# G20 annual macro data

`global_economics` retrieves fourteen World Bank World Development Indicators (WDI) selections for 23 countries and the European Union aggregate, including the nineteen G20 countries plus Malaysia, Thailand, Singapore and New Zealand. The 336 country/indicator templates are searchable offline. CLI, Python SDK, MCP and HTTP use the same reader, with no data subscription, API key, wallet or LLM.

These selections are available in the latest source checkout. The existing `v0.7.0` release is unchanged.

## Countries and regional scope

Use an explicit two-letter `country` code:

| Code | Country or aggregate                | WDI code |
| ---- | ----------------------------------- | -------- |
| AR   | Argentina                           | ARG      |
| AU   | Australia                           | AUS      |
| BR   | Brazil                              | BRA      |
| CA   | Canada                              | CAN      |
| CN   | China                               | CHN      |
| DE   | Germany                             | DEU      |
| FR   | France                              | FRA      |
| GB   | United Kingdom                      | GBR      |
| ID   | Indonesia                           | IDN      |
| IN   | India                               | IND      |
| IT   | Italy                               | ITA      |
| JP   | Japan                               | JPN      |
| KR   | Republic of Korea                   | KOR      |
| MX   | Mexico                              | MEX      |
| RU   | Russian Federation                  | RUS      |
| SA   | Saudi Arabia                        | SAU      |
| TR   | Türkiye                             | TUR      |
| US   | United States                       | USA      |
| ZA   | South Africa                        | ZAF      |
| EU   | European Union, publisher aggregate | EUU      |

The [G20 also includes the African Union](https://www.g20.org.za/about-g20/g20-members/). No African Union aggregate is selected: World Bank Africa and Sub-Saharan Africa are different geographies. `AU` means Australia. EU observations overlap its member countries; summing overlapping selections double-counts those countries. The country list describes the current selection universe, not historical G20 membership or a reconstructed historical EU composition.

## Indicators and units

| `series`                  | WDI identifier    | Normalized unit and definition                                    |
| ------------------------- | ----------------- | ----------------------------------------------------------------- |
| `gdp_nominal`             | NY.GDP.MKTP.CD    | Full current US dollars                                           |
| `gdp_real`                | NY.GDP.MKTP.KD    | Constant 2015 US dollars                                          |
| `gdp_growth`              | NY.GDP.MKTP.KD.ZG | Published annual real GDP growth, percent                         |
| `gdp_per_capita`          | NY.GDP.PCAP.CD    | Current US dollars per person                                     |
| `gdp_ppp`                 | NY.GDP.MKTP.PP.CD | Current international dollars, purchasing-power parity            |
| `cpi_inflation`           | FP.CPI.TOTL.ZG    | Annual consumer-price inflation, percent                          |
| `unemployment`            | SL.UEM.TOTL.ZS    | Total unemployment, percent of labour force, modeled ILO estimate |
| `population`              | SP.POP.TOTL       | Publisher population estimates, persons                           |
| `current_account`         | BN.CAB.XOKA.GD.ZS | Balance of payments current account, percent of GDP               |
| `exports`                 | NE.EXP.GNFS.ZS    | Goods and services exports, percent of GDP                        |
| `imports`                 | NE.IMP.GNFS.ZS    | Goods and services imports, percent of GDP                        |
| `investment`              | NE.GDI.TOTL.ZS    | Gross capital formation including inventories, percent of GDP     |
| `central_government_debt` | GC.DOD.TOTL.GD.ZS | Central-government debt, percent of GDP                           |
| `official_exchange_rate`  | PA.NUS.FCRF       | Local currency per USD, official annual period average            |

Values remain exact decimal strings. Source display-decimal metadata does not round a cell. Reported negative growth, inflation and external balances remain negative; zero remains usable. CPI inflation is not a monthly index or necessarily December-on-December growth. PPP international dollars and market-exchange-rate USD remain different units. Central-government debt does not represent general-government debt. The annual official exchange rate is not a current spot quote or an Atlas currency conversion. Historical LCU denominations follow the publisher's conventions; Atlas does not assign today's ISO currency to past observations.

WDI may report national estimates, modeled observations and country-specific reference-year conventions. Metadata notes identify original producers and definitions. December 31 maps the source year into the date selector; it does not establish an actual fiscal period end, census date or release timestamp. Calendar alignment in a panel does not reconcile these conventions.

## Queries and country overview

```sh
uv run --offline atlas research catalog --dataset global_economics --country IN --json
uv run --offline atlas research get global_economics --country IN --series gdp_growth --start-date 2000-01-01 --end-date 2025-12-31 --json
uv run --offline atlas research preset g20_country_overview --country BR --preview --json
uv run --offline atlas research preset g20_country_overview --country BR --json
```

Public reads require ordinary network access to `api.worldbank.org`. Use `--provider fixture` for explicitly fictional offline responses. The fixture templates contain no actual national statistics.

The separate `country_macro_overview` preset accepts every registered geography, including `MY`, `TH`, `SG` and `NZ`, with the same ten annual items. Their [CPI and government-rate coverage](country-macro-bonds.md) uses distinct IMF/OECD definitions. The original G20 preset continues to reject nonmember countries.

The `g20_country_overview` preset returns GDP level/growth, inflation, unemployment, GDP per capita, population, exports, imports, current account and central-government debt. Its ten independent reads share source caches, preserve separate errors and accept `include`, date bounds and per-item quality requirements. Defaults cover the last three completed source-year labels. Explicit paired date bounds select up to thirty years using inclusive December 31 labels. The preset returns up to thirty rows per item; ordinary queries use the common `limit` and snapshot paging options.

SDK example:

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    result = atlas.get_research(
        {
            "dataset": "global_economics",
            "country": "ID",
            "series": "cpi_inflation",
            "start_date": "2020-01-01",
            "end_date": "2025-12-31",
        }
    )
```

The same JSON works with MCP `atlas_get_research`, HTTP `POST /v1/research` and saved CLI queries. [An annual panel example](../examples/research_panels/g20_growth.json) compares explicit Indian, Brazilian and Indonesian growth series through the existing `macro_panel` service. Panels and batches accept at most ten independent queries; no unbounded all-country request is hidden behind one query.

## Missingness, provenance and history

Registration does not establish observation availability. In particular, debt, external-account and regional FX selections can have long gaps or only null cells. Each null remains unavailable. Nonempty source status flags and future reference-year values remain unavailable with their original values retained. Missing years appear in coverage; no interpolation, forecast substitution or alternate-source fallback occurs. A response with no eligible rows raises `INCOMPLETE_COVERAGE`.

Every read checks the requested country/aggregate metadata, WDI source `2`, indicator identity and expected unit/base definition. A changed definition, foreign country, duplicate year, incomplete page or malformed numeric value fails explicitly. Original country metadata, indicator metadata and observation bytes retain hashes, retrieval timestamps and JSON pointers. `lastupdated` is a dataset update date, not an observation publication time; unknown publication times remain null.

The shared private cache retains source versions and local observation events. [Source history](source-history.md) can retrieve captured bytes and local-observation `as_of` versions. Historical WDI year queries return current revised values; they do not establish release-time PIT availability. Metadata is cached for one day and observations for six hours.

[World Bank API documentation](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures) and [data licenses](https://datacatalog.worldbank.org/public-licenses#cc-by) apply independently of the code license. Credit World Bank WDI and the original producers. Retain dataset and third-party exceptions; do not imply endorsement.

## Opt-in verification

```sh
uv run --offline python scripts/verify_g20_live.py --allow-network --output build/g20-live
```

The script checks all twenty areas and fourteen indicators over 2020 through the last completed year. It saves raw responses, normalized results, exact source-cell checks and a coverage summary under private ignored output. It preserves its SQLite cache across runs. Optional `--countries`, `--series`, `--start-year` and `--end-year` narrow the selection. Offline CI does not make source requests. See [dated verification results](verification.md#g20-annual-macro-coverage).
