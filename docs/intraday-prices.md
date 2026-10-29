# Intraday price observations

`intraday_prices` adds explicitly selected recent Yahoo Finance intervals to the
shared research service. It uses the same optional personal-use source, private
cache, source history and limiter as [daily prices](yahoo-finance.md). It is
disabled unless the operator sets `ATLAS_ENABLE_YAHOO_FINANCE=1`.

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline atlas research get intraday_prices \
  --identifier AAPL --start-date 2026-10-28 --end-date 2026-10-28 \
  --interval 5m --limit 500 --json
```

Choose recent dates when running the public example. The fixture provider supports
the same AAPL `5m` example with fictional observations, and `7203.T` at `60m` on
2026-10-28. Fixture dates are stable and do not depend on today's date.

| Interval                  | Maximum inclusive date window | Conservative live lookback           |
| ------------------------- | ----------------------------- | ------------------------------------ |
| `1m`                      | 7 calendar days               | Latest 7 source-local calendar days  |
| `5m`, `15m`, `30m`, `60m` | 31 calendar days              | Latest 60 source-local calendar days |

Both dates and the interval are required. These client bounds do not promise
upstream retention. The same explicitly supported US/Tokyo securities and reference
symbols as the daily reader are accepted; metadata must match the expected symbol,
currency, exchange, timezone and asset class. Yahoo's `1h` metadata is accepted only
as the equivalent source spelling for a requested `60m` interval. There is no
resampling or fallback to a different interval.

## What a bar means

Each row retains its exact UTC `observation_at`, the source-local timestamp and UTC
offset in `observation_local`, the source-local `date`, interval, OHLC, volume and
original JSON pointers. Values are decimal strings; a zero volume remains zero and
nulls remain unavailable. The `price_basis` is `yahoo_reported_intraday`:
split/dividend adjustment and unadjusted trade-price identity are **unverified**.
No adjusted-close series or risk-model returns are manufactured from these bars.

The request sets `includePrePost=false`. Where Yahoo supplies regular-session
ranges, Atlas retains their provenance and uses those ranges to exclude terminal
quote markers and shorten the final interval at the reported session end. Otherwise
`session_verified=false` explicitly means no matching source range was available.
It does not certify an exchange calendar even when `session_verified=true`.

An interval is returned only when its `bar_end_at` is at or before the source
capture time. For a historical cached response, this uses the recorded capture
time rather than the current clock. `bar_status=elapsed_interval` establishes elapsed
time only; `bar_finality_verified=false` remains explicit. Publication time,
consolidated trade coverage and real-time availability are not established. Source
responses are cached for 15 minutes.

Rows are returned newest first. The source response is bounded to 12,000 timestamped
rows and the selected output to 500. Coverage reports truncation, null or ambiguous
rows, date exclusions, unelapsed intervals, shortened sessions and timestamp gaps.
Gaps can include overnight closures, weekends or scheduled breaks: Atlas does not
insert prices, infer missing bar counts or claim a complete session. Duplicate or
unordered timestamps, overlapping elapsed intervals, misaligned arrays and wrong
source metadata fail explicitly.
OHLC contradictions preserve their original values with `status=ambiguous` and
`ohlc_bounds_conflict`. `requirements.allow_missing=false` rejects those results.

## Interfaces and evidence

The Python SDK, CLI, MCP and HTTP use the same typed query and response:

```json
{
  "dataset": "intraday_prices",
  "identifier": "AAPL",
  "start_date": "2026-10-28",
  "end_date": "2026-10-28",
  "interval": "5m",
  "limit": 500
}
```

Compact responses retain interval, timestamp and adjustment interpretation while
deduplicating evidence. Source history stores the original response and local
observation times; this is not publisher point-in-time data. Daily price analytics
and risk connectors continue to use their existing daily input contracts.

Opt-in verification captures private source data, compares every returned OHLC,
volume and timestamp cell with the original response, and verifies elapsed-bar
bounds:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_intraday_live.py \
  --allow-network --date 2026-10-28 --output build/intraday-live
```

The output directory is private (`0700`), its data files use `0600`, and `build/`
is excluded from Git and distribution. A `PARTIAL` result or failed request exits
nonzero. Dated results do not establish continuous source availability. Yahoo and
its data providers retain their data rights; the code license does not grant
redistribution or commercial-use rights.
