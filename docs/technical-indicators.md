# Technical indicators

Atlas calculates **23 trailing indicators** through the shared `price_analytics` service. CLI, Python SDK, MCP and HTTP use the same formulas. An offline export applies those calculations to a complete [multi-year stock history](yahoo-finance.md#multi-year-stock-history), preserving every date and the original source bytes.

## Available calculations

| Family                     | Metric names                                                      | Definition                                                                                                                                           |
| -------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Simple moving average      | `sma_5`, `sma_20`, `sma_25`, `sma_50`, `sma_75`, `sma_200`        | Arithmetic mean of the last N prices; N observations required                                                                                        |
| Exponential moving average | `ema_12`, `ema_20`, `ema_26`, `ema_50`                            | First N-price SMA seed, then `EMA[t] = EMA[t-1] + 2/(N+1) * (P[t]-EMA[t-1])`                                                                         |
| RSI                        | `rsi_14`                                                          | Wilder's 14-change smoothed mean gains/losses; `100*gain/(gain+loss)`; 15 prices required                                                            |
| Bollinger bands            | `bollinger_middle_20`, `bollinger_upper_20`, `bollinger_lower_20` | Middle = SMA20; upper/lower = middle ± twice the **population** standard deviation of those 20 prices                                                |
| Bandwidth                  | `bollinger_bandwidth_20`                                          | `(upper-lower)/middle`, expressed as a ratio                                                                                                         |
| Percent B                  | `bollinger_percent_b_20`                                          | `(P-lower)/(upper-lower)`, expressed as a ratio; it can be outside 0–1                                                                               |
| MACD                       | `macd`, `macd_signal`, `macd_histogram`                           | EMA12 − EMA26; its SMA-seeded EMA9 signal; MACD − signal. First outputs require 26/34/34 prices                                                      |
| Returns and risk           | `return_1d`, `cumulative_return`, `volatility_20`, `drawdown`     | Preceding-observation return, return from the selected start, sample standard deviation of 20 returns, and decline from the selected running maximum |

RSI is 100 with gains and no losses, 0 with losses and no gains, and 50 for flat prices. A flat Bollinger window has zero bandwidth and unavailable percent B because its denominator is zero. Bandwidth and percent B are **ratios**, while RSI uses the 0–100 scale. Volatility is per observation and is not annualized.

Calculations use a private Decimal context with 256-digit precision and return decimal strings rounded half-even to 18 decimal places. This describes computational rounding, not upstream accuracy or price-tick precision.

## Select the price basis

`price_field` accepts `adjusted_close` (the existing default) or `close`. Yahoo security close is already split-adjusted; adjusted close additionally reflects the source's dividend adjustment. Neither is independently verified original unadjusted trade data. The chosen field's exact value, basis and JSON pointer are retained; a missing value never falls back to the other field.

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get price_analytics --identifier 7203.T --start-date 2021-01-01 --end-date 2026-11-06 --price-field close --metrics sma_5,sma_25,sma_75,bollinger_middle_20,bollinger_upper_20,bollinger_lower_20,rsi_14,macd,macd_signal,macd_histogram --limit 500 --json
```

Submit the equivalent query to `Atlas.get_research`, MCP `atlas_get_research` or HTTP `POST /v1/research`:

```json
{
  "dataset": "price_analytics",
  "identifier": "7203.T",
  "start_date": "2021-01-01",
  "end_date": "2026-11-06",
  "price_field": "close",
  "metrics": [
    "sma_25",
    "bollinger_middle_20",
    "bollinger_upper_20",
    "bollinger_lower_20",
    "rsi_14"
  ],
  "limit": 500
}
```

Public reads require operator-enabled personal-use Yahoo access. The result limit counts **metric rows**: five metrics over 100 dates already use 500 rows. Inspect truncation. Selected source prices remain in `price_inputs`, including warm-up history; no earlier prices are fetched. Direct response-size, date-span and input-array bounds remain in force.

## Calculate a saved history offline

Use a completed `price-history` directory, including its manifest, CSV, annual results and raw responses. The exporter verifies hashes and replays the original source parser before calculation. Incomplete, changed or inconsistent inputs are rejected.

```sh
uv run --offline atlas research technicals --input build/toyota-history --output build/toyota-technicals --price-field close --json
```

No source enablement or network access is needed for this local calculation. The default bundle contains SMA5/25/75, the three Bollinger20 lines, RSI14 and the three MACD values. Use `--metrics sma_20,sma_50,sma_200,ema_20,ema_50,rsi_14` or another unique selection from the table. Choose a new output directory; existing files are never overwritten.

```python
from pathlib import Path
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    result = atlas.export_technicals(
        Path("build/toyota-history"),
        output=Path("build/toyota-technicals"),
        metrics=["sma_25", "bollinger_upper_20", "bollinger_lower_20", "rsi_14"],
        price_field="close",
    )
```

The private output contains:

- `technicals.csv`: one ascending row per original date, the chosen price and each selected indicator. Unavailable cells are blank and `unavailable_metrics` names them.
- `calculations.jsonl`: every metric/date value, status, unavailable reason, formula and input range, including warm-up rows.
- `price_inputs.json`: the shared input table with exact price/date source pointers. Indices are inclusive and global across the history.
- `raw/`: original annual source bytes needed to resolve those pointers.
- `manifest.json`: price basis, formulas, counts, file hashes, source mappings, completion state and limitations.

Annual boundaries do **not** restart EMA, RSI or MACD. A missing, nonpositive or source-ambiguous chosen price restarts rolling and recursive warm-up. Cumulative return and drawdown remain unavailable after such a gap because they start at the selected beginning. Missing calendar dates are not inserted; future observations never enter calculations. Warm-up rows remain unavailable rather than zero: SMA75 requires 75 valid prices, and RSI14 requires 15.

Local input is bounded to 30 annual windows, 64 MiB of source-history files and 11,000 prices; calculation JSONL is bounded to 256 MiB. Revisions, source adjustments and exchange-session completeness remain unverified. `cross_window_adjustment_verified=false` is retained; indicators do not repair query-dependent source differences or establish publisher PIT data.

The [validation record](verification.md#technical-indicators-and-offline-history-calculations) separates fictional regressions, calculations over captured actual prices, installed-package checks and GitHub CI.
