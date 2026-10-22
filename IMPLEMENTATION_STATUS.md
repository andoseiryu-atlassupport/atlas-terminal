# Atlas Terminal capabilities

Atlas Terminal retrieves US equity research, macro data and reference FX from free public primary sources. Its CLI, Python SDK, stdio MCP server and HTTP API share the same datasets and selection rules.

## Datasets

| Area           | Capabilities                                                                                           |
| -------------- | ------------------------------------------------------------------------------------------------------ |
| Companies      | Current ticker / CIK search, issuer profiles, SIC industry, exchanges and fiscal year end              |
| Financials     | 37 US-GAAP concepts covering income, balance sheets, cash flow, EPS, shares and dividends              |
| Periods        | Annual, explicitly reported discrete quarters and YTD, matched to actual dates and filing metadata     |
| Analysis       | Six ratios, five YoY growth rates, FCF and FCF margin, with formulas and reported inputs               |
| Comparison     | Up to ten issuers at a common reported end; mismatched actual input periods are explicit gaps          |
| XBRL           | Concept and unit inventories, plus observations with their original accounting contexts                |
| Disclosures    | All-form filing discovery and ownership, fund, proxy, offering and corporate-action filing groups      |
| Ownership      | Form 3/4/5 records and a manager's 13F information table, retrieved one filing at a time               |
| Economics      | Eight BLS series covering consumer prices, employment, unemployment, wages and job openings            |
| Treasury       | Daily nominal and TIPS-based real par yield curves; federal Debt to the Penny observations             |
| Macro analysis | CPI/core CPI YoY, payroll monthly changes and the same-day 10Y–2Y nominal spread, with original inputs |
| Rates / FX     | Overnight EFFR/SOFR; ECB daily reference FX for EUR, USD, JPY, GBP, CHF, AUD, CAD, NZD and CNY         |
| Policy         | Federal Reserve release headlines, publication times and original links                                |

Run `atlas research catalog --json` for identifiers, query fields, units and limits for all 21 datasets. The [research guide](docs/research.md) includes examples for each dataset.

## Interfaces

| Interface        | Entry points                                                              |
| ---------------- | ------------------------------------------------------------------------- |
| CLI              | `atlas research catalog` / `atlas research get`                           |
| Python SDK       | `Atlas.research_catalog()` / `Atlas.get_research(query)`                  |
| MCP              | Twelve default tools; hosted adds native x402 purchase and reconciliation |
| HTTP             | `GET /v1/research/catalog` / `POST /v1/research`                          |
| Annual snapshots | `financials get` / `Atlas.get_financials`, covering five annual metrics   |

Research requires no data subscription, API key, wallet or LLM. SEC retrieval requires a private `SEC_USER_AGENT` containing the application name and an actual contact email. The `fixture` provider returns fictional data without network access.

MCP research publishes dataset-specific query schemas and a validated common response schema. Optional compact responses replace repeated provenance with content-addressed evidence IDs; field projection preserves interpretation and calculation context. Snapshot-bound cursors resume without refetching upstream data. CLI, SDK and HTTP share these services. Catalogs support dataset filtering and compact discovery. Error envelopes expose safe field hints, next actions and known retry delays. See the [agent contracts](docs/agents.md) for retention and paging limits.

Optional requirements enforce the oldest source age, latest usable observation date, missingness, derived records and explicit truncation on the complete selected result. Conditions fail closed with `QUALITY_NOT_MET`; cursor reads recheck source age without refetching. Source age does not imply publication freshness, and requirements do not expand coverage or force a refresh.

Research batches support up to ten ordered independent queries, 16 KiB requests and a 4 MiB response budget. Each item has a caller ID, result or safe error, with explicit retryable read IDs. Malformed envelopes execute no items. CLI, SDK, MCP and HTTP share the service; batches never purchase, sign or retry failed items automatically.

Three named presets (`company_overview`, `us_macro_overview`, `fx_context`) expand into the same independent research reads. Preview returns the expanded plan without source access. Item selection, common/per-item requirements, compact/full evidence and reusable JSON queries work across all interfaces. An overview separates observation, explicit publication and source retrieval times, units, source cadence, missingness and coverage flags. Original batch records and attributions are retained. The envelope is bounded to 5 MiB including the existing 4 MiB batch budget. Read success does not imply complete observations or synchronized source dates. [Preset contracts](docs/presets.md).

