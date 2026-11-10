# Financial research for AI agents

Atlas Terminal is an Apache-2.0 toolkit for retrieving financial data, calculating research indicators and checking original evidence through one MCP connection. A Python SDK, CLI and HTTP API share the research services. Company financials, macro/rates/FX, optional stock and futures prices, technical indicators, Japanese margin balances, Buffett-inspired scoring and Polymarket outcomes are available in the current source checkout.

The source checkout contains **68 datasets, 27 upstream source families and 28 presets**. These guides describe `main`; installed versions may expose a smaller catalog, and the existing `v0.7.0` release does not include every later feature. Start with `atlas research catalog --json` to inspect your installation.

## Choose a research task

| Task                                                   | Start here                                                                                                                                                | Important scope                                                                                     |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Inspect company financials, filings and ownership      | [Financial research](research.md) · [TTM and original filings](filing-research.md)                                                                        | Reported US-GAAP periods and source evidence; missing values stay unavailable                       |
| Score annual business quality and a valuation scenario | [Buffett-inspired scoring](buffett-scoring.md) · [Japanese EDINET scoring](japan-buffett.md)                                                              | Fixed weights, original inputs and missing-point bounds; qualitative judgment is manual             |
| Research economies, interest rates and FX              | [Economic indicators](economic-indicators.md) · [Rates and FX](macro.md) · [Government bonds](fixed-income.md)                                            | Selected source definitions, dated history and explicit country/series gaps                         |
| Retrieve multi-year stocks or daily commodity futures  | [Stock history](yahoo-finance.md#multi-year-stock-history) · [European/Korean coverage](europe-korea-equities.md) · [Commodities](commodities-futures.md) | Optional personal-use Yahoo observations; daily futures remain separate from IMF monthly references |
| Calculate SMA, Bollinger bands, RSI and MACD           | [Technical indicators](technical-indicators.md) · [Series statistics](time-series-statistics.md)                                                          | Chosen close/adjusted-close basis, exact inputs and unavailable warm-up values                      |
| Inspect Japanese credit trading and disclosures        | [Daily margin balances](margin-trading.md) · [Japanese companies](japan-companies.md)                                                                     | JPX report dates and source cells; EDINET requires a private API key                                |
| Read event prices as probability indicators            | [Polymarket event prices](prediction-markets.md)                                                                                                          | Named outcomes and lifecycle flags; quote timestamps are unverified, with no trading                |
| Model a portfolio using supplied histories             | [Native factor risk](risk-model.md) · [Data connections](data-connections.md)                                                                             | Experimental models with a suitable estimation universe; no verified return forecast                |

Additional workflows cover [G20 annual macro data](g20-macro.md), [series discovery and macro panels](international-macro.md), [official headlines](news.md), [crypto depth and report discovery/import](order-books-reports.md), [local credit-rating observations](credit-ratings.md), [offline charts and workbenches](research-workbench.md), and [private source history](source-history.md). See the [complete capability reference](../IMPLEMENTATION_STATUS.md).

## Start from the current source

Use Python 3.12+ and uv:

```sh
git clone https://github.com/andoseiryu-atlassupport/atlas-terminal.git
cd atlas-terminal
uv sync --locked
uv run --offline atlas research catalog --json
uv run --offline atlas research polymarket Anthropic --provider fixture --json
```

The final command uses fictional data, not actual Anthropic prices. After dependency installation, fixture queries need no external data account or source network access. For actual data, select the public provider and follow the source-specific configuration in the linked guide.

Public primary sources are the default. BEA, Census, FRED and EDINET need separate free registered API keys; SEC needs an identifying User-Agent supplied privately by the operator. Optional Yahoo market data are a secondary source, disabled by default and limited to operator-enabled personal use. Research needs no built-in LLM or wallet; source access and redistribution terms remain separate.

## Try the stable walkthroughs

The existing `v0.7.0` release includes two complete fictional workflows:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas showcase research
atlas showcase japan-risk
```

Both use an actual official MCP client session. Add `--json` to inspect queries, calculations and resolved evidence. [Rates/FX walkthrough and replay](agent-research-walkthrough.md) · [Japanese risk walkthrough and replay](japan-risk-walkthrough.md).

The research walkthrough supports actual ECB/New York Fed reads with `--provider public --allow-network`. Portfolio risk requires supplied histories and a broader estimation universe; the fictional demonstration does not establish real-market forecast accuracy.

## Connect an agent or application

Use the [Codex setup](codex.md), [general MCP quickstart](quickstart.md), [Python/research guide](research.md) or [HTTP API guide](self-hosting.md). A separate [research-only Streamable HTTP service](remote-mcp.md) supports operator-hosted deployments. There is no official public Atlas remote endpoint or public ChatGPT plugin. The Python package is available from GitHub and is not yet published on PyPI.

[Presets](presets.md) combine independent queries for company, macro, FX and government-bond context. Preview selects the exact queries without source access. [Batches and quality requirements](agents.md) preserve each result's units, dates, missingness and errors. [Reproducible research cases](research-examples.md) show complete query/evidence workflows.

## Understand the evidence

Results retain source URLs, raw-response hashes, retrieval times and original field locations. Calculations retain formulas and original inputs. Use [citation guidance](citations.md) to cite the original publisher for numerical facts, and Atlas for its normalization and methodology.

[Verification records](verification.md) separate fictional tests, dated live checks, installed-package tests and CI. Live Polymarket checks matched selected Anthropic outcomes to original Gamma responses; this does not establish complete market coverage or verified quote timestamps. Other data sources retain their own verified and unverified scope.

Missing values remain unavailable. Retrieval time is not publication or observation time, current/revised history is not point-in-time history, and market-implied percentages are not calibrated forecasts. Atlas does not provide consolidated real-time quotes, analyst consensus, complete news coverage or trading. [Sources and terms](../DATA_SOURCES.md) · [Coverage](../IMPLEMENTATION_STATUS.md) · [FAQ](faq.md).

[GitHub repository](https://github.com/andoseiryu-atlassupport/atlas-terminal) · [Atlas and OpenBB](atlas-and-openbb.md) · [Release notes](../CHANGELOG.md) · [Support](support.md) · [Privacy](privacy.md) · [Terms](terms.md)
