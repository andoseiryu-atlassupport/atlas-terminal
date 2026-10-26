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
| Economics      | Sixteen BLS series covering consumer prices, employment, unemployment, wages and job openings          |
| Treasury       | Daily nominal and TIPS-based real par yield curves; federal Debt to the Penny observations             |
| Macro analysis | CPI/core CPI YoY, payroll monthly changes and the same-day 10Y–2Y nominal spread, with original inputs |
| Rates / FX     | Overnight EFFR/SOFR; ECB daily reference FX for 31 currencies, with per-currency history limits        |
| Policy         | Federal Reserve release headlines, publication times and original links                                |

US national accounts add sixteen BEA series; Japanese macro adds twenty-nine BOJ/Statistical Dashboard series. Eurostat adds 48 exact European selections and ONS adds five UK series. Five activity/global presets join the existing macro presets through shared batches with dates, source IDs and per-item failures. [Series and statistical definitions](docs/economic-indicators.md).

Run `atlas research catalog --json` for identifiers, query fields, units and limits for all 32 datasets. The [research guide](docs/research.md) includes examples for each dataset.

`activity_economics` supplies twenty-two curated US/Australian/Canadian/Chinese selections through Census, Federal Reserve G.17, DOL series distributed by FRED, ABS, StatCan, OECD and World Bank WDI. Six activity/country presets bring the total to twenty-two, including a three-item US goods/services trade overview. Seven Japanese Dashboard selectors cover machinery orders, trade, current account and corporate statistics. Weekly claims keep Saturday dates; Canadian flags distinguish true zeros from suppressed/missing values; China WDI GDP stays annual. Australian monthly CPI uses `ABS,CPI,2.0.0`; quarterly CPI uses the separate `CPI_Q` flow. Census preserves program and time-slot identity, including reported negative trade balances. Text-cell locators extend compact evidence for G.17. Live source compatibility, including exact new Dashboard and ABS labour selectors, remains unverified.

IMF/OECD/ABS SDMX dataflow/code discovery and metadata-validated single-series reads add international monthly, quarterly and annual coverage. `macro_panel` aligns up to ten explicit queries with last/mean aggregation, exact inputs and missing cells. Offline series search covers registered series and supported FX pairs, with country/frequency/unit/source filters. [Discovery, limits and examples](docs/international-macro.md).

Japanese government bonds add Ministry of Finance 1–40-year constant-maturity yields. Three bond presets cover US, Japan or combined context through the existing six research tools. Same-date curve slopes, butterflies, US nominal/real par gaps and preceding-observation changes retain exact inputs and basis-point formulas. MOF original CSV cells resolve through compact evidence IDs, with current-month rows authoritative over overlapping history. Par inflation compensation is a proxy, not pure expected inflation. Live MOF/Treasury compatibility for this expansion remains unverified. [Government bond guide](docs/fixed-income.md).

