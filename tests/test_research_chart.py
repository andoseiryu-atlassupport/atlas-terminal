"""Standalone charts retain exact research data and never activate source text."""

import json
from copy import deepcopy
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser

import pytest
from typer.testing import CliRunner

from atlas_terminal.cli import app
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_chart import MAX_CHART_BYTES, render_research_chart
from atlas_terminal.util import canonical, digest


def research_result():
    source = {
        "raw_source_url": "https://fixtures.atlas.invalid/example.json",
        "raw_sha256": "a" * 64,
        "retrieved_at": "2026-10-29T00:00:00Z",
        "json_pointer": "/observations",
    }
    return {
        "schema_version": "0.2.0",
        "dataset": "economics",
        "query": {"dataset": "economics", "series": "cpi"},
        "source_id": "bls",
        "source_terms_url": "https://fixtures.atlas.invalid/terms",
        "upstream_data_cost": "0",
        "is_synthetic": True,
        "source_text_is_untrusted": True,
        "retrieved_at": "2026-10-29T00:00:00Z",
        "records": [
            {
                "date": day,
                "value": value,
                "unit": "index",
                "series": "cpi",
                "status": "reported" if value is not None else "unavailable",
                "source": source,
            }
            for day, value in (
                ("2026-01-31", "100.000000000000000001"),
                ("2026-02-28", None),
                ("2026-03-31", "101.000000000000000001"),
            )
        ],
        "sources": [source],
        "coverage": {"truncated": False, "point_in_time": False},
        "limitations": ["Fictional data. Current source snapshots are not point-in-time history."],
    }


