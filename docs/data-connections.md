# Native inputs for factor risk

Atlas reads operator-supplied JSON, CSV bundles or SQLite tables/views into its own risk schemas. A single `run` request selects a connection ID, estimation method, as-of cutoff and portfolio weights, then reads, normalizes, validates, imports, fits and analyzes. The estimation universe comes from the connection and can be much larger than the portfolio. No external financial framework is required.

This experimental extension is unreleased. It bundles no equity-price feed or verified historical database. Current research snapshots cannot be substituted for historical vintages. See the [risk methodology](risk-model.md) for model assumptions and limits.

## Configure a local input

Copy [the configuration template](../examples/risk/connections.template.json) outside the repository, replace the absolute paths and keep only the connections you need. Configurations conform to `RiskConnectionConfig` in [the exported schema](../schemas/research.schema.json).

```sh
chmod 600 /operator/private/connections.json
export ATLAS_RISK_CONNECTIONS_FILE=/operator/private/connections.json
atlas risk connections --json
atlas risk run examples/risk/run-cross-sectional.json --json
```

The file must be private on POSIX systems, contain `schema_version="0.1.0"` and at most 32 uniquely named connections. The service loads configuration once per instance; restart after editing it. Cataloging reads configuration only, not datasets. Paths, mappings and SQL remain operator configuration and never become tool arguments. All enabled connections share one operator state volume and authorization; this is not tenant isolation.

The same request works as `Atlas.risk(query)`, `atlas_risk({"query": query})` or `POST /v1/risk`. Enable MCP/HTTP risk support with `--enable-risk`. The [cross-sectional request](../examples/risk/run-cross-sectional.json) and [time-series request](../examples/risk/run-time-series.json) use illustrative IDs and dates that must match your data.

Set `"preview": true` in a `run` request to inspect the stages and selected method without opening data files, importing inputs, fitting or making network calls. An executing response includes `model`, `analysis` and `input_evidence`. It is not a transaction across all stages; a later fit failure can leave validated input chunks in private state.

## Canonical JSON

Use `kind="json"`, an absolute file path and the matching `method`. The object is either:

- `RiskPanelInput`: `manifest`, `fundamentals`, `sessions` for `method="cross_sectional"`.
- `TimeSeriesPanelInput`: `manifest`, `sessions` for `method="time_series"`.

JSON uses canonical identifiers and fields directly; column/table/asset mappings and price conversion are rejected. The existing [fundamental panel template](../examples/risk/panel-template.json) illustrates fields but is deliberately too small to fit. Both `atlas risk demo` variants generate complete fictional input panels in Python through `atlas_terminal.risk_demo`.

## CSV bundles and SQLite

A CSV connection points to an absolute directory containing `manifest.json`, `sessions.csv`, `returns.csv` and either `fundamentals.csv` or `factors.csv`. Headers must be unique and rows complete. Numeric cells remain strings; no spreadsheet percentage or scientific notation is inferred.

SQLite uses the same logical tables plus exactly one JSON row in `manifest(body TEXT)`. Amount columns must be stored or exposed as decimal text. Numeric `REAL` values are rejected rather than silently converted to strings. Atlas opens the database with `mode=ro`, `query_only=ON` and a read transaction. Tables and views are supported; there is no caller-supplied SQL. An operator may map logical table names to existing table/view names using `tables`, for example `{"returns":"adjusted_return_history"}`. The `manifest` table name is fixed.

