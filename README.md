# Atlas Terminal

**Free financial data for AI agents through one MCP connection.**

<!-- mcp-name: io.github.andoseiryu-atlassupport/atlas-terminal -->

Atlas Terminal gives AI agents company financials, US/Japan macro indicators and reference FX through one MCP connection, using free public primary sources. The same retrieval, calculation and evidence services are available through a CLI, Python SDK and HTTP API.

Optional [native factor risk models](docs/risk-model.md) analyze supplied [US, Japanese, Korean and British stock histories](docs/equity-markets.md). One request reads a named JSON, CSV or SQLite connection, validates inputs, fits a cross-sectional or time-series model and analyzes a portfolio. The models are experimental; equity prices and verified historical vintages are not included.

[Documentation website](https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site) · [Connect Codex](docs/codex.md) · [Reproducible examples](docs/research-examples.md) · [Capabilities](IMPLEMENTATION_STATUS.md) · [Data sources](DATA_SOURCES.md)

## Try two complete workflows

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas showcase research
atlas showcase japan-risk
```

Both commands use an actual official MCP client connection with **fictional data**, no data account, wallet or LLM. They run from the installed package. Add `--json` to inspect complete queries, calculations and resolved evidence. The walkthroughs use temporary state; embedded reports remain readable after their session ends.

- [Rates and FX walkthrough, 60-second replay and recorded evidence](docs/agent-research-walkthrough.md).
- [Japanese portfolio walkthrough, 60-second replay and recorded model output](docs/japan-risk-walkthrough.md).

Run `atlas showcase research --provider public --allow-network` for actual ECB/New York Fed reads from a host permitted to reach those sources. Risk analysis of your own portfolio requires a broader estimation universe and rights-cleared histories; the fictional walkthrough does not download equity prices.

## Available data

Atlas provides 26 datasets from SEC EDGAR, BLS, US Treasury yields, Treasury Fiscal Data, the Federal Reserve, the ECB, the New York Fed, BEA, the Bank of Japan, Japan's Statistical Dashboard, IMF and OECD.

| Area        | Information                                                                                                                           |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Companies   | Current ticker / CIK directory, issuer profiles, SIC industry, exchanges and fiscal year end                                          |
| Financials  | 37 US-GAAP concepts covering income, balance sheets, cash flow, EPS, shares and dividends; annual, reported discrete quarters and YTD |
| Analysis    | Six ratios, five year-over-year growth rates, free cash flow and FCF margin; comparison of up to ten issuers at a common period       |
| XBRL        | Concept inventory and raw observations with their taxonomy, units and contexts                                                        |
| Disclosures | All-form filing discovery, including ownership, fund, proxy, offering and corporate-action filings                                    |
| Ownership   | Form 3/4/5 transactions and positions; a selected manager's 13F information table                                                     |
| Macro       | Eight BLS series, CPI/core CPI YoY, payroll monthly changes, nominal/real Treasury par yields, 10Y–2Y spread and federal debt         |
| Rates / FX  | EFFR and SOFR overnight rates; ECB daily reference FX for 31 currencies, with traceable inverse and cross calculations                |
| US accounts | BEA nominal/real GDP, GDP growth, PCE/core PCE price indexes, personal/disposable income and consumption                              |
| Japan       | CPI, unemployment, overnight call rate, Tokyo USD/JPY, Tankan, monetary base, M2, bank loans and real effective FX                    |
| Policy      | Federal Reserve release headlines, publication times and original links                                                               |

Values retain their units, periods, source hashes, retrieval times and JSON pointers or XML paths. Missing or ambiguous values are explicit. Derived metrics include their formulas and reported inputs.

Series search filters registered macro/FX templates by text, country, frequency, source and unit. IMF/OECD metadata discovery retrieves new series without indicator-specific adapters; macro panels align observations with explicit aggregation, gaps and source evidence. [Discovery and panel guide](docs/international-macro.md).

## Get started

The package is available from versioned GitHub releases and is not yet published on PyPI. To work in a cloned repository:

```sh
uv sync --locked
uv run --offline atlas research catalog --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
```

The fixture provider runs without network access. Its company identifiers and values are fictional, with `is_synthetic=true` and `.invalid` source links.

For public data, research commands use `--provider public` by default. SEC requests require a private `SEC_USER_AGENT` containing your application name and actual contact email. Other sources do not require that setting.

```sh
uv run atlas research get statements --identifier AAPL --period quarterly --metrics revenue,eps_basic --count 3 --allow-partial --json
uv run atlas research get financial_analytics --identifier AAPL --metrics revenue_growth_yoy,free_cash_flow,free_cash_flow_margin --count 1 --allow-partial --json
uv run atlas research get compare --identifiers MSFT,GOOGL --metrics revenue,revenue_growth_yoy,free_cash_flow --period quarterly --allow-partial --json
uv run atlas research get economics --series cpi --start-date 2000-01-01 --end-date 2026-12-31 --limit 500 --json
uv run atlas research preset japan_macro_overview --json
uv run atlas research get national_accounts --series gdp_real --json
uv run atlas research get treasury_yields --year 2026 --json
uv run atlas research get fx_rates --base-currency USD --quote-currency JPY --json
uv run atlas research get reference_rates --series sofr --json
uv run atlas research get treasury_real_yields --year 2026 --json
uv run atlas research get macro_analytics --series cpi_yoy --json
```

Research needs no data subscription, wallet or LLM. BEA national accounts require a free registered `BEA_API_KEY` set privately in the process environment. Japan macro and other non-SEC sources work without registration. Commands return JSON, and errors use nonzero exit codes. Dataset names, metrics, series identifiers and query limits are listed in `research catalog` and the [research guide](docs/research.md).

Growth matches actual prior-year periods; `0.25` means 25%. Free cash flow is reported operating cash flow minus PP&E acquisition payments. Comparisons select the latest shared reported period end and suppress values with different actual input dates. Missing quarters and nonpositive growth baselines remain unavailable. [Calculation rules](docs/research.md#growth-and-cash-flow-analytics).

FX observations are daily reference rates for information purposes. ECB retrieval uses the full published history, with optional observation-date bounds; EFFR/SOFR retrieval covers the latest 90 observations. Cross rates use inputs from the same date. Missing dates and macro baselines are not filled. [Macro and FX guide](docs/macro.md).

## Python, MCP and HTTP

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_research(
        {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "metrics": ["revenue", "eps_basic"],
            "period": "quarterly",
            "count": 3,
        }
    )
    print(result["records"])
```

