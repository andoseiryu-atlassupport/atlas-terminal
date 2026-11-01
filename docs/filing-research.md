# Trailing financials and original SEC filing research

These datasets use the shared research query through the SDK, CLI, MCP and HTTP. They are available in the latest source checkout. Public SEC requests require an operator-configured `SEC_USER_AGENT` with a real contact address. No contact setting is sent to other publishers. Fixture mode is fictional and works offline.

| Dataset               | Required fields           | Optional selection                      | Result                                                     |
| --------------------- | ------------------------- | --------------------------------------- | ---------------------------------------------------------- |
| `trailing_financials` | `identifier`              | `metrics`, `count`, `allow_partial`     | Exact trailing monetary flows, FCF and margins             |
| `filing_documents`    | `identifier`, `accession` | `limit`                                 | Safe members of the selected issuer's filing directory     |
| `filing_facts`        | `identifier`, `accession` | `document_name`, `concept`, `limit`     | Original numeric XBRL facts with scoped units and contexts |
| `segment_financials`  | `identifier`, `accession` | `document_name`, `metrics`, `limit`     | Monetary facts on selected standard segment axes           |
| `filing_sections`     | `identifier`, `accession` | `document_name`, `section`, `limit`     | Visible Item occurrences or selected document text         |
| `filing_tables`       | `identifier`, `accession` | `document_name`, `table_index`, `limit` | Reported HTML cells and spans                              |

`count` is 1–20; `limit` is 1–500. Every filing selection needs an explicit accession in the resolved issuer's history. Inventory first, then select an exact `document_name`; arbitrary URLs, parent paths and external schema retrieval are unavailable. Supported forms are 10-K, 10-Q, 20-F, 40-F, 6-K, 8-K, S-1, F-1 and their amendments. Inline-only filings without a uniquely selected extracted XML instance are not converted automatically.

## Trailing monetary windows

`trailing_financials` chooses the latest reported period anchors and uses this priority separately for each additive flow:

1. A reported annual window ending at the target date.
2. Four independently reported, exactly contiguous quarters.
3. The direct identity `previous_annual + current_ytd - prior_ytd`, with matching actual fiscal starts, adjacent annual/current periods and compatible prior-year dates.

Every operand must be unambiguous, have the same source concept and USD unit, and retain its actual dates, filing metadata, JSON pointer, raw hash and retrieval time. Trailing spans allow 330–400 elapsed days to accommodate fiscal calendars. The third method returns a trailing window; it does not create a fourth-quarter statement. Missing target windows remain null, with `INCOMPLETE_COVERAGE` unless `allow_partial=true`.

The catalog lists 17 additive monetary flow metrics. EPS, weighted-average shares, balances and per-share dividends are excluded. Derived selections also include `free_cash_flow`, `net_margin`, `gross_margin`, `operating_margin` and `free_cash_flow_margin`. FCF means operating cash flow minus nonnegative reported PP&E acquisition payments. Ratios require positive denominators and equal actual periods; they use 18 decimal places with half-even rounding. Reported monetary sums and differences use Decimal arithmetic without float conversion.

One frozen company-facts/submissions input supplies all three period selections. Current amended facts can differ across operands; the service does not establish a common restatement basis or historical release-time availability.

```sh
atlas research get trailing_financials --identifier ATLASDEMO --metrics revenue,net_income --count 4 --allow-partial --provider fixture --json
atlas research get filing_documents --identifier ATLASDEMO --accession 9999999999-26-000001 --provider fixture --json
atlas research get filing_facts --identifier ATLASDEMO --accession 9999999999-26-000001 --concept CommonStockSharesOutstanding --provider fixture --json
atlas research get segment_financials --identifier ATLASDEMO --accession 9999999999-26-000001 --metrics revenue,operating_income --provider fixture --json
```

## XBRL facts and segment interpretation

The reader checks the instance's DEI issuer CIK, document type and reported period against the selected submission. Context identifiers must also match the issuer. Actual duration/instant dates, namespace-scoped concept and dimension QNames, typed members, divided units, nils and source precision remain explicit. A `decimals="-3"` value is already expressed in its reported unit; it is not multiplied by 1,000. Source paths identify the original XML member, not a synthetic wrapper or a transformed document. DTD/entity declarations, malformed periods, missing references, conflicting IDs and unsupported numeric bounds fail closed.

`segment_financials` recognizes standard US-GAAP revenue, operating income, net income, assets and depreciation/amortization concepts with standard business, product/service or geography axes. Issuer-defined member QNames and all crossed dimensions are retained. Custom concepts/axes can be inspected through `filing_facts`; they are not assigned economic meanings from their labels. Duplicate values retain references; conflicting facts for the same metric, dimensions, unit and actual period are ambiguous. Geographic and business selections can overlap, so they are not added together or reconciled to a company total. Numeric facts with future reference periods remain available in raw facts but are excluded from normalized historical segment selections.

## Visible text and reported tables

`filing_sections` accepts `section=all` (default), `document`, `item_7`, `item_1a`, `item_2.02` and equivalent bounded Item identifiers. Item occurrences include possible table-of-contents entries and repeated headings; the reader returns their occurrence numbers instead of silently treating one as authoritative. Visible text excludes script/style/head content and explicitly hidden elements. It normalizes whitespace, splits selected text into bounded chunks, and retains decoded-source character envelopes and original line coordinates. Heading recognition is heuristic; unusual layouts and part-qualified duplicate Items are not semantically reconstructed.

```sh
atlas research get filing_sections --identifier ATLASDEMO --accession 9999999999-26-000001 --section item_7 --provider fixture --json
atlas research get filing_sections --identifier ATLASDEMO --accession 9999999999-26-000001 --document-name earnings.htm --section document --provider fixture --json
atlas research get filing_tables --identifier ATLASDEMO --accession 9999999999-26-000001 --table-index 1 --provider fixture --json
```

Tables retain original `table_index`, `row_index`, `cell_index`, header/data-cell kind, text and row/column spans. Cell order precedes span expansion, and nested-table text is excluded from an outer cell. These are source text cells, not a reconstructed financial-statement presentation tree, standardized line-item hierarchy or inferred numeric unit. Invalid spans, unsupported encoding, unclosed cells and resource limits produce errors. Large results retain explicit truncation; compact responses and paging preserve interpretation and resolvable evidence.

Selected releases can contain company guidance or non-GAAP discussion, and custom XBRL facts can contain operational metrics. Atlas does not standardize these into a KPI/guidance/adjusted-earnings database or supply analyst consensus, surprise values or market-valuation multiples. Current shares and split-adjusted historical prices are not combined to invent historical capitalization.

## Verification boundary

Offline regression tests use fictional filings and mocked transports. During the 2026-11-01 verification, normal proxy requests to SEC destinations returned `403 Forbidden`, and `SEC_USER_AGENT` was unset. SEC live compatibility remains unverified; this is a local access/configuration limit, not evidence of a publisher outage. [Recorded checks](verification.md) and [source conditions](../DATA_SOURCES.md).
