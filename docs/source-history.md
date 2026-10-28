# Local source versions and revision differences

`source_history` reads private retained responses from `ATLAS_STATE_DIR/public-cache.sqlite3` without making a source request. Public-source HTTP 200 captures and conditional 304 validations append observation events; ordinary cache hits do not. Distinct versions are keyed by canonical credential-free URL and SHA-256. An unchanged response reuses its bytes while recording the new validation time.

| `history_action` | Additional arguments      | Result                                                                            |
| ---------------- | ------------------------- | --------------------------------------------------------------------------------- |
| `list` (default) | Optional `limit`          | Versions plus the observation sequence, each with separate truncation information |
| `get`            | `sha256`                  | One retained body and version metadata                                            |
| `as_of`          | Timezone-aware `as_of`    | Latest locally observed version at or before the inclusive cutoff                 |
| `diff`           | `before_sha256`, `sha256` | Raw-hash change and up to 500 exact JSON-cell changes                             |

All actions require `source_url` to be an exact approved public-source URL. Copy it from returned provenance; credential-bearing or arbitrary URLs are rejected. Yahoo history also requires `ATLAS_ENABLE_YAHOO_FINANCE=1`. SEC's separate cache is not queried through this dataset.

```json
{
  "dataset": "source_history",
  "source_url": "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
  "history_action": "as_of",
  "as_of": "2026-10-29T01:00:00+09:00"
}
```

Run any saved query using `atlas research run --input /operator/private/query.json --provider public --json`, or pass it to the shared SDK/MCP/HTTP research operation. The example cutoff succeeds only if this state directory already captured the URL by that instant. A missing earlier observation returns `INCOMPLETE_COVERAGE`. `as_of` is accepted only for this local-history dataset; it does not enable historical availability queries elsewhere.

The sequence **A → B → A** retains two bodies and three events. A cutoff during B returns B even though A was first observed earlier and later reappeared. Identical timestamps retain insertion order. Timestamps are compared in UTC with microseconds, including exact whole-second boundaries and equivalent timezone offsets. An `as_of` result's `last_seen` is capped at its selected event rather than disclosing later observations as part of that version's historical metadata. Version `retrieved_at` describes the first retained retrieval of those bytes.

**Availability means observed in this local archive.** `point_in_time=false` is explicit. Captures do not prove original publication time, recover vintages before recording began or reproduce a publisher's historical information set. A 304 establishes another local validation, not a new data release. Revised current macro data and current JPX classifications remain revised/current data. Byte differences can include formatting or metadata changes as well as numeric revisions.

JSON differences preserve exact decimal tokens, escaped JSON pointers and added/removed/null distinctions; array reordering is visible. XLSX, ZIP and other non-JSON bodies support raw-hash comparisons only. UTF-8 text can be returned; binary bodies are identified and not rendered. This is a raw-source difference service, not a normalized economic-revision feed.

Retained bytes are hash-checked on reads. Limits are 1,024 distinct URL/hash versions, 256 MiB combined body bytes, 100,000 observation events and a 4 MiB response. Quota failure rolls back the new capture and cache replacement without deleting old versions or resetting source-use counters. There is no automatic archive eviction, export or reconstructed history. Operator state must remain private and persistent. The normal HTTP cache and presentation snapshots have their own limits.
