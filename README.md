# Atlas Terminal

**Financial research for AI agents through MCP, with traceable data and calculations.**

<!-- mcp-name: io.github.andoseiryu-atlassupport/atlas-terminal -->

Atlas Terminal is an Apache-2.0 toolkit for retrieving financial data, running research calculations and inspecting the evidence behind each result. One MCP connection provides company financials and filings, macro indicators, rates and FX, optional market prices, technical indicators, Japanese margin balances, company scoring and prediction-market prices. A Python SDK, CLI and HTTP API share the research services.

Public primary sources are the default. Optional international Yahoo Finance observations are a secondary source, disabled by default and available only with explicit operator enablement for personal use. Research needs no built-in LLM, wallet or paid data subscription; source access and reuse terms apply separately.

[Documentation](https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site) · [Connect an agent](docs/codex.md) · [Capabilities](IMPLEMENTATION_STATUS.md) · [Sources and terms](DATA_SOURCES.md) · [Verification](docs/verification.md)

## What you can do

The current source checkout contains **68 research datasets, 27 upstream source families and 27 presets**. This overview describes `main`; the existing `v0.7.0` release does not include every later addition. Inspect your installed `research catalog` before selecting a dataset.

| Research task                                     | Available capabilities                                                                                                                                                                         | Scope and guide                                                                                                                                                                                                                                                         |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Understand a company                              | 37 US-GAAP financial concepts; annual, reported discrete quarterly and YTD statements; TTM flows, ratios, growth, free cash flow, filing text/tables and ownership disclosures                 | SEC reported facts retain actual periods and filing evidence. Missing financial values stay unavailable. [Financial research](docs/research.md) · [Original filings](docs/filing-research.md)                                                                           |
| Assess business quality and valuation assumptions | Buffett-inspired annual scorecards with fixed weights, missing-point bounds, original inputs and an explicit price-versus-value scenario                                                       | SEC or indexed EDINET Japan GAAP/IFRS facts, plus supplied financials including JPY. Qualitative judgment remains manual; this is not Buffett's own scoring system or a return prediction. [Scoring](docs/buffett-scoring.md) · [Japanese setup](docs/japan-buffett.md) |
| Research economies, rates and currencies          | Inflation, employment, national accounts, government yields, policy rates, curve spreads, EFFR/SOFR history and ECB reference FX                                                               | Selected US, Japanese and international series with explicit definitions and gaps. Dated EFFR checks cover history from 2000-07-03. [Economic indicators](docs/economic-indicators.md) · [Rates and FX](docs/macro.md) · [Government bonds](docs/fixed-income.md)       |
| Study stock and commodity prices                  | Optional daily/intraday observations for US, Tokyo, Korean and registered European securities; multi-year stock exports; daily commodity futures and separate IMF monthly commodity references | Yahoo access is optional and for personal use. Source adjustments, quote units, contract rolls and missing observations remain explicit. [Stock history](docs/yahoo-finance.md#multi-year-stock-history) · [Commodities](docs/commodities-futures.md)                   |
| Calculate technical indicators and compare series | 23 indicators including SMA, EMA, Bollinger bands, Wilder RSI and MACD; price comparison; offline statistics, charts and a local research workbench                                            | Choose close or adjusted close. Saved histories retain every source date, input, formula and unavailable warm-up value. [Technicals](docs/technical-indicators.md) · [Statistics](docs/time-series-statistics.md) · [Workbench](docs/research-workbench.md)             |
| Inspect Japanese market context                   | JPX daily credit buy/sell balances, changes and margin ratios; listed-company reference data; EDINET disclosure/XBRL readers                                                                   | Daily margin reports cover currently linked dates. Own broker positions and JSF financing/lending balances are unavailable. EDINET needs a private API key. [Margin balances](docs/margin-trading.md) · [Japanese companies](docs/japan-companies.md)                   |
| Read prediction-market indicators                 | Polymarket Gamma keyword/exact-slug event discovery, named outcome prices, exact percentages, lifecycle flags and source evidence                                                              | Prices are market-implied indicators. Closed/inactive prices are references, and actual quote timestamps remain unverified. No wallet or trading integration. [Polymarket](docs/prediction-markets.md)                                                                  |
| Analyze a portfolio with supplied data            | Experimental native cross-sectional and time-series factor models, risk attribution, active risk, shocks and holdout checks                                                                    | US, Japanese, Korean and British universes remain separate. Histories and a suitable estimation universe must be supplied; forecast accuracy is unverified. [Risk models](docs/risk-model.md) · [Data connections](docs/data-connections.md)                            |

Additional research includes [official news feeds](docs/news.md), [one-venue crypto books and report discovery/import](docs/order-books-reports.md), [licensed local credit-rating observations](docs/credit-ratings.md), [G20 annual macro data](docs/g20-macro.md), [series discovery and macro panels](docs/international-macro.md), and [private source history](docs/source-history.md). The [capability reference](IMPLEMENTATION_STATUS.md) describes individual datasets and limits.

## Evidence and missing data

Each reported value retains its unit, actual period, source URL, source hash, retrieval time and original JSON pointer, XML path, CSV cell or document location. Derived results retain their formulas and exact inputs. Compact responses expose evidence IDs that can be resolved through the same connection.

Missing or ambiguous values are explicit. Atlas does not manufacture missing financial quarters, fill absent prices, infer a prediction outcome's price from its complement, or combine unrelated markets into one probability distribution. Source retrieval time, publisher metadata updates, observation dates and contract deadlines have different meanings. Current or revised source snapshots are not historical point-in-time vintages.

Optional quality requirements can reject missing values, stale captures or truncated results. Presets and batches retain independent results and failures; they do not imply a synchronized snapshot. [Agent contracts](docs/agents.md) · [Financial methodology](docs/methodology.md) · [Citation guidance](docs/citations.md).

## Get started

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). For the capabilities documented on `main`, work from the source checkout:

