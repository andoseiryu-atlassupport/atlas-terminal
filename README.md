# Atlas Terminal

Free public-source US equity research for agents through a CLI, Python SDK, stdio MCP and reference HTTP API. Explore SEC financial statements, ownership disclosures, institutional positions and official economic context. No data subscription, wallet or LLM API key is needed for research.

[日本語](README.ja.md) · [Research guide](docs/research.md) · [Implementation status](IMPLEMENTATION_STATUS.md) · [Methodology](docs/methodology.md)

## Free research datasets

The research service has 16 datasets backed by five free primary sources: SEC EDGAR, BLS, US Treasury yields, Treasury Fiscal Data and Federal Reserve releases. It preserves exact numeric strings, original units, periods, source hashes and JSON pointers or XML paths.

| Area        | Available information                                                                                                                         |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Companies   | Current SEC ticker directory, issuer profiles, SIC industry, exchanges and fiscal year end                                                    |
| Financials  | 37 US-GAAP concepts: income statement, balance sheet, cash flow, EPS, shares and dividends; reported annual, discrete quarter and YTD periods |
| Analysis    | Six ratios with formulas and source inputs; explicit comparison of up to ten issuers                                                          |
| XBRL        | Concept inventory and raw observations with original taxonomy, units and contexts                                                             |
| Disclosures | All-form filing discovery plus ownership, fund, proxy, offering and corporate-action filing groups                                            |
| Ownership   | Parsed Form 3/4/5 positions and transactions; one manager's 13F information table per query                                                   |
| Macro       | Eight BLS inflation and employment series, daily Treasury par yields, federal debt                                                            |
| Policy      | Official Federal Reserve release titles, timestamps and original links                                                                        |

```sh
uv run --offline atlas research catalog --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
uv run --offline atlas research get institutional_holdings --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get economics --series cpi --provider fixture --json
```

For live sources, use `--provider public` (the research default). SEC datasets require your private `SEC_USER_AGENT`; macro and policy datasets do not. There are no automatic paid-source fallbacks. Research HTTP requests use `POST /v1/research` without a quote or payment, including when the separate annual-snapshot service uses mock/testnet payments.

**Coverage limitations:** Consolidated equity prices, order books, option chains, analyst consensus, licensed newswires, earnings-call transcripts and a PIT database are unavailable. Filing discovery is labeled metadata and does not imply normalized corporate actions or parsed fund portfolios. Free access is not blanket permission to redistribute third-party content. Source terms and implemented gaps are included in `research catalog` and [DATA_SOURCES.md](DATA_SOURCES.md).

All research datasets have offline fixture tests. **Live source access and the latest source terms pages have not been verified in this environment.** Fixture results are fictional and marked `is_synthetic=true`.

## Try the offline demo

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). From this checkout:

```sh
uv sync --locked --all-extras --dev
uv run --offline atlas demo --json
```

The demo normalizes packaged, fictional SEC-shaped inputs. It uses no network, model key or wallet. Every fact includes its period, USD unit, SEC tag, accession, raw JSON pointer, source hash and retrieval time. `is_synthetic=true` and `.invalid` source links identify the demo.

## Local SEC retrieval

Set `SEC_USER_AGENT` privately to your identifying application name and actual contact email. Atlas does not supply a contact address. Then run:

```sh
uv run atlas company resolve AAPL --provider sec --json
uv run atlas financials get AAPL --metrics revenue,net_income --period annual --count 3 --provider sec --json
uv run atlas filings list AAPL --form 10-K --limit 3 --provider sec --json
```

SEC is the default provider. A SEC failure never switches to hosted payment. `--provider fixture` selects the fictional company `ATLASDEMO`. All commands emit one JSON object; errors use nonzero exits (2 input, 3 upstream, 4 policy/config, 5 payment, 1 unexpected).

The existing `financials get` command retains its annual snapshot contract: `revenue`, `net_income`, `assets`, `liabilities`, `operating_cash_flow`, annual US-GAAP USD facts in primary-year 10-K/10-K/A filings. Missing or conflicting values cause `INCOMPLETE_COVERAGE` unless `--allow-partial` is explicit. Use `research get statements` for the expanded metrics and periods. Historical `as_of`/PIT queries remain unsupported; later comparative restatements are outside the normalized selection policy.

## Python and MCP

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_financials(
        {
            "identifier": "ATLASDEMO",
            "metrics": ["revenue", "net_income"],
            "period": "annual",
            "count": 3,
            "allow_partial": False,
        }
    )
    print(result.model_dump_json())
```

```sh
uv run --offline atlas mcp serve --provider fixture
```

MCP stdout is protocol-only. Configure your host using [examples/mcp.fixture.json](examples/mcp.fixture.json), replacing its absolute checkout path. Eight tools expose research catalog/retrieval, resolution, annual financials, filings, quote, purchase and status. Only purchase spends funds, under a process-configured policy; tools cannot choose service origins, wallets or policy files. [Connection details](docs/quickstart.md).

## Optional reference API and payments

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
uv run --offline python examples/mock_purchase.py
```

The second command runs a complete offline simulated purchase and recovery. Modes are `off` (zero price), `mock` (fictional assets, `simulated=true`) and `testnet` (official x402 V2/exact/EIP-3009, Base Sepolia USDC only). Payments are off by default; mainnet is rejected in code. The reference server supports one process and a private SQLite volume, with loopback binding, bounded quote preparation and authenticated recovery.

For a separately running service, set `ATLAS_HOSTED_ORIGIN` and a private `ATLAS_STATE_DIR`. Paid purchases additionally require `ATLAS_POLICY_FILE`. Copy the mock policy to private runtime storage first; it is explicitly enabled for mock only.

```sh
uv run atlas quote financials ATLASDEMO --metrics revenue,net_income --count 3 --json
uv run atlas purchase QUOTE_ID --json
uv run atlas purchase-status QUOTE_ID --json
```

Hosted financials never buy implicitly. Quote JSON omits fact values and recovery tokens. The client stores the private recovery capability outside tool output. Unknown settlement retains the budget and prevents a new signature. [Self hosting](docs/self-hosting.md) · [Payment and opt-in testnet steps](docs/payments.md).

## Verification and limitations

```sh
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline python -m build --no-isolation
npm ci
npm run check
```

Default tests prohibit IP sockets and exclude SEC-live and real-testnet tests. Official SDK wire handling and ephemeral local signatures are tested with an offline facilitator transport. **Live SEC retrieval and real Base Sepolia transfer have not been run.** No live service, production support or settlement availability is claimed. Review [actual commands and results](IMPLEMENTATION_STATUS.md), [dependency audit](DEPENDENCY_AUDIT.md), [data conditions](DATA_SOURCES.md) and [security boundaries](SECURITY.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

Source-data terms are described separately in [DATA_SOURCES.md](DATA_SOURCES.md).
