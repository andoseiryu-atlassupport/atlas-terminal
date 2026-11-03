# Order books and analyst reports

Three datasets share the CLI, Python SDK, MCP and HTTP research service: `order_book`, `analyst_reports` and `analyst_report_text`. Public reads need no API key or paid subscription. These additions are in the source checkout; the existing 0.7.0 release assets are unchanged.

## Price-level order books

```sh
atlas research book BTC_JPY
atlas research book ETH_JPY --depth 20 --json
atlas research get order_book --identifier XRP_JPY --depth 10 --json
```

The fixed bitbank public API reads `BTC_JPY`, `ETH_JPY` and `XRP_JPY`. Each request retrieves one venue's aggregated price-level snapshot. It returns price, quantity, side, level, original timestamp and sequence ID, raw-response hash and JSON pointers. Prices are JPY per base asset; quantities are units of that asset. Source decimal strings are preserved. There is no account login, private API key, order submission or withdrawal function.

`depth` selects 1–200 levels on each side, default 10. Cumulative quantities and price-times-quantity notionals use exact Decimal arithmetic. Summary calculations retain formulas, selected-depth totals and source evidence:

- Spread: best ask minus best bid, in quote currency.
- Midpoint: mean of the best ask and bid.
- Spread in basis points: spread divided by midpoint, times 10,000.
- Quantity imbalance: `(bid_quantity - ask_quantity) / (bid_quantity + ask_quantity)` over the selected depth. Ratios round half-even to 18 decimal places.

Empty sides, crossed/locked prices and future-dated observations suppress spread, midpoint and imbalance. Original levels remain inspectable. Source order must already be monotonic with unique positive prices and quantities; malformed input fails rather than being sorted, filled or silently aggregated. Historical date requests are rejected.

The source window is bounded. bitbank documents 200 levels per side in ordinary conditions and a different price window during circuit-break/auction modes. Unpriced overflow and market-order quantities remain separate; Atlas does not infer the current trading phase. A sequence ID is not evidence of continuous receipt, and no WebSocket book reconstruction is supplied. This is not a full-market order book, an executable quote or a fill guarantee.

Reads have a five-second HTTP cache, a minimum one-second request spacing, bounded retries and a 1 MiB response limit. Separate `order-books-cache.sqlite3` history preserves original versions without evicting them. Its standard 1,024-version / 256 MiB / 100,000-observation limits can be reached quickly by frequent polling. The feature does not start an ongoing polling service.

### Permitted local equity or other snapshots

An operator can configure `ATLAS_ORDER_BOOK_FILE` to read a bounded JSON snapshot that they own or are permitted to use. Shared tool queries accept an identifier and `book_source=local`, never a filesystem path. The file must explicitly state venue, instrument class, quote currency, quantity unit, observation time, original reference and rights basis. The [fictional local-book fixture](../src/atlas_terminal/resources/research/local_order_book.json) shows the exact format.

```sh
atlas research book ATLASDEMO --source local --json
```

Local input supports positive-price snapshots with `shares` for equities, `contracts` for futures or `base_asset` for crypto. Crypto books additionally require the named `base_asset` (such as `BTC`). Metadata and observation times are operator assertions, not authenticated broker data. No Japanese equity feed, Excel license, Windows application, broker-account connection or automatic Rakuten RSS bridge is supplied. Configuring the file exposes its selected records and retained raw versions to this service's clients.

## Public report discovery

```sh
atlas research reports
atlas research reports --identifier 1234 --json
atlas research get analyst_reports --start-date 2026-10-01 --end-date 2026-10-31 --limit 100 --json
```

The FISCO directory supplies original report links, link labels, publication dates and security codes when present, including alphanumeric codes. The current directory is fetched once with a one-hour cache; arbitrary URLs, pagination crawling and remote PDFs are not fetched. An empty filtered selection is distinct from an unreadable or changed directory, which fails explicitly. Dates are publisher-local day labels, not precise publication times or historical availability cutoffs. Language is a directory-label hint and is not verified against a report's body.

