"""Bounded, self-contained local charts with original research evidence."""

import html
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from atlas_terminal.errors import AtlasError
from atlas_terminal.research_views import ResearchResult
from atlas_terminal.util import canonical, digest

MAX_CHART_BYTES = 4 * 1024 * 1024
MAX_CHART_ROWS = 500


def _invalid(message: str) -> AtlasError:
    return AtlasError("INVALID_INPUT", message)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise _invalid("Chart values must be exact decimal strings, integers or explicit nulls.")
    if len(str(value)) > 100:
        raise _invalid("Chart numeric value exceeds bounds.")
    try:
        number = Decimal(value)
        parts = number.as_tuple()
        if (
            not number.is_finite()
            or not isinstance(parts.exponent, int)
            or not -40 <= parts.exponent <= 40
            or len(parts.digits) > 80
        ):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise _invalid("Chart values must be finite bounded decimals.") from None
    return number


def _unit(row: dict[str, Any], field: str) -> str:
    value = row.get("volume_unit") if field == "volume" else row.get("unit")
    if value is None and field in {"open", "high", "low", "close", "adjusted_close"}:
        value = row.get("currency")
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise _invalid("Each plotted record must retain its explicit unit.")
    return value


def _link(value: str) -> str:
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and not any(ord(c) < 32 for c in value)
        )
    except ValueError:
        valid = False
    escaped = _escape(value)
    return f'<a href="{escaped}" rel="noreferrer noopener">{escaped}</a>' if valid else escaped


def _selected_rows(
    result: dict[str, Any], field: str, metric: str | None
) -> tuple[list[dict[str, Any]], list[Decimal | None], str]:
    raw = result["records"]
    if len(raw) > MAX_CHART_ROWS:
        raise _invalid("Chart input exceeds 500 records; request an explicit smaller result.")
    rows = [row for row in raw if metric is None or row.get("metric") == metric]
    if not rows:
        raise AtlasError("INCOMPLETE_COVERAGE", "No records match the selected chart metric.")
    values: dict[str, Decimal | None] = {}
    identity = None
    units: set[str] = set()
    for row in rows:
        observed = row.get("date")
        try:
            if (
                not isinstance(observed, str)
                or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", observed)
                or date.fromisoformat(observed).isoformat() != observed
                or observed in values
            ):
                raise ValueError
        except ValueError:
            raise _invalid(
                "Chart records need unique valid ISO dates for one selected series."
            ) from None
        if field not in row:
            raise _invalid("The selected chart field is absent; null values must remain explicit.")
        units.add(_unit(row, field))
        current = canonical(
            {
                key: row.get(key)
                for key in (
                    "identifier",
                    "series",
                    "series_id",
                    "metric",
                    "currency",
                    "dimensions",
                    "country",
                    "region",
                    "tenor",
                    "maturity",
                    "frequency",
                    "date_basis",
                )
            }
        )
        if identity is None:
            identity = current
        elif current != identity:
            raise _invalid("Chart records must describe one series with a consistent basis.")
        values[observed] = _number(row[field])
    if len(units) != 1:
        raise _invalid("Chart records have incompatible units; select one series.")
    rows.sort(key=lambda row: row["date"])
    numbers = [values[row["date"]] for row in rows]
    if all(value is None for value in numbers):
        raise AtlasError("INCOMPLETE_COVERAGE", "No numeric observations are available to plot.")
    return rows, numbers, next(iter(units))


def _svg(rows: list[dict[str, Any]], values: list[Decimal | None], unit: str) -> str:
    actual = [value for value in values if value is not None]
    with localcontext() as context:
        context.prec = 100
        minimum, maximum = min(actual), max(actual)
        spread = maximum - minimum
        padding = spread / Decimal(20) if spread else max(abs(minimum) / Decimal(20), Decimal(1))
        low, high = minimum - padding, maximum + padding
        first = date.fromisoformat(rows[0]["date"]).toordinal()
        last = date.fromisoformat(rows[-1]["date"]).toordinal()
        paths: list[str] = []
        path: list[str] = []
        circles = []
        for row, value in zip(rows, values, strict=True):
            if value is None:
                if path:
                    paths.append(" ".join(path))
                    path = []
                continue
            day = date.fromisoformat(row["date"]).toordinal()
            x = Decimal(70) + (Decimal(day - first) / Decimal(last - first or 1)) * 840
            y = Decimal(350) - (value - low) / (high - low) * 300
            command = "L" if path else "M"
            path.append(f"{command}{x:.2f},{y:.2f}")
            circles.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3"><title>'
                f"{_escape(row['date'])}: {_escape(value)} {_escape(unit)}</title></circle>"
            )
        if path:
            paths.append(" ".join(path))
        grid = []
        for step in range(5):
            y = Decimal(350) - Decimal(step) * 75
            label = low + (high - low) * Decimal(step) / 4
            grid.append(
                f'<line x1="70" x2="910" y1="{y}" y2="{y}" class="grid"/>'
                f'<text x="64" y="{y + 4}" text-anchor="end">{_escape(f"{label:.6g}")}</text>'
            )
    return (
        '<svg viewBox="0 0 960 410" role="img" aria-labelledby="chart-title chart-desc">'
        '<title id="chart-title">Research observations</title>'
        '<desc id="chart-desc">One dated source series. Explicit missing values break the line. '
        "Exact values and source evidence follow the chart.</desc>"
        + "".join(grid)
        + "".join(f'<path d="{path}" class="series"/>' for path in paths)
        + "".join(circles)
        + f'<text x="70" y="380">{_escape(rows[0]["date"])}</text>'
        + f'<text x="910" y="380" text-anchor="end">{_escape(rows[-1]["date"])}</text>'
        + f'<text x="70" y="25">{_escape(unit)}</text></svg>'
    )