Start the MCP server with:

```sh
uv run --offline atlas mcp serve --provider fixture --research-only
```

Research-only mode exposes six tools for datasets, presets, batches, connection diagnostics and saved evidence. It has no quote, purchase or wallet handler. `atlas_get_research` publishes dataset-specific input branches and a common output schema. Use `atlas_research_catalog` with an optional dataset filter and compact view to reduce discovery output. [MCP setup](docs/quickstart.md) · [Codex connection and skill](docs/codex.md).

The [company, CPI and USD/JPY example](docs/company-macro-fx.md) uses one official MCP session to retrieve four datasets and resolve all evidence IDs. Its executable contract evaluation reports explicit quality and unsupported-dataset rejection without an LLM. [Public checks](docs/public-checks.md) separately inspect published documentation HTTP/crawler signals and an optionally configured actual remote endpoint.

The original full stdio mode, without `--research-only`, exposes fourteen tools including company resolution, annual snapshots, filings, budget/preflight inspection and separately configured quote/purchase recovery. Research-only Streamable HTTP is available for [self-hosting](docs/remote-mcp.md). No official public remote endpoint or public ChatGPT plugin is currently operated.

Research accepts optional `response` settings for compact provenance references, field selection and snapshot pagination. `atlas_get_evidence` retrieves the original provenance by ID. Errors include a structured next action and a retry delay when known. The same contracts work through CLI, SDK and HTTP. [Agent usage](docs/agents.md).

Results link the relevant Atlas documentation while preserving original publisher attribution. Cite SEC, BLS, Treasury, Federal Reserve, ECB, New York Fed, BEA, BOJ or Statistics Bureau provenance for numerical facts, and Atlas methodology for its calculation and selection rules. [Citation guidance](docs/citations.md).

