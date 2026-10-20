# Quickstart and MCP

Use Python 3.12+ and uv. Run `uv sync --locked --all-extras --dev` in the repository. After dependencies are installed, `uv run --offline atlas demo --json` works without external requests. The package has not been published to PyPI.

## Retrieve data

```sh
uv run --offline atlas demo --json
uv run --offline atlas company resolve ATLASDEMO --provider fixture --json
uv run --offline atlas financials get ATLASDEMO --metrics revenue,net_income --count 3 --provider fixture --json
uv run --offline atlas filings list ATLASDEMO --form 10-K --limit 3 --provider fixture --json
uv run --offline atlas research catalog --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
uv run --offline atlas research get economics --series cpi --provider fixture --json
```

For SEC retrieval, configure `SEC_USER_AGENT` privately with your identifying application name and actual contact email. Use a real issuer identifier and the `sec` provider for company/annual commands, or `public` for research commands. No API key, wallet or model key is required. `.env.example` lists variable names; Atlas does not load it automatically. Keep contact details out of chat and logs.

## Connect an MCP host

Replace `/absolute/path/to/atlas-terminal` in the [configuration example](../examples/mcp.fixture.json) with the repository path. Make uv available on the host's PATH, or use its absolute executable path.

```json
{
  "mcpServers": {
    "atlas-terminal": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/atlas-terminal",
        "--offline",
        "atlas",
        "mcp",
        "serve",
        "--provider",
        "fixture"
      ]
    }
  }
}
```

The server supports initialize, tools/list and tools/call with the official MCP client. For `atlas_get_financials`, pass `identifier=ATLASDEMO`, `metrics=[revenue, net_income]` and `count=3` to receive the same structured result as the CLI and SDK.

Configure `SEC_USER_AGENT` privately for SEC requests. The `hosted` provider uses `ATLAS_HOSTED_ORIGIN`, a private `ATLAS_STATE_DIR` and, for paid purchases, `ATLAS_POLICY_FILE`. Never put real keys in examples or model inputs. MCP arguments cannot change providers, URLs, policies or wallets.

| Tool                   | Behavior                                                            |
| ---------------------- | ------------------------------------------------------------------- |
| atlas_resolve_company  | Resolve a current issuer                                            |
| atlas_get_financials   | Retrieve fixture/SEC annual data; hosted returns QUOTE_REQUIRED     |
| atlas_list_filings     | Return filing metadata and source references                        |
| atlas_quote            | Save an immutable artifact and price without authorizing payment    |
| atlas_purchase         | Spend within the operator's delegated policy and budget             |
| atlas_purchase_status  | Recover status using a private capability without paying            |
| atlas_research_catalog | List 17 free datasets, units, query schemas and coverage gaps       |
| atlas_get_research     | Retrieve research using a `query` object without a quote or payment |

For example, call `atlas_get_research` with `{"query":{"dataset":"economics","series":"cpi"}}`. Use `--provider public` for direct government sources. SEC contact settings are checked only when accessing SEC. The `hosted` provider does not proxy research. Only `atlas_purchase` spends funds. See the [research guide](research.md) for dataset queries.

Startup failures go to stderr. Once running, stdout belongs exclusively to the official MCP protocol. Windows execution has not been verified.
