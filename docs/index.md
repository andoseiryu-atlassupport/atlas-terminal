# Financial data for AI agents through one MCP connection

Atlas Terminal connects AI agents to company financials, US/Japan and international macro indicators and reference foreign exchange data from free public primary sources. One MCP connection exposes 32 datasets, twenty-three research presets and traceable evidence. A Python SDK, CLI and HTTP API use the same services. Research needs no data subscription, wallet or built-in LLM. BEA national accounts require a free registered API key.

US/Japan/euro area/Australia [government-bond research](fixed-income.md) adds Japanese 1–40-year curves, local slopes, butterflies, rate changes and US nominal/real par inflation-compensation proxies with original inputs.

## Try a complete workflow in five minutes

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas showcase research
atlas showcase japan-risk
```

Use Python 3.12+ and uv. Installation downloads the package; both walkthroughs then use fictional data through an actual official MCP session. They need no data account or LLM. Add `--json` for the exact queries and complete evidence. One session retrieves reference FX and overnight rates; the other reads a native connection, estimates a Japanese cross-sectional model and analyzes a portfolio.

[Watch the rates/FX replay and reproduce it](agent-research-walkthrough.md) · [Watch the Japanese risk replay and reproduce it](japan-risk-walkthrough.md) · [Connect your own Codex agent](codex.md)

For actual data, the research walkthrough supports `--provider public --allow-network`. Portfolio risk uses supplied histories and a broad estimation universe; equity-price data are not bundled. Public-source connectivity and real-market model accuracy are distinct from the fictional demonstrations.

## Start with a task

| Task                                                | Atlas workflow                                                                            | Original sources                    |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------- |
| Inspect a company's reported financial position     | `company_overview`: profile, statements, growth/FCF and filings                           | SEC EDGAR                           |
| Retrieve US inflation, employment and rate context  | `us_macro_overview`: CPI, CPI YoY, payrolls, unemployment, Treasury yields, EFFR and SOFR | BLS, US Treasury, New York Fed      |
| Retrieve USD/JPY with US inflation and rate context | `fx_context`: reference FX, CPI, Treasury yields, EFFR and SOFR                           | ECB, BLS, US Treasury, New York Fed |

Select only the preset items you need. A preview shows the exact queries without source access. Results preserve units, observation periods, source retrieval times, explicit missingness and original source references. [Reproducible research cases](research-examples.md) · [Preset contracts](presets.md).

The [company, CPI and USD/JPY example](company-macro-fx.md) retrieves four datasets through one official MCP session and resolves every returned evidence ID. Its fictional run and explicit public-data mode are separate. [Public HTTP and remote checks](public-checks.md) explain how to verify a published deployment.

[Series discovery and macro panels](international-macro.md) explain IMF/OECD dataflows, metadata-selected series and explicit calendar aggregation.

An optional [fundamental risk workflow](risk-model.md) uses the same connection for supplied US equity observations, factor estimation and portfolio risk attribution. `atlas risk demo` exercises the complete fictional workflow; no price feed or verified historical fundamental database is bundled.

## Connect an agent

Start with the [Codex setup](codex.md) or [general MCP quickstart](quickstart.md). Local stdio is available for compatible desktop clients. A separate [research-only Streamable HTTP service](remote-mcp.md) supports operator-hosted deployments. No official public Atlas remote endpoint is currently operated; local installation is available from the tagged source distribution.

Try the packaged fictional data before making any public-source requests:

```sh
uvx --from 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0' atlas research preset fx_context --base-currency USD --quote-currency JPY --include fx,us_sofr --provider fixture --json
```

This example uses synthetic data, not current market observations. It needs Python 3.12+ and uv, and its first installation needs network access. The Python package is not yet published on PyPI.

## Understand the evidence

Use [source and citation guidance](citations.md) to attribute numerical facts to their original publishers. Atlas documents its normalization, [financial calculations](methodology.md), [macro and FX calculations](macro.md), and [coverage limits](../IMPLEMENTATION_STATUS.md). Data freshness and publication time are distinct.

Current or revised source snapshots are not point-in-time history. Atlas does not provide real-time prices, consolidated market quotes, analyst consensus, trading or complete news coverage. ECB reference FX is daily reference data. Source availability and redistribution terms remain source-specific. See [sources and terms](../DATA_SOURCES.md), [FAQ](faq.md) and [verification scope](verification.md).

[GitHub repository](https://github.com/andoseiryu-atlassupport/atlas-terminal) · [Atlas and OpenBB](atlas-and-openbb.md) · [Release notes](../CHANGELOG.md) · [Support](support.md) · [Privacy](privacy.md) · [Terms](terms.md)