| Logical table  | Required columns                                                                                                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sessions`     | `start_at`, `end_at`, `available_at`, `source_id`, `locator`                                                                                                                                            |
| `returns`      | `end_at`, `asset_id`, `total_return`; also `market_cap` for cross-sectional inputs                                                                                                                      |
| `fundamentals` | `asset_id`, `period_end`, `available_at`, `source_id`, `locator`, US `sic` or local `industry_code`, `book_equity`, `total_assets`, `net_income_ttm`, `total_debt`; explicit `currency` for Japan/Korea |
| `factors`      | `end_at`, `factor_id`, `return`                                                                                                                                                                         |

Time-series `sessions` also requires `factor_source_id` and `factor_locator`. Include `risk_free_return` only for `factor_convention="excess_return"`; it is required then. Empty CSV risk-free cells are omitted. A SQLite NULL also represents an omitted risk-free value.

Each return/factor row joins one session by its exact time-zone-aware `end_at` instant. Every session must contain the exact declared asset universe and, for time-series inputs, the exact factor set. Session boundaries must be contiguous and source IDs declared in the manifest. Unknown, duplicate, missing or unmatched observations fail; nothing is forward-filled. Monetary inputs and returns must already use the manifest currency. Atlas performs no FX conversion or security-master inference here.

Explicit column mappings select and rename source columns; unselected columns are ignored only when a mapping exists. For example:

```json
{
  "columns": {
    "returns": {
      "end_at": "Timestamp",
      "asset_id": "Ticker",
      "total_return": "Total Return",
      "market_cap": "Market Cap"
    }
  },
  "asset_map": { "SOURCE/A": "STOCK_A" }
}
```

Column/asset mappings are one-to-one and all selected source columns must exist. `asset_map` applies to relational asset observations and fundamentals; manifest IDs are already canonical. An asset mapping never merges two instruments into one. Fundamental `industry_map` can explicitly map source labels to canonical local codes, including many source labels to one industry. Targets must match the manifest classification. Blank optional `sic`, `industry_code` and `currency` CSV cells are omitted before validation. See [local-market inputs](equity-markets.md); no country, currency or accounting conversion is inferred from an identifier.

## Convert supplied prices to returns

For CSV/SQLite, set `return_input="adjusted_prices"`. Replace `returns.csv` with `prices.csv`, or use a `prices` table/view. The logical mapping key remains `returns`, so an existing `columns.returns` or `tables.returns` mapping selects the price fields/table.

Every price row requires `end_at`, `asset_id`, `available_at` and positive `adjusted_close`. Supply an exact price at both the start and end of every return period, including one initial price before the first period. The declared adjustment convention is split-and-dividend-adjusted. Atlas does not infer dividends or repair corporate actions.

```text
simple total return = adjusted_close(end) / adjusted_close(start) - 1
```

The ratio is rounded half-even to 18 decimal places. Its session availability is at least the maximum of the declared session availability and both source-price availability timestamps. Missing boundaries, duplicate/unused price rows, nonpositive prices and availability before close fail.

For cross-sectional inputs, every ending price row must also contain either a positive `market_cap` or positive unadjusted `close` and positive whole `shares_outstanding`. Do not supply both bases. Capitalization is reported directly or calculated as `close * shares_outstanding`, in the manifest currency. Adjusted close is never multiplied by raw shares. Share availability and historical correctness remain operator assertions; do not use today's shares for a historical close. Time-series price inputs need no capitalization columns.

## Evidence and bounds

An immutable ingestion receipt retains each read document's SHA-256, byte count and encoding, the normalized input-chain ID and a hash of mappings. JSON/CSV hashes cover the original bytes. SQLite hashes cover deterministically sorted canonical logical rows, not the mutable database file. This proves which local inputs Atlas used; it does not verify upstream source assertions or license rights.

Only SQLite reads promise a transaction snapshot (`snapshot_atomic=true`). JSON and CSV reads do not lock writers. Publish immutable files/bundles before executing a request; CSV multi-file changes can otherwise be inconsistent. Timestamp/coverage validation cannot establish consistency for every concurrent edit.

Retrieve the receipt after restart with `{"action":"get","model_id":"<saved ID>","view":"inputs"}`. Paths and SQL are excluded from receipts. Complete normalized observations remain in private content-addressed chunks; compact responses return model summaries and selected exposures instead of full price histories.

Inputs are bounded to 32 MiB per read, 640 return sessions, 200 assets, 10,000 fundamental vintages and 12 time-series factors. SQLite reads have a 10-second execution deadline. Configurations are bounded to 64 KiB. Existing [private-state quotas](risk-model.md#storage-and-reproducibility) also apply.

## Trusted API extensions

Python callers may register `Atlas.register_risk_connection(connection_id, connector)`. A trusted connector implements `RiskConnector` from `atlas_terminal.risk_connections`: `method`, a bounded `kind`, a boolean `uses_network`, and `read()` returning `RiskConnectionRead(panel, documents, snapshot_atomic, normalization)`. The panel must pass the same schemas and timing/coverage checks as local files. Configure credentials privately in the extension, never in a query or returned provenance.

No equity API provider is bundled. Network providers must declare `uses_network=True`; execution fails before reading unless the request includes `allow_network=true`. Preview remains offline. The request cannot change provider URLs, credentials, SQL or local paths. Registration runs trusted Python in the operator process and is not a sandbox. A normal remote service configured only from the connection file exposes the three built-in local formats.
