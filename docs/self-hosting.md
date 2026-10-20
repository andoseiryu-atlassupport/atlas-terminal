# Self-hosting

The reference API supports one process and one private SQLite volume. The CLI binds only to loopback. Startup rejects `WEB_CONCURRENCY` or `UVICORN_WORKERS` values other than 1. Independent state volumes on multiple hosts do not share a signer's budget.

## Free mode

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
```

For SEC retrieval, use `--provider sec` and a private `SEC_USER_AGENT`. Set `ATLAS_STATE_DIR` to a dedicated persistent server directory; its default is `.local/state/atlas-terminal` under the user's home directory. New directories use mode 0700 and databases use 0600. Databases with shared permissions are rejected. Protect and back up SQLite WAL/SHM files as well.

## HTTP endpoints

| Method | Path                                         | Behavior                                                         |
| ------ | -------------------------------------------- | ---------------------------------------------------------------- |
| GET    | /healthz                                     | Health status without secret settings                            |
| GET    | /v1/catalog                                  | Annual metrics, periods and pricing mode                         |
| GET    | /v1/research/catalog                         | Research datasets, query fields, units and limits                |
| POST   | /v1/research                                 | Research retrieval without a quote or payment                    |
| GET    | /v1/companies/resolve?query=ATLASDEMO        | Free issuer resolution                                           |
| GET    | /v1/filings?cik=9999999999&form=10-K&limit=3 | Free filing metadata                                             |
| POST   | /v1/quotes                                   | Prepare data, save an immutable artifact and return a free quote |
| GET    | /v1/quotes/{quote_id}/result                 | Authenticated delivery; positive-price modes require settlement  |
| GET    | /v1/quotes/{quote_id}/status                 | Authenticated status recovery                                    |

Example body for `POST /v1/quotes`:

```json
{
  "identifier": "ATLASDEMO",
  "metrics": ["revenue", "net_income"],
  "period": "annual",
  "count": 3,
  "allow_partial": false
}
```

`Atlas-Recovery-Token` is returned once in a response header, never in JSON, MCP output or logs. Use it as `Authorization: Bearer` to retrieve status and purchased results. A quote ID alone does not grant access. The token does not authorize a new purchase or bypass payment for an unpaid result.

`/openapi.json` exposes the generated FastAPI specification. Domain contracts use [annual snapshot JSON Schema](../schemas/atlas.schema.json) and [research JSON Schema](../schemas/research.schema.json). Official SDK models define x402 wire payloads.

## Limits and retention

Quotes expire after five minutes. Preparation is limited to 30 requests/minute across the service, 128 stored quotes and 64 MiB of snapshots. Each artifact is limited to 2 MiB; POST bodies are limited to 16 KiB. Purchased snapshots remain available for at least 24 hours after confirmed settlement. Quote expiry and purchased-artifact retention are separate. Unknown settlements are not automatically deleted.

SEC retrieval shares a SQLite rate/cache store: two requests/second, burst one, at most two concurrent requests, bounded response size/time, finite retries and etag/last-modified validation. Cache targets are 24 hours for tickers, 15 minutes for submissions and one hour for companyfacts. A failed refresh never presents expired data as current.

Public-facing authentication, TLS termination, access control, monitoring, commercial pricing, multiple replicas and SLAs are not provided. The API is intended for a single process and loopback connections.
