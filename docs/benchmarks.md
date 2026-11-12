# Common index benchmarks

`benchmark_catalog` and `benchmark_prices` use the same Python research service through the SDK, CLI, MCP and REST API. They identify an index independently of its data vendor, comparison currency and dividend treatment. Existing `equity_prices`, `fx_rates`, price analytics and factor-risk APIs keep their contracts.

## Catalog and coverage

| Benchmark ID       | Index                        | Native currency | Selected source             | History                           |
| ------------------ | ---------------------------- | --------------- | --------------------------- | --------------------------------- |
| `topix`            | TOPIX                        | JPY             | JPX historical index slots  | Five currently published sessions |
| `nikkei225`        | Nikkei 225                   | JPY             | International Yahoo `^N225` | Available daily observations      |
| `sp500`            | S&P 500                      | USD             | International Yahoo `^GSPC` | Available daily observations      |
| `nasdaq_composite` | NASDAQ Composite             | USD             | International Yahoo `^IXIC` | Available daily observations      |
| `dow_jones`        | Dow Jones Industrial Average | USD             | International Yahoo `^DJI`  | Available daily observations      |
| `nasdaq100`        | NASDAQ-100                   | USD             | International Yahoo `^NDX`  | Available daily observations      |

NASDAQ Composite and NASDAQ-100 are different selections. No ETF, futures contract or similar index substitutes for a selected benchmark. TOPIX is not mapped to an unverified Yahoo ticker.

The initial adapters support **`price_return` only**. `gross_total_return` and `net_total_return` remain explicit unsupported conditions, including TOPIX: the existence of a separate total-return cell does not establish a supported historical adapter. Requests fail before retrieval instead of using ordinary or adjusted close as a total-return proxy.

Both `JPY` and `USD` comparison currencies are accepted. Yahoo queries span at most ten calendar years, split into at most ten annual reads with no more than 500 source rows per year. The response returns the most recent `limit` rows (1–500); inspect `coverage.truncated` and split long exports into annual queries. Portfolio comparison can consume up to 500 supplied NAV observations and uses the full requested benchmark window, independent of output pagination or `limit`.

TOPIX uses only JPX's five rotating historical slots, with three bounded reads per slot. It verifies the exact TOPIX name and repeated price cells, and requires equal date-marker reads before and after the price read. The marker identifies an after-close edition; its update time is not an original quote timestamp. This detects observed rotation but is not a publisher-atomic snapshot guarantee. Older requested dates stay missing. A long TOPIX backtest needs a separately implemented historical source or permitted historical-data import; neither is supplied by this API. The current interface does not silently accumulate or reconstruct an older TOPIX series.

All source reads share the existing transport, source history, caching, limits and TLS/proxy settings. A request has a 120-second source budget. Index raw caches use exact vendor URLs; the ECB XML body is reused across annual subqueries. JPX rotating slots are revalidated rather than combined from independently fresh cached editions. Result snapshot fingerprints include the index ID, comparison currency, dividend treatment, date convention, date window and portfolio inputs. Raw source bytes can be shared across currency conversions without sharing normalized result identities.

## Calls

Catalog discovery makes no source requests:

```sh
uv run --offline atlas research get benchmark_catalog --json
```

Public Yahoo reads, including cached reads and cursor continuation, require the existing operator setting `ATLAS_ENABLE_YAHOO_FINANCE=1`. It enables optional personal-use international Yahoo access; it does not grant public redistribution rights. TOPIX uses JPX without that setting. ECB needs no API key. No paid account, wallet or LLM is required.

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run atlas research get benchmark_prices \
  --benchmark-id sp500 --benchmark-currency JPY \
  --dividend-treatment price_return \
  --start-date 2026-11-06 --end-date 2026-11-11 --limit 500 --json
```

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    catalog = atlas.benchmark_catalog()
    result = atlas.benchmark_prices(
        "sp500",
        start_date="2026-11-06",
        end_date="2026-11-11",
        benchmark_currency="JPY",
        dividend_treatment="price_return",
    )
```

The common query works with `Atlas.get_research`, CLI `research run --input`, HTTP `POST /v1/research`, and MCP `atlas_get_research` with `{"query": QUERY}`:

