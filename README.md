# Atlas Terminal

SEC issuer resolution, annual financial facts and filing metadata for agents, through a CLI, Python SDK and stdio MCP. Local retrieval is free and needs no wallet or LLM API key. An optional reference HTTP service offers immutable snapshots with explicit quote and purchase steps.

[日本語](README.ja.md) · [Implementation status](IMPLEMENTATION_STATUS.md) · [Methodology](docs/methodology.md)

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

Supported metrics: `revenue`, `net_income`, `assets`, `liabilities`, `operating_cash_flow`. Scope: annual US-GAAP USD facts in primary-year 10-K/10-K/A filings. Missing or conflicting values cause `INCOMPLETE_COVERAGE` unless `--allow-partial` is explicit. There are no prices, news, alerts, trading, paid data providers or built-in LLM analysis. Historical `as_of`/PIT queries are unsupported; later comparative restatements are outside the selection policy.

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

MCP stdout is protocol-only. Configure your host using [examples/mcp.fixture.json](examples/mcp.fixture.json), replacing its absolute checkout path. Six tools expose resolution, financials, filings, quote, purchase and status. Only purchase spends funds, under a process-configured policy; tools cannot choose service origins, wallets or policy files. [Connection details](docs/quickstart.md).

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
