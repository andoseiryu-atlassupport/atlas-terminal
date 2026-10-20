# Project overview

Atlas Terminal retrieves US equity research from free public primary sources. The CLI, Python SDK, stdio MCP server and reference HTTP API share retrieval and validation services.

## Capabilities

- Five government sources and 17 research datasets: SEC company, industry, financial, filing and ownership data; BLS inflation and employment series; Treasury yields and debt; Federal Reserve releases.
- 37 reported financial concepts across annual, explicitly reported discrete quarters and YTD, raw XBRL contexts, six ratios, five YoY growth rates, FCF and FCF margin with traceable inputs.
- Comparison of reported and derived financial metrics for up to ten specified issuers at the latest common reported end, with exact input-period alignment and explicit gaps.
- XML records from one Form 3/4/5 or 13F filing per query. Beneficial ownership, fund and corporate-action discovery returns filing metadata and links.
- Values include units, periods, source hashes and retrieval times. Missing data, archive limits and partial coverage remain explicit.
- Research requires no data subscription, API key, wallet or LLM. SEC requests use a private, operator-supplied User-Agent with an actual contact email.
- Immutable snapshots of five annual metrics and an optional x402 API. Payments default to off; testnet is restricted to Base Sepolia USDC, and mainnet is rejected.

## Stack

Python 3.12+, uv, Pydantic, httpx, Typer, FastAPI, the official MCP and x402 SDKs, and SQLite. XML parsing uses the standard library after rejecting DTD/entity declarations, unsafe filenames and arbitrary URLs. Node.js 24 and Prettier support documentation checks.

## Scope

Consolidated equity prices, order books, options, analyst consensus, newswire articles, earnings-call transcripts, complete corporate-action or security-master datasets, PIT, trading and built-in LLM analysis are unavailable. Free access does not imply unrestricted redistribution rights for third-party content.

See the [research guide](research.md) for queries and limits, [capabilities](../IMPLEMENTATION_STATUS.md) for the feature overview, [sources and terms](../DATA_SOURCES.md) for data conditions, and [validation scope](verification.md) for verified and unverified behavior.