Optional `requirements` gate source age, the latest usable observation date, missing values, derived records and truncation before returning data. Failed conditions return `QUALITY_NOT_MET`; pages recheck time-dependent conditions. `Atlas.get_research_batch`, MCP `atlas_get_research_batch`, HTTP `POST /v1/research/batch` and `atlas research batch` process up to ten independent reads with an ID and status per item. [Requirements and batches](docs/agents.md#require-data-quality-explicitly).

```sh
uv run --offline atlas research batch --input examples/research_batch.json --provider fixture --json
uv run atlas research check-sources --sources ecb,new_york_fed --allow-network --json
```

Source checks require an explicit network opt-in, revalidate through shared source caches/quotas and record response hashes, structural fingerprints and observation dates against private last-success baselines. A separate manual workflow supports read-only official-source verification. [Source checks and limits](docs/source-checks.md).

### Multiple datasets through one connection

`company_overview`, `us_macro_overview` and `fx_context` expand into inspectable free research queries. Select items, preview the exact plan, then execute it through the existing batch service. Results include units, source cadence, usable observations, missingness and separate observation/publication/retrieval times. Missing publication times remain unknown; this is not an atomic snapshot.

```sh
uv run --offline atlas research preset company_overview --identifier ATLASDEMO --preview --provider fixture --json
uv run --offline atlas research preset --input examples/research_presets/fx_context.json --provider fixture --json
uv run --offline atlas research doctor --sources ecb,new_york_fed --json
```

SDK `Atlas.get_research_preset`, MCP `atlas_get_research_preset` and HTTP `POST /v1/research/preset` share the same query. `research doctor`, SDK `research_diagnostics`, MCP `atlas_research_diagnostics` and HTTP `POST /v1/research/diagnostics` check configuration without exposing private contact settings. Source probes require explicit `allow_network=true`; fixtures never establish live connectivity. [Presets, saved queries and diagnostics](docs/presets.md).

The hosted MCP provider adds native x402 purchases through standard payment metadata and read-only chain reconciliation. Its external-wallet path uses the caller's spending controls. [MCP payment setup](docs/payments.md#standard-http-and-native-mcp-clients).

Start the local HTTP API with:

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
```

`GET /v1/research/catalog` lists datasets and query schemas. `POST /v1/research` retrieves research without a quote or payment. [API configuration](docs/self-hosting.md).

## Annual snapshots and optional payments

`financials get` and `Atlas.get_financials` provide annual US-GAAP USD snapshots for `revenue`, `net_income`, `assets`, `liabilities` and `operating_cash_flow`. Use `research get statements` for the full metric set and quarterly or YTD periods.

The HTTP API also supports immutable annual snapshots with explicit quote, purchase and recovery steps. Payment modes are `off`, `mock` and `testnet`; payments default to off. Testnet uses x402 V2 exact/EIP-3009 with Base Sepolia USDC. Optional finalized-chain reconciliation recovers unknown orders without signing or settling again. Mainnet is unsupported. Research retrieval does not purchase snapshots. [Payment configuration](docs/payments.md).

Use a stable `intent_id` when preparing a quote to recover the same order after response loss or restart. `purchase-preflight` and `budget-status` expose order readiness, policy denials, spending and reserved amounts without signing or reserving funds. Preflight is advisory; purchase still enforces the operator's policy atomically. [Planning and task replay](docs/payments.md#inspect-a-purchase-before-spending).

## Sources and coverage

Filing discovery returns document metadata and links. Structured ownership results describe one selected filing, with amendment, reporting-lag and coverage limits. Current snapshots and revised economic observations are not point-in-time historical data.

Consolidated equity prices, order books, option chains, analyst consensus, newswire articles and earnings-call transcripts are not provided. Source access limits and third-party content terms apply independently of the code license.

[Source terms](DATA_SOURCES.md) · [Financial methodology](docs/methodology.md) · [Validation scope](docs/verification.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## License

Atlas Terminal is licensed under [Apache-2.0](LICENSE). Source-data and dependency terms apply separately.

Prepared MCP Registry namespace: `io.github.andoseiryu-atlassupport/atlas-terminal`; registration is pending.
