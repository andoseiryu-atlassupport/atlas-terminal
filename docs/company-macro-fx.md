# Company financials, CPI and USD/JPY through one MCP connection

This reproducible workflow retrieves annual revenue, annual free cash flow, US CPI and USD/JPY reference FX through one Atlas MCP session. It resolves every returned evidence ID, including calculation inputs. The example runs offline with fictional packaged sources; an explicit public-data mode uses the same tools and service.

## Run the fictional example

From a checkout with the locked development dependencies installed:

```sh
uv run --offline python scripts/run_research_workflow.py --output build/evals/company-macro-fx.json
```

The runner starts one research-only stdio MCP process and uses the official MCP client. It discovers datasets, checks configuration without probes, executes the [four-query batch](../examples/company_macro_fx.json), resolves evidence, and checks explicit quality and unsupported-dataset rejection. A nonzero exit indicates a failed check. Full records, query arguments, source URLs, hashes, locators and retrieval times are saved in the output file; stdout contains the check summary.

The packaged example produces these **fictional observations**, not current data:

| Item                            | Fictional value | Unit                 | Observation period            |
| ------------------------------- | --------------- | -------------------- | ----------------------------- |
| ATLASDEMO revenue               | `150000000`     | USD                  | 2025-01-01 through 2025-12-31 |
| ATLASDEMO free cash flow        | `25000000`      | USD                  | 2025-01-01 through 2025-12-31 |
| US CPI, not seasonally adjusted | `321.500`       | Index, 1982–84 = 100 | September 2026                |
| USD/JPY reference cross         | `120`           | JPY per USD          | 2026-10-19                    |

Free cash flow retains reported operating cash flow of `30000000` USD minus PP&E acquisition payments of `5000000` USD, with identical annual input periods. The FX cross retains same-day EUR inputs: `150.00` JPY per EUR divided by `1.2500` USD per EUR. Both outputs are marked derived. The runner does not convert company financials into JPY or infer an economic relationship between the observations.

The batch requires no missing values. Annual financials, a monthly price index and daily reference FX retain their distinct dates; they are not an atomic snapshot. Source retrieval time does not establish publication time or point-in-time availability.

## Use the same batch in an agent

Call `atlas_research_catalog` to discover the relevant schemas. Pass the contents of `examples/company_macro_fx.json` as `query` to `atlas_get_research_batch`. Inspect all four item statuses, quality and coverage. For every returned evidence ID, call `atlas_get_evidence` on the same connection/state volume before citing it. The saved query uses ATLASDEMO and therefore belongs on a fixture-configured server.

The same saved query also works through the CLI:

```sh
uv run --offline atlas research batch --input examples/company_macro_fx.json --provider fixture --json
```

## Opt into real public sources

Configure an actual private SEC contact setting and allow outbound access to SEC, BLS and ECB. Then run:

```sh
uv run --offline python scripts/run_research_workflow.py --provider public --identifier AAPL --allow-network --output build/evals/public-company-macro-fx.json
```

This command requests real source data. It is not run by offline CI and does not fall back to fictional observations. Missing SEC configuration, upstream errors, unavailable values and quality failures remain failures in the report. No API subscription, model or wallet is needed. The public-data command has not been used to establish a live AAPL example in this documentation.

For an operator-hosted endpoint, use `--url https://your-verified-host.example/mcp --allow-network`. The bearer token, if needed, is read only from `ATLAS_MCP_AUTH_TOKEN`; never put it in the URL. `--max-fx-age-days 10` optionally requires a usable FX observation within ten calendar days. This requirement does not refresh the source or fill holidays. Remote evaluation also requires non-synthetic `/healthz` metadata and only the six research tools.

Numerical claims from real runs should cite SEC, BLS and ECB evidence. Cite Atlas for its query interface, period selection and calculations. The runner checks contracts and evidence access; it does not measure an LLM's reasoning, numerical accuracy against an independent ground truth, search discovery or citation frequency.

[Citation guidance](citations.md) · [Financial methodology](methodology.md) · [Macro and FX methodology](macro.md) · [Public checks](public-checks.md)
