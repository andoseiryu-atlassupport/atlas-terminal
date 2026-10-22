# Use Atlas Terminal with Codex and ChatGPT desktop

Connect Atlas Terminal as an MCP server to retrieve free primary-source company financials, US macro indicators and reference FX. One connection provides dataset discovery, exact research queries, batches, presets, evidence lookup and configuration diagnostics. Local installation does not register a public ChatGPT plugin.

## Install a tagged version

Install Python 3.12+, uv and your MCP client. This command installs the v0.3.0 source distribution from GitHub; PyPI publication is not yet available:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.3.0'
atlas research catalog --json
atlas research preset fx_context --base-currency USD --quote-currency JPY --include fx,us_sofr --provider fixture --json
```

The first installation needs network access. The fixture command then runs without external data requests and returns explicitly fictional values. Use a dedicated private `ATLAS_STATE_DIR` for cache and evidence storage. Atlas reads process environment variables; it does not load `.env` files automatically.

## Add the MCP connection in Codex

Use the same environment in which `uv` is available:

```sh
codex mcp add atlas-terminal -- uvx --from 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.3.0' atlas mcp serve --provider public --research-only
codex mcp list
```

To verify without source access, temporarily choose `--provider fixture`. Fictional fixture success does not verify actual source connectivity. A research-only connection exposes six tools and has no quote, purchase or wallet handler. The original full stdio mode remains available for separately configured snapshot workflows.

For SEC company data, configure `SEC_USER_AGENT` privately with your application name and actual contact email. Forward it from the MCP client's private environment. A local Codex configuration can forward the variable without placing its value in the file:

```toml
[mcp_servers.atlas-terminal]
command = "uvx"
args = ["--from", "git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.3.0", "atlas", "mcp", "serve", "--provider", "public", "--research-only"]
env_vars = ["SEC_USER_AGENT", "ATLAS_STATE_DIR"]
```

Choose either the CLI registration or this configuration for a given server name. Never publish real contact settings or credentials. BLS, Treasury, Federal Reserve, ECB and New York Fed research do not require the SEC setting or a data API key.

## Add the research skill

Copy the repository's `skills/atlas-research` directory into `.agents/skills/atlas-research` in a project where the configured MCP connection should be used. Its `SKILL.md` explains when to discover datasets, preview a preset, inspect partial results and resolve evidence. The companion `agents/openai.yaml` declares the Atlas MCP dependency. Installation does not grant new permissions or override explicit user instructions.

The skill supports requests such as:

- "Use Atlas to retrieve AAPL's reported quarterly revenue and free cash flow with sources."
- "Get US CPI, unemployment and SOFR through my Atlas connection."
- "Retrieve USD/JPY reference FX and US inflation context; show the observation dates."

Ask for unsupported real-time quotes or a historical as-of snapshot to verify that the workflow states the coverage limit rather than inventing data. See [workflow evaluation cases](../evals/research-workflows.json).

## ChatGPT desktop and hosted ChatGPT

Compatible ChatGPT desktop versions can connect local stdio servers in MCP settings. Use `uvx` as the command and the arguments above; supply private environment settings through the client. Availability depends on the client and workspace configuration.

Hosted ChatGPT environments use their own plugin connections and do not read your local Codex configuration. An operator can deploy [remote MCP](remote-mcp.md), test the HTTPS endpoint, and prepare a public plugin submission. A [plugin package generator](distribution.md) is included. Public listing and installation are separate from web search indexing, and no public listing is claimed here.

[Official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp) · [Official skill packaging](https://developers.openai.com/plugins/build/skills) · [Quickstart](quickstart.md)
