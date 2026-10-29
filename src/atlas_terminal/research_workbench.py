"""Private, offline research workbenches with complete captured evidence."""

import base64
import hashlib
import html
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from atlas_terminal.errors import AtlasError
from atlas_terminal.research_chart import _selected_rows, _svg, _unit
from atlas_terminal.research_views import ResearchResult
from atlas_terminal.util import canonical, digest

MAX_WORKBENCH_PANELS = 8
MAX_WORKBENCH_INPUT_BYTES = 8 * 1024 * 1024
MAX_WORKBENCH_RESULT_BYTES = 4 * 1024 * 1024
MAX_WORKBENCH_ROWS = 2000
MAX_WORKBENCH_HTML_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class WorkbenchPanel:
    """One independent full capture; select a field only to request a chart."""

    result: dict[str, Any]
    field: str | None = None
    metric: str | None = None
    title: str | None = None


# This constant contains no captured source text. Its exact bytes are authorized by CSP.
_SCRIPT = """\"use strict\";
for (const panel of document.querySelectorAll(".panel")) {
  const input = panel.querySelector(".record-search");
  const rows = Array.from(panel.querySelectorAll("tbody tr"));
  const count = panel.querySelector(".filter-count");
  const apply = () => {
    const query = input.value.toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
      if (!row.hidden) visible += 1;
    }
    count.textContent = `${visible} of ${rows.length} selected records shown`;
  };
  input.disabled = false;
  input.addEventListener("input", apply);
  const clear = panel.querySelector(".clear-search");
  clear.disabled = false;
  clear.addEventListener("click", () => { input.value = ""; apply(); input.focus(); });
}
"""

