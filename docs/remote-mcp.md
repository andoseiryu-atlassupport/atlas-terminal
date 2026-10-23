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

### Prepare a dedicated Linux host

The repository includes a single-process systemd unit and a Caddy HTTPS configuration. They require a dedicated operator-managed Linux host with systemd, a system-installed Python 3.12+, uv, Git, Caddy, a persistent local disk and a DNS hostname pointing to that host. Static documentation hosting does not run this Python service. No host or domain is provisioned by these files.

Render files into a new local directory:

```sh
uv run --offline python scripts/prepare_remote_deploy.py --origin https://your-verified-host.example --provider public --output build/remote-deploy
```

This prepares files without installing services, requesting source data, obtaining certificates or generating credentials. The default is bearer-required; the operator must privately supply a token before the service can start. Add `--anonymous` only for an intentionally public anonymous service. Omit `--provider public` to render a fictional staging service instead. Never advertise a fixture service as real-source access.

Install a version-pinned Atlas environment at `/opt/atlas-terminal/.venv`, using a system Python interpreter outside protected home directories. For example, on a host with `/usr/bin/python3.12`:

```sh
sudo install -d -m 0755 /opt/atlas-terminal
sudo /usr/bin/python3.12 -m venv /opt/atlas-terminal/.venv
sudo uv pip install --python /opt/atlas-terminal/.venv/bin/python 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.6.0'
sudo install -d -m 0700 /etc/atlas-terminal
sudo install -m 0600 build/remote-deploy/.env.example /etc/atlas-terminal/mcp.env
sudoedit /etc/atlas-terminal/mcp.env
```

In the private environment file set the actual token and, for company data, the real SEC contact setting. Leave the token unset only for an explicitly anonymous unit. Do not commit the populated file. The unit creates a private persistent `/var/lib/atlas-terminal` state directory with systemd-managed ownership, binds only to `127.0.0.1:8001`, runs one worker, restarts after failures and restricts filesystem writes. Keep this volume across upgrades and process restarts.

Validate the rendered files on the target host before installation:

```sh
systemd-analyze verify build/remote-deploy/atlas-mcp.service
caddy validate --config build/remote-deploy/Caddyfile --adapter caddyfile
```

Install the reviewed unit in `/etc/systemd/system/atlas-mcp.service`. For a dedicated host, install the reviewed Caddy configuration as `/etc/caddy/Caddyfile`; merge its settings if Caddy already serves other applications. Run `systemctl daemon-reload`, start/enable `atlas-mcp.service`, and reload Caddy after validation. Permit public ingress only to the intended HTTPS/TLS-certificate ports; keep port 8001 private. Caddy preserves Host, caps bodies and sets read timeouts; access logging is not enabled by the supplied configuration.

The templates do not install a firewall, backups, OAuth, account isolation, perimeter rate limiting or distributed storage. Target-host startup, certificate issuance and persistence must be checked on the actual host. Deployment preparation is not evidence of a running endpoint.

### Verify the running endpoint

1. Reach `/healthz` over its real HTTPS URL and confirm `is_synthetic=false` for public data.
2. Initialize an official MCP client and confirm the six advertised tools.
3. Run `atlas_research_diagnostics` first without network probes; inspect configuration readiness.
4. Execute a selected real research query and inspect returned source evidence, units, observation dates and partial failures. SEC requires the operator's real identifying contact setting.
5. Fetch evidence again after a process restart to verify the persistent volume.
6. Check authentication, invalid origins/hosts, size/concurrency limits and safe error output at the proxy and application.

Run the [reproducible company/macro/FX workflow](company-macro-fx.md) against the actual URL, then enable the optional [remote checks](public-checks.md) after its evidence and failure handling are verified. Restart the service and resolve saved evidence again from the same volume. A script that verifies an already running endpoint does not itself deploy or restart it.

The server permits fixed read-only source probes only through explicit `allow_network=true`; fixture mode never performs them. Do not label a deployment verified from fixture output. [Operator source checks](source-checks.md) and [verification scope](verification.md) explain remaining live checks.

## Package for compatible clients

After deploying a working HTTPS endpoint, generate a [plugin package](distribution.md) with the actual endpoint and its matching authentication mode. Public distribution additionally requires a publisher account, verification and review. A local setup or MCP Registry metadata does not automatically connect all ChatGPT users.