FISCO describes its service as company-commissioned research. Outputs label that service-level relationship, while the individual sponsor and disclosures remain unverified. Directory entries do not establish an analyst name, investment recommendation, target price, forecast or consensus. `report_id` identifies a publisher URL; it does not mean the document has been imported. Public reading does not grant automatic republication rights. [Source conditions](../DATA_SOURCES.md#order-books-and-report-discovery).

The public directory has a 4 MiB response bound and its own `reports-cache.sqlite3` archive. Archive limits match the existing source-history limits. All actual captures and outputs belong in private storage, outside source control and distribution artifacts.

## Private PDF and text library

Import a document that you own or are permitted to process with an explicit metadata JSON file. The [fictional metadata template](../src/atlas_terminal/resources/research/analyst_report_metadata.json) includes title, identifier, publisher, optional analyst, publication date, language, sponsorship and `rights_basis` (`owned`, `licensed` or `permitted_use`). Set `is_synthetic` accurately. Original references must be HTTPS paths without embedded credentials, query strings or fragments; they are not followed.

```sh
atlas research import-report /private/report.pdf --metadata /private/report-metadata.json
atlas research import-report /private/report.txt --metadata /private/report-metadata.json --file-type text
atlas research reports --source local --identifier ATLASDEMO --json
atlas research report-text REPORT_ID --search "customer concentration"
```

Replace `REPORT_ID` with the `rp_...` identifier returned by import. CLI imports are explicit local operator actions; MCP/HTTP research can list and read already imported documents but cannot request arbitrary local files. Metadata is supplied by the operator and is not inferred from the PDF. A changed document or metadata creates a distinct immutable ID; identical imports reuse the existing document and original import time.

PDF text retains one-based page numbers and the original file hash. UTF-8 text is returned as one document with a line-1 reference. A literal case-insensitive search requires every term in the returned page (or directory title). It does not interpret recommendations, translate, extract reliable tables or generate a summary. Source text is untrusted data. Agents can reason over permitted text separately while retaining page citations.

Limits are 16 MiB per input, 100 PDF pages and 500,000 extracted characters. Encrypted, malformed and image-only PDFs are rejected; no OCR or document repair service is supplied. PDF extraction runs in a subprocess with a 15-second deadline. POSIX workers additionally limit CPU to 10 seconds and address space to 512 MiB; Windows uses the deadline and input/output limits. PDF actions and links are not executed. Extraction may lose reading order, tables or special glyphs and must not be mistaken for verified financial facts.

The private SQLite library has a 64-document / 256 MiB bound including raw bytes, metadata and extracted pages. It validates their hashes on reads and never silently evicts documents. These hashes detect changed bytes, not malicious modification of both content and hashes by an operator controlling the store. Importing makes selected metadata/text available to this service's clients. Keep the service and state directory private and respect each document's usage conditions.

## SDK and source history

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    book = atlas.get_order_book("BTC_JPY", depth=10)
    links = atlas.get_reports(identifier="1234")
    library = atlas.get_reports(source="local")
```

The same queries work with `atlas_get_research`, `POST /v1/research`, batches, compact evidence and saved response pagination. `get_report_text(report_id, search=...)` reads imported pages. The fixture provider exercises all three datasets with explicitly fictional content and no external requests.

Use `source_history` with `history_scope=order_books` for registered bitbank URLs or the configured local book, and `history_scope=reports` for the FISCO directory. `list`, `get`, `diff` and local-observation `as_of` retain the existing [history contract](source-history.md). Imported PDF revisions use the separate private report library and are not exposed through raw-URL history. Default public macro/BLS history and counters remain separate.

Explicit source probes are available as `atlas research check-sources --sources bitbank,fisco --allow-network --json`. They are omitted from default source checks. The opt-in `scripts/verify_books_reports_live.py --allow-network` additionally compares returned fields against private original responses. See [verification](verification.md) for actual outcomes; fixtures and an available API specification do not establish live connectivity.