```sh
git clone https://github.com/andoseiryu-atlassupport/atlas-terminal.git
cd atlas-terminal
uv sync --locked
uv run --offline atlas research catalog --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
```

The fixture provider uses **fictional data** with `is_synthetic=true` and `.invalid` source links. After installation, fixture queries require no source network access, data account, wallet or LLM. The package is also available from versioned GitHub releases; it is not yet published on PyPI.

### Try two complete workflows

The existing `v0.7.0` release includes two installed walkthroughs:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas showcase research
atlas showcase japan-risk
```

Both use an actual official MCP client session with fictional data. Add `--json` for the complete queries, calculations and resolved evidence. [Rates/FX walkthrough and replay](docs/agent-research-walkthrough.md) · [Japanese risk walkthrough and replay](docs/japan-risk-walkthrough.md).

The research walkthrough also supports `--provider public --allow-network` for actual ECB/New York Fed access. Portfolio risk requires supplied histories and a broader estimation universe; its walkthrough does not download stock prices.

### Read public data

Research commands default to `--provider public`. These examples make actual source requests and require normal permitted HTTPS access:

```sh
uv run atlas research get reference_rates --series effr --limit 5 --json
uv run atlas research get fx_rates --base-currency USD --quote-currency JPY --json
uv run atlas research margin 7203.T --json
uv run atlas research polymarket Anthropic --json
```

SEC research and automatic `atlas research buffett AAPL` scoring require a private `SEC_USER_AGENT` with an application name and actual contact email. BEA, Census, FRED and EDINET need separate free registered API keys. Polymarket needs no key, but its source terms and network restrictions still apply.

For optional personal-use Yahoo access, a multi-year stock export can feed offline technical calculations:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run atlas research price-history 7203.T --start-date 2021-01-01 --end-date 2026-11-06 --output build/toyota-history --json
uv run --offline atlas research technicals --input build/toyota-history --output build/toyota-technicals --price-field close --json
```

Use new private output directories. Source bytes, daily CSVs and calculation evidence stay local; a successful export does not establish complete exchange calendars or consistent cross-window adjustments. [History conventions](docs/yahoo-finance.md) · [Indicator formulas](docs/technical-indicators.md).

## Python, MCP and HTTP

The same validated research query works across the SDK, CLI, MCP and HTTP interfaces:

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

Start a local MCP server with fictional data:

```sh
uv run --offline atlas mcp serve --provider fixture --research-only
```

Research-only mode provides six tools for catalog discovery, exact queries, batches, presets, diagnostics and evidence. Use `atlas_get_research` with a `query` object, or send that object to HTTP `POST /v1/research`. `GET /v1/research/catalog` describes the available datasets. Optional risk tools require `--enable-risk`.

[Codex setup](docs/codex.md) · [MCP quickstart](docs/quickstart.md) · [Python and research queries](docs/research.md) · [HTTP API](docs/self-hosting.md) · [Research-only Streamable HTTP](docs/remote-mcp.md).

No official public Atlas remote endpoint or public ChatGPT plugin is operated. Remote use requires an operator-managed service. `company_overview`, `japan_macro_overview`, `fx_context` and government-bond presets combine independent reads; preview a preset before fetching an unfamiliar scope. [Presets](docs/presets.md) · [Reproducible research cases](docs/research-examples.md).

## Verification and limits

[Verification records](docs/verification.md) distinguish offline fictional tests, dated actual-source checks, installed-package checks and CI. Dated live checks cover selected macro/rate series, stock histories, daily futures, JPX margin reports and Polymarket outcomes. Successful reads certify the checked captures and calculations, not every source, continuous availability or forecast accuracy. SEC/EDINET live retrieval and several country-series selections retain documented limits.

Polymarket's dated live check matched three Anthropic events, 29 open markets and 58 outcome prices/percentages to original Gamma responses. The search was bounded, price observation timestamps remained unknown, and retained closed-market prices had null current probabilities. [Exact verification scope](docs/verification.md#polymarket-event-prices).

Atlas does not provide consolidated real-time equity prices or order books, option chains, analyst consensus, complete news/transcript coverage, a complete security master, historical release vintages or trading. Reference FX is daily reference data. Public API access and the Apache-2.0 code license do not grant redistribution rights to source data.

## Optional snapshot API

Annual snapshots of five financial metrics have a separate optional quote/purchase/recovery API. Payments default to off; mock payments are fictional, and the annual API and local signer remain restricted to Base Sepolia USDC using x402 V2. Research queries do not purchase or sign. [Payment configuration and limits](docs/payments.md).

The isolated [BLS seller API](docs/seller.md) offers one prepared-series product with immutable results, explicit capture age and original evidence. Its optional Base mainnet receiving adapter uses an external buyer wallet, a configured CDP facilitator and a trusted reconciliation RPC; it never needs the receiving wallet's private key. SDK, CLI and seller-only MCP tools share this API. Buyer requests cannot access the free research routes or refresh BLS data. Mainnet settlement and public seller hosting remain unverified; no official public seller endpoint is operated.

## License and contributing

Atlas Terminal is licensed under [Apache-2.0](LICENSE). Source-data and dependency terms remain separate. [Sources and terms](DATA_SOURCES.md) · [Contributing and checks](CONTRIBUTING.md) · [Security](SECURITY.md).

Prepared MCP Registry namespace: `io.github.andoseiryu-atlassupport/atlas-terminal`; registration is pending.
