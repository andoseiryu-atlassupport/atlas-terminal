# CPI effects and relative importance

`cpi_effects` supplies monthly CPI-U expenditure effects for the U.S. city average through the shared research service. SDK, CLI, REST and MCP use the same parser, calculations and evidence. Existing `economics` series IDs and `macro_analytics` changes retain their existing behavior.

**Live verification remains incomplete.** The 2026-11-11 attempt could not retrieve the BLS archive through the normal proxy/TLS route. Offline fixtures establish regression behavior, not successful parsing of current official HTML or agreement with actual BLS numbers. See [verification](verification.md#cpi-effects-and-relative-importance).

## Measures and scope

| Field                     | Meaning                                                                   | Unit                     |
| ------------------------- | ------------------------------------------------------------------------- | ------------------------ |
| `rate_pct`                | Change in that item's price index                                         | Percent                  |
| `effect_pt`               | Effect on the explicitly selected target                                  | Percentage points        |
| `relative_importance_pct` | Published relative importance in **all-items** CPI at `weight_base_month` | Percent                  |
| `contribution_share_pct`  | `100 × effect_pt / target_rate_pct`, using displayed values               | Percent of target change |

Numeric values are decimal strings or `null`. Printed spellings remain in `reported_inputs.*.raw_value`; calculations use Python `Decimal` with 50-digit working precision. `contribution_share_pct` is an optional calculated diagnostic, **not** the height of a stacked bar. It is null when the displayed target change is zero. Individual effects can remain nonzero and offset one another when the aggregate change is zero.

| Target      | Change | Adjustment | Method                                                                |
| ----------- | ------ | ---------- | --------------------------------------------------------------------- |
| `all_items` | `mom`  | `sa`       | Reported supplemental Table 6                                         |
| `all_items` | `yoy`  | `nsa`      | Reported supplemental Table 7                                         |
| `core`      | `mom`  | `nsa`      | Calculated from monthly Table 1 indexes and relative importance       |
| `core`      | `yoy`  | `nsa`      | Chain twelve checked monthly effects, including annual weight changes |

Other combinations return `status: "unsupported"`, a reason and no observations; they never switch adjustment, period or target. Core SA is unsupported because seasonal aggregation weights and revision vintages have not been verified. All-items NSA MoM and SA YoY are outside the supported analysis-table combinations. A Table 6 item marked “Not seasonally adjusted” keeps its official SA-target effect, but its requested SA `rate_pct` is null; the original NSA rate and reason remain visible. An all-items row's dash in the effect column remains null.

## Requests

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    result = atlas.get_cpi_effects(
        start_month="2024-12",
        end_month="2025-02",
        category_set="all_items_basic",
        change="mom",
        seasonal_adjustment="sa",
        target="all_items",
    )
```

```sh
uv run atlas research cpi-effects \
  --start-month 2024-12 --end-month 2025-02 \
  --category-set all_items_basic --change mom \
  --seasonal-adjustment sa --target all_items

uv run atlas research cpi-effects \
  --start-month 2025-01 --end-month 2025-01 \
  --category rent_cpi --category owners_equivalent_rent_cpi
```

The command emits the shared JSON response. REST `POST /v1/research`, MCP `atlas_get_research({"query": ...})`, SDK `get_research(...)`, and CLI `research run --input query.json` accept:

```json
{
  "dataset": "cpi_effects",
  "start_month": "2024-12",
  "end_month": "2025-02",
  "category_set": "core_basic",
  "target": "core",
  "change": "mom",
  "seasonal_adjustment": "nsa"
}
```

`categories` is a list of explicit catalog keys and is mutually exclusive with `category_set`. Omitting both selects the target's basic set. Supply both month boundaries or neither; omitting both selects the latest **linked dated HTML edition**, not the latest nonmissing value. Requests accept at most 36 consecutive months, from 1994 through 2100. This is an input bound, not an assertion of source availability. Core calculations start with January 2023 MoM / December 2023 YoY, conditional on all required source inputs and checks.

`GET /v1/research/catalog?dataset=cpi_effects` and `Atlas.research_catalog(dataset="cpi_effects")` expose the combination matrix, definitions, categories, parent relationships, stacking sets and query templates. `topic=cpi&breakdown=effects` discovers the four supported templates without network access.

## Classification and stacking

`all_items_basic` contains `food_cpi`, `energy_cpi`, `core_goods_cpi`, `core_services_cpi`. `core_basic` contains the last two. These are the two verified complete classification sets. Thirteen mapped categories also include food at/away from home, gasoline, shelter, primary rent, owners' equivalent rent and education. Each observation contains its BLS item code and NSA series identifier; the series identifier is metadata and does not imply a BLS API request.

Parent/child pairs such as shelter/rent, energy/gasoline and core/core goods overlap. Education spans goods and services; intersections with detailed core selections are conservatively marked unverified. Rent and OER can be displayed together as a **partial** shelter selection, but do not exhaust shelter. Only a declared complete set has `covers_target: true`. Median and trimmed-mean measures are rejected as expenditure categories.

Each month's `stacking` includes:

- `classification_stackable`, `non_overlapping`, `overlaps`, `outside_target`, `reasons`;
- `covers_target`, which describes classification scope independently of numeric availability;
- `missing_categories` and `value_reasons`;
- `ready`, which requires nonoverlapping, in-target categories and present effects;
- `complete`, which additionally requires complete target coverage.

Missing components never become zero or a synthetic “other” bar. `reconciliation` retains the component sum, target rate, residual, and rounding bounds. No value is altered to close the residual. A complete reported set may still have a difference exceeding rounding bounds, including differences associated with seasonal aggregation; display the difference and check its status instead of silently balancing the chart.

## Calculated core effects

The implementation follows the [BLS effect method](https://www.bls.gov/cpi/factsheets/contributions-effects.htm) and [relative-importance guidance](https://www.bls.gov/cpi/tables/relative-importance/home.htm). It does not divide a published all-items effect by the latest core weight. For each month `m`, let `RI` be that month's printed beginning-period relative importance, and `I` the NSA index levels in the same release:

```text
monthly_effect_i,core = (RI_i,m-1 / RI_core,m-1) × (I_i,m / I_i,m-1 − 1) × 100
period_effect_i,core  = Σ_m monthly_effect_i,core × I_core,m-1 / I_core,start
```

The RI ratio expresses a core expenditure share; the returned **published** RI itself remains relative to all items. The formula is the fixed-weight cost-change formula with a normalized cost base, so absolute dollar cost weights are not required. It never uses rounded published rates as calculation operands and avoids division by the aggregate's price change.

Monthly linking uses every intermediate month's printed weights and indexes. At a January pivot, the preceding December RI must also match the explicitly identified **new** annual expenditure-weight table, within printed rounding intervals. The annual schedule starting in 2023 uses spending from two years earlier. The old-vintage table is never selected by HTML order. Missing pivots, index gaps, conflicting overlapping index values across releases, and earlier schedules produce missing effects with reasons.

For YoY calculations, the final release's year-ago indexes must also match the starting indexes of the monthly chain. A missing or corrected annual baseline leaves the effect unavailable; a conflicting baseline retains its source cell for inspection.

For every month, both core-goods and core-services effects must reconcile with the core NSA index change within intervals derived from half a unit of each input's last printed decimal. Failed reconciliation suppresses the calculated effect while retaining available operands and the failed check. The final period's sum is also compared with the published target rate. Intervals are conservative independent rounding bounds, not statistical confidence intervals; Decimal arithmetic is finite precision. Computed outputs are explicitly **estimates from published rounded inputs**, not BLS production effects. Exact agreement with unrounded BLS calculations is not claimed.

`mapping_version: "bls-cpi-effects-v1"` identifies this method and classification mapping. `calculation_inputs` and `calculation_checks` are content-addressed dictionaries. A component's `calculation.inputs[].items.*.input_id` and `monthly_reconciliation[].check_id` resolve into those dictionaries, avoiding repetition of identical source cells across annual windows. Each cell retains its original text, source and value.

## Source history and coverage

The service discovers dates from the [official CPI release archive](https://www.bls.gov/bls/news-release/cpi.htm), reads the linked dated HTML releases containing Tables 1, 6 and 7, and reads explicitly dated annual RI tables when needed. The [supplemental-files directory](https://www.bls.gov/cpi/tables/supplemental-files/home.htm) describes the same analysis tables; its XLSX/ZIP downloads and PDF/TXT-only releases are not parsed in this version. A missing HTML table is reported as unavailable, not reconstructed from another release or index series.

`coverage` distinguishes advertised HTML bounds, retrieved/parsed release bounds, available-effect bounds, requested months, dependency months, missing effects, unlinked HTML months and retrieval failures. These fields describe the current query and captured archive, not a preverified full historical database. Failure to retrieve the index leaves advertised bounds null and months unretrieved; it does not establish that BLS omitted publication. The supported actual first/last months remain unknown until a successful live run.

Each request permits up to 54 document reads within a 90-second source deadline, with a three-failure cutoff and the existing per-request retry/rate/size limits. One release supplies all requested categories; monthly documents and annual tables are memoized within the request. The existing conditional HTTP cache and retained source versions support subsequent queries without discarding earlier captures. The archive cache TTL is six hours and document TTL is one day. Remaining unqueried/failed months are explicit; retrying can reuse completed downloads. The common 4 MiB research-response limit still applies; narrow categories/months if needed.

These document reads do not call BLS API V1, spend its daily request allowance, reset usage, or revise existing BLS API observations. They use normal HTTPS, certificate verification and configured proxy behavior. Allow `www.bls.gov` for the document routes. No API key, paid data or LLM is required.

Every numerical source cell includes its URL, original release date (from the dated archive link), retrieval timestamp, raw SHA-256, table caption, row/column and decoded-HTML character offsets. Annual RI publication dates are null when absent from the parsed metadata. Retrieval time is not the observation month or release date. Archived copies may contain corrections; original-release fidelity and subsequent revision history are unverified. SA values are not silently mixed with the BLS API's later revised history.

The character view preserves valid UTF-8 and replaces isolated invalid bytes outside tables with U+FFFD. Invalid bytes inside any table still reject the document. `offset_encoding` describes this decoding policy; `decode_html` reconstructs the same character view for evidence checks. Source bytes and SHA-256 are unchanged.

## Web integration

Keep the service on the server and call the existing REST research endpoint; no browser BLS scraping or second scoring/calculation implementation is needed. Render the outer `records[].rate_pct` as the target line, and each nested `records[].records[].effect_pt` as a bar only after checking `stacking`. Use a shared percentage-point/percent-change scale, with separate negative and positive stacks. Show `relative_importance_pct`, `weight_base_month`, `weight_target`, and the reported/calculated status in the table.

Pin the backend source installation to the implementation commit identified by the PR; the existing published `v0.7.0` package does not include this addition. Refresh `schemas/research.schema.json` and `schemas/openapi.json` from that same revision. This change prepares the OSS service and schemas; it does not deploy a Web backend.

The required Web type additions are:

```ts
type DecimalValue = string | null;
type CPITarget = "all_items" | "core";
type CPIComponent = {
  month: string;
  category: string;
  bls_item_code: string;
  parent: string | null;
  target: CPITarget;
  change: "mom" | "yoy";
  seasonal_adjustment: "sa" | "nsa";
  rate_pct: DecimalValue;
  effect_pt: DecimalValue;
  relative_importance_pct: DecimalValue;
  weight_base_month: string | null;
  weight_target: "all_items";
  effect_status: "reported" | "calculated" | "unavailable";
  missing_reasons: Record<string, string | null>;
};
```

Retain the full response's `reported_inputs`, `field_sources`, `calculation`, input/check dictionaries, `coverage`, `reconciliation` and classification metadata. Validate the supported query matrix from the catalog. Treat HTTP 200 plus `status: "unsupported"` or `"unavailable"` as an empty/error-state chart, not successful data coverage. Convert a decimal to a plotting number only at the presentation boundary; preserve its string for the numeric table. Never coerce null to zero. A partial nonoverlapping set may be plotted only with a partial-coverage label. Display reconciliation warnings and gaps even when a bar set is otherwise ready.

This shortened **fictional** response excerpt can be reproduced with `Atlas("fixture").get_cpi_effects(start_month="2025-01", end_month="2025-01", target="core", seasonal_adjustment="nsa")`; it is not an official-data example:

```json
{
  "month": "2025-01",
  "target": "core",
  "rate_pct": "1.0",
  "records": [
    {
      "category": "core_goods_cpi",
      "effect_pt": "0.300",
      "relative_importance_pct": "24.000",
      "weight_base_month": "2024-12",
      "effect_status": "calculated"
    },
    {
      "category": "core_services_cpi",
      "effect_pt": "0.700",
      "relative_importance_pct": "56.000",
      "weight_base_month": "2024-12",
      "effect_status": "calculated"
    }
  ]
}
```

## Live verification

```sh
uv run --offline python scripts/verify_cpi_effects_live.py \
  --allow-network --output build/cpi-effects-YYYY-MM-DD/live
```

The opt-in script checks the latest linked month, August 2024, December 2024–February 2025, and core calculations across the annual pivot. It checks saved raw-cell positions/hashes and independently recomputes returned core formulas. Raw HTML, full responses, CSV and connection diagnostics stay under private ignored `build/`; no actual observations are public fixtures. No received rows or no usable effects cannot pass. A blocked archive stops dependent cases with an explicit “not attempted” status. A failed connection or fixture pass is never reported as real-data verification success.
