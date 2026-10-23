# Analyze a Japanese portfolio with a native cross-sectional model

This walkthrough reads a local named connection, estimates a cross-sectional factor model and analyzes a 20-position portfolio through one MCP session. Its estimation universe contains 60 **fictional Japanese stocks** in JPY with JPX33 industry codes. It demonstrates the processing workflow; it is not a real-market forecast validation or a numerical comparison with MSCI/Barra.

## Watch the recorded execution

[Watch the 60-second Japanese risk replay](assets/showcase/japan-risk.mp4)

[Transcript](assets/showcase/japan-risk.txt) · [Complete recorded JSON](assets/showcase/japan-risk.json) · [Captions](assets/showcase/japan-risk.vtt) · [Recording metadata and checksums](assets/showcase/manifest.json)

The replay presents output from an executed fictional workflow. Playback takes 60 seconds; that is not a runtime benchmark. No equity-price feed or LLM is used.

## Run the installed workflow

Use Python 3.12+ and uv:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas showcase japan-risk
atlas showcase japan-risk --json > japan-risk-report.json
```

The command creates a private temporary JSON connection, starts the installed MCP server with the native risk extension and calls `atlas_risk` with `action=run`. That request reads and validates observations, estimates the model and calculates portfolio risk. A second call evaluates the saved forecast against subsequent supplied sessions. Both calls share one official MCP client session.

## Inspect what was estimated

The included execution uses 60 estimation sessions and nine factors: a market factor, four JPX33 industry factors and size, value, profitability and leverage styles. Twenty equal-weight holdings are a subset of the broader universe. The recorded standardized portfolio style exposures, rounded to six decimal places, are:

| Style         | Fictional portfolio exposure |
| ------------- | ---------------------------- |
| Size          | -0.169465                    |
| Value         | -0.131739                    |
| Profitability | -0.282661                    |
| Leverage      | 0.216816                     |

These values describe positions in the model's standardized exposure units. They are not return forecasts, percentages or calibrated investment signals. The JSON report preserves unrounded values, market and industry exposures, common and specific variance, position contributions, model settings, input hashes and the later holdout result.

The methods use lagged supplied characteristics, constrained weighted cross-sectional regression and shrunk EWMA covariance estimates. Historical availability timestamps remain operator assertions. The [methodology](risk-model.md) describes identification, normalization, weighting, covariance and validation assumptions.

## Substitute your own histories

For an actual portfolio, prepare a rights-cleared, balanced estimation universe of 30–200 stocks from one supported country. Holdings alone do not provide the cross-section needed for estimation. Japanese inputs need explicit JPY monetary units, JPX33 classifications, adjusted total returns or declared adjusted-price inputs, and availability timestamps.

Configure a private absolute-path [JSON, CSV or read-only SQLite connection](data-connections.md), using the [connection template](../examples/risk/connections.template.json). Set `ATLAS_RISK_CONNECTIONS_FILE` privately. Adapt the [Japanese run request](../examples/risk/run-japan.json) to your connection ID, actual as-of cutoff and portfolio weights, then:

```sh
atlas risk connections --json
atlas risk run my-japan-request.json --json > my-japan-risk.json
```

The normal workflow retains model artifacts in operator-managed private state. The showcase removes its temporary state on exit, so use its embedded report for inspection rather than attempting to reuse its model IDs.

US/USD, Korea/KRW and Britain/GBP use separate local-market models with their own [classification and price-unit rules](equity-markets.md). There is no combined global country/FX model. Atlas does not bundle equity prices, independently verified historical vintages or a vendor model. Actual-data calibration is still required before relying on forecast accuracy.

[Report a first-run outcome](https://github.com/andoseiryu-atlassupport/atlas-terminal/issues/new?template=first-run.yml) · [Rates and FX walkthrough](agent-research-walkthrough.md) · [Connect an agent](codex.md)
