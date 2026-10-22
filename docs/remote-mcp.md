# Host research-only Streamable HTTP MCP

Atlas Terminal includes an operator-hosted Streamable HTTP endpoint at `/mcp`. It uses the official MCP SDK and the same research service as stdio, CLI, Python and the local HTTP API. It exposes six research tools: catalog, exact queries, batches, presets, evidence and diagnostics. No quote, purchase, wallet or payment handler is registered.

No official public Atlas remote endpoint is currently operated. This guide describes self-hosting. A successful fixture test is not evidence of a public deployment or live source connectivity.

## Verify locally

After installing Atlas, run:

```sh
atlas mcp http --provider fixture --allow-anonymous --host 127.0.0.1 --port 8001
```

Connect a compatible Streamable HTTP MCP client to `http://127.0.0.1:8001/mcp`. `GET /healthz` returns transport, package version and an explicit synthetic-mode flag. `/healthz` confirms process liveness, not upstream data readiness. `tools/list` must show only the six research tools. Test a preset, inspect per-item results, and resolve compact evidence on the same state volume.

## Deploy behind HTTPS

Use one Python process with a private persistent disk and an operator-managed reverse proxy. Configure these environment variables privately through the host's secret/configuration mechanism:

| Variable                    | Meaning                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------- |
| `ATLAS_MCP_PUBLIC_ORIGIN`   | Exact external HTTPS origin, without a path, credentials, query or fragment                       |
| `ATLAS_STATE_DIR`           | Private persistent directory for caches, response snapshots, evidence and source-check state      |
| `ATLAS_MCP_AUTH_TOKEN`      | Optional private 32–256 character bearer token; never put it in tool arguments or committed files |
| `ATLAS_MCP_ALLOWED_ORIGINS` | Optional comma-separated exact HTTPS browser origins; no wildcard                                 |
| `SEC_USER_AGENT`            | Operator's identifying application and actual contact email, required only for SEC requests       |

For a private deployment with a configured bearer token:

```sh
atlas mcp http --provider public --host 0.0.0.0 --port 8001
```

For an intentionally anonymous public research service, leave the bearer token unset and explicitly add `--allow-anonymous`. You cannot combine the two modes. A public plugin can use anonymous access only when its declared authentication matches this deployment. No OAuth, per-user accounts or user-specific billing is implemented.

Terminate TLS at the proxy, preserve the external Host header, restrict direct ingress to the application port, cap request bodies at 16 KiB, and apply connection/read timeouts and perimeter abuse limits. Bind the proxy to the origin configured above. If the client sends an Origin header, allow only the exact client origin needed. Non-browser clients may omit Origin. Forwarded headers do not alter Atlas host/origin checks or rate-limit identity.

Atlas rejects unexpected Host/Origin headers, oversized requests, unconfigured authentication and multiple workers. Ingress allows 60 MCP HTTP requests/minute globally, with at most two simultaneous requests, a ten-second body-read timeout, and 429 with `Retry-After` when capacity is unavailable. These are process-level limits; a reverse proxy must also protect connections and reject abusive clients. Upstream data-source limits still apply independently. Access logging is disabled by the CLI, and all endpoint responses use `Cache-Control: no-store`.

The transport is stateless JSON Streamable HTTP, while research evidence and pagination use the persistent state volume. Research data in that volume are public-source snapshots shared by the service; the endpoint is not a tenant-isolated storage system. Avoid multiple replicas or ephemeral/serverless disks. Persistent evidence cannot be assumed to survive host replacement without the state volume. No distributed limiter, SLA or monitoring service is installed.

## Connect and validate the actual deployment

1. Reach `/healthz` over its real HTTPS URL and confirm `is_synthetic=false` for public data.
2. Initialize an official MCP client and confirm the six advertised tools.
3. Run `atlas_research_diagnostics` first without network probes; inspect configuration readiness.
4. Execute a selected real research query and inspect returned source evidence, units, observation dates and partial failures. SEC requires the operator's real identifying contact setting.
5. Fetch evidence again after a process restart to verify the persistent volume.
6. Check authentication, invalid origins/hosts, size/concurrency limits and safe error output at the proxy and application.

The server permits fixed read-only source probes only through explicit `allow_network=true`; fixture mode never performs them. Do not label a deployment verified from fixture output. [Operator source checks](source-checks.md) and [verification scope](verification.md) explain remaining live checks.

## Package for compatible clients

After deploying a working HTTPS endpoint, generate a [plugin package](distribution.md) with the actual endpoint and its matching authentication mode. Public distribution additionally requires a publisher account, verification and review. A local setup or MCP Registry metadata does not automatically connect all ChatGPT users.
