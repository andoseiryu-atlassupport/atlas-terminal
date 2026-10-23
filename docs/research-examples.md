# Reproduce financial research with original source evidence

Atlas Terminal can retrieve US inflation, overnight rates, reference FX and company financials through one research-only MCP connection. Every example preserves the selected queries, observation periods, exact values, units and resolved original evidence. Numerical facts belong to the original publisher; Atlas documents retrieval, normalization and calculations.

## Three complete cases

| Question                                                    | Reproducible case                                     | What to inspect                                                 |
| ----------------------------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------------- |
| How can an agent retrieve CPI, SOFR and USD/JPY together?   | [Inflation, rates and FX](inflation-rates-fx.md)      | Different observation dates and same-day ECB cross inputs       |
| How can an agent verify a free cash flow calculation?       | [Reported free cash flow](reported-free-cash-flow.md) | Actual financial periods, operating cash flow and PP&E payments |
| How can macro series be aggregated without concealing gaps? | [Calendar alignment](calendar-alignment.md)           | Explicit aggregation, complete input periods and missing cells  |

The [saved cases](../examples/citation_cases.json) execute through the official MCP client. They require a source checkout and its locked development dependencies. The fixture run is offline and fictional:

```sh
uv sync --locked --dev
uv run --offline python scripts/capture_research_examples.py
```

This produces content-addressed JSON files under `build/research-examples/`. Each contains the exact batch query, server/package version, capture time, records and every resolved evidence reference. It is a transport and evidence check without an LLM.

Embedded resolved evidence remains inspectable after temporary MCP evidence IDs expire. The capture retains selected values and evidence metadata; it does not archive complete publisher response bodies.

## Capture actual public data

Run on a host permitted to reach the selected official sources:

The cases use HTTPS endpoints at `api.bls.gov`, `www.ecb.europa.eu`, `markets.newyorkfed.org`, `www.sec.gov` and `data.sec.gov`. Configure permitted destinations through your execution environment's supported network settings. SEC cases also require the private contact setting below.

```sh
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case inflation-rates-fx
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case reported-free-cash-flow
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case calendar-alignment
```

SEC reads require your private `SEC_USER_AGENT` setting with an actual contact email. The other two cases need no data account. `--offline` applies to uv dependency resolution; `--allow-network` explicitly permits the program's source requests. Each invocation uses one MCP session. Reads have independent observation and retrieval times.

Successful actual-source captures can become fixed documentation pages:

```sh
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case inflation-rates-fx --publish --output docs
uv run --offline python scripts/build_docs.py
```

Review the selected source's terms and results before deploying. Link the generated snapshot page from this guide and update the relevant case's verification status when publishing actual observations. JSON and Markdown filenames include the SHA-256 of the complete capture. Existing snapshots are preserved, so a later retrieval gets a different URL. The generated page links its complete JSON and original sources. Failed requests, unresolved evidence, incomplete pagination and fictional evidence cannot produce a public observation page. A recorded missing value remains missing and must not be presented as a measured number.

## Verification status

No actual-source observation snapshots are published with these guides yet. The packaged case runs use fictional source data. A successful fixture run does not establish live compatibility, source availability, current values, search indexing or AI citation. See [verification scope](verification.md).

[Connect Codex](codex.md) · [Original-source citation rules](citations.md) · [Sources and terms](../DATA_SOURCES.md)
