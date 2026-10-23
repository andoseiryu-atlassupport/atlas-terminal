# Self-hosting

The reference API supports one process and one private SQLite volume. The CLI binds only to loopback. Startup rejects `WEB_CONCURRENCY` or `UVICORN_WORKERS` values other than 1. Independent state volumes on multiple hosts do not share a signer's budget.

## Free mode

```sh
uv run atlas serve --provider fixture --payments off --host 127.0.0.1 --port 8000
```

For SEC retrieval, use `--provider sec` and a private `SEC_USER_AGENT`. Set `ATLAS_STATE_DIR` to a dedicated persistent server directory; its default is `.local/state/atlas-terminal` under the user's home directory. New directories use mode 0700 and databases use 0600. Databases with shared permissions are rejected. Protect and back up SQLite WAL/SHM files as well.

Add `--enable-risk` to register `POST /v1/risk` for the optional [native factor risk models](risk-model.md). This local route accepts bounded chunks or [named input connections](data-connections.md), fits cross-sectional/time-series models and analyzes portfolios using the same private state as the CLI/SDK. Its body limit is 64 KiB; other routes retain their 16 KiB limit. Built-in risk connectors read local inputs; trusted SDK API providers require `allow_network=true`. The loopback API does not provide tenant isolation or remote risk authentication; use the bearer-required MCP extension for an operator-hosted connection.

## HTTP endpoints

| Method | Path                                         | Behavior                                                         |
| ------ | -------------------------------------------- | ---------------------------------------------------------------- |
| GET    | /healthz                                     | Health status without secret settings                            |
| GET    | /v1/catalog                                  | Annual metrics, periods and pricing mode                         |
| GET    | /v1/research/catalog                         | Research datasets, query fields, units and limits                |
| POST   | /v1/research                                 | Research retrieval without a quote or payment                    |
| POST   | /v1/research/batch                           | Up to ten ordered independent free reads, with per-item status   |
| GET    | /v1/research/evidence/{evidence_id}          | Saved provenance for a compact research result                   |
| GET    | /v1/companies/resolve?query=ATLASDEMO        | Free issuer resolution                                           |
| GET    | /v1/filings?cik=9999999999&form=10-K&limit=3 | Free filing metadata                                             |
| POST   | /v1/quotes                                   | Prepare data, save an immutable artifact and return a free quote |
| GET    | /v1/quotes/{quote_id}/result                 | Authenticated delivery; positive-price modes require settlement  |
| GET    | /v1/quotes/{quote_id}/status                 | Authenticated status recovery                                    |
| GET    | /v1/quotes/{quote_id}/preflight              | Authenticated order readiness; no client wallet or budget access |
| POST   | /v1/quotes/{quote_id}/reconcile              | Authenticated finalized-chain reconciliation without payment     |

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

`Atlas-Recovery-Token` is returned in a response header, never in JSON, MCP output or logs. Use it as `Authorization: Bearer` to retrieve status, order preflight and purchased results. A quote ID alone does not grant access. The token does not authorize a new purchase or bypass payment for an unpaid result. Optional private `Atlas-Intent-Key` capabilities replay the same quote and recovery header after a lost response; see [task-bound quote replay](payments.md#reuse-a-tasks-quote-safely).

`/openapi.json` exposes the generated FastAPI specification. Domain contracts use [annual snapshot and reconciliation JSON Schema](../schemas/atlas.schema.json) and [research JSON Schema](../schemas/research.schema.json). Official SDK models define x402 wire payloads.

Research catalogs accept `dataset` and `view=compact` query parameters. Research POST bodies accept optional `response` settings for compact evidence references, field selection and snapshot pagination. Shared error contracts describe next actions and retry hints. See [agent contracts](agents.md) for examples and interpretation of coverage versus page size.

Research `requirements` apply before projection/paging. A failed condition returns HTTP 409 with `QUALITY_NOT_MET`. Batch requests use `{"items":[{"id":"caller-id","query":{...}}]}`; valid envelopes return HTTP 200 even when items fail. Inspect root `status`, `summary.failed` and each item. Batches share existing source caches and rate limits, without a transaction or a simultaneous-observation guarantee. Their aggregate response budget is 4 MiB. Duplicate IDs, oversized requests and malformed envelopes execute no items.

Optional server-only `ATLAS_TESTNET_RPC_URL` enables capability-authenticated reconciliation against canonical finalized Base Sepolia state. Requests use `{}` or an optional `transaction` hash hint. No payment is signed or submitted. See [reconciliation rules](payments.md#finalized-chain-reconciliation) for required RPC methods, outcomes, bounded discovery and unresolved cases.

## Limits and retention

Quotes expire after five minutes. Preparation is limited to 30 requests/minute across the service, 128 stored quotes and 64 MiB of snapshots. Each artifact is limited to 2 MiB; POST bodies are limited to 16 KiB. Purchased snapshots remain available for at least 24 hours after confirmed settlement. Quote expiry and purchased-artifact retention are separate. Unknown settlements are not automatically deleted.

The server retains at most 10,000 private intent-hash bindings independently of quote cleanup. Replays do not consume new quote capacity or renew expiry; retired bindings remain tombstones. Exhaustion rejects new intent bindings without erasing existing recovery state. Budgets remain on the caller's private client volume and are not exposed by this server.

Optional research presentation uses a separate `research-responses.sqlite3` database on the same private state volume. Its 24-hour snapshots/evidence are bounded to 64 MiB of logical payloads, 128 snapshots and 20,000 evidence objects, with a 4 MiB per-snapshot limit. Expired entries are pruned on writes. Full unpaginated research does not use this store. Expiry or capacity failures never release payment reservations or remove purchase state.

SEC retrieval shares a SQLite rate/cache store: two requests/second, burst one, at most two concurrent requests, bounded response size/time, finite retries and etag/last-modified validation. Cache targets are 24 hours for tickers, 15 minutes for submissions and one hour for companyfacts. A failed refresh never presents expired data as current.

Public-facing authentication, TLS termination, access control, monitoring, commercial pricing, multiple replicas and SLAs are not provided. The API is intended for a single process and loopback connections.
