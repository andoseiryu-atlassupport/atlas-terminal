# Japanese company scoring from EDINET

Use `buffett_score` with `country="JP"` to resolve a Japanese security, select indexed EDINET annual reports and amendments, normalize consolidated financial inputs in JPY, and run the existing [Buffett-inspired rubric](buffett-scoring.md). The weights, formulas, three-to-ten-year window and missing-point policy are unchanged. Automatic Japanese scoring now identifies its disclosure definitions as `buffett_quantitative_jp_disclosures_v2`; US and supplied-input scoring retain `buffett_quantitative_v1`. [Version 2 definitions and Web migration](japan-buffett-v2.md). No paid financial feed or LLM is required.

This implementation has fictional contract tests; **live EDINET financial retrieval remains unverified**. The verification environment has no EDINET API key. A cold server also needs an operator-maintained disclosure index before it can supply multi-year financials. An empty or insufficient index is a coverage gap, not successful financial verification. [Actual verification record](verification.md#japanese-edinet-company-scoring).

## One scoring service for Web, SDK, CLI and MCP

```sh
uv run atlas research buffett 7203.T --country JP --years 5 --json
```

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    result = atlas.get_buffett_score("7203.T", country="JP", years=5)
    scorecard = result["records"][0]
    print(scorecard["known_points"], scorecard["unevaluated_weight"])
```

HTTP `POST /v1/research` accepts this request body. MCP `atlas_get_research` accepts the same object as its `query` argument:

```json
{
  "dataset": "buffett_score",
  "identifier": "7203.T",
  "country": "JP",
  "count": 5
}
```

An omitted country or `country="US"` preserves the existing SEC path. Explicit `financial_input` retains the existing unverified supplied-data mode, including JPY; it does not become EDINET-verified because the caller declares Japan. Source queries cannot supply an API key, filesystem path or arbitrary download URL.

Accepted automatic Japanese identifiers are a four-character security code (`7203`, or an alphanumeric code), its `.T` form, the five-character EDINET security code with a final zero (`72030`), or an EDINET issuer code (`E02144`). The code relationship must agree with the observed EDINET lists in both directions. Missing mappings, code changes or conflicting issuers remain unavailable. Five-character JPX class-share symbols are not inferred from EDINET's final-zero convention.

The current JPX reference table must identify a domestic common equity and a recognized JPX33 industry. Banks (`7050`), securities/commodity futures (`7100`), insurers (`7150`) and other financing (`7200`) are excluded before downloading financial ZIPs. Missing/unknown classifications and unsupported instruments are also excluded. Current classification is not historical sector membership; financial subsidiaries within a nonfinancial group still require manual review.

## Server configuration and index preparation

Configure `EDINET_API_KEY` privately in the server process using the [official free registration procedure](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html). Use a persistent private `ATLAS_STATE_DIR` shared by the operator's indexing process and the API/MCP process. Keep keys out of browser code, requests, source control and logs. Normal verified HTTPS access is needed to `api.edinet-fsa.go.jp` and `www.jpx.co.jp`; redirects and provider fallbacks are not used.

EDINET's API lists documents by submission date. Atlas maintains `edinet-history.sqlite3` separately from its raw-source cache. The index records only annual securities reports (`120`) and their corrections (`130`), with source hashes, list pointers and capture times. Ordinary public `japan_disclosures` and `japan_filing_facts` reads also populate it. Fixture scoring has a separate private index of fictional raw documents.

Build or extend the index explicitly in chunks of **at most 31 calendar days** within the latest **4,392 days**:

```sh
# One example chunk, not a complete multi-year index.
uv run atlas research edinet-index --start-date 2022-06-01 --end-date 2022-06-30
```

```python
with Atlas("public") as atlas:
    progress = atlas.sync_edinet_history("2022-06-01", "2022-06-30")
    print(progress["partial"], progress["next_date"])
```

Cover the desired history through the current date, including submission dates outside the usual annual filing month, when corrections may appear. Re-run bounded chunks when source lists change. Each invocation has a 90-second elapsed budget; completed dates persist, and `next_date` identifies where to resume after a partial read. Inspect `partial` and `failure` rather than treating a normal command exit as complete coverage. The index is reusable across companies; scoring never launches a full multi-year calendar scan.

A public score refreshes at most seven recent calendar dates and selected report/ancestor dates, with a combined maximum of 21 daily-list reads, 256 indexed issuer documents, 11 filing ZIP reads and a 90-second request budget. Recent-date refresh has a nested 10-second limit; selected-date refresh has a nested 20-second limit, reserving time for financial work. Parsed filings are checkpointed by complete filing metadata, raw ZIP hash and mapping version. Repeating a query schedules unfinished filings first and still validates raw-source freshness before reusing a checkpoint. `source_coverage.progress` exposes required dates, acquired/missing periods, failed stages and the resume document; unlocated history remains a separate gap. Network phase timeouts shrink to the remaining budget; no new read starts after expiry. Existing response/archive/XML limits remain in force. Cached daily lists, filings and the JPX table have six-hour TTLs. Cache hits count toward the request's read bounds too. The budget stops further work between reads and body chunks; an in-flight network phase (at most 20 seconds), bounded parsing and local SQLite cleanup may finish after that deadline.

`source_coverage.history` exposes indexed and unindexed days, bounded missing-date ranges, capture timestamps and `selection_scope="indexed_submission_dates_only"`. Old index captures are not proof of current disclosure availability. Selected submission dates are re-read through the cache; refresh failures prevent evaluating those periods. Missing dates can hide reports or later corrections, so `latest_amendment_guaranteed` remains false even when the requested score inputs are complete. No publisher point-in-time availability is claimed.

## Report selection and annual periods

Annual roots and corrections must have consistent issuer/security identities, periods and submission ordering. Corrections follow `parentDocID`; cycles, missing ancestors, conflicting periods, competing annual roots or tied submission times block evaluation. A correction may inherit absent report-period metadata from its linked annual root, but its XBRL DEI dates and individual fact contexts must still agree.

The most recent linked, publicly available correction in the index replaces the whole annual document. A selected correction without XBRL blocks that period. Missing values in a correction are never overlaid with values from the original report. Withdrawn/unavailable documents are retained as selection evidence and excluded. `filing_selection` includes candidate metadata, selected document IDs, selection reasons and normalization failures.

Each selected report must declare one supported accounting standard, consolidated statements, the matching EDINET identity and exact fiscal dates in DEI metadata. Only complete twelve-calendar-month periods are normalized. Short/long transition periods after a fiscal year change remain unavailable; nonconsecutive histories cannot earn longitudinal points. Same-date conflicting report periods are not silently overwritten.

Opening equity comes from the exact instant immediately before **each** annual start in that same selected filing. It has a separate input ID, so a restated comparative balance cannot overwrite another report's closing equity. Missing opening equity is not replaced by closing equity or an older filing. Comparative income/cash-flow values are not used to manufacture missing annual reports.

## Supported financial mapping

Mapping version: `edinet_consolidated_annual_v2`. The following core mappings use exact, namespace-qualified tags from `jppfs_cor` (Japan GAAP) or `jpigp_cor` (IFRS). Exact revenue summary aliases, narrowly scoped Toyota extensions and reviewed note extraction are described in the [version 2 registry](japan-buffett-v2.md). Namespace prefixes may vary; the EDINET namespace family must match. Annual duration facts and instant balances remain distinct. DEI metadata uses `jpdei_cor`.

| Input                      | Japan GAAP local tag                                                                         | IFRS local tag                                   |
| -------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `revenue`                  | `NetSales`                                                                                   | `RevenueIFRS`                                    |
| `net_income`               | `ProfitLossAttributableToOwnersOfParent`                                                     | `ProfitLossAttributableToOwnersOfParentIFRS`     |
| `operating_income`         | `OperatingIncome`                                                                            | `OperatingProfitLossIFRS`                        |
| `operating_cash_flow`      | `NetCashProvidedByUsedInOperatingActivities`                                                 | `NetCashProvidedByUsedInOperatingActivitiesIFRS` |
| `capital_expenditure`      | `PurchaseOfPropertyPlantAndEquipmentInvCF`                                                   | `PurchaseOfPropertyPlantAndEquipmentInvCFIFRS`   |
| `stockholders_equity`      | Explicit sum of `ShareholdersEquity` and `AccumulatedOtherComprehensiveIncome`               | `EquityAttributableToOwnersOfParentIFRS`         |
| `assets`                   | `Assets`                                                                                     | `AssetsIFRS`                                     |
| `liabilities`              | `Liabilities`                                                                                | `LiabilitiesIFRS`                                |
| `interest_expense`         | `InterestExpensesNOE` or `InterestExpensesOpeCF`, only with consistent equivalent candidates | `InterestExpensesOpeCFIFRS`                      |
| `stock_based_compensation` | `ShareBasedCompensationExpensesOpeCF`                                                        | `ShareBasedPaymentExpensesOpeCFIFRS`             |

Japan GAAP parent equity requires both reported components in one context; missing OCI is not assumed zero. This keeps net income and equity attributable to owners of the parent. Group net assets and total group profit are not substitutes. Only the explicitly registered revenue aliases and issuer extensions are accepted. The equity sum and all exact operands are returned as a normalization calculation.

Capex is **PP&E cash purchases**, matching the existing rubric. The supported cash-flow tags carry signed cash outflows: Atlas negates a nonpositive reported amount into nonnegative spending, retaining the original signed value and formula. A positive value is not silently converted with an absolute value. Combined PP&E/intangible purchases, net investing cash flow, interest paid and broad finance costs are not substitutes for the requested concepts. IFRS cash-flow classification differences are not reclassified; cross-company comparability needs review.

Amounts must use one undivided ISO 4217 JPY measure. The XBRL amount itself is in yen; `decimals` describes precision and never multiplies the amount. Inline `scale`/`sign` attributes in an original XBRL instance are rejected. Missing/nil values, unsupported precision/units, conflicting facts, mismatched contexts, entity branches and unsupported dimensions remain unavailable with reasons. Equal duplicates with matching semantic contexts, units and values are counted once; all original locations remain available. Consolidated defaults are accepted only after DEI confirms consolidated statements; standalone and segment facts cannot fill gaps. Verified equivalent tag aliases share a canonical definition ID across years. Changed definitions, attribution scopes or accounting standards make affected longitudinal components unavailable.

This is a conservative initial registry, not complete taxonomy harmonization. Other standards, standalone-only issuers, unregistered custom extensions, sector-specific revenue tags and unsupported historical taxonomy representations can yield partial or entirely unavailable scores. [FSA taxonomy documentation](https://www.fsa.go.jp/search/EDINET_Taxonomy_All.html) · [Raw EDINET reader and source terms](japan-companies.md).

## Common response for Web clients

Render the server's result; do not recalculate scores in the browser. An abbreviated **fictional** response from `9999.T`, `country="JP"`, `count=3`, `provider="fixture"` is:

```json
{
  "dataset": "buffett_score",
  "is_synthetic": true,
  "input_origin": "edinet_primary_annual_facts",
  "records": [
    {
      "identifier": "9999.T",
      "currency": "JPY",
      "score": null,
      "quality_score": "80.000000000000000000",
      "known_points": "80.000000000000000000",
      "available_weight": "80",
      "unevaluated_weight": "20",
      "score_bounds": {
        "lower": "80.000000000000000000",
        "upper": "100.000000000000000000"
      },
      "missing_inputs": {}
    }
  ]
}
```

The full response also contains:

| Field                              | Display purpose                                                                                                                                      |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `records[0].components`            | Each fixed weight, achieved points or null, formula, exact input IDs and unavailable reason; absent valuation leaves 20 points unevaluated.          |
| `records[0].reported_inputs`       | Values, JPY units, actual periods, accounting standard, consolidation, attribution scope, filing ID/time, original facts and normalization operands. |
| `reported_inputs[*].fact_inputs`   | Original tag, namespace, context/entity/dimensions, unit definition, decimals, reported value, raw URL/hash, ZIP member/hash and XML position.       |
| `reported_inputs[*].filing_source` | Original daily-list hash and JSON pointer linking the fact to its filing metadata.                                                                   |
| `records[0].missing_inputs`        | Stable input IDs with missing/ambiguous/unsupported reasons. Component reasons also describe missing years and unavailable valuation.                |
| `issuer` / `filing_selection`      | Resolved EDINET/security codes, current JPX classification evidence, exclusions and why reports were selected or rejected.                           |
| `source_coverage` / `coverage`     | Search/read limits, history gaps, source failures, requested/available years and explicit non-PIT status.                                            |
| `supplied_documents`               | Canonical user-supplied financials or valuation, kept distinct from publisher facts.                                                                 |

Keep nulls visible, show the evaluated and unevaluated weights, and never rescale partial results to 100. Compact evidence IDs and field projection preserve the score's inputs and interpretation. `is_synthetic` remains essential: an EDINET-shaped fictional fixture is not an actual filing.

## JPY valuation and limitations

The optional `valuation` request object, or CLI `--valuation-input` file, uses the existing explicit market-capitalization input. It requires `is_synthetic`, a positive whole-equity `market_cap` decimal string, `currency="JPY"`, `observed_on` and a timezone-aware `captured_at`. Growth, discount, terminal-growth and projection-year assumptions use the existing bounded model. The observation date cannot precede the selected reports' submission dates. A same-day automatic Japanese valuation additionally requires a timezone-aware `observed_at` at or after submission, no later than `captured_at`; these checks do not prove historic public availability or the supplied price.

Market capitalization and assumptions remain `operator_supplied` with `publisher_values_verified=false`. No stock price/share multiplication, split adjustment, currency conversion or automatic valuation lookup is performed. Missing financial inputs may also prevent valuing a supplied market cap. No trade, ranking guarantee, return probability, Buffett endorsement or verified investment performance is implied.

Web operators provide their own authenticated backend and source-access permissions using the [HTTP configuration](self-hosting.md). The index and API must share private persistent state. No deployment, release, public financial-data fixture or hosted index is included.