def render_research_chart(
    result: dict[str, Any],
    *,
    field: str = "value",
    metric: str | None = None,
    title: str | None = None,
) -> str:
    """Return standalone HTML; never read sources, write files or publish observations."""
    try:
        body = canonical(result)
    except (TypeError, ValueError, RecursionError):
        raise _invalid("Chart input must be a bounded JSON research result.") from None
    if len(body) > MAX_CHART_BYTES:
        raise _invalid("Chart input exceeds the 4 MiB result budget.")
    try:
        ResearchResult.model_validate(result, strict=True)
    except (ValidationError, TypeError, ValueError):
        raise _invalid("Chart input must be a complete Atlas research result envelope.") from None
    if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", field):
        raise _invalid("Choose one top-level numeric record field.")
    if metric is not None and (not isinstance(metric, str) or not 1 <= len(metric) <= 100):
        raise _invalid("Chart metric selector exceeds bounds.")
    if title is not None and (not isinstance(title, str) or not 1 <= len(title) <= 200):
        raise _invalid("Chart title must contain 1–200 characters.")
    response = result.get("response") or {}
    if (
        response.get("view") == "compact"
        or not result["sources"]
        or any("raw_source_url" not in source for source in result["sources"])
    ):
        raise _invalid("Charts need a full result with embedded original-source evidence.")
    rows, values, unit = _selected_rows(result, field, metric)
    display_title = title or str(result.get("series_title") or metric or result["dataset"])
    chart = _svg(rows, values, unit)
    table = "".join(
        f"<tr><td>{_escape(row['date'])}</td><td>"
        f"{'Unavailable' if value is None else _escape(row[field])}</td>"
        f"<td>{_escape(unit)}</td><td>{_escape(row.get('status', 'unspecified'))}</td></tr>"
        for row, value in zip(rows, values, strict=True)
    )
    limitations = "".join(f"<li>{_escape(value)}</li>" for value in result["limitations"])
    source_links = "".join(
        "<li>" + _link(source["raw_source_url"]) + "</li>" for source in result["sources"]
    )
    synthetic = "Fictional data" if result["is_synthetic"] else "Source observations"
    evidence = _escape(json.dumps(json.loads(body), ensure_ascii=False, indent=2))
    coverage = _escape(json.dumps(result["coverage"], ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{_escape(display_title)} — Atlas Terminal</title>
<style>
:root{{color-scheme:light dark;font-family:system-ui,sans-serif;color:#193347;background:#f4f7fa}}
body{{margin:0 auto;padding:2rem;max-width:1100px}}
main{{background:#fff;padding:2rem;border-radius:1rem}}
h1{{margin:.5rem 0}}.tag{{color:#176658;font-weight:600}}.meta{{color:#516474;font-size:.9rem}}
svg{{width:100%;height:auto;background:#fff}}svg text{{font-size:11px;fill:#455867}}
.grid{{stroke:#dce5ec;stroke-width:1}}.series{{stroke:#0b7a75;stroke-width:2;fill:none}}circle{{fill:#0b7a75}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:.5rem;border-bottom:1px solid #dce5ec}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf2f6;padding:1rem;font-size:.8rem}}
details{{margin:1rem 0}}a{{color:#076b9b;overflow-wrap:anywhere}}li{{margin:.5rem 0}}
@media(prefers-color-scheme:dark){{
:root{{color:#e0e9ef;background:#14212b}}main,svg{{background:#1b2c39}}
svg text{{fill:#c2d1dd}}.meta{{color:#bdcbd5}}pre{{background:#14212b}}
.tag,a{{color:#72d0c5}}.grid{{stroke:#40515e}}}}
</style></head><body><main>
<p class="tag">Atlas Terminal · {_escape(synthetic)}</p><h1>{_escape(display_title)}</h1>
<p class="meta">Field: {_escape(field)} · Unit: {_escape(unit)} · {len(rows)} selected records ·
Retrieved: {_escape(result["retrieved_at"])}</p>
{chart}
<p>Lines connect consecutive supplied observations. Explicit missing values break the line;
unreported dates are not reconstructed. Display coordinates are rounded; exact values follow.
This report renders only the supplied result and does not fetch additional pages.</p>
<details open><summary>Exact observations</summary><table><thead><tr><th>Date</th><th>Value</th>
<th>Unit</th><th>Source status</th></tr></thead><tbody>{table}</tbody></table></details>
<details open><summary>Coverage and limitations</summary>
<pre>{coverage}</pre><ul>{limitations}</ul></details>
<details><summary>Original sources and terms</summary><ul>{source_links}</ul>
<p>Source terms: {_link(result["source_terms_url"])}</p>
<p>Local rendering does not grant data redistribution rights.
Retain the original source notices.</p></details>
<details><summary>Complete result and provenance</summary><p>Input SHA-256: {digest(body)}</p>
<pre>{evidence}</pre></details>
<p class="meta">Standalone local report.
No scripts, external assets or automatic network requests.</p>
</main></body></html>"""
