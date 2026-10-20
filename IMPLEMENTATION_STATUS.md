# Atlas Terminal capabilities

Atlas Terminal retrieves US equity research from free public primary sources. Its CLI, Python SDK, stdio MCP server and HTTP API share the same datasets and selection rules.

## Datasets

| Area        | Capabilities                                                                                       |
| ----------- | -------------------------------------------------------------------------------------------------- |
| Companies   | Current ticker / CIK search, issuer profiles, SIC industry, exchanges and fiscal year end          |
| Financials  | 37 US-GAAP concepts covering income, balance sheets, cash flow, EPS, shares and dividends          |
| Periods     | Annual, explicitly reported discrete quarters and YTD, matched to actual dates and filing metadata |
| Analysis    | Six ratios, five YoY growth rates, FCF and FCF margin, with formulas and reported inputs           |
| Comparison  | Up to ten issuers at a common reported end; mismatched actual input periods are explicit gaps      |
| XBRL        | Concept and unit inventories, plus observations with their original accounting contexts            |
| Disclosures | All-form filing discovery and ownership, fund, proxy, offering and corporate-action filing groups  |
| Ownership   | Form 3/4/5 records and a manager's 13F information table, retrieved one filing at a time           |
| Economics   | Eight BLS series covering consumer prices, employment, unemployment, wages and job openings        |
| Treasury    | Daily Treasury par yields and federal Debt to the Penny observations                               |
| Policy      | Federal Reserve release headlines, publication times and original links                            |

Run `atlas research catalog --json` for identifiers, query fields, units and limits for all 17 datasets. The [research guide](docs/research.md) includes examples for each dataset.

## Interfaces

| Interface        | Entry points                                                            |
| ---------------- | ----------------------------------------------------------------------- |
| CLI              | `atlas research catalog` / `atlas research get`                         |
| Python SDK       | `Atlas.research_catalog()` / `Atlas.get_research(query)`                |
| MCP              | Nine default tools; hosted adds native x402 purchase and reconciliation |
| HTTP             | `GET /v1/research/catalog` / `POST /v1/research`                        |
| Annual snapshots | `financials get` / `Atlas.get_financials`, covering five annual metrics |

Research requires no data subscription, API key, wallet or LLM. SEC retrieval requires a private `SEC_USER_AGENT` containing the application name and an actual contact email. The `fixture` provider returns fictional data without network access.

MCP research publishes dataset-specific query schemas and a validated common response schema. Optional compact responses replace repeated provenance with content-addressed evidence IDs; field projection preserves interpretation and calculation context. Snapshot-bound cursors resume without refetching upstream data. CLI, SDK and HTTP share these services. Catalogs support dataset filtering and compact discovery. Error envelopes expose safe field hints, next actions and known retry delays. See the [agent contracts](docs/agents.md) for retention and paging limits.

## Provenance and data quality

- Numeric values are decimal strings with units, actual periods, accessions, source hashes, JSON pointers or XML paths, and retrieval times.
- Missing and conflicting values are explicit. Coverage distinguishes requested periods from available data; incomplete financial results require `allow_partial`.
- Unreported Q4 values, currency conversions, 13F dollar/thousand-dollar units and CUSIP-to-ticker mappings are not inferred.
- Filing discovery, reported records and derived ratios are distinct outputs. Current snapshots cannot satisfy point-in-time historical requests.
- Growth compares corresponding actual prior-year durations and positive baselines. FCF uses reported operating CF minus nonnegative PP&E acquisition payments; margin divides by same-period revenue.
- Comparisons support reported and derived metrics at the latest shared reported end, with identical actual periods per metric, including growth baselines. Missing or unaligned values require `allow_partial`.
- Retrieval uses approved endpoints, caching, rate limits, response bounds and finite retries. Source failures never trigger a paid-provider fallback.

[Sources and terms](DATA_SOURCES.md) · [Financial methodology](docs/methodology.md) · [Validation scope](docs/verification.md)

## Optional snapshot API

Immutable snapshots of five annual metrics support explicit quotes, purchases and authenticated recovery. Payments default to off. `mock` uses fictional payments; `testnet` uses x402 V2 exact/EIP-3009 with Base Sepolia USDC. Policy checks, budget reservations before signing and authenticated recovery protect the payment path. A configured trusted RPC supports on-demand reconciliation: finalized matching USDC/nonce evidence recovers settled artifacts; finalized expiry plus an unused nonce establishes failure. Other evidence remains pending. Hashed audit records and terminal-state guards cover concurrent recovery. Research endpoints do not charge for retrieval.

Official Python and TypeScript HTTP clients are exercised offline. Hosted MCP accepts standard payment metadata from an external wallet, with a transport compatibility adapter for the pinned Python SDKs. Its external-wallet path does not sign with the local Atlas wallet. Local/external purchase claims are durable and exclusive per quote.

## Coverage limits

Consolidated equity prices, daily prices and adjustment factors, order books, options, analyst consensus, newswire articles, earnings-call transcripts, a complete security master or corporate-action history, PIT and trading are unavailable.

The HTTP API supports one process, one private SQLite volume and loopback connections. Mainnet, distributed budgets, background reconciliation and public-facing authentication or TLS are unsupported. Reconciliation may remain unresolved for legacy deadlines, old nonce logs without a transaction hint, ambiguous batched receipts and pre-submission crashes. Live RPC/Facilitator behavior and real transfers remain unverified.

Remote MCP hosting, batched research jobs, freshness/coverage requirements and model-driven task-success or token benchmarks are not implemented. Agent contract tests use official MCP messages and offline fixtures, without an LLM.

[Quickstart](docs/quickstart.md) · [API configuration](docs/self-hosting.md) · [Payments](docs/payments.md) · [Apache-2.0](LICENSE)
