# Japanese scoring definitions and Web integration

Automatic `country="JP"` scoring uses mapping `edinet_consolidated_annual_v2` and model `buffett_quantitative_jp_disclosures_v2`. The existing ten component weights, thresholds, arithmetic and missing-point policy are reused. A separate model ID identifies the expanded Japanese disclosure definitions, including note-reported expenses; it does not imply that an earlier score is numerically comparable. US and supplied-financial-input requests keep `buffett_quantitative_v1`.

**Live EDINET normalization and complete real-issuer 80/100-point coverage remain unverified.** The current environment has no configured EDINET key. The changes below have fictional structural regression tests and separate primary-document text review. Do not label either as a successful authenticated API audit. [Verification evidence](verification.md#japanese-edinet-company-scoring).

## Mapping and comparisons

The [core registry](japan-buffett.md#supported-financial-mapping) is supplemented by these explicit revenue mappings:

| Standard    | Namespace family / local concept                                         | Scope                                                          |
| ----------- | ------------------------------------------------------------------------ | -------------------------------------------------------------- |
| Japan GAAP  | `jppfs` / `NetSales`; `jpcrp` / `NetSalesSummaryOfBusinessResults`       | Exact annual consolidated group revenue                        |
| IFRS        | `jpigp` / `RevenueIFRS`; `jpcrp` / `RevenueIFRSSummaryOfBusinessResults` | Exact annual consolidated group revenue                        |
| Toyota IFRS | issuer extension / `TotalNetRevenuesIFRS`                                | E02144 / 72030, fiscal ends 2024-03-31 through 2026-03-31 only |

Toyota's namespace must exactly equal `http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/{fiscal_end}/01/{submission_date}`. Other issuer identities, namespace hosts, branches, years and similarly named concepts are rejected. Corrections retaining a different original namespace are not implicitly accepted. This intentionally narrow rule requires live XBRL corroboration before broader historical support is claimed.

All aliases pass the same entity, period, consolidation and dimension checks. Standalone and segment facts are not alternatives to the whole consolidated group. Verified aliases share a `definition_id` and `comparability_key`; a raw tag or taxonomy-year change alone does not break a longitudinal comparison. Changes to the accounting standard, entity/attribution or canonical definition still do. Source tag names and namespaces remain in the evidence.

Equivalent repeats must agree on semantic concept/operand, entity and scheme, actual period, dimensions, expanded monetary unit and exact Decimal value. Context/unit IDs and namespace prefixes may differ without changing meaning. Precision is retained separately: `decimals` never scales yen. Values that disagree are unavailable even if rounding might make them appear equal. A default consolidated context and an explicit dimensional context are not automatically collapsed. Metadata repeats must also agree.

`fact_inputs` and `source_locations` preserve every occurrence. `normalization.operands` identifies the unique operands used in arithmetic; consumers must not sum every repeated fact. Japan GAAP parent equity still requires both shareholder equity and accumulated OCI, with no zero substitution. XML order never chooses a winning value.

## Cash purchases and expense evidence

CapEx means **gross cash purchases of PP&E, including assets purchased for rental to customers**. It excludes intangible purchases, asset-sale proceeds, net investing cash flow, noncash right-of-use additions and lease principal payments. Disposal proceeds are not netted. The displayed spending, FCF criteria, capital-intensity ratio and DCF cash-flow proxy all use the same amount.

For Toyota, the exact Japanese labels `有形固定資産の購入＜賃貸資産を除く＞` and `賃貸資産の購入` define two required operands. Matching uses only NFKC/whitespace normalization, within the issuer/year/namespace restriction above. The original issuer XSD must declare monetary duration concepts, and the original Japanese standard-label linkbase must link those concepts to those exact labels. Schema and label hashes, XML positions and locator links are retained. This is explicit definition matching, not similarity matching of a tag name. At most 64 schema/label members are inspected under the source deadline. Unknown types, incomplete components or contradictory metadata remain unavailable.

The two signed cash outflows are negated and summed once. A separately reported total, when present, must agree and is corroborating evidence rather than another additive operand. Positive nonzero purchase amounts are not converted using an absolute value. Different component contexts are rejected.

Interest expense is distinct from cash interest paid and broad finance costs. Numeric expense addbacks retain `measurement_bases`; `InterestPaid` is not an alias. Stock compensation CF reconciliation adjustments are distinguished from current P&L expense, grant-date values, outstanding awards and liabilities. No missing amount is zero-filled.

The narrow Toyota note rules recognize the reviewed consolidated wording for total interest expense including financial operations and current stock-compensation expense. They retain the original XBRL text block and its context, a hash of the escaped HTML text, the matched normalized text span, the exact quote, current/prior period binding, JPY display unit and `reported_note_amount * 1000000`. The expense definition remains distinct from the CF-adjustment definition; mixed definitions across years cannot silently satisfy a longitudinal criterion. Numeric and note amounts that disagree remain unavailable.

A table fallback is limited to `ConsolidatedStatementOfCashFlowsIFRSTextBlock`, an explicit consolidated statement heading, million-yen units, matching current/prior years in that same table and the two exact purchase rows. It preserves physical table, row and cell coordinates, HTML character positions, original values and conversion operands. Spanned or unrecognized layouts fail closed. General/segment blocks, PDF/search text and browser-submitted values are not this extraction route. The documented live document shapes have not yet been tested against downloaded EDINET ZIPs.

## Retrieval and resumption

The 90-second query budget, 21 daily-list reads, 11 filing reads, 256 issuer documents and archive/body bounds remain. Recent-date refresh is limited to ten seconds; selected submission-date refresh to twenty. A private normalization checkpoint is keyed by complete filing metadata, ZIP hash and mapping version. Raw sources still pass the normal six-hour cache/revalidation path. A changed selected amendment, list evidence, ZIP or mapping cannot reuse an incompatible checkpoint.

Repeat the same request after interruption. Unfinished filings are scheduled first; parsed checkpoints avoid repeating completed normalization. Checkpoints are bounded to 256 entries / 64 MiB, with an 8 MiB per-entry limit, and do not reset the raw source cache. An oversized parsed result can still be returned without being checkpointed.

`source_coverage.progress` reports required submission dates, acquired and missing period ends, reused normalized periods, unlocated annual-period count, failed stages and a `resume` action/document/period. Acquisition does not mean all financial inputs were available. `financial_inputs_complete` and `amendment_search_complete` remain separate. Missing index dates can conceal amendments; `latest_amendment_guaranteed` is always false. No complete amendment history or historical PIT evaluation is claimed.

## Web request and response changes

SDK, CLI, REST and MCP continue to call the same service. For example:

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    result = atlas.get_buffett_score("7203.T", country="JP", years=3)
```

```sh
uv run atlas research buffett 7203.T --country JP --years 3 --json
```

HTTP `POST /v1/research`, or MCP `atlas_get_research`'s `query`, takes:

```json
{
  "dataset": "buffett_score",
  "identifier": "7203.T",
  "country": "JP",
  "count": 3
}
```

The server alone holds `EDINET_API_KEY` and persistent `ATLAS_STATE_DIR`. The existing explicit JPY `valuation` input adds optional timezone-aware `observed_at`. For an automatic Japanese quote on the submission date, this timestamp is required and must be at or after the latest selected submission and no later than capture. Later observation dates continue to work without it. Currency/date checks remain, and intraday chronology checks do not establish historical public availability. DCF assumptions and market cap remain unverified user inputs; no price/share/split multiplication is introduced.

Web consumers need these additive changes; they must not reimplement the score:

| Field/type                                       | Handling                                                                                                      |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `model_version`, `normalization_version`         | Display and retain both; do not combine model versions in rankings.                                           |
| `valuation.observed_at: string \| null`          | Supply an explicit offset/time for a same-day Japanese observation; show server validation errors.            |
| `reported_inputs[*].evidence_type`               | Support `numeric_xbrl`, `xbrl_note_text`, `xbrl_note_table`; no `unitRef` is invented for a text block.       |
| `definition_id`, `measurement_bases`             | Show the economic definition and expense/CF distinction.                                                      |
| `source_locations`, `fact_inputs`, `note_inputs` | Render all source positions and note display-unit conversions. Numeric facts additionally retain `raw_value`. |
| `normalization.operands`                         | Explain unique operands and duplicate consolidation; never add every repeated location.                       |
| `source_coverage.progress`                       | Show fetched/missing years, stage failures and the server's resume target separately from index coverage.     |
| Null scores / missing reasons                    | Preserve `quality_score`, `score`, evaluated/unevaluated weights and bounds without rescaling.                |

A **fictional** `9998.T` / `provider="fixture"` three-year result illustrates the unchanged coverage contract:

```json
{
  "model_version": "buffett_quantitative_jp_disclosures_v2",
  "quality_score": "80.000000000000000000",
  "quality_max_points": "80",
  "score": null,
  "max_points": "100",
  "known_points": "80.000000000000000000",
  "available_weight": "80",
  "unevaluated_weight": "20"
}
```

With sufficient financial coverage and a valid explicit valuation, `score` can become non-null; it need not equal 100. The fixture's supplied one-trillion-yen scenario evaluates all 100 points and earns 100. This is not a Toyota or Nintendo valuation. When financial coverage is incomplete, neither a supplied price nor a missing-weight redistribution completes the quality score.

## Reproducible live audit

Use a new private output directory. The optional submission dates seed only those dates, not a complete amendment search:

```sh
uv run --offline python scripts/verify_edinet_score_live.py \
  --allow-network --identifier 7203.T --years 3 \
  --submission-date 2024-06-25 --submission-date 2025-06-18 \
  --submission-date 2026-06-10 --output build/toyota-edinet-audit
```

Add `--valuation-input path/to/private-valuation.json` for the existing explicit scenario. The script uses an isolated private cache, compares cold/warm financial reads, resolves source/member hashes and XML positions, checks duplicate arithmetic and note conversions, and saves complete inputs, components and coverage. An empty/partial result is not PASS; an absent key is BLOCKED. A PASS with valuation omitted only establishes checked 80-point input coverage, and still does not establish complete amendment discovery. Private artifacts and actual financial values must not be published as fixtures.
