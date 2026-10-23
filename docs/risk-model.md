# Fundamental equity risk model

Atlas includes an independent experimental fundamental risk model for supplied US common-stock data in USD. It estimates daily market, industry and style returns by cross-sectional regression, then calculates portfolio volatility, common/specific risk, contributions, benchmark-relative tracking error and factor shocks. It uses no MSCI/Barra model files or Fama–French factor-return downloads and makes no claim of numerical equivalence to either vendor.

The same deterministic service is available through the Python SDK, CLI, optional MCP tool and optional reference HTTP route. There is no price subscription, external model API, LLM or payment dependency. **Atlas does not supply an equity price feed or a verified historical fundamental database.** Bring observations whose use you are authorized to permit, including any derived-output use.

## Run the complete fictional example

From a source checkout with the locked dependencies installed:

```sh
uv run --offline atlas risk demo --json
```

The example imports 60 fictional stocks, fits 60 daily cross sections using four styles, analyzes a 20-stock portfolio against a specified benchmark, applies a market shock and compares the fixed forecast with 20 subsequent sessions and a historical portfolio EWMA baseline. It writes content-addressed inputs and models to private local state. `is_synthetic=true` is preserved even with the `public` provider. The numbers demonstrate execution, not accuracy on real markets.

## Supply observations

Inspect the complete operation and input schema:

```sh
atlas risk catalog --json
```

For a local bulk import, provide a JSON object with `manifest`, `fundamentals` and `sessions`, conforming to `RiskPanelInput` in [the exported contract](../schemas/research.schema.json). An input template is available in [the example](../examples/risk/panel-template.json); it contains two illustrative rows and is deliberately too small to fit. The executable demo generates a complete fictional panel.

```sh
atlas risk import /path/to/rights-cleared-panel.json --json
```

The importer validates the complete panel, then saves immutable bounded chunks. Retain `head_id`. Local bulk input is limited to 32 MiB. An individual request or input chunk must fit within 64 KiB. MCP and HTTP accept JSON chunks, never arbitrary file paths or URLs.

- The manifest identifies 30–200 assets, US common-stock/USD scope, sources, synthetic status and the exact split-and-dividend-adjusted simple-total-return convention. All assets must have a return and positive end-of-session market capitalization in every supplied session.
- Fundamental vintages contain asset ID, actual period end, `available_at`, SIC, book equity, total assets, trailing twelve-month net income, total debt, source ID and locator. All monetary inputs use USD. Total assets must be positive; debt must be nonnegative. Negative equity/income remain reported negative values.
- Sessions contain explicit start/end/availability timestamps, source ID, locator and asset observations. Their boundaries must be contiguous; availability must be no earlier than period end and nondecreasing. Weekend gaps can lie inside a daily period. Atlas does not infer an exchange holiday calendar.
- Source references include an original-byte SHA-256 assertion, retrieval timestamp and `usage_basis=operator_supplied_rights_cleared`. URLs are references only. Atlas does not download or verify those original files. The actual submitted JSON chunks and model artifacts receive independently checked content hashes.
- All numbers are decimal strings; timestamps include time zones. Missing returns, duplicates, unsupported units/types, conflicting vintages and invalid denominators fail explicitly. Returns of -100% and nonpositive market caps are outside this balanced-universe prototype. Returns are never filled or silently imputed.

**Availability is caller-asserted and unverified.** Atlas checks that timestamps are internally consistent and excludes information not yet available at each cutoff. It cannot certify that a supplied timestamp is historically correct. Revised SEC companyfacts snapshots are not historical filing vintages; adding a fixed reporting lag does not establish PIT data. Existing research datasets still reject unsupported historical requests.

## Fit and analyze

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    imported = atlas.import_risk_panel(panel)  # a validated local JSON object
    fitted = atlas.risk(
        {
            "action": "fit",
            "head_id": imported["head_id"],
            "as_of": "2025-12-31T21:05:00Z",
            "lookback": 126,
            "styles": ["size", "value", "profitability", "leverage"],
        }
    )
    model_id = fitted["data"]["model_id"]
    result = atlas.risk(
        {
            "action": "analyze",
            "model_id": model_id,
            "positions": [
                {"asset_id": "STOCK_A", "weight": "0.6"},
                {"asset_id": "STOCK_B", "weight": "0.4"},
            ],
            "benchmark": [{"asset_id": "STOCK_A", "weight": "1"}],
            "shocks": [{"factor": "market", "return_shock": "-0.05"}],
        }
    )
