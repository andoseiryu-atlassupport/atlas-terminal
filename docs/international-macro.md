# Discover and align macro series

Series search, IMF/OECD retrieval and macro panels use the same SDK, CLI, MCP and HTTP research service. These additions are available in the current source checkout, ahead of the next tagged release. No wallet, subscription or LLM is required. Live IMF/OECD compatibility remains unverified; examples marked `fixture` use fictional source bytes.

## Search existing series

```sh
uv run atlas research catalog --search inflation --country US --frequency monthly --source-id bls --view compact --json
uv run atlas research catalog --country US --unit percent_per_annum --json
```

The corresponding MCP call is `atlas_research_catalog` with `{"search":"inflation","country":"US","frequency":"monthly","source_id":"bls","view":"compact"}`. Python uses `Atlas.research_catalog(search="inflation", country="US")`; HTTP accepts the same filters on `GET /v1/research/catalog`.

`series_search.records` contains executable query templates, source IDs, units when known, frequency, seasonal-adjustment metadata, key requirements and source terms. Search covers registered BLS/BEA/Japanese/reference-rate/derived series and supported ECB currency pairs. Text matching is case-insensitive and includes selected Japanese aliases and source series codes. All text terms must match. `country` uses `US` or `JP` for the registered national series; FX pairs have no country assignment. `unit` is an exact filter; unknown source-resolved units remain null.

This search is offline. It describes supported queries, not current availability or the full BLS/BEA/BOJ universe. Results are bounded to 100 templates in full view or 20 in compact view; refine filters when `truncated=true`. The registries used by retrieval also generate these templates, so a new registered series does not need a separate search implementation.

## Discover IMF and OECD data

Use `sdmx_catalog` to search publisher metadata over the normal cached public-source transport:

```sh
uv run atlas research get sdmx_catalog --source-id imf --search "consumer price" --json
uv run atlas research get sdmx_catalog --source-id oecd --search "unemployment" --json
```

Each dataflow record includes a query for its structure. Submit that returned query to obtain ordered series dimensions and classification codes. A dataflow ID has the form `agency,id,version`; retain the returned version. To narrow a large structure, supply `dimension` and/or `search`. For example, after finding a structure whose country dimension is `REF_AREA`, use `--dimension REF_AREA --search Japan` with its returned `--dataflow`.

The `dimensions` metadata lists each dimension's ID and key position. Records contain codes and labels for dimensions and attributes, with original XML locators. A code being present does not guarantee that every combination has observations. There is no automatic substitution of country, frequency, adjustment or measure codes.

Then submit `international_economics` with:

| Field                    | Meaning                                                                          |
| ------------------------ | -------------------------------------------------------------------------------- |
| `source_id`              | `imf` or `oecd`                                                                  |
| `dataflow`               | Exact returned `agency,id,version`                                               |
| `dimensions`             | A JSON object containing every series dimension and one exact code per dimension |
| `start_date`, `end_date` | Inclusive observation period-end filters, up to 30 calendar years                |
| `limit`                  | At most 500 returned observations; truncation is explicit                        |

Atlas resolves and validates the structure before requesting data. It builds the series key in the publisher's dimension order. New dataflows or codes can be used through metadata without adding indicator-specific Python code. Arbitrary URLs, wildcard series requests, multiple codes per dimension and unknown codes are rejected. Only monthly, quarterly and annual observations are supported; unsupported structures fail explicitly.

The shared parser supports SDMX 2.1 structure metadata, generic data XML and structure-specific data XML. IMF reads use its SDMX 2.1 structure and 3.0 data endpoints; OECD reads use its public SDMX REST API. Values stay decimal strings in their reported scale. Returned fields preserve dimension labels, unit codes, multipliers, base periods, adjustment, observation status and other source attributes. Missing metadata stays null. A reported value may be provisional or a forecast: inspect observation status and source definitions. No publication timestamp or vintage is inferred.

Metadata is cached for 24 hours and observations for six hours. OECD attempts share a persistent rolling limit of 60 per hour, including retries; upstream IP limits apply independently. Requests retain normal response-size bounds, finite retries and redirect rejection. Metadata and data source hashes are both retained. See [sources and terms](../DATA_SOURCES.md).

## Build a calendar-aligned panel

`macro_panel` accepts 1–10 named series, a target frequency (`monthly`, `quarterly` or `annual`), and dates bounding complete calendar periods. Each item explicitly selects `aggregation: "last"` or `"mean"`. Dates, a 500-record read limit and full evidence are supplied by the panel; omit those fields from individual queries.

Run the packaged fictional IMF/OECD example:

```sh
uv run --offline atlas research run --input examples/research_panels/us_japan.json --provider fixture --json
```

Its dataflow identifiers begin with `IMF.DEMO` and `OECD.DEMO`. For public data, obtain real dataflow IDs and codes from the publisher catalogue first. A separate [macro/FX example](../examples/research_panels/macro_fx.json) uses registered US/Japan CPI and ECB USD/JPY queries:

```sh
uv run atlas research run --input examples/research_panels/macro_fx.json --json
```

Use `--input -` to read JSON from stdin. The same JSON goes to `Atlas.get_research(query)`, MCP `atlas_get_research({"query": query})` or `POST /v1/research`. Existing compact evidence, saved pagination and quality requirements apply. The research-only MCP tool count is unchanged.

Each output period contains named cells with the original input observations, units, calculation, sample count and actual first/last input dates. The period end is the alignment bucket; a cell's `date` is its latest actual input observation. Source-age checks include metadata and calculation inputs. Observation-date requirements therefore cannot mistake an incomplete current month for an observed month end.

- `mean` uses Decimal arithmetic and 28 significant digits with half-even rounding. It requires all expected monthly/quarterly/annual inputs; a missing or null input makes the cell unavailable. It does not prorate or sum annualized flows.
- `last` selects the last available observation inside the period. It does not forward-fill or certify a period-end fixing. Missing expected calendar observations also remain explicit.
- Daily aggregates use the dates actually supplied. No business-day or holiday calendar certifies daily completeness. An explicit null observation makes the aggregate unavailable.
- Lower-frequency series cannot be upsampled. Unit, scale, adjustment, base-period or frequency changes within an input series cause an explicit failure. Columns may have different definitions and bases: calendar alignment does not establish statistical comparability.
- Default behavior rejects missing cells or failed reads. `allow_partial: true` returns null cells and per-item errors/coverage; it never fabricates observations or retries a failed item automatically. Individual transport retries still follow the normal finite read policy.

Daily ECB and BOJ reads are split into nonoverlapping calendar years so a long query does not silently use only the latest 500 dates. A panel allows at most 64 research reads, a 16 KiB request and a 4 MiB result; reduce dates/items when a bound is exceeded. NY Fed coverage remains the latest 90 observations. BLS annual-average rows are excluded from calendar aggregation. Treasury curves/spreads, sum aggregation, automatic rebasing and statistical-definition harmonization are unsupported.

## Verification

Fictional fixtures and mock HTTP cover structure identity, new metadata-defined codes, mismatched series, numeric precision, malformed XML, rate limits, missing observations, changing units, aggregation and source evidence. An actual local MCP session exercises discovery, panel retrieval and evidence lookup. These checks do not establish upstream availability or live response compatibility. [Validation record](verification.md).
