# Agent research contracts

Atlas exposes free research through typed MCP input, structured output, the Python SDK, CLI and HTTP. Research never signs or purchases. No model service is required by the server.

## Discover only the needed contract

Call `atlas_research_catalog` with `{"dataset":"statements","view":"compact"}` for dataset limits, metric names and source terms without large schemas. Use `view="full"` for query and common response schemas. Omit `dataset` to discover all 17 datasets. The default catalog view remains full.

`atlas_get_research` publishes a typed `query` parameter with a separate JSON Schema branch for each dataset. Branches specify required identity, supported metrics or economic series, periods, bounds and applicable fields. Its `outputSchema` describes the common result envelope, provenance references and page metadata; dataset-specific record fields remain extensible. Invalid tool arguments return a sanitized error without echoing supplied values.

## Retrieve a compact page and its evidence

Pass this argument to `atlas_get_research`:

```json
{
  "query": {
    "dataset": "statements",
    "identifier": "ATLASDEMO",
    "metrics": ["revenue", "net_income"],
    "count": 3,
    "response": {
      "view": "compact",
      "page_size": 2
    }
  }
}
```

Use the fixture provider for ATLASDEMO. Public retrieval uses real identifiers and the operator's private source configuration.

Compact output replaces each source provenance object with `{"evidence_id":"…"}`. Read that ID with `atlas_get_evidence(evidence_id)`. The response contains the exact saved source URL, raw-byte SHA-256, retrieval time, JSON pointer or XML path, and any original filing metadata. The evidence ID is the SHA-256 of the canonical provenance object. It refers to saved provenance, not a downloaded document or an arbitrary fetch URL.

Values, units, actual periods, missing/conflicting statuses, formulas and reported calculation inputs remain present. Source text stays untrusted. `response.fields` optionally selects leaf-record fields while retaining identifying dimensions, units, periods, statuses, missingness, source references and calculation context. Unknown fields are rejected against the selected result. Nested comparison wrappers retain their issuer identity, coverage and alignment.

The default view remains full and embeds original provenance. Compact output reduces repeated provenance; small results can grow because of page metadata. No fixed token saving is guaranteed.

## Continue the same result

When `response.next_cursor` is non-null, repeat the same data query and response options with that value in `response.cursor`. Keep the original `page_size`, `view` and `fields`. The next page reads a saved snapshot without a source request. Changing the query or presentation options returns `INVALID_CURSOR`.

`response.total_records`, `returned_records`, `offset` and `next_cursor` describe presentation pagination. `coverage` describes the entire selected result, including upstream truncation and missing cells. A cursor cannot retrieve records excluded by the original `limit`, archive window or coverage rules. In `compare`, a page contains issuer records, not individual metric cells.

`response.result_sha256` hashes the original full result before projection, compacting and pagination. `snapshot_id` also binds the query, response options and fixture/public scope. Neither identifier establishes point-in-time historical availability. Retrieval time and filing/observation dates keep their existing meanings.

Snapshots and evidence share the configured private state volume. They expire after 24 hours; evidence shared by another result can be retained longer. The store bounds logical payloads to 64 MiB, 128 snapshots and 20,000 evidence objects, with a 4 MiB maximum snapshot. Expired entries are pruned on writes. A full store fails with `CACHE_FULL`; it does not delete live purchase state. Full unpaginated output does not need this presentation store. Cursors and evidence IDs are lookup handles, not authorization for paid artifacts. Fixture and public scopes are separate.

SDK example:

```python
from atlas_terminal.client import Atlas

query = {
    "dataset": "statements",
    "identifier": "ATLASDEMO",
    "metrics": ["revenue", "net_income"],
    "count": 3,
    "response": {"view": "compact", "page_size": 2},
}
with Atlas("fixture") as atlas:
    page = atlas.get_research(query)
    evidence = atlas.get_evidence(page["records"][0]["source"]["evidence_id"])
    cursor = page["response"]["next_cursor"]
    if cursor:
        query["response"]["cursor"] = cursor
        next_page = atlas.get_research(query)
```

CLI equivalents use `research catalog --dataset statements --view compact`, `research get ... --view compact --fields value --page-size 2 --cursor CURSOR` and `research evidence EVIDENCE_ID`. All support `--json`; retrieval and evidence commands use `--provider fixture` for offline data. Keep the same private `ATLAS_STATE_DIR` across processes. SDK callers can also set `Atlas(..., directory=path)` for the presentation store.

HTTP uses `GET /v1/research/catalog?dataset=statements&view=compact`, the same flat query body at `POST /v1/research`, and `GET /v1/research/evidence/{evidence_id}`. The reference API remains single-process and loopback-oriented.

## Handle errors by contract

Errors retain `code`, `message`, `retryable`, `details` and `request_id`, with additive `next_action` and nullable `retry_after_seconds` fields. A request ID stays stable when the same error is serialized in multiple places. Validation hints contain known field names, never submitted values.

| Condition                                              | Next action             | Caller behavior                                                                        |
| ------------------------------------------------------ | ----------------------- | -------------------------------------------------------------------------------------- |
| Invalid input/cursor                                   | `fix_query`             | Inspect `next_action.fields` and correct the intended request                          |
| Unsupported metric/period/history                      | `inspect_catalog`       | Read capabilities; do not silently replace a historical request with current data      |
| Missing/ambiguous issuer                               | `resolve_company`       | Resolve identity before research                                                       |
| Incomplete coverage                                    | `review_coverage`       | Decide whether the task accepts partial results; Atlas does not enable `allow_partial` |
| Source quota                                           | `wait`                  | Honor the bounded retry hint and the caller's retry limit                              |
| Retryable source read                                  | `retry_read`            | Retry only an idempotent read within the caller's limits                               |
| Expired/absent presentation data                       | `restart_query`         | Fetch a new free result if the task permits changed source contents                    |
| Hosted quote required                                  | `request_quote`         | Inspect an annual snapshot quote; this does not authorize purchase                     |
| Unknown payment/ambiguous purchase transport           | `check_purchase_status` | Inspect the existing order; never create a fresh authorization to bypass it            |
| Configuration, policy, budget, ledger or cache failure | `contact_operator`      | Stop; do not alter operator settings                                                   |
| Other non-retryable failure                            | `stop`                  | Preserve the error for the caller                                                      |

All current hints have `automatic=false`. They guide the caller's policy and never grant spending permission, change a query or execute another tool. `retryable` alone does not authorize a payment retry. The server returns HTTP `Retry-After` when it knows a delay; hints are bounded to 86,400 seconds. Local BLS quota exhaustion reports time until the next UTC day. Missing hints remain null rather than inventing an upstream recovery time.

[Research methodology](research.md) · [Payments and recovery](payments.md) · [Verification](verification.md)