_STYLE = """
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;

color:#163044;
background:#edf2f6;
font-synthesis:none;
color-scheme:light}

*{box-sizing:border-box}
body{margin:0}
a{color:#006d77;
text-underline-offset:.18em}

header{background:#102a3b;
color:#f4fbff;
padding:2.4rem max(1.5rem,calc((100vw - 1400px)/2))}

.eyebrow{font-size:.75rem;
font-weight:750;
letter-spacing:.16em;
text-transform:uppercase;
color:#8ed3cb}

h1{font-size:clamp(1.8rem,4vw,2.8rem);
line-height:1.15;
margin:.6rem 0 1rem}

header p{max-width:78ch;
line-height:1.6;
color:#cee0e8}
.summary{display:flex;
flex-wrap:wrap;
gap:1rem}

.summary span{border:1px solid #416071;
border-radius:5px;
padding:.45rem .7rem;
font-size:.85rem}

.layout{max-width:1450px;
display:grid;
grid-template-columns:225px minmax(0,1fr);
gap:1.4rem;

margin:auto;
padding:1.5rem}
.sidebar{align-self:start;
position:sticky;
top:1rem}

.sidebar h2{font-size:.72rem;
letter-spacing:.12em;
text-transform:uppercase;
color:#526c7b}

nav ol{padding:0;
list-style:none}
nav li{margin:.45rem 0}
nav a{display:block;
border-radius:6px;

padding:.65rem .75rem;
color:#234859;
background:#fff;
text-decoration:none;
overflow-wrap:anywhere}

nav a:hover{background:#d9edea}
nav small{display:block;
color:#607987;
margin-top:.25rem}

.sidebar p{font-size:.8rem;
line-height:1.5;
color:#526c7b}
.panels{min-width:0}
.panel{background:#fff;

border:1px solid #d6e1e8;
border-radius:10px;
margin-bottom:1.4rem;
padding:1.5rem;
scroll-margin-top:1rem}

.panel h2{font-size:1.4rem;
margin:.6rem 0}
.badges{display:flex;
flex-wrap:wrap;
gap:.4rem}

.badge{display:inline-block;
font-size:.72rem;
font-weight:700;
padding:.25rem .45rem;
border-radius:4px;

background:#e7f3ef;
color:#1e6051}
.badge.synthetic,.badge.limited{background:#fff0d7;
color:#805012}

.meta{font-size:.82rem;
color:#526c7b;
line-height:1.7;
overflow-wrap:anywhere}
.stats{display:grid;

grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:.7rem;
margin:1rem 0 1.2rem}

.stat{background:#f2f6f8;
border-radius:6px;
padding:.8rem}
.stat span{display:block;
font-size:.7rem;

text-transform:uppercase;
letter-spacing:.06em;
color:#607987}
.stat strong{display:block;

font-size:.95rem;
line-height:1.5;
overflow-wrap:anywhere;
margin-top:.2rem}
.chart{overflow:hidden;

border:1px solid #e3ebf0;
border-radius:6px;
margin:1rem 0}
.chart svg{width:100%;
height:auto;
display:block}

.chart svg text{font-size:11px;
fill:#455e6c}
.grid{stroke:#e2eaf0;
stroke-width:1}

.series{stroke:#007f79;
stroke-width:2;
fill:none}
circle{fill:#007f79}
.notice{font-size:.85rem;

line-height:1.6;
background:#f2f6f8;
border-left:3px solid #5d929e;
padding:.75rem 1rem}

details{margin:.9rem 0}
summary{cursor:pointer;
font-weight:650;
font-size:.9rem;
padding:.35rem 0}

.controls{display:flex;
gap:.6rem;
align-items:end;
flex-wrap:wrap;
margin:.8rem 0}
.controls label{
font-size:.78rem;
font-weight:600;
flex:1;
min-width:180px}
.controls input{display:block;
width:100%;

margin-top:.3rem;
font:inherit;
font-weight:400;
border:1px solid #aabdc8;
border-radius:5px;
padding:.55rem}

button{font:inherit;
font-size:.8rem;
border:1px solid #aabdc8;
background:#f6f9fb;
border-radius:5px;

padding:.55rem .8rem;
cursor:pointer}
input:focus-visible,button:focus-visible,a:focus-visible,
summary:focus-visible{outline:3px solid #d29523;
outline-offset:3px}
.filter-count{font-size:.78rem;

color:#526c7b;
flex-basis:100%;
margin:0}
.table-scroll{overflow:auto;
max-height:540px;
border:1px solid #dae4eb;

border-radius:5px}
table{border-collapse:collapse;
width:100%;
font-size:.78rem}
thead{position:sticky;

top:0;
background:#eaf1f5;
z-index:1}
th,td{padding:.6rem .7rem;
text-align:left;
border-bottom:1px solid #e1e9ef;

vertical-align:top;
min-width:95px}
td{max-width:280px;
overflow-wrap:anywhere}
th:first-child,td:first-child{
min-width:35px;
color:#607987}
tbody tr:hover{background:#f7fafc}
td details{margin:0}

pre{white-space:pre-wrap;
overflow-wrap:anywhere;
tab-size:2;
font-size:.75rem;
line-height:1.5;

background:#f0f5f8;
padding:.8rem;
border-radius:5px;
max-height:450px;
overflow:auto}
td pre{
min-width:250px;
max-width:650px;
margin:.4rem 0}
code{font-size:.85em;
overflow-wrap:anywhere}

li{margin:.35rem 0;
line-height:1.5}
.sources{padding-left:1.2rem;
font-size:.8rem}

.sources a{overflow-wrap:anywhere}
.source-meta{display:block;
color:#607987;
font-size:.72rem}

footer{max-width:1400px;
padding:0 1.5rem 2rem;
margin:auto;
color:#526c7b;
font-size:.8rem;
line-height:1.6}

[hidden]{display:none!important}
@media(max-width:850px){.layout{grid-template-columns:1fr;
padding:1rem}

.sidebar{position:static}
.sidebar nav ol{display:flex;
flex-wrap:wrap;
gap:.5rem}
.sidebar nav li{margin:0}

.sidebar p{display:none}
.panel{padding:1rem}
.sidebar h2{margin-top:0}
}

@media print{header{padding:1rem;
background:#fff;
color:#163044}
header p,.eyebrow{color:#163044}

.layout{display:block;
padding:0}
.sidebar,.controls{display:none}
.panel{break-inside:avoid;
border:0}

.table-scroll,pre{max-height:none;
overflow:visible}
details:not([open]){display:none}
thead{position:static}
}

"""


