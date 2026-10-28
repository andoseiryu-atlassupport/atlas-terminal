# Japanese company disclosures and reference data

The source checkout adds three research datasets across CLI, SDK, MCP and HTTP. The existing `v0.7.0` release assets are unchanged. EDINET supplies official filing metadata and original numeric XBRL facts; JPX supplies a current listed-security/industry reference table. These are separate from [optional Yahoo prices](yahoo-finance.md).

| Dataset              | Selection                                                                                                 | Returned information                                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `japan_disclosures`  | Explicit `filing_date`; optional exact EDINET code or five-character EDINET security code as `identifier` | One dated EDINET document list, submission timestamps, report-period metadata, availability/withdrawal flags and parent documents |
| `japan_filing_facts` | `filing_date`, exact `document_id`; optional local `concept` name                                         | Numeric facts from the one original `XBRL/PublicDoc/*.xbrl` instance in that document's ZIP                                       |
| `japan_securities`   | Optional JPX code with `.T`, such as `7203.T`                                                             | Name, market/product, original JPX33 industry code/label and table reference date                                                 |

EDINET public retrieval requires a free `EDINET_API_KEY`, configured privately in the process environment. Obtain it through the [official API instructions](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html); never put it in a query, URL, source file or chat. The key is added only to the exact canonical `api.edinet-fsa.go.jp/api/v2/documents` request; redirects are disabled. Cache identities, raw-source URLs, application errors and ordinary network-library logs exclude it. Missing configuration stops only EDINET reads. Review the [EDINET terms](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html), third-party filing rights and [API v2 specification](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf).

```sh
# Fictional offline example; no API key needed.
uv run --offline atlas research get japan_filing_facts --filing-date 2026-06-25 --document-id S100DEMO --provider fixture --json
uv run --offline atlas research get japan_securities --identifier 9999.T --provider fixture --json
```

The same SDK/MCP/HTTP query is:

```json
{
  "dataset": "japan_filing_facts",
  "filing_date": "2026-06-25",
  "document_id": "S100DEMO",
  "concept": "NetSales"
}
```

`S100DEMO` is fictional. Real reads must select an actual document from its dated list. A withdrawn or unavailable filing is rejected before downloading its ZIP. Submission time is reported separately from unknown first public availability. Amendments remain separate document IDs.

Facts retain their taxonomy namespace, actual instant/duration/forever context, issuer identifier including its branch suffix, consolidation/other dimensions, original and namespace-resolved unit measures, divided-unit numerator/denominator, decimals and nil status. Report-level periods never replace individual fact periods. A local concept-name filter can match more than one namespace or context; it does not select a normalized accounting concept. Share counts are reported facts, with their own period and unit. No accounting-standard harmonization, share-class reconciliation, fiscal-period inference or derived financial ratio is provided here.

Each fact links both its daily-list record and original ZIP, member filename/hash, expanded element name and top-level XML child position. ZIP reads stay in memory and reject unsafe paths, links, encryption, duplicate member names, unsupported compression and ambiguous instances. Limits are 2,000 members, 128 MiB combined declared expansion and 32 MiB for a selected instance; XML declaration/element bounds also apply. Only numeric original XBRL is supported; PDFs, inline XBRL and complete filing text are not extracted.

JPX's [current list page](https://www.jpx.co.jp/markets/statistics-equities/misc/01.html) links `data_j.xlsx`; the earlier `.xls` URL returns 404. The parser supports current XLSX and retained BIFF XLS bytes, preserving worksheet coordinates for every selected field. JPX identifiers include four-character alphanumeric and five-character class codes. A `.T` reference identifier does not establish Yahoo instrument availability. Missing industry classifications such as `-` remain as reported.

**JPX rights:** The [site terms](https://www.jpx.co.jp/term-of-use/index.html) require JPX consent for commercial collection, secondary use and retransmission. Public download access grants no blanket reuse or public-service redistribution license. Operators must establish permission for their use. Actual verification outputs remain private; bundled examples are fictional.

The current reference table is not a historical security master. `as_of` is rejected for these three datasets. Never backdate today's industry membership or shares. Never multiply split-adjusted historical prices by unadjusted historical shares to construct capitalization. The Yahoo time-series connector requires no capitalization or current JPX assignments.

Queries return at most 500 selected records, with explicit truncation. Use an identifier or concept filter for focused reads; presentation pagination covers the selected result only. [Local source history](source-history.md) can replay captured bytes, with local observation times rather than publisher PIT guarantees. [Verification results and remaining gaps](verification.md#data-integrity-and-japanese-company-extensions).
