# BLS live verification: 2026-10-27

All six additions were retrieved through `Atlas("public").get_research` and the shared `PublicTransport` at source commit `8e26aff35c04c409d831aafd526ba05342c11c7e`. This is a recorded live retrieval, separate from fictional fixtures and subsequent offline replay.

The normal inherited HTTP/HTTPS proxy and TLS verification were used. The runtime reported a current, enforced policy allowing `api.bls.gov`, using configuration version `cecfgver_6adf7916015081a29f3ed15529146065`. This records the running task's applied version, not an inspection of a later settings-screen draft. The retained runtime status outside the public repository also identifies the environment and full configuration/version IDs. No route, proxy, credential or quota reset was used.

## Results

Each normal GET requested the latest three years. Each dated request used the actual read-only POST with `startyear="2025"` and `endyear="2026"`; Atlas then applied inclusive observation-date bounds of 2025-01-01 through 2026-10-27. All 12 requests returned HTTP 200, `REQUEST_SUCCEEDED` and an empty BLS message list, without retries.

| Series                         | BLS ID              | GET rows | Dated POST rows | Latest date | Latest value |
| ------------------------------ | ------------------- | -------: | --------------: | ----------- | ------------ |
| `employment_cost_compensation` | `CIS1010000000000I` |       10 |               6 | 2026-06-30  | 177.178      |
| `employment_cost_wages`        | `CIS1020000000000I` |       10 |               6 | 2026-06-30  | 176.754      |
| `nonfarm_productivity_growth`  | `PRS85006092`       |       10 |               6 | 2026-06-30  | 1.4          |
| `unit_labor_cost_growth`       | `PRS85006112`       |       10 |               6 | 2026-06-30  | 1.2          |
| `import_prices`                | `EIUIR`             |       32 |              20 | 2026-09-30  | 152.2        |
| `export_prices`                | `EIUIQ`             |       32 |              20 | 2026-09-30  | 170.8        |

All 168 returned records match the original value strings, year, period, calendar period-end date, footnotes, SHA-256 and JSON pointer. The GET/POST overlaps agree. No parser, series-ID, unit or annualization defect was found.

Quarterly data contain Q01–Q04. Each productivity/cost GET also contains two Q05 annual summaries, which remain in the original bytes and are correctly excluded from quarterly results; the dated POST responses contain no Q05 rows. Negative productivity in 2025 Q01 (`-0.9`) and unit labor costs in 2025 Q02 (`-2.9`) are retained in both retrieval modes. Monthly import/export data omit 2025 M10 in both modes; Atlas retains the gap without inserting a row or value. The capture alone does not establish why that month is absent.

These responses contain no zero values, explicit missing-value cells or M13 annual averages. Their handling is established by the separate fictional offline cases in `tests/test_bls_costs.py`, not by this live sample. Likewise, absent 2026 Q03/Q04 rows do not authorize filling or inferring future quarters. Current revised snapshots do not establish release-time vintages or publication freshness.

## Definitions checked separately

The BLS V1 responses contain `seriesID` and observation data, without units, index bases, seasonal-adjustment or annualization metadata. Those fields remain curated Atlas definitions, checked against the following official documents through the connected web-reading tool on the same date. They were not independently supplied or validated by the API response.

- [ECI format](https://download.bls.gov/pub/time.series/ci/ci.txt), sections 1, 4 and 7, and the civilian all-worker rows in the [compensation](https://www.bls.gov/news.release/eci.t01.htm) and [wages](https://www.bls.gov/news.release/eci.t02.htm) tables: seasonally adjusted index levels, December 2005=100, with March/June/September/December reference months. The June 2026 levels match 177.178 and 176.754; adjacent change columns are separate measures.
- [Productivity format](https://download.bls.gov/pub/time.series/pr/pr.txt), sections 3 and 7, [measure codes](https://download.bls.gov/pub/time.series/pr/pr.measure), [duration codes](https://download.bls.gov/pub/time.series/pr/pr.duration) and [nonfarm business table 2](https://www.bls.gov/news.release/prod2.t02.htm): seasonally adjusted percent changes from the previous quarter at annual rates. Measures 09/11 identify productivity/unit labor costs; duration 2 identifies previous-quarter changes. The June 2026 entries match 1.4 and 1.2; Atlas retains the published percent values without dividing by 100 or annualizing again. Q05 identifies an annual summary. Index bases in the format/mapping files do not apply to these growth selections, whose `base_period` is null.
- [International price format](https://download.bls.gov/pub/time.series/ei/ei.txt), sections 2, 4 and 7, and the all-commodity rows of the [import](https://www.bls.gov/news.release/ximpim.t01.htm) and [export](https://www.bls.gov/news.release/ximpim.t02.htm) tables: BEA end-use commodity indexes, unadjusted, 2000=100. The September 2026 levels match 152.2 and 170.8. M13 is a separate annual average; earlier historical frequency changes remain outside this sample.

## Interfaces and quota

SDK compact responses and original evidence resolution were checked for all 168 records. An actual official stdio MCP client connected to the public provider, discovered the six research tools, repeated the same 12 cached queries and resolved all 168 records' evidence. Both interfaces reconstructed the complete saved records exactly.

The complete eight-item `us_inflation_costs` preset succeeded through both SDK and MCP. Its six new-series reads reused the dated cache; only CPI and PPI required another POST each. SDK and MCP preset records and resolved evidence agree. The preset's five-row selection remains explicit in each item's truncation/quality coverage; success does not mean a complete date-window history.

The persistent BLS ledger progressed from 0 to 12 after primary retrieval, stayed at 12 during SDK evidence checks, reached 14 after the SDK preset and stayed at 14 throughout MCP verification. This is below the 25-attempt UTC-day limit. The same state/cache was used throughout. No key, paid data, transfer, release publication or service deployment was involved.

## Retained evidence and offline replay

- [Manifest and all 14 HTTP request records](manifest.json): actual method, URL, safe POST body, capture time, HTTP status and raw-body hash/path.
- `raw/<sha256>.json`: original decoded HTTP response-body bytes, never reformatted. Normalized records use the shared transport's original retrieval time; request-log times record the response hook immediately before cache persistence.
- `results/<series>-get.json` and `results/<series>-post.json`: exact input query and complete shared-service response, including source retrieval times and per-row JSON pointers.
- [Interface/evidence checks](interfaces.json), [SDK preset](sdk-preset.json) and [MCP preset](mcp-preset.json): interface outcomes, resolved provenance, exact preset queries and returned records.

Run from the source checkout:

```sh
uv run --offline python scripts/verify_bls_capture.py
uv run --offline pytest tests/test_bls_capture.py tests/test_bls_costs.py
```

The verifier independently audits every saved value/date/locator and replays the shared service and preset against the saved bytes with no network fallback. Its temporary replay store has no connection to the operator's persistent live quota. A replay PASS does not establish a new live retrieval or present source availability. The added regression test uses these recorded real responses; the existing 35 cost-series cases use fictional data. Existing source fixtures and release assets remain unchanged.
