# Agent research contracts

Atlas exposes free research through typed MCP input, structured output, the Python SDK, CLI and HTTP. Research never signs or purchases. No model service is required by the server.

The optional [native factor risk extension](risk-model.md) adds `atlas_risk` to the same connection with `--enable-risk`. It supports fundamental cross-sectional and observed-factor time-series estimation. Operators can configure [native input connections](data-connections.md), then agents call `connections`, preview a `run`, or execute read/validate/fit/analyze with weights in one request. Bounded chunk uploads remain available. Default research tool counts and contracts remain unchanged. Risk imports/fits write private state; remote risk mode requires bearer authentication. API provider extensions require `allow_network=true`; built-in JSON/CSV/SQLite inputs use no network. Historical availability remains caller-asserted and unverified.

[Research presets](presets.md) provide inspectable company, US/Japan macro and FX-context query sets through the same connection. They reuse batch/quality services, retain per-item failures and separate observation/publication/retrieval times. Configuration diagnosis is offline by default; source probes require explicit opt-in.

## Discover only the needed contract

Call `atlas_research_catalog` with `{"dataset":"statements","view":"compact"}` for dataset limits, metric names and source terms without large schemas. Use `view="full"` for query and common response schemas. Omit `dataset` to discover all 31 datasets. The default catalog view remains full. Optional `search`, `country`, `frequency`, `source_id` and `unit` filters populate `series_search` with executable registered-series/FX templates without source access. For international series, use `sdmx_catalog` to discover dataflows and exact dimension codes, then `international_economics` to retrieve them. `macro_panel` aligns explicit queries with last/mean aggregation through the same research tool. [Discovery and panel contracts](international-macro.md).

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

For FX, projection also retains base/quote currencies, quote convention, rate type and rounding context. Derived FX provenance is under `inputs`; its output `source` is null. Macro calculations likewise retain their reported inputs and required comparison period. Root-level `attributions` retain ECB conditions and New York Fed copyright/reference-rate notices in both views; preserve these when redistributing results. See [macro and FX](macro.md) for units, source windows and missing-input rules.

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

## Require data quality explicitly

Add an optional `requirements` object to any research query. Omitted requirements preserve existing behavior. Supplied conditions are checked on the complete selected result, including calculation inputs and nested comparison records, before field selection or pagination:

```json
{
  "dataset": "fx_rates",
  "base_currency": "USD",
  "quote_currency": "JPY",
  "limit": 2,
  "requirements": {
    "max_source_age_seconds": 3600,
    "min_observation_date": "2026-10-19",
    "allow_missing": false,
    "allow_derived": true,
    "allow_truncated": true
  }
}
```

| Field                    | Meaning                                                                                                                                                                                                                |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `max_source_age_seconds` | Maximum age of the oldest source retrieval/check used, including reported inputs. Integer 0–31,536,000; unknown or future source timestamps fail the condition.                                                        |
| `min_observation_date`   | Minimum ISO date for the latest usable observation in the selected output. Unknown dates and unavailable values cannot satisfy it. It does not require every historical row to be newer.                               |
| `allow_missing`          | Defaults true. False rejects Atlas-marked unavailable/ambiguous cells, null `value` fields, null primary debt/13F amounts, empty results and explicitly partial coverage. Optional nullable metadata is not certified. |
| `allow_derived`          | Defaults true. False rejects output or dependency records marked derived, including FX cross/inverse calculations.                                                                                                     |
| `allow_truncated`        | Defaults true. False rejects explicit record, archive or footnote truncation. A fixed upstream window alone is not truncation.                                                                                         |

Source age and observation age are separate. A successful HTTP 304 revalidation updates the source check/retrieval time; it does not change an observation's date. Requirements do not force a cache refresh, infer release times, fill gaps, extend history or relax financial `allow_partial` rules. A stale cached result is rejected until a valid refresh is available. Observation dates use reported dates or period ends; BLS monthly periods map to calendar month end and M13 averages to year end. Raw XBRL uses the reported context end, filing discovery uses the filing date, and policy-release metadata uses its explicit publication date. Ownership records can inherit the selected filing's explicit report date and retain any actual transaction dates. Undated company/profile metadata remains unknown. No release/holiday calendar or PIT reconstruction is implied.

A failed condition returns `QUALITY_NOT_MET` (HTTP 409), with `details.quality` containing the actual values and failed checks, and `next_action.action="review_requirements"`. The caller decides whether changed requirements or a fresh read are acceptable. No condition is silently relaxed.