```json
{
  "dataset": "benchmark_prices",
  "benchmark_id": "sp500",
  "benchmark_currency": "JPY",
  "dividend_treatment": "price_return",
  "alignment": "utc_day_end",
  "start_date": "2026-11-06",
  "end_date": "2026-11-11",
  "limit": 500
}
```

An exact **fictional** fixture excerpt for S&P 500 on `2026-10-19` is:

```json
{
  "benchmark_id": "sp500",
  "benchmark_currency": "JPY",
  "dividend_treatment": "price_return",
  "date": "2026-10-19",
  "comparison_cutoff": "2026-10-19T23:59:59Z",
  "currency": "JPY",
  "native_currency": "USD",
  "unit": "JPY_valued_index_points",
  "index_value": "108.125",
  "fx_value": "120",
  "fx_date": "2026-10-19",
  "value": "12975.000",
  "status": "derived",
  "formula": "index_close * (quote_per_EUR / base_per_EUR)",
  "conversion_formula": "USD_index_close * JPY_per_USD"
}
```

The full record includes `inputs`: the original index close, source-local date, timestamp, metadata and JSON positions; and the FX cross rate with its original same-date ECB currency-per-EUR inputs and XML attribute positions. Every source retains its URL, retrieval time and SHA-256. `index_value` is an index level, not an amount of money. Converted index points provide a return-comparison level, not an investor portfolio NAV.

FX cross division uses the existing 28-significant-digit, half-even Decimal convention. The index multiplication preserves that rounded cross rate's exact product with 256-digit working precision. Raw decimal strings and formulas are retained. The API uses neither the newest FX quote for older prices nor a carry from the previous fixing day.

## Date alignment and comparison

`utc_day_end_v1` is a **retrospective end-of-UTC-day daily-mark comparison**. The date key is the published exchange-local session date, interpreted at `23:59:59 UTC` for comparison. US and Japanese closes and ECB fixings occur at different instants. Index bar timestamps remain original source timestamps and are not relabeled as closing or publication times. This convention does not establish historically available information or permit using that day's US close at the earlier Tokyo close.

- An index close and an FX rate must have the exact same observation date. Missing FX makes the converted value `null` while keeping the original index value and missing-input reason.
- `coverage.missing_dates` reports each calendar date without a usable selected value. Missing source rows are labeled `no_index_observation_non_session_or_missing`, since the adapter does not verify an exchange calendar. Weekends are not inserted as zero returns.
- No forward fill, backfill, interpolation or same-name proxy is applied. `available_start/end`, requested bounds and `coverage.comparison_period` distinguish source coverage from usable converted observations. An empty result has zero usable values and is not a successful live verification.
- `comparison.comparison_period`, when present, describes the actual paired portfolio-return intervals. Its bounding dates may contain excluded intervals; inspect their reasons and counts.

An optional `portfolio` object supplies daily **unitized NAV with external flows removed**, currency, dividend treatment, valuation basis, observations and an optional risk-free assumption. These are unverified caller inputs; account balances containing deposits/withdrawals must not be treated as NAV. Currency and dividend treatment must match the benchmark selection. At least two unique ascending dates, inside the requested window, are required. `value: null` remains missing.

```json
{
  "currency": "JPY",
  "dividend_treatment": "price_return",
  "valuation_basis": "utc_day_end",
  "value_basis": "unitized_nav_external_flows_removed",
  "sessions_per_year": 252,
  "risk_free_rate_annual": "0.01",
  "observations": [
    { "date": "2026-11-06", "value": "100" },
    { "date": "2026-11-09", "value": "101" },
    { "date": "2026-11-10", "value": "100.5" },
    { "date": "2026-11-11", "value": "102" }
  ]
}
```

Pass this as `portfolio=` to SDK `benchmark_prices`, as the query's `portfolio` field, or as bounded JSON in CLI `--portfolio`. The example NAV and risk-free rate are hypothetical.

`benchmark-comparison-v1` returns separate result groups:

