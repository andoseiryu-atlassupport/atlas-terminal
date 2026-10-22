# Atlas Terminal

<!-- mcp-name: io.github.andoseiryu-atlassupport/atlas-terminal -->

Atlas Terminal gives AI agents company financials, US macro indicators and reference FX through one MCP connection, using free public primary sources. The same retrieval, calculation and evidence services are available through a CLI, Python SDK and HTTP API.

[Documentation website](https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site) · [Connect Codex](docs/codex.md) · [Examples](docs/use-cases.md) · [Capabilities](IMPLEMENTATION_STATUS.md) · [Data sources](DATA_SOURCES.md)

## Available data

Atlas provides 21 datasets from SEC EDGAR, BLS, US Treasury yields, Treasury Fiscal Data, the Federal Reserve, the ECB and the New York Fed.

| Area        | Information                                                                                                                           |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Companies   | Current ticker / CIK directory, issuer profiles, SIC industry, exchanges and fiscal year end                                          |
| Financials  | 37 US-GAAP concepts covering income, balance sheets, cash flow, EPS, shares and dividends; annual, reported discrete quarters and YTD |
| Analysis    | Six ratios, five year-over-year growth rates, free cash flow and FCF margin; comparison of up to ten issuers at a common period       |
| XBRL        | Concept inventory and raw observations with their taxonomy, units and contexts                                                        |
| Disclosures | All-form filing discovery, including ownership, fund, proxy, offering and corporate-action filings                                    |
| Ownership   | Form 3/4/5 transactions and positions; a selected manager's 13F information table                                                     |
| Macro       | Eight BLS series, CPI/core CPI YoY, payroll monthly changes, nominal/real Treasury par yields, 10Y–2Y spread and federal debt         |
| Rates / FX  | EFFR and SOFR overnight rates; ECB daily reference FX for nine currencies, with traceable inverse and cross calculations              |
| Policy      | Federal Reserve release headlines, publication times and original links                                                               |

Values retain their units, periods, source hashes, retrieval times and JSON pointers or XML paths. Missing or ambiguous values are explicit. Derived metrics include their formulas and reported inputs.

## Get started

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). Install the tagged GitHub source distribution; the package is not yet published on PyPI:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.3.2'
atlas research preset fx_context --base-currency USD --quote-currency JPY --include fx,us_sofr --provider fixture --json
```

Or work in a cloned repository:

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
uv run atlas research get economics --series cpi --json
uv run atlas research get treasury_yields --year 2026 --json
uv run atlas research get fx_rates --base-currency USD --quote-currency JPY --json
uv run atlas research get reference_rates --series sofr --json
uv run atlas research get treasury_real_yields --year 2026 --json
uv run atlas research get macro_analytics --series cpi_yoy --json
```

Research needs no data subscription, API key, wallet or LLM. Commands return JSON, and errors use nonzero exit codes. Dataset names, metrics, series identifiers and query limits are listed in `research catalog` and the [research guide](docs/research.md).

Growth matches actual prior-year periods; `0.25` means 25%. Free cash flow is reported operating cash flow minus PP&E acquisition payments. Comparisons select the latest shared reported period end and suppress values with different actual input dates. Missing quarters and nonpositive growth baselines remain unavailable. [Calculation rules](docs/research.md#growth-and-cash-flow-analytics).

FX observations are daily reference rates for information purposes. The ECB window covers the latest 90 calendar days; EFFR/SOFR retrieval covers the latest 90 observations. Cross rates use inputs from the same date. Missing dates and macro baselines are not filled. [Macro and FX guide](docs/macro.md).

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

The original full stdio mode, without `--research-only`, exposes twelve tools including company resolution, annual snapshots, filings and separately configured quote/purchase recovery. Research-only Streamable HTTP is available for [self-hosting](docs/remote-mcp.md). No official public remote endpoint or public ChatGPT plugin is currently operated.

Research accepts optional `response` settings for compact provenance references, field selection and snapshot pagination. `atlas_get_evidence` retrieves the original provenance by ID. Errors include a structured next action and a retry delay when known. The same contracts work through CLI, SDK and HTTP. [Agent usage](docs/agents.md).

Results link the relevant Atlas documentation while preserving original publisher attribution. Cite SEC, BLS, Treasury, Federal Reserve, ECB or New York Fed provenance for numerical facts, and Atlas methodology for its calculation and selection rules. [Citation guidance](docs/citations.md).

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

## Sources and coverage

Filing discovery returns document metadata and links. Structured ownership results describe one selected filing, with amendment, reporting-lag and coverage limits. Current snapshots and revised economic observations are not point-in-time historical data.

Consolidated equity prices, order books, option chains, analyst consensus, newswire articles and earnings-call transcripts are not provided. Source access limits and third-party content terms apply independently of the code license.

[Source terms](DATA_SOURCES.md) · [Financial methodology](docs/methodology.md) · [Validation scope](docs/verification.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## License

Atlas Terminal is licensed under [Apache-2.0](LICENSE). Source-data and dependency terms apply separately.

Prepared MCP Registry namespace: `io.github.andoseiryu-atlassupport/atlas-terminal`; registration is pending.
