# Verify public documentation and remote MCP access

Atlas separates public HTTP access, search inclusion, client installation and successful research execution. The checks below produce inspectable reports for HTTP access and installed-tool contracts. They do not establish search ranking, official crawler access or model recommendations.

## Public documentation HTTP checks

```sh
uv run --offline python scripts/check_public_docs.py --allow-network > build/public-docs-checks.json
```

Create `build/` first if it does not exist. The checker uses the published documentation origin from `src/atlas_terminal/documentation.py`. An optional `--origin` accepts an exact HTTPS origin without credentials, paths or query parameters. It does not follow redirects or send authentication.

The report checks:

- Root `robots.txt` responses and path access for simulated OAI-SearchBot, ChatGPT-User and Googlebot user agents, plus an ordinary audit agent.
- A bounded, same-origin sitemap and every listed HTML page as OAI-SearchBot.
- HTTP 200, HTML content, expected documentation content, an exact canonical URL and the absence of applicable `noindex` directives in HTML or `X-Robots-Tag` headers.
- Home-page responses under the other user agents, including authentication, redirect and HTTP-denial failures.

Responses are limited to 1 MiB each and 64 sitemap pages. Reports retain safe failure codes, not response bodies, raw headers or network exception text. A redirected page fails and should be reviewed; redirects are not followed to another host. Robots matching covers Allow/Disallow paths, wildcard patterns, terminal anchors and specific agent groups. This is a focused technical check, not a complete search-engine emulator.

The `Public access checks` workflow runs documentation checks weekly and on manual dispatch, retaining a JSON artifact for 30 days. It runs separately from offline pull-request tests and official-source checks. Workflow failure is an operational signal; no third-party messages or marketing submissions are sent.

Changing a User-Agent does not reproduce an official crawler's IP address or CDN/WAF treatment. A passing report therefore keeps `official_crawler_ip_verified`, `search_indexing_verified` and `model_citation_measured` false. Confirm actual search-bot access through the host's request logs and the publisher's [current crawler guidance](https://developers.openai.com/api/docs/bots). Search-crawler access and training-crawler preferences are separate. Neither structured data nor `llms.txt` guarantees inclusion or citation.

## Actual remote research checks

The [company, macro and FX runner](company-macro-fx.md) can connect to a real HTTPS `/mcp` endpoint. It checks process liveness, rejects synthetic public-server metadata, initializes the official MCP client, verifies the six research-only tools, retrieves the four datasets and resolves their evidence. Explicit failures include missing source configuration, item errors, inaccessible evidence, unsupported datasets and unmet FX quality requirements.

After deploying and validating an endpoint, configure these GitHub repository settings to enable its checks:

| Setting                | Type                | Meaning                                                                      |
| ---------------------- | ------------------- | ---------------------------------------------------------------------------- |
| `ATLAS_MCP_URL`        | Repository variable | Actual HTTPS endpoint ending in `/mcp`, with no credentials/query            |
| `ATLAS_MCP_AUTH_TOKEN` | Repository secret   | Bearer token only if required by that endpoint                               |
| `MCP_MONITOR_ENABLED`  | Repository variable | Set to `true` to enable weekly remote checks                                 |
| `MCP_MAX_FX_AGE_DAYS`  | Repository variable | Maximum FX observation age in calendar days, default `10`, allowed `0`–`365` |

A manual run can select `check_remote=true`; scheduled remote checks remain off until explicitly configured. The endpoint must have its own private SEC contact setting. The workflow does not configure the server or send SEC contact information in tool arguments. Its reports contain public research data and source evidence, not authentication or operator settings.

FX observation age, monthly CPI observation time, financial reporting periods and source retrieval age are different measures. A fresh retrieval can still return old observations. The JSON report preserves the complete selected-result quality metadata for each item. Review item errors and source cadence rather than interpreting a successful health check as data readiness. A weekly check is not a continuous uptime monitor or SLA.

## Host changes and custom domains

To move the documentation to an operator-controlled domain, first configure DNS, HTTPS and the existing host's domain binding. Update `SITE_ORIGIN`, rebuild the documentation, publish it at that actual domain, and rerun the HTTP checker. Check old-domain redirects and canonical consistency before promoting the new URL. Domain ownership and search-console verification require the operator's account; prepared metadata does not establish either.

[Remote deployment](remote-mcp.md) · [Sources and terms](../DATA_SOURCES.md) · [Evaluation cases](../evals/README.md) · [Verification scope](verification.md)