class Elements(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_full_report_preserves_exact_values_missing_gaps_units_and_original_evidence():
    result = research_result()
    before = deepcopy(result)
    report = render_research_chart(result)
    nodes = Elements(report).elements
    assert len([1 for tag, _ in nodes if tag == "path"]) == 2
    assert len([1 for tag, _ in nodes if tag == "circle"]) == 2
    assert "100.000000000000000001" in report and "Unavailable" in report
    assert "Fictional data" in report and "not point-in-time" in report
    assert digest(canonical(result)) in report and "a" * 64 in report
    assert escape(json.dumps(json.loads(canonical(result)), ensure_ascii=False, indent=2)) in report
    assert "additional pages" in report and "truncated" in report
    assert result == before


def test_supplied_rows_are_sorted_without_changing_input_or_inserting_days():
    result = research_result()
    result["records"].reverse()
    before = deepcopy(result)
    report = render_research_chart(result)
    table = report.split("<tbody>")[1].split("</tbody>")[0]
    assert table.index("2026-01-31") < table.index("2026-02-28") < table.index("2026-03-31")
    assert table.count("<tr>") == 3
    assert result == before


def test_source_text_and_titles_are_escaped_and_links_never_become_scripts():
    result = research_result()
    attack = '</title><script src="https://evil.invalid/a.js">alert(1)</script>'
    result["limitations"] = [attack]
    result["source_terms_url"] = "javascript:alert(1)"
    result["sources"][0]["raw_source_url"] += '?q="onmouseover="alert(1)'
    report = render_research_chart(result, title=attack)
    nodes = Elements(report).elements
    assert not any(
        tag in {"script", "iframe", "img", "object", "embed", "link"} for tag, _ in nodes
    )
    assert not any(key.startswith("on") for _, attrs in nodes for key in attrs)
    assert not any(attrs.get("href", "").startswith("javascript:") for _, attrs in nodes)
    assert "&lt;script" in report and "default-src &#x27;none&#x27;" not in report
    assert any(
        tag == "meta"
        and attrs.get("http-equiv") == "Content-Security-Policy"
        and "default-src 'none'" in attrs["content"]
        for tag, attrs in nodes
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e9999", True, 1.25, ""])
def test_nonfinite_inexact_or_malformed_plot_values_are_rejected(value):
    result = research_result()
    result["records"][0]["value"] = value
    with pytest.raises(AtlasError) as error:
        render_research_chart(result)
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("fault", ["unit", "series", "duplicate", "missing", "date", "basis"])
def test_ambiguous_or_uninterpretable_series_fail(fault):
    result = research_result()
    row = result["records"][1]
    if fault == "unit":
        row["unit"] = "percent"
    elif fault == "series":
        row["series"] = "other"
    elif fault == "duplicate":
        row["date"] = result["records"][0]["date"]
    elif fault == "missing":
        del row["value"]
    elif fault == "date":
        row["date"] = "2026-02-30"
    else:
        row["date_basis"] = "publication_date"
    with pytest.raises(AtlasError):
        render_research_chart(result)


def test_metric_filter_selects_one_series_and_does_not_merge_different_metrics():
    result = research_result()
    second = deepcopy(result["records"])
    for row in result["records"]:
        row["metric"] = "sma_20"
    for row in second:
        row["metric"] = "ema_12"
    result["records"].extend(second)
    with pytest.raises(AtlasError):
        render_research_chart(result)
    report = render_research_chart(result, metric="sma_20")
    assert "3 selected records" in report
    with pytest.raises(AtlasError) as error:
        render_research_chart(result, metric="missing_metric")
    assert error.value.code == "INCOMPLETE_COVERAGE"


def test_volume_uses_volume_unit_and_never_uses_price_unit():
    result = research_result()
    for row in result["records"]:
        row.update(volume="100", volume_unit="shares", unit="USD")
    report = render_research_chart(result, field="volume")
    assert "Unit: shares" in report
    assert "Unit: USD" not in report


def test_one_constant_or_negative_observation_has_finite_display_coordinates():
    for values in (("0",), ("-5", "-5", "-5"), ("-1e-30", "0", "1e-30")):
        result = research_result()
        result["records"] = result["records"][: len(values)]
        for row, value in zip(result["records"], values, strict=True):
            row["value"] = value
        report = render_research_chart(result)
        nodes = Elements(report).elements
        assert len([1 for tag, _ in nodes if tag == "circle"]) == len(values)
        assert "NaN" not in report and "Infinity" not in report


def test_all_missing_data_remains_unavailable_instead_of_becoming_zero():
    result = research_result()
    for row in result["records"]:
        row["value"] = None
    with pytest.raises(AtlasError) as error:
        render_research_chart(result)
    assert error.value.code == "INCOMPLETE_COVERAGE"


def test_row_and_byte_bounds_are_enforced_without_silent_sampling():
    result = research_result()
    template = result["records"][0]
    result["records"] = [
        {**template, "date": (date(2024, 1, 1) + timedelta(days=i)).isoformat()} for i in range(501)
    ]
    with pytest.raises(AtlasError, match="500"):
        render_research_chart(result)
    result = research_result()
    result["extra"] = "x" * MAX_CHART_BYTES
    with pytest.raises(AtlasError, match="4 MiB"):
        render_research_chart(result)


def test_compact_or_untyped_envelopes_cannot_be_presented_as_full_evidence():
    result = research_result()
    result["sources"] = [{"evidence_id": "a" * 64}]
    with pytest.raises(AtlasError, match="full result"):
        render_research_chart(result)
    result = research_result()
    result["is_synthetic"] = "false"
    with pytest.raises(AtlasError, match="complete Atlas"):
        render_research_chart(result)


@pytest.mark.parametrize(
    "options",
    [{"field": "nested.value"}, {"field": None}, {"title": ""}, {"title": "a" * 201}],
)
def test_chart_options_are_bounded(options):
    with pytest.raises(AtlasError):
        render_research_chart(research_result(), **options)


def test_cli_writes_private_standalone_report_and_refuses_to_overwrite(tmp_path):
    source = tmp_path / "result.json"
    target = tmp_path / "report.html"
    source.write_bytes(canonical(research_result()))
    arguments = ["research", "chart", "--input", str(source), "--output", str(target)]
    runner = CliRunner()
    run = runner.invoke(app, arguments)
    assert run.exit_code == 0, run.output
    response = json.loads(run.output)
    assert response["network_used"] is False and response["published"] is False
    assert target.stat().st_mode & 0o777 == 0o600
    contents = target.read_text()
    assert "<svg" in contents
    run = runner.invoke(app, arguments)
    assert run.exit_code != 0
    assert target.read_text() == contents
