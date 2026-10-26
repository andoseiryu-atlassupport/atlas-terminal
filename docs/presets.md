# Research presets and connection diagnostics

One Atlas MCP connection exposes company data, international macro indicators, government curves and reference FX. Named presets expand into ordinary free research queries and execute through the shared batch service. The same service is available in the Python SDK, CLI and local HTTP API. There is no LLM dependency, data subscription or wallet requirement.

Three additional government-bond presets, `us_bond_context`, `japan_bond_context` and `us_japan_bond_context`, combine curves, local changes, funding and optional reference FX. Each accepts a curve `year`, selection, preview and item-level requirements. The year applies to curve items only; dates for funding, CPI and FX remain independent. [Bond preset items and methodology](fixed-income.md#bond-presets-through-one-connection).

`euro_area_bond_context` and `australia_bond_context` add source-local curves, shapes, changes and reference FX. `global_bond_context` retrieves US nominal/real, Japanese, ECB AAA/all-rating and Australian curves with reference USD/JPY, EUR/USD, AUD/USD and SOFR in ten items. A shared year selects curve records only; conventions and observation dates remain source-specific. [Regional details](fixed-income.md#euro-area-and-australia).

## Discover and preview

`us_inflation_costs` combines eight registration-free BLS selections: CPI, PPI final demand, civilian compensation/wage ECI, nonfarm productivity/unit labor cost growth, and import/export price indexes. It retains monthly/quarterly frequencies and distinguishes index levels from published annualized changes. See [definitions and examples](economic-indicators.md#us-employment-costs-productivity-and-trade-prices). It accepts paired optional dates and expands into ordinary BLS reads under the shared API quota.

Additional activity presets are `us_activity_extended` (8 items), `us_trade_overview` (3), `japan_external_corporate` (7), `australia_macro_overview` (4), `canada_macro_overview` (3) and `china_macro_overview` (4). They accept optional paired dates, selected item IDs and quality requirements. Their JSON templates live in `examples/research_presets/`. Preview lists sources and configuration diagnosis checks free Census/FRED key requirements before any read; the other new sources need no key. China's GDP remains annual WDI data; Australia's monthly and quarterly CPI remain distinct. [Definitions and coverage limits](economic-indicators.md).

Five additional economic presets accept optional paired observation dates: `japan_activity_overview` (10 items), `us_activity_overview` (10), `euro_area_macro_overview` (9), `uk_macro_overview` (6) and `global_macro_overview` (10). They cover GDP, real activity, consumption and labour/price indicators through existing batches, with selection, preview and independent errors. BEA items require a free key; other included statistical sources do not. Definitions, annualization, euro-area composition and UK three-month rolling unemployment remain source-specific. [Exact series, source contracts and remaining gaps](economic-indicators.md).

`atlas_research_catalog` includes a `presets` section with preset names, accepted parameters, required fields and item IDs. The full catalog includes input and output schemas; `view="compact"` omits them. Discovery and preview make no source requests.

| Preset                 | Parameters                                                                             | Included item IDs                                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `company_overview`     | Required `identifier`; optional `period` (default `quarterly`) and `count` (default 1) | `profile`, `statements`, `analytics`, `filings`                                                                                |
| `us_macro_overview`    | Optional Treasury `year`                                                               | `cpi`, `cpi_yoy`, `payrolls`, `unemployment`, `treasury_yields`, `effr`, `sofr`                                                |
| `fx_context`           | Required distinct `base_currency` and `quote_currency`; optional Treasury `year`       | `fx`, `us_cpi`, `us_treasury_yields`, `us_effr`, `us_sofr`                                                                     |
| `japan_macro_overview` | Optional paired `start_date`, `end_date`                                               | `cpi`, `unemployment`, `call_rate`, `tankan`, `money_stock_m2`, `usd_jpy_tokyo`                                                |
| `us_japan_overview`    | Optional paired `start_date`, `end_date`; BEA free key for US accounts                 | `us_cpi`, `us_unemployment`, `us_gdp`, `us_pce`, `us_core_pce`, `jp_cpi`, `jp_unemployment`, `jp_call_rate`, `jp_tankan`, `fx` |

The Japanese and US/Japan presets return one latest selected observation per item. Paired dates filter observation period ends, not release dates, and span at most 30 calendar years. Japan data need no registration. The US/Japan preset uses BEA for GDP and PCE; missing `BEA_API_KEY` fails those items independently while other reads continue. A CPI or PCE index level is not an inflation percentage. Ordinary dataset queries retrieve histories; presets provide current/selected context. No common publication-time snapshot is implied.

Company statements request revenue, net income, assets, liabilities and operating cash flow. Analytics request revenue YoY growth, FCF and FCF margin. Both use the requested reported period and count, with explicit partial coverage permitted. Filings return up to five discovery records, not filing text. Each dataset selects its own available records; the preset does not align every financial metric to a common date.

The macro preset reads one latest selected CPI, CPI YoY, payroll, unemployment, EFFR and SOFR observation, and up to 20 nominal Treasury date/maturity records. FX context reads up to five ECB reference observations and US inflation/rate context. US context is not a macro profile for both currencies. Treasury records can cover more than one day; use their actual dates and maturities.

When a selected Treasury item has no explicit year, the plan resolves the current UTC year and reports `year_basis="current_utc_year"`. An explicit year applies only to Treasury; it is not an as-of date for the other items. Use a saved query with an explicit year if that is required. Current/revised observations are not historical release vintages or PIT data.

Preview the exact expanded queries, source IDs and excluded items before executing:

```sh
uv run --offline atlas research preset company_overview --identifier ATLASDEMO --preview --provider fixture --json
uv run --offline atlas research preset fx_context --base-currency USD --quote-currency JPY --include fx,us_sofr --preview --provider fixture --json
```

Use `include` to select a nonempty list of unique item IDs in the desired execution order. Unselected items make no source requests. Unknown parameters, item IDs, URLs and payment settings are rejected before execution. `preview=true` returns `mode="preview"`, the plan, an empty overview and `batch=null`.

## Retrieve through one connection

Start the stdio MCP server using the [client configuration](quickstart.md#connect-an-mcp-host). Call `atlas_get_research_preset` with:

```json
{
  "query": {
    "preset": "fx_context",
    "base_currency": "USD",
    "quote_currency": "JPY",
    "include": ["fx", "us_cpi", "us_sofr"],
    "view": "compact"
  }
}
```

The fixture provider uses fictional values, synthetic identifiers and `.invalid` source links. Use the public provider for actual source requests, and configure the operator's private SEC contact setting when selecting company data. Neither contact details nor credentials belong in tool arguments.

Python uses the identical query:

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_research_preset(
        {
            "preset": "fx_context",
            "base_currency": "USD",
            "quote_currency": "JPY",
            "include": ["fx", "us_sofr"],
        }
    )
    print(result["overview"])
    print(result["batch"]["summary"])
```

HTTP accepts the flat query at `POST /v1/research/preset`. It returns HTTP 200 for a valid executed preset, including partial/all-item failures; inspect `status` and `batch.summary`. Invalid envelopes fail before any item runs. Research never purchases or signs, including when the local HTTP API has optional snapshot payments enabled.

## Read observation times and missingness

Each `overview` row corresponds to one planned item and contains:

- `availability`: `available`, `partial`, `unavailable` or `error`. A successful read can contain unavailable values. `available` is not a freshness or completeness certificate; also inspect quality and coverage.
- `returned_records` and `usable_records`, measured over returned leaf records. Failed reads have null counts rather than invented zero observations.
- `units`, source IDs and source-specific `cadence_and_limits`.
- `observation_dates`: earliest/latest usable observation or period end. BLS monthly/quarterly/yearly periods map to calendar period ends; filing discovery uses filing dates, and undated profiles remain unknown. Original periods remain in the batch records.
- `publication_times`: only explicit, timezone-aware `published_at` values, normalized to UTC, with known/unknown record counts. Filing acceptance dates, observation dates and retrieval times do not fill missing publication timestamps.
- `source_retrieval_times`: oldest/newest source retrieval/check used by the selected result, including calculation inputs. Source retrieval is not publication freshness.
- `quality`: complete-result missingness, partial/truncated coverage, derivation and requirement checks. Missingness counts can include unavailable calculation inputs as well as output records.

`batch` preserves the original records, periods, formulas, inputs, terms, attributions and error recovery hints. Compact view is the default; `atlas_get_evidence` resolves its saved provenance IDs on the same private state volume. Choose `view="full"` for inline provenance. The existing evidence lifetime and storage bounds apply. No source text is trusted as instructions.

The preset has `atomic_snapshot=false` and `point_in_time=false`. Items run sequentially, with different source/observation times and normal source caches and quotas. No gap filling, cross-period joining or automatic currency conversion is performed.

## Requirements, failures and saved queries

`requirements` supplies common conditions. `requirements_by_item` overrides only explicitly supplied fields for a selected item, allowing monthly data and daily rates to use different observation-date conditions. Unspecified fields inherit common conditions. Unselected or unknown override keys are invalid.

```json
{
  "preset": "fx_context",
  "base_currency": "USD",
  "quote_currency": "JPY",
  "include": ["fx", "us_sofr"],
  "requirements": { "allow_missing": false },
  "requirements_by_item": {
    "fx": { "min_observation_date": "2026-10-19" },
    "us_sofr": { "min_observation_date": "2026-10-16" }
  }
}
```

Each item retains its own success/error and next action. A failed quality condition returns `QUALITY_NOT_MET` with its quality report, and other items still execute. `batch.summary.retryable_read_ids` lists eligible failed reads; nothing retries a whole preset automatically. A preview is not an execution token or a promise that later sources will be unchanged.

Save a query as JSON and rerun it through CLI `--input`, SDK or HTTP. This adds no background scheduler or persistent watchlist service. Three examples are included:

```sh
uv run --offline atlas research preset --input examples/research_presets/company_overview.json --provider fixture --json
uv run --offline atlas research preset --input examples/research_presets/us_macro_overview.json --provider fixture --json
uv run --offline atlas research preset --input examples/research_presets/fx_context.json --provider fixture --json
```

`--input -` reads stdin. File/stdin input cannot be combined with positional/query flags; `--preview` can be used to inspect a saved query. Requests are limited to 16 KiB, with at most ten planned reads. The existing batch payload budget is 4 MiB; the preset envelope, overview and batch together are bounded to 5 MiB. CLI exits 1 for any failed read and 0 for a successful read set or preview. Successful reads containing unavailable data still require inspection of `overview`.

## Diagnose configuration and connectivity

Configuration diagnosis makes no source requests, constructs no source clients and does not expose actual contact settings, secrets or private state paths:

```sh
uv run --offline atlas research doctor --sources ecb,new_york_fed --json
uv run --offline atlas research doctor --json
```

The public provider reports SEC as `configuration_required` until its private `SEC_USER_AGENT` passes the same validator as SEC retrieval. BEA, Census and FRED are `configuration_required` until their private free registered `BEA_API_KEY`, `CENSUS_API_KEY` and `FRED_API_KEY` settings are present. Other sources need no API key. `configured` only means the selected source configuration is ready; connectivity is `not_checked`. The default all-source diagnostic exits 4 if SEC, BEA, Census or FRED configuration is missing, even when non-SEC sources are configured.

Explicitly opt in to fixed read-only connectivity probes:

```sh
uv run atlas research doctor --sources ecb,new_york_fed --allow-network --json
```

MCP uses `atlas_research_diagnostics` with `{"query":{"sources":["ecb","new_york_fed"],"allow_network":false}}`. SDK uses `atlas.research_diagnostics(...)`. HTTP accepts the flat query at `POST /v1/research/diagnostics`. The provider is configured by the operator, not changed through diagnostic arguments.

Opted-in probes reuse [official source checks](source-checks.md), source caches/quotas and private last-success baselines in the configured state directory. Individual failures remain visible with safe next actions. `verified` means all selected fixed probes passed in that run, not that every dataset is available or up to date. Failed probes report `degraded`; fixture diagnostics always report `synthetic` and never probe live sources, even with opt-in. CLI exits 3 for degraded checks and 4 for required configuration; HTTP/MCP return the diagnostic report for inspection.

Live connectivity, source coverage and remaining upstream quotas cannot be established by configuration checks or fixtures. Diagnostic results do not install a release/holiday calendar or continuous monitor. [Validation scope](verification.md).