```

The identifiers must exist in the estimation universe. Weights are fractions of NAV, may be negative and are never normalized automatically. Gross absolute weight is bounded to 10. Cash/financing risk is omitted. Benchmark weights are explicit; Atlas does not retrieve index constituents. `active.daily_volatility` and `active.annualized_volatility` are the tracking error for portfolio minus benchmark. Shock output is the linear common-factor return only, excluding individual shocks, nonlinear effects and financing.

For CLI operations, save the same request object to a local JSON file and run `atlas risk run request.json --json`. Each query is bounded to 64 KiB. The output includes `schema_version`, `methodology_version`, `action`, `is_synthetic` and operation-specific `data`.

## Use the same MCP connection

Enable the additional tool explicitly:

```sh
atlas mcp serve --provider public --research-only --enable-risk
```

This exposes the six existing research tools plus `atlas_risk`. Existing configurations retain their original tools. `atlas_risk` is marked as a local, non-destructive, idempotent operation that can write private state; it is not marked read-only. A compatible Codex configuration can append `"--enable-risk"` to its existing Atlas server arguments. The [example configuration](../examples/mcp.risk.json) uses a local source checkout; replace its absolute directory placeholder.

Call `atlas_risk` with a `query` object:

1. `{"action":"catalog"}` describes the complete input schema and limits.
2. `{"action":"put","chunk":{...manifest...}}` returns `data.chunk_id`.
3. Append `kind="fundamentals"` or `kind="sessions"` chunks with `previous` equal to the last chunk ID and `records` containing complete observations. Each chunk references its predecessor; keep the last ID as `head_id`. A chunk holds up to 40 fundamentals or 10 complete sessions, subject to the byte limit.
4. Fit with `{"action":"fit","head_id":"<returned ID>","as_of":"<explicit cutoff>"}`. Repeating exactly the same inputs/options returns the same `model_id`.
5. Analyze with positions, retrieve selected exposures or compare a later holdout. An operator may also bulk-import locally into the same state volume and let the agent use the returned model ID.

`get` defaults to a compact summary. `view="factors"` returns ordered daily factor covariance and forecast transformations. `view="assets"` requires 1–20 explicit asset IDs and returns their exposures, specific variances and selected input vintages. `view="history"` returns fitted daily factor returns and regression diagnostics. Stock-by-stock covariance and raw price histories are not sent by default.

For an operator-hosted Streamable HTTP connection, add `--enable-risk` to `atlas mcp http` and configure the private bearer token described in [remote MCP](remote-mcp.md). Risk mode rejects anonymous startup. Its MCP envelope limit is 66 KiB, including a 64 KiB risk query; existing rate, concurrency and host/origin checks remain active. One authenticated operator state volume is required; there is no tenant isolation. The risk extension makes no external requests.

The loopback reference API exposes `POST /v1/risk` only with `atlas serve --enable-risk`. It is for local/operator use; see [self-hosting](self-hosting.md). Research/payment behavior is unchanged.

## Methodology: atlas-us-fundamental-v1

For each estimated session, Atlas constructs exposures using information with `available_at <= start_at` and estimates:

```text
r(t) = X(start(t)) f(t) + residual(t)
asset covariance = X(as_of) F X(as_of)' + diagonal(specific variances)
```

Market capitalization comes from the most recent available completed session, no more than seven calendar days old. A close received after a return period starts cannot be used for that period; an earlier available session may be selected. Fundamentals use the latest available actual period and its latest available vintage; its period end must be within 550 days. Forecast exposures use only information available at `as_of`. Future observations in the input chain cannot change earlier fitted numerical outputs, although the chain hash records the complete supplied input.

| Style               | Raw descriptor                                                           | Required prior available sessions |
| ------------------- | ------------------------------------------------------------------------ | --------------------------------- |
| Size                | Natural log of market capitalization                                     | 1                                 |
| Value               | Book equity / market capitalization                                      | 1                                 |
| Profitability       | TTM net income / total assets                                            | 1                                 |
| Leverage            | Total debt / total assets                                                | 1                                 |
| Momentum            | Compounded return over 252 sessions, excluding the latest 21             | 252                               |
| Beta                | OLS beta to the equal-weight return of the supplied universe             | 126                               |
| Residual volatility | Residual standard deviation from that 126-session OLS, with an intercept | 126                               |

The last two descriptors use a supplied-universe proxy, not an external market index. Momentum uses session counts, not exact calendar months. All selected styles must be available for every asset/date. Each raw style is clipped to linearly interpolated cross-sectional 5th/95th percentiles, then standardized with WLS-weighted mean and population variance. No missing style is set to zero.

Industries aggregate the public two-digit SIC ranges into ten broad divisions independently of proprietary classifications. At least two divisions with three assets each are required. Membership must stay fixed throughout the estimation window and forecast snapshot. This is a coarse industry model, not a fine industry taxonomy.

The WLS objective weights each squared residual proportionally to the square root of lagged market capitalization. Market plus all industry indicators are identified by the constraint `sum(industry_market_cap * industry_factor_return) = 0`. Atlas eliminates the largest-cap industry coefficient, solves the reduced system with partial pivoting, and reconstructs its factor return. It rejects flat styles, insufficient observations, collinearity and small relative pivots below `1e-10`; it never uses a hidden ridge term to conceal an unidentified model. There must be at least 30 stocks and three observations per free regression parameter. A small portfolio alone is not a valid estimation universe.

Factor covariance uses 60–252 fitted sessions, centered normalized EWMA weights (default decay `0.97`) and a weighted-sample denominator `1 - sum(normalized_weight²)`. Default `0.15` shrinkage toward the covariance diagonal preserves positive semidefiniteness. Specific variances use the same EWMA, a per-session `N/(N-p)` residual-variance adjustment, default `0.20` shrinkage toward the industry average and a daily floor of `0.00000001`. The simple degrees-of-freedom adjustment is an approximation for unequal regression weights. No autocorrelation correction or structural specific-risk model is implemented.

Risk contributions use Euler decomposition of volatility. Asset contributions sum to total volatility; common-factor contributions plus asset-specific contributions also sum to it, up to Decimal rounding. Correlated factor or hedged-asset contributions can be negative. Annualized volatility is daily volatility times `sqrt(252)`, assuming fixed exposures and no serial correlation. This is not a separately calibrated long-horizon forecast. Arithmetic uses an isolated 42-digit Decimal context with round-half-even and decimal strings; caller precision, rounding and trap settings do not change a saved model. This is finite-precision estimation, not exact real arithmetic.

## Validate without overstating accuracy

`validate` takes `model_id`, an input `head_id` and positions. It selects at least 20 sessions whose **start** is at or after the saved model's `as_of`, requires the same universe, and compares realized portfolio sample variance with both the fixed model forecast and an EWMA portfolio-variance baseline computed exclusively from training returns. It reports variance ratios; it produces no accuracy certificate or automatic pass/fail. Input provenance and synthetic status remain explicit.

For practical evaluation, collect rights-cleared historical vintages, include delistings and universe-entry/exit handling, and run a chronological series of forecasts across calm/stressed periods, universes and portfolios. Assess variance calibration, standardized-return tails, residual correlation and coverage against simple covariance/EWMA baselines. Regression R² alone does not validate a risk forecast.

The current prototype requires a balanced user-selected universe and does not correct survivorship bias, infer corporate actions, provide an investable security master, model intraday/liquidity/FX risk, handle changing industry membership or supply global exposures. Synthetic tests establish arithmetic and interface behavior. Real-market forecast accuracy remains unverified.

## Storage and reproducibility

`risk-models.sqlite3` lives in the private `ATLAS_STATE_DIR`. Input chunks and model artifacts are immutable, SHA-256 addressed and checked when read; IDs survive process restarts on the same volume. Model IDs include the methodology, input-chain ID, options, factors, covariance, exposures and timing metadata. No caller can select a remote filesystem path or cause a source fetch.

Limits are 1,024 chunks per chain, 640 sessions, 10,000 fundamental vintages, 16,384 stored objects, 128 stored models and 128 MiB of logical object bodies. Quotas are checked atomically. Database/WAL overhead is additional. Repeated identical requests reuse objects. There is no expiry, automatic eviction or remote deletion API; the operator manages backups/retention and may archive or remove the dedicated risk database while services are stopped. Never submit source credentials, account data or private contact details as provenance.

See [source conditions](../DATA_SOURCES.md), [verification scope](verification.md) and [capabilities](../IMPLEMENTATION_STATUS.md).