def _invalid(message: str) -> AtlasError:
    return AtlasError("INVALID_INPUT", message)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json(value: Any) -> str:
    return _escape(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def _text(value: Any) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, (dict, list)):
        return _escape(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return _escape(value)


def _link(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not any(ord(char) < 32 for char in value)
        )
    except ValueError:
        valid = False
    return (
        f'<a href="{_escape(value)}" target="_blank" rel="noreferrer noopener">{_escape(value)}</a>'
        if valid
        else _escape(value)
    )


def _has_reference(value: Any) -> bool:
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > 64 or visited > 100_000:
            raise _invalid("Workbench captures exceed the supported nesting or node budget.")
        if isinstance(item, dict):
            if set(item) == {"evidence_id"}:
                return True
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return False


def _validate(panel: WorkbenchPanel) -> bytes:
    if not isinstance(panel, WorkbenchPanel) or not isinstance(panel.result, dict):
        raise _invalid("Each workbench panel must contain one full research result.")
    if panel.title is not None and (
        not isinstance(panel.title, str) or not 1 <= len(panel.title) <= 200
    ):
        raise _invalid("Panel titles must contain 1–200 characters.")
    if panel.field is not None:
        import re

        if not isinstance(panel.field, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,79}", panel.field
        ):
            raise _invalid("Choose one top-level numeric record field for a panel chart.")
    if panel.metric is not None and (
        panel.field is None
        or not isinstance(panel.metric, str)
        or not 1 <= len(panel.metric) <= 100
    ):
        raise _invalid("A metric selector requires an explicit chart field and 1–100 characters.")
    try:
        body = canonical(panel.result)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise _invalid("Workbench input must be a bounded JSON research result.") from None
    if len(body) > MAX_WORKBENCH_RESULT_BYTES:
        raise _invalid("Each workbench capture must fit the 4 MiB research result budget.")
    try:
        ResearchResult.model_validate(panel.result, strict=True)
    except (ValidationError, TypeError, ValueError, RecursionError):
        raise _invalid(
            "Workbench input must be a validated Atlas research result envelope."
        ) from None
    response = panel.result.get("response") or {}
    if (
        response.get("view") == "compact"
        or response.get("fields")
        or response.get("offset", 0) != 0
        or response.get("next_cursor") is not None
        or response.get("returned_records", len(panel.result["records"]))
        != response.get("total_records", len(panel.result["records"]))
        or response.get("returned_records", len(panel.result["records"]))
        != len(panel.result["records"])
        or not panel.result["sources"]
        or _has_reference(panel.result)
    ):
        raise _invalid(
            "Workbench panels need full, unprojected results with embedded evidence and no "
            "omitted response pages. Capture a complete result before rendering."
        )
    return body


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return "—"


def _table(rows: list[dict[str, Any]], field: str | None) -> str:
    if not rows:
        return (
            '<p class="notice">This capture contains no records. Coverage and evidence follow.</p>'
        )
    cells = []
    for index, row in enumerate(rows, 1):
        columns = [
            _first(
                row, ("observation_at", "date", "period_end", "end", "filed_on", "reference_date")
            ),
            _first(
                row, ("identifier", "series", "series_id", "name", "issuer_name", "document_id")
            ),
            _first(row, ("metric", "concept", "event_type", "form", "asset_category")),
            row[field]
            if field is not None
            else _first(
                row, ("value", "adjusted_close", "close", "amount", "balance", "screen_status")
            ),
            _unit(row, field)
            if field is not None
            else _first(row, ("unit", "currency", "value_unit", "balance_unit_code")),
            _first(row, ("status", "session_status", "screen_status")),
        ]
        cells.append(
            f"<tr><td>{index}</td>"
            + "".join(f"<td>{_text(value)}</td>" for value in columns)
            + f"<td><details><summary>Inspect</summary><pre>{_json(row)}</pre></details></td></tr>"
        )
    return (
        '<div class="table-scroll" tabindex="0" aria-label="Exact captured records">'
        '<table><thead><tr><th scope="col">#</th><th scope="col">Date / period</th>'
        '<th scope="col">Identifier / series</th><th scope="col">Metric / type</th>'
        '<th scope="col">Value / summary</th><th scope="col">Unit</th>'
        '<th scope="col">Status</th><th scope="col">Complete record</th></tr></thead>'
        "<tbody>" + "".join(cells) + "</tbody></table></div>"
    )


