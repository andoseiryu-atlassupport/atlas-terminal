# Use Atlas Terminal with Codex and ChatGPT desktop

Connect Atlas Terminal as an MCP server for financial research with original sources and inspectable calculations. The current source checkout covers company financials/filings, macro/rates/FX, optional market prices, technical indicators, Japanese margin balances, Buffett-inspired scoring and Polymarket outcomes. One connection provides catalog discovery, exact queries, batches, presets, evidence lookup and diagnostics. Public primary sources are the default; optional Yahoo access requires operator-enabled personal use. Local installation does not register a public ChatGPT plugin.

## Install a tagged version

Install Python 3.12+, uv and your MCP client. This command installs the v0.7.0 source distribution from GitHub; PyPI publication is not yet available:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas research catalog --json
atlas showcase research
atlas showcase japan-risk
```

The first installation needs network access. The fixture command then runs without external data requests and returns explicitly fictional values. Use a dedicated private `ATLAS_STATE_DIR` for cache and evidence storage. Atlas reads process environment variables; it does not load `.env` files automatically.

The [research](agent-research-walkthrough.md) and [Japanese risk](japan-risk-walkthrough.md) walkthroughs include recorded output, source/model interpretation and the steps to substitute actual inputs. They use isolated temporary sessions; your regular MCP connection uses the private state configured below.

The existing `v0.7.0` release does not include every later source-checkout feature. Use the installed catalog to determine availability, or use the current checkout below for the capabilities documented on `main`.

## Use the current source checkout

```sh
git clone https://github.com/andoseiryu-atlassupport/atlas-terminal.git
cd atlas-terminal
uv sync --locked
uv run --offline atlas research catalog --json
```

Register that checkout with an absolute directory path. Replace the example path with its actual location:

```sh
codex mcp add atlas-terminal-source -- uv run --offline --directory /absolute/path/atlas-terminal atlas mcp serve --provider public --research-only
```

This uses the checkout's locked environment. `--offline` prevents dependency downloads; the public provider still makes source requests. Choose `--provider fixture` to verify the connection with fictional data. Configure private source settings in the MCP process environment as described below. Use the checkout or tagged connection appropriate to the task; do not assume their catalogs are identical.

## Add the MCP connection in Codex

Use the same environment in which `uv` is available:

```sh
codex mcp add atlas-terminal -- uvx --from 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0' atlas mcp serve --provider public --research-only
codex mcp list
```

To verify without source access, temporarily choose `--provider fixture`. Fictional fixture success does not verify actual source connectivity. A research-only connection exposes six tools and has no quote, purchase or wallet handler. The original full stdio mode remains available for separately configured snapshot workflows.

For SEC company data, configure `SEC_USER_AGENT` privately with your application name and actual contact email. Forward it from the MCP client's private environment. A local Codex configuration can forward the variable without placing its value in the file:

```toml
[mcp_servers.atlas-terminal]
command = "uvx"
args = ["--from", "git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0", "atlas", "mcp", "serve", "--provider", "public", "--research-only"]
env_vars = ["SEC_USER_AGENT", "ATLAS_STATE_DIR"]
```

Choose either the CLI registration or this configuration for a given server name. Never publish real contact settings or credentials. BLS, Treasury, Federal Reserve, ECB, New York Fed and Japan macro research do not require the SEC setting or a data API key. BEA national accounts require `BEA_API_KEY` in the MCP server process environment after free registration. Never put it in tool arguments.

## Add the research skill

Copy the repository's `skills/atlas-research` directory into `.agents/skills/atlas-research` in a project where the configured MCP connection should be used. Its `SKILL.md` explains when to discover datasets, preview a preset, inspect partial results and resolve evidence. The companion `agents/openai.yaml` declares the Atlas MCP dependency. Installation does not grant new permissions or override explicit user instructions.

The skill supports requests such as:

- "Use Atlas to retrieve AAPL's reported quarterly revenue and free cash flow with sources."
- "Get US CPI, unemployment and SOFR through my Atlas connection."
- "Retrieve USD/JPY reference FX and US inflation context; show the observation dates."

When the installed catalog exposes the corresponding source-checkout datasets, it also supports requests such as "Calculate Toyota's RSI from the selected price basis," "Read Toyota's latest linked JPX margin report," and "Find Anthropic events on Polymarket and show each outcome's price, percentage and status." Optional sources still require their operator settings and source terms; these examples do not enable access or establish live connectivity.

Ask for unsupported real-time quotes or a historical as-of snapshot to verify that the workflow states the coverage limit rather than inventing data. See [workflow evaluation cases](../evals/research-workflows.json).

## ChatGPT desktop and hosted ChatGPT

Compatible ChatGPT desktop versions can connect local stdio servers in MCP settings. Use `uvx` as the command and the arguments above; supply private environment settings through the client. Availability depends on the client and workspace configuration.

Hosted ChatGPT environments use their own plugin connections and do not read your local Codex configuration. An operator can deploy [remote MCP](remote-mcp.md), test the HTTPS endpoint, and prepare a public plugin submission. A [plugin package generator](distribution.md) is included. Public listing and installation are separate from web search indexing, and no public listing is claimed here.

[Official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp) · [Official skill packaging](https://developers.openai.com/plugins/build/skills) · [Quickstart](quickstart.md)