Successful results include `quality`, whose scope is `complete_selected_result`. A continuation repeats the exact requirements; changed requirements return `INVALID_CURSOR`. Time-dependent checks run again against the saved full result without fetching upstream data. `response.result_sha256` continues to identify the original saved result, including its initial assessment; a resumed page's `quality.evaluated_at` and age describe the current assessment.

CLI equivalents include `--max-source-age-seconds 3600`, `--min-observation-date 2026-10-19`, `--no-allow-missing`, `--no-allow-derived` and `--no-allow-truncated`. Fixtures have a fixed source retrieval time; short age limits intentionally fail as that fictional snapshot ages.

## Retrieve independent queries in a batch

Use `Atlas.get_research_batch`, MCP `atlas_get_research_batch`, HTTP `POST /v1/research/batch` or `atlas research batch --input FILE`. Each uses this envelope:

```json
{
  "items": [
    { "id": "cpi", "query": { "dataset": "economics", "series": "cpi" } },
    {
      "id": "sofr",
      "query": { "dataset": "reference_rates", "series": "sofr" }
    }
  ]
}
```

Queries use the catalog's existing dataset schemas, including their own requirements and presentation options. The batch tool types its envelope and output; the inner query is an object so invalid queries can produce item errors independently. Results preserve input order and IDs. An item has `status="ok"` and `result`, or `status="error"` and the shared safe error payload. Root `status` is `ok`, `partial` or `error`, with requested/successful/failed counts and `summary.retryable_read_ids`. Partial batches remain successful MCP calls and HTTP 200 responses: callers must inspect item statuses. No item is retried automatically, and retry hints grant no payment authorization.

There are at most ten items, unique IDs of 1–64 ASCII letters/digits/underscores/hyphens, a 16 KiB request limit and a 4 MiB aggregate response budget. Structural/envelope failures execute nothing; query errors affect only their item. Items exceeding the remaining output budget return `RESPONSE_TOO_LARGE` and can be retried with a deliberately smaller result. Execution is sequential and shares normal source caches, quotas and retries. A batch is not an atomic or simultaneous snapshot, an asynchronous job or a payment operation. IDs correlate results without guaranteeing persistent idempotency across calls.

`atlas research batch --input -` reads stdin. CLI emits one JSON document and exits 0 when every item succeeds, 1 for item failures, and the ordinary nonzero domain code for invalid envelopes/configuration. [Offline four-query example](../examples/research_batch.json):

```sh
uv run --offline atlas research batch --input examples/research_batch.json --provider fixture --json
```

## Handle errors by contract

Errors retain `code`, `message`, `retryable`, `details` and `request_id`, with additive `next_action` and nullable `retry_after_seconds` fields. A request ID stays stable when the same error is serialized in multiple places. Validation hints contain known field names, never submitted values.

| Condition                                              | Next action             | Caller behavior                                                                        |
| ------------------------------------------------------ | ----------------------- | -------------------------------------------------------------------------------------- |
| Invalid input/cursor                                   | `fix_query`             | Inspect `next_action.fields` and correct the intended request                          |
| Unsupported metric/period/history                      | `inspect_catalog`       | Read capabilities; do not silently replace a historical request with current data      |
| Missing/ambiguous issuer                               | `resolve_company`       | Resolve identity before research                                                       |
| Incomplete coverage                                    | `review_coverage`       | Decide whether the task accepts partial results; Atlas does not enable `allow_partial` |
| Unmet explicit quality requirements                    | `review_requirements`   | Review actual quality checks; do not silently loosen the requested conditions          |
| Source quota                                           | `wait`                  | Honor the bounded retry hint and the caller's retry limit                              |
| Retryable source read                                  | `retry_read`            | Retry only an idempotent read within the caller's limits                               |
| Expired/absent presentation data                       | `restart_query`         | Fetch a new free result if the task permits changed source contents                    |
| Hosted quote required                                  | `request_quote`         | Inspect an annual snapshot quote; this does not authorize purchase                     |
| Unknown payment/ambiguous purchase transport           | `check_purchase_status` | Inspect the existing order; never create a fresh authorization to bypass it            |
| Configuration, policy, budget, ledger or cache failure | `contact_operator`      | Stop; do not alter operator settings                                                   |
| Other non-retryable failure                            | `stop`                  | Preserve the error for the caller                                                      |

All current hints have `automatic=false`. They guide the caller's policy and never grant spending permission, change a query or execute another tool. `retryable` alone does not authorize a payment retry. The server returns HTTP `Retry-After` when it knows a delay; hints are bounded to 86,400 seconds. Local BLS quota exhaustion reports time until the next UTC day. Missing hints remain null rather than inventing an upstream recovery time.

[Research methodology](research.md) · [Payments and recovery](payments.md) · [Verification](verification.md)
