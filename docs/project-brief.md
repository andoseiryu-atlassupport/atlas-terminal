# Project overview

Atlas Terminal retrieves company financials, US/Japan macro data and reference FX from free public primary sources through one MCP connection. The CLI, Python SDK, stdio/Streamable HTTP MCP and reference HTTP API share retrieval and validation services.

## Capabilities

- Ten public primary source families and 23 research datasets: SEC company, industry, financial, filing and ownership data; BLS inflation and employment series; nominal/real Treasury yields and debt; Federal Reserve releases; ECB reference FX; New York Fed EFFR/SOFR; BEA GDP/PCE/income; BOJ and Japanese government macro statistics.
- Macro calculations include CPI/core CPI YoY, payroll monthly changes and the 10Y–2Y nominal spread, with original inputs and explicit missing baselines. FX cross rates use same-day ECB observations.
- 37 reported financial concepts across annual, explicitly reported discrete quarters and YTD, raw XBRL contexts, six ratios, five YoY growth rates, FCF and FCF margin with traceable inputs.
- Comparison of reported and derived financial metrics for up to ten specified issuers at the latest common reported end, with exact input-period alignment and explicit gaps.
- XML records from one Form 3/4/5 or 13F filing per query. Beneficial ownership, fund and corporate-action discovery returns filing metadata and links.
- Values include units, periods, source hashes and retrieval times. Missing data, archive limits and partial coverage remain explicit.
- Agent contracts include dataset-specific MCP input, a common output schema, filtered catalogs, optional compact evidence references, snapshot pagination and structured recovery hints across the shared interfaces.
- Optional source-age, observation-date and coverage requirements fail explicitly. Up to ten ordered independent research reads share a batch response with per-item errors. Operator source checks record response structures and last-success changes behind an explicit network opt-in.
- Company, US macro, Japan macro, US/Japan and FX-context presets expand into inspectable batches, with item selection, per-item requirements, saved JSON queries and a missingness/time overview. Preview and configuration diagnosis make no source requests; opt-in connectivity diagnosis reuses fixed read-only probes.
- Research-only stdio/Streamable HTTP exposes six tools with no payment handlers. Remote ingress requires explicit authentication or anonymous access, exact host/origin checks and bounded requests, with one worker and operator-managed HTTPS/persistent disk.
- Public guides explain supported tasks, Codex setup, calculations and original-source citations. A focused research skill and versioned plugin/Registry metadata preparation support client installation; no public remote endpoint or official plugin listing is operated.
- A reproducible company/CPI/FX batch and evidence workflow uses one official MCP connection. Public HTTP checks and opt-in remote checks run separately from offline contract tests and manual model-discovery observations. Single-host systemd/Caddy files support operator deployment without provisioning a service.
- Research requires no data subscription, wallet or LLM. BEA uses a private, free registered API key. SEC requests use a private, operator-supplied User-Agent with an actual contact email.
- Immutable snapshots of five annual metrics and an optional x402 API. Payments default to off; testnet is restricted to Base Sepolia USDC, and mainnet is rejected.
- On-demand reconciliation reads finalized chain evidence without signing or settling again. Hosted MCP supports external-wallet payment metadata; official Python and TypeScript clients have offline interoperability checks.
- Purchase preflight and budget summaries support agent planning without signing or reserving funds. Optional task IDs and private replay capabilities preserve the same quote across response loss, restart and concurrent preparation.

## Stack

Python 3.12+, uv, Pydantic, httpx, Typer, FastAPI, the official MCP and x402 SDKs, and SQLite. XML parsing uses the standard library after rejecting DTD/entity declarations, unsafe filenames and arbitrary URLs. Node.js 24 and Prettier support documentation checks.

## Scope

Consolidated equity prices, order books, options, analyst consensus, newswire articles, earnings-call transcripts, complete corporate-action or security-master datasets, PIT, trading and built-in LLM analysis are unavailable. Free access does not imply unrestricted redistribution rights for third-party content.

See the [research guide](research.md) for queries and limits, [capabilities](../IMPLEMENTATION_STATUS.md) for the feature overview, [sources and terms](../DATA_SOURCES.md) for data conditions, and [validation scope](verification.md) for verified and unverified behavior.
