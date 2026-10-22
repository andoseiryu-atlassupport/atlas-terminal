# Install, package and publish Atlas Terminal

Atlas Terminal can be installed from a tagged GitHub source distribution or release wheel. The Python package is not yet published on PyPI, and no official public remote MCP endpoint or OpenAI plugin listing is currently operated. Prepared metadata, local plugin installation and public listing are different states.

## GitHub installation and releases

Use the [Codex setup](codex.md) for a version-pinned installation. A release contains a wheel, source distribution, research plugin ZIP and static-documentation archive. Python 3.12+ and uv are required for the documented Git source workflow. The plugin bundles only the focused research skill and a research-only MCP connection; it has no payment workflow or lifecycle hooks.

Build the local package without publishing:

```sh
uv run --offline python -m build --no-isolation
uv run --offline python scripts/prepare_distribution.py
uv run --offline python scripts/build_docs.py
```

The `Release artifacts` GitHub Actions workflow builds an existing version tag, checks that its package version matches and its commit is reachable from main, and runs the offline checks before publishing. It produces wheel/source, plugin, documentation, draft Registry metadata and checksum assets. Only its final publication job receives repository-content write permission; build/PR checks remain read-only. Trigger it with a version tag or dispatch it from main with the explicit existing tag. Existing releases/assets are never overwritten automatically. This workflow publishes GitHub release artifacts, not PyPI packages, Registry registrations or OpenAI plugins.

`build/distribution/plugin` uses the portable `plugin.json`, `mcp.json` and `skills/` layout. Its MCP command fetches the tagged GitHub distribution through uv. Install it only through a client that supports this local/desktop format. Installation does not make tools available to unrelated clients or register a public plugin. The generated `server.json` is an MCP Registry submission draft that depends on a matching actual PyPI package publication.

## PyPI publication

A publisher must create or select the PyPI project, verify ownership and configure publishing credentials or a trusted publisher. The repository intentionally contains no PyPI token or account setup. The package README contains the matching MCP ownership marker, and package metadata links the public documentation and source repository.

After verifying the exact built distribution and the intended publisher account, an operator can publish the built wheel and source distribution with uv's supported publishing authentication. Confirm the actual package version on PyPI before using PyPI-based install instructions or submitting its Registry metadata. Do not upload registry metadata for a package version that does not exist.

## MCP Registry

`scripts/prepare_distribution.py` creates the versioned namespace `io.github.andoseiryu-atlassupport/atlas-terminal` with PyPI package metadata and research-only stdio arguments. Validate it with the official `mcp-publisher` tool after the package exists, authenticate as the verified namespace owner, and publish. Registry registration is not OpenAI plugin review or automatic connection by MCP clients.

For an operator-hosted anonymous HTTPS endpoint, a remote-only draft can be generated instead:

```sh
uv run --offline python scripts/prepare_distribution.py --remote-url https://your-verified-host.example/mcp --output build/remote-distribution
```

Replace the example host with the actual endpoint. The command does not contact, deploy or verify the host. The generated remote manifest declares anonymous access; it is unsuitable for a bearer-only deployment without updating the platform's authentication declaration. Private Codex connections can use `codex mcp add --url` with a private bearer-token environment variable instead.

## ChatGPT and Codex public plugin distribution

Follow the [official plugin packaging specification](https://developers.openai.com/plugins/build/plugins) and [submission process](https://developers.openai.com/plugins/deploy/submission). A remote public submission requires a working HTTPS endpoint, a publishing account, identity/domain verification where required, accurate metadata, support/privacy/terms URLs and review test cases. Local desktop submissions have their own eligibility requirements.

The included privacy/terms/support pages describe the open-source software and local data handling. A service operator must adapt them to the actual hosting, log retention, identity, support and data-access practices before a public hosted-service submission. The package does not claim verified publisher status, approval, directory placement or automated recommendations. Use [workflow evaluation cases](../evals/research-workflows.json) to prepare truthful positive and unsupported-request test cases.

## Static documentation hosting

The documentation generator creates HTML with canonical URLs, page metadata, factual structured data, Markdown alternatives, sitemap, a 404 page and a supplemental `llms.txt`. It verifies internal file and section links without network access. The workflow builds and uploads the site for reviewed branches; deployment is enabled only when repository variable `PAGES_ENABLED=true` and GitHub Pages is configured for GitHub Actions.

The default site origin is the project's GitHub Pages URL in `src/atlas_terminal/documentation.py`; it is a deployment target, not evidence that the site is live. Current tool and package links use the public GitHub documentation so they do not depend on a pending site deployment. If hosting elsewhere, change `SITE_ORIGIN` before rebuilding and verify the actual domain, canonical URLs, redirects, sitemap, robots directives and CDN/WAF access.

A project Pages site cannot change the account domain's root `/robots.txt`. The generated project-level file documents crawl intent; inspect the real root after deployment. Search access is independent of training-crawler preferences. Neither `llms.txt` nor structured data guarantees indexing, citation or tool use. See [OpenAI crawler documentation](https://developers.openai.com/api/docs/bots).
