# Atlas Terminal questions and answers

## What is Atlas Terminal?

Atlas Terminal is an Apache-2.0 financial research toolkit for AI agents and applications. One MCP connection provides company financials and filings, macro/rates/FX, optional market prices, technical indicators, Japanese margin balances, Buffett-inspired scoring and Polymarket outcomes with inspectable sources and calculations. A Python SDK, CLI and HTTP API share the research services; optional native risk models analyze supplied histories. The current source checkout has 68 datasets, 27 upstream source families and 28 presets.

## Are all documented features in the latest release?

No. These guides describe the current `main` source checkout. The existing `v0.7.0` release includes the stable walkthroughs and native risk extension, but not every later research addition. Use `atlas research catalog --json` to inspect your installed version, or follow the [source-checkout setup](../README.md#get-started) for the current capabilities. A source update does not replace published release assets.

## Is the research data free?

Research has no built-in paid data-provider, wallet or LLM dependency. Public primary sources are the default. BEA, Census, FRED and EDINET require separate free registered API keys, and SEC needs an operator-supplied identifying User-Agent with an actual contact email. Optional Yahoo observations are a secondary source for explicitly enabled personal use. Upstream access and redistribution terms still apply, including Polymarket's restrictions; public API access is not a data license. Operating a remote server can incur hosting costs. The optional snapshot-payment path is separate and defaults to off.

## Can I get company data, macro data and FX through one MCP server?

Yes. Use `atlas_research_catalog` to discover datasets, `atlas_get_research` for an exact query, or `atlas_get_research_preset` for `company_overview`, `us_macro_overview` or `fx_context`. Each item preserves its own source, units, dates and errors. A preset is a group of independent reads, not an atomic snapshot.

## Does it work with Codex and ChatGPT?

Local stdio MCP can be configured in compatible Codex and desktop clients. A research-only Streamable HTTP service can be deployed behind operator-managed HTTPS for remote clients. Atlas is not currently listed as an official public ChatGPT plugin, and no official public remote endpoint is operated. See [Codex setup](codex.md) and [remote MCP](remote-mcp.md).

## Can I retrieve Toyota history, technical indicators or daily commodities?

Yes, with operator-enabled personal-use Yahoo access. Multi-year stock exports support Tokyo identifiers such as `7203.T`, plus US, Korean and registered European securities. Saved histories can be analyzed offline with SMA, EMA, Bollinger bands, Wilder RSI, MACD and other indicators. Daily commodity observations are futures quotations, separate from IMF monthly reference prices. Source adjustments, contract rolls, missing dates and warm-up gaps remain explicit. [Stock history](yahoo-finance.md) · [Technicals](technical-indicators.md) · [Commodities](commodities-futures.md).

## What do Japanese margin balances show?

`atlas research margin 7203.T` reads JPX daily credit buy/sell balances, changes and a share-based margin ratio for a selected issue. It preserves application/publication dates and original PDF cells. The available history is limited to currently linked reports. It does not expose your broker positions or JSF financing/lending balances. [Margin guide](margin-trading.md).

## What do company scores and prediction-market percentages mean?

Buffett-inspired scores apply an explicit Atlas rubric to annual financial inputs and optional valuation assumptions. Fixed weights, missing-point bounds and formula inputs are inspectable; qualitative judgment remains manual. This is not Buffett's own scoring system or a verified return forecast. [Scoring methodology](buffett-scoring.md).

Polymarket percentages are exactly `100 * reported_price` for markets reported open. Outcomes follow their original array positions; No is never inferred as `1 - Yes`, and different markets' Yes prices are not summed. Closed/inactive prices remain references with null current probabilities. Price observation time is unknown even when source retrieval time is recorded. [Prediction-market semantics](prediction-markets.md).

## Does Atlas provide real-time trading data?

No consolidated real-time equity quotes, consolidated equity order books, executable FX quotes or trading are provided. Optional daily/intraday market observations, one-venue crypto books and Polymarket prices are bounded source snapshots with their own limitations. ECB FX is daily reference data; EFFR/SOFR are dated reference rates, with explicit historical observation windows. Retrieval time does not establish quote freshness. [Macro and FX](macro.md) · [Market coverage](../IMPLEMENTATION_STATUS.md).

## Can I query what was known at a past date?

Historical observation dates are supported for documented datasets, but they do not answer what was available to a reader at that time. Current/revised snapshots are not historical release vintages or point-in-time data. Private source history records locally captured versions, not a complete publisher archive. A year used for a Treasury query is not an as-of date for all sources.

## What happens when data is missing or a source fails?

Missing and ambiguous values remain explicit. Presets and batches return per-item outcomes. Requirements can reject insufficient freshness or coverage with `QUALITY_NOT_MET`. Atlas does not invent missing quarters, silently loosen requirements or switch to a paid data source.

## How should I cite results?

Use the returned original publisher references for numerical facts. Resolve compact evidence IDs before citing source provenance. Use Atlas documentation to explain its retrieval, normalization and calculation rules. [Citation guidance](citations.md).

## Is the Python package on PyPI?

Not yet. Install the tagged GitHub source or a GitHub release wheel using the [setup instructions](codex.md). A PyPI release and MCP Registry listing require verified publisher setup. Generated distribution files are preparation, not proof of publication.

## Does llms.txt make ChatGPT use Atlas automatically?

No. The Markdown documentation and `llms.txt` are optional reading aids. Search indexing, citation, plugin installation and actual tool selection are separate. None is guaranteed by publishing a file or registering server metadata.
