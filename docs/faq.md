# Atlas Terminal questions and answers

## What is Atlas Terminal?

Atlas Terminal is an open-source financial-data toolkit for AI agents. One MCP connection provides company financials, US/Japan macro indicators and reference FX from free primary sources. A Python SDK, CLI and HTTP API share the same services. It has 27 datasets and eight multi-source presets.

## Is the research data free?

Research requires no data subscription, wallet or LLM. BEA GDP/PCE/income data require a free registered API key; Japanese macro data need no registration. Upstream publishers set their own access limits and reuse terms. SEC requests require an operator-supplied identifying User-Agent with an actual contact email. Operating your own remote server can incur hosting costs. The optional snapshot-payment path is separate and defaults to off.

## Can I get company data, macro data and FX through one MCP server?

Yes. Use `atlas_research_catalog` to discover datasets, `atlas_get_research` for an exact query, or `atlas_get_research_preset` for `company_overview`, `us_macro_overview` or `fx_context`. Each item preserves its own source, units, dates and errors. A preset is a group of independent reads, not an atomic snapshot.

## Does it work with Codex and ChatGPT?

Local stdio MCP can be configured in compatible Codex and desktop clients. A research-only Streamable HTTP service can be deployed behind operator-managed HTTPS for remote clients. Atlas is not currently listed as an official public ChatGPT plugin, and no official public remote endpoint is operated. See [Codex setup](codex.md) and [remote MCP](remote-mcp.md).

## Does Atlas provide live prices or real-time FX?

No. Atlas does not provide consolidated equity quotes, trading, order books or executable currency prices. Its FX connector uses ECB daily reference rates for 31 currencies from the full published history, with per-currency availability. EFFR and SOFR cover the latest 90 effective observations. See [macro and FX limits](macro.md).

## Can I query what was known at a past date?

No. Current and revised source snapshots do not establish historical release vintages or point-in-time availability. A year used for a Treasury query is not an as-of date for all sources.

## What happens when data is missing or a source fails?

Missing and ambiguous values remain explicit. Presets and batches return per-item outcomes. Requirements can reject insufficient freshness or coverage with `QUALITY_NOT_MET`. Atlas does not invent missing quarters, silently loosen requirements or switch to a paid data source.

## How should I cite results?

Use the returned original publisher references for numerical facts. Resolve compact evidence IDs before citing source provenance. Use Atlas documentation to explain its retrieval, normalization and calculation rules. [Citation guidance](citations.md).

## Is the Python package on PyPI?

Not yet. Install the tagged GitHub source or a GitHub release wheel using the [setup instructions](codex.md). A PyPI release and MCP Registry listing require verified publisher setup. Generated distribution files are preparation, not proof of publication.

## Does llms.txt make ChatGPT use Atlas automatically?

No. The Markdown documentation and `llms.txt` are optional reading aids. Search indexing, citation, plugin installation and actual tool selection are separate. None is guaranteed by publishing a file or registering server metadata.
