# Atlas Terminal

Atlas Terminal gives AI agents access to US equity research from free public primary sources. It provides company profiles, financial statements, disclosures, reported ownership and economic indicators through a CLI, Python SDK, stdio MCP and HTTP API.

[Capabilities](IMPLEMENTATION_STATUS.md) · [Research guide](docs/research.md) · [Data sources](DATA_SOURCES.md)

## Available data

Atlas provides 16 datasets from SEC EDGAR, BLS, US Treasury yields, Treasury Fiscal Data and the Federal Reserve.

| Area        | Information                                                                                                                           |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Companies   | Current ticker / CIK directory, issuer profiles, SIC industry, exchanges and fiscal year end                                          |
| Financials  | 37 US-GAAP concepts covering income, balance sheets, cash flow, EPS, shares and dividends; annual, reported discrete quarters and YTD |
| Analysis    | Six ratios with formulas and reported inputs; comparison of up to ten specified issuers                                               |
| XBRL        | Concept inventory and raw observations with their taxonomy, units and contexts                                                        |
| Disclosures | All-form filing discovery, including ownership, fund, proxy, offering and corporate-action filings                                    |
| Ownership   | Form 3/4/5 transactions and positions; a selected manager's 13F information table                                                     |
| Macro       | Eight BLS inflation and employment series, daily Treasury par yields and federal debt                                                 |
| Policy      | Federal Reserve release headlines, publication times and original links                                                               |

Values retain their units, periods, source hashes, retrieval times and JSON pointers or XML paths. Missing or ambiguous values are explicit. Financial ratios include their formulas and inputs.

## Get started

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/) in the repository directory:

```sh
uv sync --locked
uv run --offline atlas research catalog --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
```

The fixture provider runs without network access. Its company identifiers and values are fictional, with `is_synthetic=true` and `.invalid` source links.

For public data, research commands use `--provider public` by default. SEC requests require a private `SEC_USER_AGENT` containing your application name and actual contact email. BLS, Treasury and Federal Reserve requests do not require that setting.

```sh
uv run atlas research get statements --identifier AAPL --period quarterly --metrics revenue,eps_basic --count 3 --allow-partial --json
uv run atlas research get economics --series cpi --json
uv run atlas research get treasury_yields --year 2026 --json
```

Research needs no data subscription, API key, wallet or LLM. Commands return JSON, and errors use nonzero exit codes. Dataset names, metrics, series identifiers and query limits are listed in `research catalog` and the [research guide](docs/research.md).

## Python, MCP and HTTP

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_research(
        {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "metrics": ["revenue", "eps_basic"],
            "period": "quarterly",
            "count": 3,
        }
    )
    print(result["records"])
```

Start the MCP server with:

```sh
uv run --offline atlas mcp serve --provider fixture
```

Eight MCP tools expose the research catalog and datasets, company resolution, annual snapshots, filings, quotes, purchases and purchase status. Use `atlas_research_catalog` to discover datasets, then `atlas_get_research` with a `query` object. [MCP setup](docs/quickstart.md).

Start the local HTTP API with:

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
```

`GET /v1/research/catalog` lists datasets and query schemas. `POST /v1/research` retrieves research without a quote or payment. [API configuration](docs/self-hosting.md).

## Annual snapshots and optional payments

`financials get` and `Atlas.get_financials` provide annual US-GAAP USD snapshots for `revenue`, `net_income`, `assets`, `liabilities` and `operating_cash_flow`. Use `research get statements` for the full metric set and quarterly or YTD periods.

The HTTP API also supports immutable annual snapshots with explicit quote, purchase and recovery steps. Payment modes are `off`, `mock` and `testnet`; payments default to off. Testnet uses x402 V2 exact/EIP-3009 with Base Sepolia USDC. Mainnet is unsupported. Research retrieval does not purchase snapshots. [Payment configuration](docs/payments.md).

## Sources and coverage

Filing discovery returns document metadata and links. Structured ownership results describe one selected filing, with amendment, reporting-lag and coverage limits. Current snapshots and revised economic observations are not point-in-time historical data.

Consolidated equity prices, order books, option chains, analyst consensus, newswire articles and earnings-call transcripts are not provided. Source access limits and third-party content terms apply independently of the code license.

[Source terms](DATA_SOURCES.md) · [Financial methodology](docs/methodology.md) · [Validation scope](docs/verification.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## License

Atlas Terminal is licensed under [Apache-2.0](LICENSE). Source-data and dependency terms apply separately.