def _chart(panel: WorkbenchPanel, index: int) -> tuple[str, list[dict[str, Any]]]:
    if panel.field is None:
        return "", panel.result["records"]
    rows = [
        row
        for row in panel.result["records"]
        if panel.metric is None or row.get("metric") == panel.metric
    ]
    if not rows:
        raise AtlasError("INCOMPLETE_COVERAGE", "No records match the selected panel metric.")
    try:
        rows, values, unit = _selected_rows({"records": rows}, panel.field, None)
    except AtlasError as exc:
        if exc.code != "INCOMPLETE_COVERAGE":
            raise
        return (
            '<p class="notice">No numeric observations are available for this chart. '
            "Exact missing observations remain in the table.</p>",
            rows,
        )
    values = [
        None
        if row.get("status") in {"unavailable", "missing", "ambiguous"}
        or (panel.field in {"open", "high", "low", "close"} and row.get("ohlc_consistent") is False)
        else value
        for row, value in zip(rows, values, strict=True)
    ]
    if all(value is None for value in values):
        chart = '<p class="notice">No usable numeric observations are available for this chart.</p>'
    else:
        chart = _svg(rows, values, unit).replace("chart-title", f"panel-{index}-chart-title")
        chart = chart.replace("chart-desc", f"panel-{index}-chart-desc")
    return (
        f'<p class="meta">Chart field: {_escape(panel.field)} · '
        f"Metric: {_escape(panel.metric or 'single supplied series')} · Unit: {_escape(unit)}</p>"
        f'<div class="chart">{chart}</div><p class="meta">Lines connect supplied dates. '
        "Explicit missing or ambiguous observations break the line; omitted dates are not "
        "reconstructed. Coordinates are rounded only for display. Exact values remain below.</p>",
        rows,
    )


def _panel(panel: WorkbenchPanel, index: int, body: bytes) -> tuple[str, str]:
    result = panel.result
    title = panel.title or str(result.get("series_title") or result["dataset"])
    chart, rows = _chart(panel, index)
    coverage = result["coverage"]
    limited = any(
        coverage.get(key) is True for key in ("truncated", "partial", "incomplete", "archive_limit")
    )
    badges = (
        '<span class="badge synthetic">Fictional data</span>'
        if result["is_synthetic"]
        else '<span class="badge">Source observations</span>'
    )
    if limited:
        badges += '<span class="badge limited">Limited capture — review coverage</span>'
    badges += f'<span class="badge">{_escape(result["dataset"])}</span>'
    references = dict.fromkeys(
        (source["raw_source_url"], source["raw_sha256"], source["retrieved_at"])
        for source in result["sources"]
    )
    sources = "".join(
        f'<li>{_link(url)}<span class="source-meta">Retrieved: {_escape(retrieved)} · '
        f"SHA-256: {_escape(sha)}</span></li>"
        for url, sha, retrieved in references
    )
    limitations = "".join(f"<li>{_escape(item)}</li>" for item in result["limitations"])
    record_table = _table(rows, panel.field)
    nav = (
        f'<li><a href="#panel-{index}">{_escape(title)}'
        f"<small>{_escape(result['source_id'])} · {len(rows)} selected records</small></a></li>"
    )
    return (
        f'<section class="panel" id="panel-{index}" aria-labelledby="panel-{index}-title">'
        f'<div class="badges">{badges}</div><h2 id="panel-{index}-title">{_escape(title)}</h2>'
        '<div class="stats">'
        '<div class="stat"><span>Source</span>'
        f"<strong>{_escape(result['source_id'])}</strong></div>"
        f'<div class="stat"><span>Selected / captured records</span><strong>{len(rows)} / '
        f"{len(result['records'])}</strong></div>"
        '<div class="stat"><span>Result retrieval time</span>'
        f"<strong>{_escape(result['retrieved_at'])}</strong></div></div>"
        f"{chart}<details open><summary>Captured records</summary>"
        '<div class="controls">'
        f'<label for="filter-{index}">Find text in this panel'
        f'<input class="record-search" id="filter-{index}" type="search" disabled '
        'autocomplete="off" placeholder="Date, identifier, value or source text"></label>'
        '<button type="button" class="clear-search" disabled>Clear</button>'
        f'<p class="filter-count" role="status" aria-live="polite">'
        f"{len(rows)} of {len(rows)} selected records shown</p></div>"
        f"{record_table}</details><details open><summary>Coverage and limitations</summary>"
        f"<pre>{_json(coverage)}</pre><ul>{limitations}</ul></details>"
        "<details><summary>Query and original sources</summary>"
        f'<pre>{_json(result["query"])}</pre><ul class="sources">{sources}</ul>'
        f'<p class="meta">Source terms: {_link(result["source_terms_url"])}</p></details>'
        "<details><summary>Complete result and evidence</summary>"
        f'<p class="meta">Canonical result SHA-256: <code>{digest(body)}</code></p>'
        f"<pre>{_json(json.loads(body))}</pre></details></section>",
        nav,
    )


