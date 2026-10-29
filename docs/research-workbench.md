# Offline research workbench

The local workbench combines up to eight saved research results in one HTML file.
Each panel has its own exact record table, coverage, source links, original response
hash and embedded evidence. Optional charts use one explicitly selected numeric
series per panel. Browser text search filters the visible table without changing
its chart, underlying capture or calculations.

The workbench runs entirely from a local file. It does not refresh data, start a
service, load external assets or publish a dashboard. Source links navigate only
when selected. It can place prices, company facts and macro data beside each other;
it does not join their dates, convert units or establish comparable publication
availability.

## CLI

Save full results before rendering. For example, these commands use fictional data
and make no source requests:

```sh
mkdir -p build/research
atlas research get economics --series cpi --provider fixture \
  --start-date 2026-01-01 --end-date 2026-09-30 --json > build/research/cpi.json
atlas research get fx_rates --base-currency USD --quote-currency JPY \
  --provider fixture --json > build/research/fx.json
atlas research workbench \
  --input build/research/cpi.json --input build/research/fx.json \
  --field value --title 'Prices and reference FX' \
  --output build/research/workbench.html
```

Open `build/research/workbench.html` in a browser. Omit `--field` to show generic
table panels, including records with different metrics or nested financial facts.
`--field` and `--metric` apply to every input in this CLI call. A chart requires a
single series with consistent dates, units and interpretation; use the Python API
for different selectors in each panel.

The output must be a new file. The CLI creates it with private `0600` permissions
and refuses to overwrite an existing path. Keep captures and generated reports in
an ignored private directory. Local export does not change source redistribution
conditions, including Yahoo's separate personal-use terms.

## Python

```python
import json
from pathlib import Path

from atlas_terminal.research_workbench import WorkbenchPanel, render_research_workbench

prices = json.loads(Path("build/research/prices.json").read_text())
company = json.loads(Path("build/research/company.json").read_text())

report = render_research_workbench(
    [
        WorkbenchPanel(prices, field="adjusted_close", title="Daily adjusted prices"),
        WorkbenchPanel(company, title="Reported company facts"),
    ],
    title="Company research",
)
# This function returns HTML only. The caller controls private file creation.
```

For `price_analytics`, select a metric such as
`WorkbenchPanel(result, field="value", metric="rsi_14")`. The selected metric
controls the chart and visible record table. The complete original capture remains
embedded with its own canonical JSON SHA-256 hash. The panel displays selected and captured record
counts separately.

For `intraday_prices`, choose `field="close"` or another reported OHLC field.
Charts use the original UTC bar timestamps, while exact records retain source-local
times and their interval. They do not treat intraday OHLC as dividend-adjusted prices.

## Interpretation and bounds

- Panels remain independent. Separate currencies, frequencies or dates never share
  a synthetic comparison or aggregate. Mixed series inside one requested chart fail
  explicitly; choose a table panel or a narrower source query.
- Explicit missing and ambiguous observations break chart lines. Their original
  values and statuses remain in the exact table and complete evidence. Omitted
  dates are not inserted. Coordinates alone are rounded for display.
- Source truncation and partial coverage remain visible. The file represents the
  supplied captures, not an exhaustive upstream database. Projected results,
  compact evidence references and response pages that omit records are rejected.
- Every input must be a complete typed Atlas research envelope with embedded source
  evidence. The workbench never fetches missing evidence or pages. Do not provide
  configuration files or secrets; the complete research capture is retained.
- A workbench accepts 1–8 panels, at most 2,000 captured records in total, 4 MiB per
  input, 8 MiB of combined input and 32 MiB of generated HTML. Each chart selects at
  most 500 records. Inputs are bounded to 64 nesting levels and 100,000 JSON nodes
  per capture. Bounds fail instead of silently sampling records.
- A constant, hash-authorized inline script provides table text search. Source
  text is escaped and never evaluated. A restrictive Content Security Policy blocks
  network access and external assets. Without JavaScript, charts, tables and
  evidence remain readable; search controls stay disabled.
- Result and source retrieval times describe local captures. They do not prove when
  observations became publicly available and do not establish historical PIT data.

The workbench is a local viewer for saved results. Workspace accounts, collaborative
editing, server-side refresh, streaming prices, shared dashboards and trading are
not included.

[Market research](market-research.md) · [Research contracts](agents.md) ·
[Sources and terms](../DATA_SOURCES.md)
