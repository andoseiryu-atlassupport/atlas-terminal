# Commodities and futures

Three datasets use the shared CLI, SDK, MCP and HTTP research service. Their prices have different meanings: IMF monthly reference prices, Yahoo rolling front references and explicitly dated Yahoo futures quotes.

## Monthly commodity reference prices

`commodity_prices` accepts `series`, optional paired `start_date` / `end_date`, and `limit`. It reads the IMF Primary Commodity Price System (`IMF.RES,PCPS,latest`), resolves its current structure, and checks the selected indicator, world geography, monthly frequency, US-dollar transformation and physical-unit label before returning observations.

```sh
atlas research get commodity_prices --series gold --start-date 2026-01-01 --end-date 2026-09-30 --provider public --json
```

| Group       | Registered series                                                                             |
| ----------- | --------------------------------------------------------------------------------------------- |
| Metals      | `gold`, `silver`, `copper`, `aluminum`, `iron_ore`, `platinum`, `palladium`                   |
| Energy      | `crude_oil_wti`, `crude_oil_brent`, `crude_oil_dubai`, `natural_gas_us`, `natural_gas_europe` |
| Agriculture | `wheat`, `corn`, `coffee_arabica`, `coffee_robusta`, `sugar_world`, `cotton`                  |

Values retain the publisher's decimal precision. Physical units include dollars per troy ounce, metric tonne, barrel or million Btu, and **US cents per pound**. `quote_unit_scale="0.01"` identifies cents relative to full dollars; the value itself remains in cents. An absent or changed source scale fails explicitly. Benchmark, grade and geography follow the IMF indicator definition; these are monthly reference prices, not an intraday spot feed.

Source periods such as `2026-M01` remain intact; normalized dates identify calendar period ends. Date selection is inclusive by that period end. Explicit missing values remain null, absent months are not filled, and publication time is not inferred. Original observation XML paths and separate structure hashes are retained. `macro_panel` can include these series with explicit aggregation. Current revised observations do not establish publisher PIT.

## Rolling references and dated futures

`futures_prices` accepts an explicit `identifier`, optional paired dates, and `limit`. It requires the same personal-use source enablement as [Yahoo prices](yahoo-finance.md):

```sh
export ATLAS_ENABLE_YAHOO_FINANCE=1
atlas research get futures_prices --identifier GCZ26.CMX --start-date 2026-10-01 --end-date 2026-10-30 --provider public --json
```

| Venue suffix | Registered families                                                                                                            |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `CMX`        | `GC` gold, `SI` silver, `HG` copper                                                                                            |
| `NYM`        | `PL` platinum, `PA` palladium, `CL` WTI, `BZ` Brent, `NG` natural gas, `RB` gasoline, `HO` heating oil                         |
| `CBT`        | `ZC` corn, `ZW` wheat, `ZS` soybeans, `ZM` soybean meal, `ZL` soybean oil, `YM` Dow, `ZB` bonds, `ZN`/`ZF`/`ZT` Treasury notes |
| `NYB`        | `KC` coffee, `SB` sugar, `CC` cocoa, `OJ` orange juice                                                                         |
| `CME`        | `LE` live cattle, `HE` lean hogs, `GF` feeder cattle, `ES` S&P 500, `NQ` Nasdaq 100, `RTY` Russell 2000                        |

`GC=F` is a **rolling front reference**. `GCZ26.CMX` is a **dated identifier** encoding December 2026. Month codes are `F G H J K M N Q U V X Z` for January through December; two-digit years are restricted to 2020–2049. Syntax support is not a listed-contract directory or a promise that every month trades. The returned source symbol, `FUTURE` instrument type, venue, supported USD/USX denomination, source timezone and daily interval must match the request. A wrong-type response is rejected. Cotton and dollar-index future symbols are not registered after unsuccessful identity/availability probes.

Rows preserve signed prices, zeros, nulls, volume, exact field evidence and source-local daily dates. `currency="USX"` remains US cents with `denomination_currency="USD"` and `quote_unit_scale="0.01"`; dollar quotes retain scale `"1"`. Neither numeric value is silently converted. Unconfirmed current sessions are excluded. `unit="source_quote_units"` preserves the numeric quote without a conversion. `quote_measure_unit` is the family's **reference convention**, while `source_quote_convention_verified=false` states that the source quote scale has not been independently confirmed. Do not calculate notional exposure from that reference alone. The six legacy front references remain accessible through `equity_prices` with their existing output units.

The reader does not infer an expiry day, contract multiplier, margin, delivery terms or exchange settlement price. `expiry_date` and `contract_multiplier` are null, `settlement_price_verified=false` and `roll_adjusted=false`. A dated contract month is not its expiry date. Rolling references can change the underlying contract; their changes are not a certified roll-adjusted return history. Futures remain excluded from the equity/ETF time-series risk connection.

## Same-date contract curve

`futures_curve` requires 2–10 unique **dated contracts of one family** and an explicit `observation_date`:

```json
{
  "dataset": "futures_curve",
  "identifiers": ["GCZ26.CMX", "GCG27.CMX", "GCJ27.CMX"],
  "observation_date": "2026-10-30"
}
```

Run the saved query with `atlas research run --input /operator/private/curve.json --provider public --json`, or pass it to `Atlas.get_research`, `atlas_research` or `POST /v1/research`.

Contract points sort by encoded month. Adjacent calendar spreads equal **far close minus near close**, with exact decimal arithmetic and both original inputs. They are quote differences, not carry estimates, arbitrage values or investment returns. All selected quotes must share their source quote denomination and use the exact requested local date; no previous-session fallback or interpolation is allowed. A missing entire contract/date fails even with `allow_partial=true`. That option permits inspecting an existing quote whose close is null, leaving affected spreads null. `limit` truncation is explicit.

`observation_date` filters observations; it does not reconstruct information available on that date. Source revisions and local capture history follow the existing [history rules](source-history.md). Yahoo remains an optional secondary source with separate terms and no implied redistribution license.

## Source and validation references

- [IMF PCPS dataset](https://data.imf.org/en/datasets/IMF.RES:PCPS) and [commodity-price methodology](https://www.imf.org/en/Research/commodity-prices).
- [CME gold contract specifications](https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html); contract terms must be obtained from the exchange rather than inferred from Yahoo metadata.
- [Observed validation and limitations](verification.md).

Opt-in verification saves actual bytes and normalized results under ignored private output:

```sh
ATLAS_ENABLE_YAHOO_FINANCE=1 uv run --offline python scripts/verify_commodities_futures_live.py --allow-network --output build/commodities-futures-live
```

Bundled price/credit fixtures are fictional. No actual commodity or Yahoo observations are shipped.