Euro area government research adds separate ECB AAA/all-rating, continuously compounded zero-coupon spot curves. Australian government research adds RBA interpolated nominal 2/3/5/10-year yields. Twenty-three additional source-local slopes, butterflies and preceding-observation changes bring the curve registry to 50 series. Regional and four-region presets retain source conventions and independent observation dates; no individual European sovereign, UK gilt or cross-country basis/FX conversion is inferred. [Regional bond coverage](docs/fixed-income.md#euro-area-and-australia).

## Interfaces

| Interface        | Entry points                                                                                                                 |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| CLI              | `atlas research catalog` / `atlas research get`                                                                              |
| Python SDK       | `Atlas.research_catalog()` / `Atlas.get_research(query)`                                                                     |
| MCP              | Six research-only tools over stdio or Streamable HTTP; fourteen in full stdio mode, plus hosted payment/reconciliation tools |
| HTTP             | `GET /v1/research/catalog` / `POST /v1/research`                                                                             |
| Annual snapshots | `financials get` / `Atlas.get_financials`, covering five annual metrics                                                      |

Research requires no data subscription, wallet or LLM. BEA, Census and FRED retrieval require separate free registered `BEA_API_KEY`, `CENSUS_API_KEY` and `FRED_API_KEY` settings; Japanese macro retrieval needs no registration. SEC retrieval requires a private `SEC_USER_AGENT` containing the application name and an actual contact email. The `fixture` provider returns fictional data without network access.

MCP research publishes dataset-specific query schemas and a validated common response schema. Optional compact responses replace repeated provenance with content-addressed evidence IDs; field projection preserves interpretation and calculation context. Snapshot-bound cursors resume without refetching upstream data. CLI, SDK and HTTP share these services. Catalogs support dataset filtering and compact discovery. Error envelopes expose safe field hints, next actions and known retry delays. See the [agent contracts](docs/agents.md) for retention and paging limits.

Optional requirements enforce the oldest source age, latest usable observation date, missingness, derived records and explicit truncation on the complete selected result. Conditions fail closed with `QUALITY_NOT_MET`; cursor reads recheck source age without refetching. Source age does not imply publication freshness, and requirements do not expand coverage or force a refresh.

Research batches support up to ten ordered independent queries, 16 KiB requests and a 4 MiB response budget. Each item has a caller ID, result or safe error, with explicit retryable read IDs. Malformed envelopes execute no items. CLI, SDK, MCP and HTTP share the service; batches never purchase, sign or retry failed items automatically.

Five named presets (`company_overview`, `us_macro_overview`, `fx_context`, `japan_macro_overview`, `us_japan_overview`) expand into the same independent research reads. Preview returns the expanded plan without source access. Item selection, common/per-item requirements, compact/full evidence and reusable JSON queries work across all interfaces. An overview separates observation, explicit publication and source retrieval times, units, source cadence, missingness and coverage flags. Original batch records and attributions are retained. The envelope is bounded to 5 MiB including the existing 4 MiB batch budget. Read success does not imply complete observations or synchronized source dates. [Preset contracts](docs/presets.md).

Connection diagnostics validate selected source configuration without creating source clients or echoing contact settings. Explicit network opt-in reuses fixed source probes, caches/quotas and private baselines in the configured state directory. Configuration readiness, probe status and synthetic mode remain distinct. Fixture diagnostics never run network probes.

Opt-in source checks cover twenty-one official source families with thirty-three fixed probes. IMF/OECD probes check dataflow metadata, without certifying observation retrieval. They capture raw-byte and structure hashes, observation dates, latency and changes against private last-success baselines. A manual workflow keeps live requests separate from offline CI. Fixture/mock checks do not establish live connectivity. See [operator source checks](docs/source-checks.md).

## Optional native factor risk models

`atlas risk demo` exercises an independent daily fundamental model on 60 fictional assets. US/USD, Japanese/JPY, Korean/KRW and British/GBP single-market models support market, native industry and seven optional style exposures; constrained weighted cross-sectional regression; shrunk EWMA factor covariance and specific variances; portfolio volatility, Euler contributions, active risk and shocks. Native classifications are JPX33, explicit KSIC 10/11 sections and UK SIC 2007/2026 sections; the US retains SIC divisions. Matching asset/fundamental currency, classification, timing and full local-currency units are required; mixed markets/currencies fail. `--country JP`, `--country KR` and `--country GB` run complete local-market demos. A strictly subsequent holdout compares a fixed forecast with realized variance and a historical portfolio EWMA baseline. [Local-market conventions](docs/equity-markets.md).

`atlas risk demo --method time_series` exercises observed-factor OLS or EWMA-WLS across time. It estimates each asset's betas and diagnostic intercept, with 1–200 assets, 1–12 factors and 60–252 estimation sessions. Inputs declare one currency, decimal-return units and total/excess factor convention; excess factors require an aligned risk-free return. Factor returns are supplied observations, not the cross-sectional model's estimated returns. Both methods share compact X/F/D artifacts, risk decomposition and holdout evaluation.

Native JSON, CSV and read-only SQLite connectors load operator-selected inputs without another financial framework. Explicit column, table and asset mappings normalize supplied returns or split/dividend-adjusted prices; market capitalization uses a separate raw-price/share-count basis or reported value. GBP price rows explicitly declare GBP/GBp/GBX; pence normalize before return ratios/raw-price capitalization while reported GBP capitalization stays unchanged. `run` reads, validates, imports, fits and analyzes from a named connection, as-of cutoff and weights. Preview/catalog do not open datasets. Input receipts retain actual document/logical-row hashes and normalization metadata without exposing configured paths. Trusted SDK providers can implement external APIs; none is bundled, and execution requires `allow_network=true`. See [connection contracts](docs/data-connections.md).

CLI and SDK share immutable input/model storage with optional `atlas_risk` MCP and `POST /v1/risk`. Enable the extension with `--enable-risk`; existing research tools remain unchanged. Remote risk mode requires bearer authentication. Risk requests are bounded to 64 KiB, with compact model/selected-exposure reads and operator-managed private storage.

The extension is experimental, with no vendor model data, bundled equity-price feed or verified historical vintages. Caller timestamps are validated internally but remain unverified assertions. Both methods require a balanced supplied universe. Cross-sectional estimation requires fixed industry membership and 30–200 common stocks from one supported country; portfolio holdings do not replace the broader estimation universe. Local-calendar bounds permit fourteen-day Japanese/Korean closures without inserting returns; no actual exchange calendar is inferred. Accounting standards and full monetary units must be aligned by the operator. Global country/FX factors, survivorship corrections and residual correlations are unavailable. Real-market forecast accuracy is unverified. See [methodology and usage](docs/risk-model.md).

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

Read-only local budget summaries distinguish confirmed spending, unresolved reservations and remaining session/UTC-day allowances. Hosted purchase preflight combines authenticated HTTP order readiness with operator policy and budget checks; it does not load a signing key, reserve funds or authorize payment. A public testnet payer address is required for budget inspection, and configured addresses must match the eventual local signer. HTTP exposes order readiness only, never the caller's wallet budget.

Optional task `intent_id` values bind an exact validated annual query to one quote within a private client state volume and service origin. A private capability persisted before HTTP preparation recovers the same quote after response loss; atomic server publication handles concurrent preparation. Changed queries conflict, and expiry/retention cleanup never silently replaces an order. Persistent intent tombstones are bounded to 10,000 entries per client/server store; there is no global identity or guarantee across deleted or independent state volumes. CLI, SDK and full MCP expose these contracts; research-only MCP remains free of payment handlers.

## Coverage limits

Consolidated equity prices, daily prices and adjustment factors, order books, options, analyst consensus, newswire articles, earnings-call transcripts, a complete security master or corporate-action history, PIT and trading are unavailable.

FX uses the full published ECB history for 31 currencies with per-currency gaps. BLS observation-date queries span up to 30 calendar years in ten-year requests; the default remains the latest three years. Sixteen BEA national-account series and twenty-nine Japanese macro series use explicit units and periods. EFFR/SOFR still covers the latest 90 effective observations. Real Treasury yields cover one selected year from 2003 onward. No holiday filling, real-time FX, term SOFR or historical release vintages are provided. See [macro coverage](docs/macro.md).

The reference HTTP API supports one process, one private SQLite volume and loopback connections. The separate research-only MCP HTTP service adds exact host/origin validation, explicit bearer or anonymous access, request-size/rate/concurrency limits and stateless JSON Streamable HTTP. It requires one process and a persistent state volume behind operator-managed HTTPS. It has no payment handlers, OAuth, tenant isolation or distributed limiter. No official public remote endpoint is operated. Mainnet, distributed budgets and background reconciliation are unsupported. Reconciliation may remain unresolved for legacy deadlines, old nonce logs without a transaction hint, ambiguous batched receipts and pre-submission crashes. Live RPC/Facilitator behavior and real transfers remain unverified.

The repository includes static HTML/Markdown documentation, a focused research skill, versioned plugin/Registry metadata preparation and manual discovery/workflow evaluation cases. The [documentation website](https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site) is publicly deployed through Sites; source guides remain on GitHub. Tool responses link public GitHub guides and preserve original data attribution. A runnable four-query company/CPI/FX example resolves evidence through one official MCP session and produces a contract report without an LLM. Public documentation checks run weekly/manually; actual remote workflow checks require a configured endpoint and explicit scheduling opt-in. User-agent simulation does not establish official crawler IP access, indexing or model citations. Single-host systemd/Caddy preparation preserves one worker and a private state directory without provisioning a service. PyPI publication, an official MCP Registry listing and OpenAI plugin review remain external publication steps. Asynchronous research jobs, historical value-difference feeds and model-driven task-success or token benchmarks are not implemented. Official-source checks remain explicitly invoked; no continuous uptime monitor or release/holiday calendar is installed.

Three [research capture cases](docs/research-examples.md) save complete selected results, original evidence and exact queries under content-addressed names through one official MCP session. Public observation-page export rejects fictional or incomplete captures. Manual discovery summaries preserve controlled prompts, reviewed outcomes, separate branded/unbranded rates and unknown measurements; bounded search-result probes remain separate. A manual trusted PyPI publisher workflow and public search ownership tags support operator registrations without claiming those registrations are complete.

Installed `atlas showcase research` and `atlas showcase japan-risk` commands execute one official MCP client session each, with readable output or complete JSON reports. Recorded fictional results, transcripts, captioned 60-second replays and input interpretation are available in the [rates/FX](docs/agent-research-walkthrough.md) and [Japanese risk](docs/japan-risk-walkthrough.md) walkthroughs. Public research requires explicit network opt-in. Walkthroughs use temporary state and make no LLM requests; they do not establish live source compatibility, forecast accuracy or external-user adoption.

[Quickstart](docs/quickstart.md) · [API configuration](docs/self-hosting.md) · [Payments](docs/payments.md) · [Apache-2.0](LICENSE)
