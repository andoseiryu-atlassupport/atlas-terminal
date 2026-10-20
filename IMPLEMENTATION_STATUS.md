# Atlas Terminal capabilities

Atlas Terminal retrieves US equity research from free public primary sources. Its CLI, Python SDK, stdio MCP server and HTTP API share the same datasets and selection rules.

## Datasets

| Area        | Capabilities                                                                                       |
| ----------- | -------------------------------------------------------------------------------------------------- |
| Companies   | Current ticker / CIK search, issuer profiles, SIC industry, exchanges and fiscal year end          |
| Financials  | 37 US-GAAP concepts covering income, balance sheets, cash flow, EPS, shares and dividends          |
| Periods     | Annual, explicitly reported discrete quarters and YTD, matched to actual dates and filing metadata |
| Analysis    | Six ratios with formulas and reported inputs; comparison of up to ten specified issuers            |
| XBRL        | Concept and unit inventories, plus observations with their original accounting contexts            |
| Disclosures | All-form filing discovery and ownership, fund, proxy, offering and corporate-action filing groups  |
| Ownership   | Form 3/4/5 records and a manager's 13F information table, retrieved one filing at a time           |
| Economics   | Eight BLS series covering consumer prices, employment, unemployment, wages and job openings        |
| Treasury    | Daily Treasury par yields and federal Debt to the Penny observations                               |
| Policy      | Federal Reserve release headlines, publication times and original links                            |

Run `atlas research catalog --json` for identifiers, query fields, units and limits for all 16 datasets. The [research guide](docs/research.md) includes examples for each dataset.

## Interfaces

| Interface        | Entry points                                                             |
| ---------------- | ------------------------------------------------------------------------ |
| CLI              | `atlas research catalog` / `atlas research get`                          |
| Python SDK       | `Atlas.research_catalog()` / `Atlas.get_research(query)`                 |
| MCP              | Eight tools, including `atlas_research_catalog` and `atlas_get_research` |
| HTTP             | `GET /v1/research/catalog` / `POST /v1/research`                         |
| Annual snapshots | `financials get` / `Atlas.get_financials`, covering five annual metrics  |

Research requires no data subscription, API key, wallet or LLM. SEC retrieval requires a private `SEC_USER_AGENT` containing the application name and an actual contact email. The `fixture` provider returns fictional data without network access.

## Provenance and data quality

- Numeric values are decimal strings with units, actual periods, accessions, source hashes, JSON pointers or XML paths, and retrieval times.
- Missing and conflicting values are explicit. Coverage distinguishes requested periods from available data; incomplete financial results require `allow_partial`.
- Unreported Q4 values, currency conversions, 13F dollar/thousand-dollar units and CUSIP-to-ticker mappings are not inferred.
- Filing discovery, reported records and derived ratios are distinct outputs. Current snapshots cannot satisfy point-in-time historical requests.
- Retrieval uses approved endpoints, caching, rate limits, response bounds and finite retries. Source failures never trigger a paid-provider fallback.

[Sources and terms](DATA_SOURCES.md) · [Financial methodology](docs/methodology.md) · [Validation scope](docs/verification.md)

## Optional snapshot API

Immutable snapshots of five annual metrics support explicit quotes, purchases and authenticated recovery. Payments default to off. `mock` uses fictional payments; `testnet` uses x402 V2 exact/EIP-3009 with Base Sepolia USDC. Policy checks, budget reservations before signing and authenticated recovery protect the payment path. Unknown settlement remains pending. Research endpoints do not charge for retrieval.

## Coverage limits

Consolidated equity prices, daily prices and adjustment factors, order books, options, analyst consensus, newswire articles, earnings-call transcripts, a complete security master or corporate-action history, PIT and trading are unavailable.

The HTTP API supports one process, one private SQLite volume and loopback connections. Mainnet, distributed budgets, automatic settlement reconciliation and public-facing authentication or TLS are unsupported.

[Quickstart](docs/quickstart.md) · [API configuration](docs/self-hosting.md) · [Payments](docs/payments.md) · [Apache-2.0](LICENSE)