| Group               | Metric                     | Definition                                                                                                 |
| ------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `portfolio_metrics` | `cumulative_return`        | `NAV[last] / NAV[first] - 1`                                                                               |
| `portfolio_metrics` | `cagr`                     | `(NAV[last] / NAV[first])^(365.2425 / elapsed_calendar_days) - 1`                                          |
| `portfolio_metrics` | `sharpe_ratio`             | `sqrt(sessions_per_year) * (mean(r) - rf_session) / sample_stddev(r)`                                      |
| `relative_metrics`  | `beta`                     | Sample covariance of paired portfolio/benchmark returns divided by benchmark sample variance               |
| `relative_metrics`  | `excess_cumulative_return` | Portfolio cumulative return minus selected benchmark cumulative return over the same complete interval set |

`r` uses consecutive supplied portfolio observations. `rf_session=(1+annual_effective_risk_free_rate)^(1/sessions_per_year)-1`; a missing risk-free input leaves Sharpe unavailable, rather than assuming zero. Zero variance and short samples have explicit reasons. Statistical results use 256-digit Decimal working precision and half-even output rounded to 18 decimal places. An extreme annualized result outside the documented numerical bound `abs(log(NAV[last]/NAV[first])*365.2425/days) <= 60` is unavailable.

Standalone metrics always use the same full supplied portfolio sample. Changing only the benchmark, its holidays or its missing FX observations cannot change portfolio CAGR or Sharpe. A missing portfolio value makes standalone whole-period summaries unavailable. The caller must declare complete daily NAV; omitted sessions cannot be inferred from a date vector, and sparse/monthly NAV is not daily NAV.

Relative metrics use consecutive **original portfolio endpoints** for which both benchmark values exist. They never compress the intersection into a new return across a missing portfolio interval. Beta may use a disclosed partial sample with at least two intervals and nonzero benchmark variance. Full-period excess remains unavailable unless every portfolio interval matches. `intervals`, `portfolio_inputs` and `benchmark_inputs` retain the calculation operands. Excess is benchmark-relative performance, not CAPM alpha or risk-free excess return.

## Web and saved-condition integration

1. Read `benchmark_catalog` to populate distinct index IDs, currencies, supported dividend conditions and history limitations.
2. Persist `benchmark_id`, `benchmark_currency`, `dividend_treatment` and `alignment` alongside dates and portfolio assumptions. `BenchmarkSettings` defaults an absent legacy selection to `topix` / `JPY` / `price_return` / `utc_day_end`. An explicit unsupported or unknown value is never replaced. This OSS checkout had no prior TOPIX-specific portfolio backtest database; Web-owned records must apply these defaults during their own migration.
3. Key Web caches by the complete canonical query, including these settings, dates, portfolio input version and calculation version. `selection_key` identifies only the four selection conditions and catalog version; it is **not** a complete result cache key.
4. Call the shared API. Render `portfolio_metrics` separately from `relative_metrics`; display coverage, missing reasons and the date convention. Keep decimal strings until chart rendering and retain exact values in tables/exports.
5. Render `null` as unavailable. Offer evidence from `inputs` directly or use compact responses and `atlas_get_evidence` / the existing evidence endpoint. Field projection preserves benchmark/currency/dividend/FX context. No Web-side scoring or FX reimplementation is needed.

The Python and JSON contracts are additive. Existing factor-risk `benchmark` position arrays remain separate and unchanged. All services are operator hosted; this change does not deploy a Web application or establish source-data redistribution permission.

## Verification

Normal tests are offline and fictional. Run:

```sh
uv run --offline pytest tests/test_benchmarks.py
```

For a separate live source-cell audit, choose a period overlapping the currently published TOPIX slots and a new private directory:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_benchmarks_live.py \
  --allow-network --start-date 2026-11-06 --end-date 2026-11-11 \
  --output build/benchmarks-live
```

`--resume` reuses that directory's existing source cache; it does not reset other source state. Captured bytes, hashes, complete responses and safe HTTP diagnostics remain private under `build/`. The script checks raw index/date cells, original ECB attributes and independent Decimal conversions. Its optional self-benchmark comparison uses an explicitly labeled S&P 500 JPY reference-level series, not an actual investor portfolio. See [dated results and limitations](verification.md#common-index-benchmarks).