def render_research_workbench(
    panels: list[WorkbenchPanel], *, title: str = "Research workbench"
) -> str:
    """Render selected captures locally without fetching data, writing or publishing files."""
    if not isinstance(panels, list) or not 1 <= len(panels) <= MAX_WORKBENCH_PANELS:
        raise _invalid("Choose 1–8 independent workbench panels.")
    if not isinstance(title, str) or not 1 <= len(title) <= 200:
        raise _invalid("Workbench title must contain 1–200 characters.")
    bodies = [_validate(panel) for panel in panels]
    if sum(len(body) for body in bodies) > MAX_WORKBENCH_INPUT_BYTES:
        raise _invalid("Combined workbench captures exceed the 8 MiB input budget.")
    records = sum(len(panel.result["records"]) for panel in panels)
    if records > MAX_WORKBENCH_ROWS:
        raise _invalid("Combined workbench captures exceed 2,000 records; choose smaller captures.")
    rendered = [
        _panel(panel, index, body)
        for index, (panel, body) in enumerate(zip(panels, bodies, strict=True))
    ]
    sections = "".join(section for section, _ in rendered)
    navigation = "".join(nav for _, nav in rendered)
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    sources = len({panel.result["source_id"] for panel in panels})
    synthetic = sum(panel.result["is_synthetic"] for panel in panels)
    report = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';
script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; connect-src 'none';
base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>{_escape(title)} — Atlas Terminal</title>
<style>{_STYLE}</style></head><body>
<header><div class="eyebrow">Atlas Terminal · Local research</div><h1>{_escape(title)}</h1>
<p>Explore captured results side by side. Each panel retains its own dates, units, coverage
and original evidence. These captures are independent; their periods and publication availability
are not aligned by this workbench.</p>
<div class="summary"><span>{len(panels)} panels</span><span>{records} captured records</span>
<span>{sources} source families</span><span>{synthetic} fictional captures</span>
<span>Offline snapshot</span></div>
</header><div class="layout"><aside class="sidebar"><h2>Research panels</h2>
<nav aria-label="Research panels"><ol>{navigation}</ol></nav>
<p>Find text inside each table. Filtering changes only the displayed rows;
charts, coverage and evidence
retain the original capture. No numerical comparison is inferred across panels.</p>
<p>No refresh or background connections. Source links open only when selected.</p></aside>
<main class="panels">{sections}</main></div>
<footer>Generated from supplied full Atlas research results. Retrieval times describe
local captures,
not verified historical publication availability. Local rendering does not grant
redistribution rights;
retain all source notices and keep personal-use data private. No external scripts, fonts or
assets are loaded.
<noscript>JavaScript is disabled. All records, charts, coverage and evidence remain readable;
table search is unavailable.</noscript></footer>
<script>{_SCRIPT}</script></body></html>"""
    if len(report.encode()) > MAX_WORKBENCH_HTML_BYTES:
        raise _invalid("Rendered workbench exceeds the 32 MiB output budget.")
    return report