Connection diagnostics validate selected source configuration without creating source clients or echoing contact settings. Explicit network opt-in reuses fixed source probes, caches/quotas and private baselines in the configured state directory. Configuration readiness, probe status and synthetic mode remain distinct. Fixture diagnostics never run network probes.

Opt-in source checks cover seven official source families with ten fixed probes. They capture raw-byte and structure hashes, observation dates, latency and changes against private last-success baselines. A manual workflow keeps live requests separate from offline CI. Fixture/mock checks do not establish live connectivity. See [operator source checks](docs/source-checks.md).

## Provenance and data quality

- Numeric values are decimal strings with units, actual periods, accessions, source hashes, JSON pointers or XML paths, and retrieval times.
- Missing and conflicting values are explicit. Coverage distinguishes requested periods from available data; incomplete financial results require `allow_partial`.
- Unreported Q4 values, currency conversions, 13F dollar/thousand-dollar units and CUSIP-to-ticker mappings are not inferred.
- Filing discovery, reported records and derived ratios are distinct outputs. Current snapshots cannot satisfy point-in-time historical requests.
- Growth compares corresponding actual prior-year durations and positive baselines. FCF uses reported operating CF minus nonnegative PP&E acquisition payments; margin divides by same-period revenue.
- Comparisons support reported and derived metrics at the latest shared reported end, with identical actual periods per metric, including growth baselines. Missing or unaligned values require `allow_partial`.
- Derived FX uses same-day ECB inputs with explicit base/quote units. Macro calculations require the exact prior-year month, previous month or same-day maturities; absent inputs remain unavailable.
- Retrieval uses approved endpoints, caching, rate limits, response bounds and finite retries. Source failures never trigger a paid-provider fallback.

[Sources and terms](DATA_SOURCES.md) · [Financial methodology](docs/methodology.md) · [Validation scope](docs/verification.md)

## Optional snapshot API

Immutable snapshots of five annual metrics support explicit quotes, purchases and authenticated recovery. Payments default to off. `mock` uses fictional payments; `testnet` uses x402 V2 exact/EIP-3009 with Base Sepolia USDC. Policy checks, budget reservations before signing and authenticated recovery protect the payment path. A configured trusted RPC supports on-demand reconciliation: finalized matching USDC/nonce evidence recovers settled artifacts; finalized expiry plus an unused nonce establishes failure. Other evidence remains pending. Hashed audit records and terminal-state guards cover concurrent recovery. Research endpoints do not charge for retrieval.

Official Python and TypeScript HTTP clients are exercised offline. Hosted MCP accepts standard payment metadata from an external wallet, with a transport compatibility adapter for the pinned Python SDKs. Its external-wallet path does not sign with the local Atlas wallet. Local/external purchase claims are durable and exclusive per quote.

## Coverage limits

Consolidated equity prices, daily prices and adjustment factors, order books, options, analyst consensus, newswire articles, earnings-call transcripts, a complete security master or corporate-action history, PIT and trading are unavailable.

FX covers the ECB's latest 90-calendar-day reference window. EFFR/SOFR covers the latest 90 effective observations. Real Treasury yields cover one selected year from 2003 onward; BLS calculations use the registration-free latest-three-year source window. No holiday filling, real-time FX, term SOFR, historical release vintages or GDP/PCE connector is provided. See [macro coverage](docs/macro.md).

The HTTP API supports one process, one private SQLite volume and loopback connections. Mainnet, distributed budgets, background reconciliation and public-facing authentication or TLS are unsupported. Reconciliation may remain unresolved for legacy deadlines, old nonce logs without a transaction hint, ambiguous batched receipts and pre-submission crashes. Live RPC/Facilitator behavior and real transfers remain unverified.

Remote MCP hosting, asynchronous research jobs, historical value-difference feeds and model-driven task-success or token benchmarks are not implemented. Agent contract tests use official MCP messages and offline fixtures, without an LLM. Source checks are invoked explicitly; no recurring monitor or release/holiday calendar is installed.

[Quickstart](docs/quickstart.md) · [API configuration](docs/self-hosting.md) · [Payments](docs/payments.md) · [Apache-2.0](LICENSE)
