# Publish packages and verify search ownership

Atlas Terminal is available from versioned GitHub releases and its public documentation website. PyPI publication, official MCP Registry registration and search-console ownership require the operator's external accounts. Prepared files and successful offline checks do not establish those registrations.

## PyPI trusted publisher

The manual [publication workflow](../.github/workflows/pypi.yml) uploads the wheel and source distribution from an existing GitHub release. It validates the stable tag, package name/version, main-branch ancestry and release-asset SHA-256 checksums. It uses short-lived OIDC authentication and does not require a PyPI API token in the repository.

In the intended PyPI account, configure a pending trusted publisher with these exact values:

| Field              | Value                     |
| ------------------ | ------------------------- |
| PyPI project       | `atlas-terminal`          |
| GitHub owner       | `andoseiryu-atlassupport` |
| GitHub repository  | `atlas-terminal`          |
| Workflow filename  | `pypi.yml`                |
| GitHub environment | `pypi`                    |

Create/configure the GitHub `pypi` environment according to your release policy. The workflow runs only by manual dispatch on main. After creating a GitHub release, invoke:

```sh
gh workflow run pypi.yml --ref main -f tag=v0.6.0
```

Confirm the published version and hashes on PyPI, then test installation in a fresh environment before changing the documented GitHub installation path. An existing project owned by someone else must use a different distribution name and corresponding Registry identifier. The workflow never overwrites an existing version.

## Official MCP Registry

Publish the matching PyPI version first. Generate `server.json` using `scripts/prepare_distribution.py`. Install the official publisher using the [Registry quickstart](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/quickstart.mdx), authenticate as the GitHub namespace owner, validate the prepared server metadata with the current official tool and publish it.

The prepared namespace is `io.github.andoseiryu-atlassupport/atlas-terminal`. The package README includes its ownership marker. Check the Registry's actual listing after publication; metadata generation and a local plugin ZIP do not create a listing or automatically connect a client.

## Search ownership and discovery

For the active site, add this URL-prefix property in Google Search Console and Bing Webmaster Tools:

```text
https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site/
```

Choose HTML meta-tag verification. These are public ownership tokens, not account credentials. Rebuild with the exact token supplied by each service, then deploy the resulting site using its existing project:

```sh
uv run --offline python scripts/build_docs.py --google-verification YOUR_PUBLIC_GOOGLE_TOKEN --bing-verification YOUR_PUBLIC_BING_TOKEN
```

Keep the configured verification flags on subsequent builds. After the service confirms ownership, submit `https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site/sitemap.xml`, inspect the homepage and the three research-case URLs, and review actual indexing outcomes. A Search Console property or sitemap submission is not established by this repository.

For ChatGPT search, allow OAI-SearchBot in root robots.txt and permit the IP ranges in [OpenAI's crawler documentation](https://developers.openai.com/api/docs/bots). An HTTP check with a simulated User-Agent cannot establish actual crawler-IP access; inspect the host's request logs when available. Training-crawler preferences are separate.

An operator-controlled custom domain can be attached through the existing host after DNS ownership is available. Verify HTTPS, root crawler directives, redirects, canonicals and sitemap URLs during a move. Preserve existing URLs or redirect them to the matching pages.

## Confirm the public states

Use [public access checks](public-checks.md), [reproducible research cases](research-examples.md) and [controlled discovery observations](discovery-measurement.md). Record package publication, search indexing, model citations and successful installed-tool execution separately.

## Align repository metadata

A GitHub account with repository administration permission can apply the same public description and documentation URL:

```sh
gh repo edit andoseiryu-atlassupport/atlas-terminal --description 'Free financial data for AI agents through one MCP connection.' --homepage 'https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site/' --add-topic mcp --add-topic financial-data --add-topic macroeconomics --add-topic sec-edgar --add-topic foreign-exchange
```

The ability to push code or merge a pull request does not necessarily permit editing repository settings through an integration.
