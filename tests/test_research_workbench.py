"""Offline workbench captures remain exact, bounded and inert source evidence."""

import base64
import hashlib
import json
from copy import deepcopy
from html import escape
from html.parser import HTMLParser

import pytest

from atlas_terminal.errors import AtlasError
from atlas_terminal.research_workbench import WorkbenchPanel, render_research_workbench
from atlas_terminal.util import canonical, digest


def result(*, source_id="bls", unit="index"):
    source = {
        "raw_source_url": f"https://fixtures.atlas.invalid/{source_id}.json",
        "raw_sha256": "a" * 64,
        "retrieved_at": "2026-10-30T03:00:00Z",
        "json_pointer": "/observations",
    }
    return {
        "schema_version": "0.2.0",
        "dataset": "economics",
        "query": {"dataset": "economics", "series": "cpi"},
        "source_id": source_id,
        "source_terms_url": "https://fixtures.atlas.invalid/terms",
        "upstream_data_cost": "0",
        "is_synthetic": True,
        "source_text_is_untrusted": True,
        "retrieved_at": "2026-10-30T03:00:00Z",
        "records": [
            {
                "date": day,
                "value": value,
                "unit": unit,
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


class Document(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.scripts = []
        self.in_script = False
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag == "script":
            self.in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data


def response_metadata(records):
    return {
        "view": "full",
        "fields": [],
        "snapshot_id": "b" * 64,
        "result_sha256": "c" * 64,
        "expires_at": "2026-11-01T00:00:00Z",
        "total_records": records,
        "returned_records": records,
        "offset": 0,
        "page_size": None,
        "next_cursor": None,
        "evidence_count": 1,
    }


def test_panels_keep_independent_units_dates_complete_values_and_evidence():
    first = result()
    second = result(source_id="treasury", unit="percent")
    second["records"][0]["date"] = "2025-12-31"
    before = deepcopy([first, second])
    report = render_research_workbench(
        [
            WorkbenchPanel(first, field="value", title="Price index"),
            WorkbenchPanel(second, field="value", title="Yield"),
        ],
        title="Two independent captures",
    )
    parsed = Document(report)
    assert len([tag for tag, _ in parsed.elements if tag == "section"]) == 2
    assert len([tag for tag, _ in parsed.elements if tag == "svg"]) == 2
    assert len([tag for tag, _ in parsed.elements if tag == "path"]) == 4
    assert "100.000000000000000001" in report and "Unavailable" in report
    assert "Unit: index" in report and "Unit: percent" in report
    assert "2025-12-31" in report and "2026-10-30T03:00:00Z" in report
    assert "2 panels" in report and "6 captured records" in report
    assert "not aligned by this workbench" in report
    for capture in (first, second):
        assert digest(canonical(capture)) in report
        assert (
            escape(json.dumps(json.loads(canonical(capture)), ensure_ascii=False, indent=2))
            in report
        )
    ids = [attrs["id"] for _, attrs in parsed.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert [first, second] == before


def test_table_only_panel_supports_nested_records_without_inventing_chart_values():
    capture = result()
    capture["dataset"] = "company_screen"
    capture["records"] = [
        {
            "identifier": "0000000001",
            "screen_status": "excluded",
            "filter_results": [{"metric": "profit", "value": "0", "matched": False}],
            "unknown_custom_field": {"exact": "123.4567890123456789"},
        }
    ]
    report = render_research_workbench([WorkbenchPanel(capture)])
    assert "<svg" not in report
    assert "123.4567890123456789" in report
    assert "0000000001" in report and "excluded" in report
    assert "unknown_custom_field" in report and "&quot;value&quot;: &quot;0&quot;" in report
    assert "1 / 1" in report and "Complete record" in report


def test_script_is_constant_hash_authorized_and_source_text_cannot_activate_markup():
    capture = result()
    attack = '</title><script src="https://evil.invalid/x.js">fetch("bad")</script>'
    capture["records"][0]["extra"] = attack
    capture["limitations"].append(attack)
    capture["source_terms_url"] = "javascript:alert(1)"
    capture["sources"][0]["raw_source_url"] += '?search=" onmouseover="alert(1)'
    report = render_research_workbench([WorkbenchPanel(capture, title=attack)], title=attack)
    parsed = Document(report)
    assert len(parsed.scripts) == 1
    assert not any(
        tag in {"img", "iframe", "link", "object", "embed"} for tag, _ in parsed.elements
    )
    assert not any(name.startswith("on") for _, attrs in parsed.elements for name in attrs)
    assert not any("src" in attrs for _, attrs in parsed.elements)
    assert not any(attrs.get("href", "").startswith("javascript:") for _, attrs in parsed.elements)
    assert "&lt;script" in report
    policies = [
        attrs["content"]
        for tag, attrs in parsed.elements
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    ]
    sha = base64.b64encode(hashlib.sha256(parsed.scripts[0].encode()).digest()).decode()
    assert len(policies) == 1 and f"script-src 'sha256-{sha}'" in policies[0]
    assert "default-src 'none'" in policies[0] and "connect-src 'none'" in policies[0]
    assert "fetch(" not in parsed.scripts[0]
    assert "innerHTML" not in parsed.scripts[0] and "eval(" not in parsed.scripts[0]
    assert ".textContent" in parsed.scripts[0]


def test_links_require_explicit_navigation_and_do_not_include_referrer_or_opener():
    parsed = Document(render_research_workbench([WorkbenchPanel(result())]))
    links = [
        attrs for tag, attrs in parsed.elements if tag == "a" and attrs["href"].startswith("https:")
    ]
    assert links
    assert all(attrs.get("rel") == "noreferrer noopener" for attrs in links)
    assert all(attrs.get("target") == "_blank" for attrs in links)
    assert any(tag == "noscript" for tag, _ in parsed.elements)
    assert all("disabled" in attrs for tag, attrs in parsed.elements if tag in {"input", "button"})


@pytest.mark.parametrize(
    "change",
    [
        {"view": "compact"},
        {"fields": ["value"]},
        {"offset": 3},
        {"next_cursor": "r1_" + "b" * 64 + "_1"},
        {"total_records": 4},
        {"returned_records": 2, "total_records": 2},
    ],
)
def test_compact_projected_or_missing_pages_cannot_be_exported_as_complete(change):
    capture = result()
    capture["response"] = response_metadata(3) | change
    with pytest.raises(AtlasError, match="omitted response pages"):
        render_research_workbench([WorkbenchPanel(capture)])


def test_complete_response_page_is_allowed_if_nothing_is_omitted():
    capture = result()
    capture["response"] = response_metadata(3) | {"page_size": 3}
    assert "3 captured records" in render_research_workbench([WorkbenchPanel(capture)])


@pytest.mark.parametrize("location", ["sources", "record", "empty_sources"])
def test_unresolved_evidence_is_rejected_at_any_depth(location):
    capture = result()
    if location == "sources":
        capture["sources"] = [{"evidence_id": "a" * 64}]
    elif location == "record":
        capture["records"][0]["source"] = {"evidence_id": "a" * 64}
    else:
        capture["sources"] = []
    with pytest.raises(AtlasError, match="embedded evidence"):
        render_research_workbench([WorkbenchPanel(capture)])


def test_source_truncation_stays_visible_and_is_not_mislabeled_as_full_coverage():
    capture = result()
    capture["coverage"].update(truncated=True, partial=True, omitted=100)
    report = render_research_workbench([WorkbenchPanel(capture)])
    assert "Limited capture — review coverage" in report
    assert "&quot;omitted&quot;: 100" in report
    assert "3 captured records" in report


def test_ambiguous_values_break_chart_lines_but_are_preserved_exactly_in_table():
    capture = result()
    capture["records"][1].update(value="999", status="ambiguous")
    report = render_research_workbench([WorkbenchPanel(capture, field="value")])
    parsed = Document(report)
    assert len([tag for tag, _ in parsed.elements if tag == "path"]) == 2
    assert len([tag for tag, _ in parsed.elements if tag == "circle"]) == 2
    assert ">999</td>" in report and ">ambiguous</td>" in report


def test_all_missing_chart_keeps_useful_panel_and_exact_missing_records():
    capture = result()
    for row in capture["records"]:
        row.update(value=None, status="unavailable")
    report = render_research_workbench([WorkbenchPanel(capture, field="value")])
    assert "No numeric observations are available" in report
    assert "3 / 3" in report and "Unavailable" in report
    assert "<svg" not in report


def test_empty_table_capture_keeps_coverage_instead_of_inventing_observations():
    capture = result()
    capture["records"] = []
    report = render_research_workbench([WorkbenchPanel(capture)])
    assert "contains no records" in report and "0 captured records" in report
    with pytest.raises(AtlasError, match="No records match"):
        render_research_workbench([WorkbenchPanel(capture, field="value")])


def test_metric_selection_affects_chart_and_table_but_keeps_entire_capture_evidence():
    capture = result()
    additional = deepcopy(capture["records"])
    for row in capture["records"]:
        row["metric"] = "sma_20"
    for row in additional:
        row["metric"] = "ema_12"
    capture["records"].extend(additional)
    with pytest.raises(AtlasError):
        render_research_workbench([WorkbenchPanel(capture, field="value")])
    report = render_research_workbench([WorkbenchPanel(capture, field="value", metric="sma_20")])
    assert "3 / 6" in report and "6 captured records" in report
    assert "Metric: sma_20" in report and "ema_12" in report
    assert digest(canonical(capture)) in report
    with pytest.raises(AtlasError, match="No records match"):
        render_research_workbench([WorkbenchPanel(capture, field="value", metric="missing")])


@pytest.mark.parametrize("fault", ["unit", "date", "value", "series"])
def test_chart_cannot_silently_reinterpret_malformed_or_mixed_series(fault):
    capture = result()
    if fault == "unit":
        capture["records"][1]["unit"] = "percent"
    elif fault == "date":
        capture["records"][1]["date"] = "2026-02-30"
    elif fault == "value":
        capture["records"][1]["value"] = 1.5
    else:
        capture["records"][1]["series"] = "other"
    with pytest.raises(AtlasError):
        render_research_workbench([WorkbenchPanel(capture, field="value")])


@pytest.mark.parametrize(
    "options",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"field": "nested.value"},
        {"metric": "metric_without_field"},
        {"field": "value", "metric": "x" * 101},
    ],
)
def test_panel_selectors_are_bounded(options):
    with pytest.raises(AtlasError):
        render_research_workbench([WorkbenchPanel(result(), **options)])


@pytest.mark.parametrize("panels", [[], [WorkbenchPanel(result())] * 9, [result()], None])
def test_panel_count_and_types_are_validated(panels):
    with pytest.raises(AtlasError):
        render_research_workbench(panels)


def test_titles_envelopes_and_json_are_validated_before_rendering():
    with pytest.raises(AtlasError, match="Workbench title"):
        render_research_workbench([WorkbenchPanel(result())], title="")
    capture = result()
    capture["is_synthetic"] = "false"
    with pytest.raises(AtlasError, match="validated Atlas"):
        render_research_workbench([WorkbenchPanel(capture)])
    capture = result()
    capture["invalid"] = float("nan")
    with pytest.raises(AtlasError, match="bounded JSON"):
        render_research_workbench([WorkbenchPanel(capture)])


def test_row_budget_rejects_captures_instead_of_silently_sampling():
    capture = result()
    capture["records"] *= 667
    with pytest.raises(AtlasError, match="2,000 records"):
        render_research_workbench([WorkbenchPanel(capture)])


def test_individual_aggregate_and_rendered_byte_budgets_are_enforced(monkeypatch):
    import atlas_terminal.research_workbench as module

    capture = result()
    size = len(canonical(capture))
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_WORKBENCH_RESULT_BYTES", size - 1)
        with pytest.raises(AtlasError, match="4 MiB"):
            render_research_workbench([WorkbenchPanel(capture)])
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_WORKBENCH_INPUT_BYTES", size * 2 - 1)
        with pytest.raises(AtlasError, match="8 MiB"):
            render_research_workbench([WorkbenchPanel(capture), WorkbenchPanel(capture)])
    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_WORKBENCH_HTML_BYTES", 100)
        with pytest.raises(AtlasError, match="32 MiB"):
            render_research_workbench([WorkbenchPanel(capture)])


def test_deeply_nested_untrusted_records_fail_with_safe_validation_error():
    capture = result()
    nested = "leaf"
    for _ in range(500):
        nested = {"annotation": nested}
    capture["records"][0]["annotation"] = nested
    with pytest.raises(AtlasError, match="nesting or node budget"):
        render_research_workbench([WorkbenchPanel(capture)])


def test_cli_exports_private_multi_capture_html_and_refuses_overwrite(tmp_path):
    from typer.testing import CliRunner

    from atlas_terminal.cli import app

    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_bytes(canonical(result()))
    second.write_bytes(canonical(result(source_id="other", unit="percent")))
    target = tmp_path / "workbench.html"
    args = [
        "research",
        "workbench",
        "--input",
        str(first),
        "--input",
        str(second),
        "--output",
        str(target),
        "--field",
        "value",
    ]
    runner = CliRunner()
    response = runner.invoke(app, args)
    assert response.exit_code == 0, response.output
    outcome = json.loads(response.output)
    assert outcome["panels"] == 2
    assert outcome["network_used"] is False and outcome["published"] is False
    assert target.stat().st_mode & 0o777 == 0o600
    before = target.read_bytes()
    assert "2 panels" in before.decode()
    repeated = runner.invoke(app, args)
    assert repeated.exit_code != 0
    assert target.read_bytes() == before


def test_chart_selected_field_and_corresponding_unit_are_used_in_visible_table():
    capture = result(unit="USD")
    for row in capture["records"]:
        row.update(volume="100", volume_unit="shares", adjusted_close="250")
    report = render_research_workbench([WorkbenchPanel(capture, field="volume")])
    assert "Unit: shares" in report
    assert "<td>100</td><td>shares</td>" in report
    assert "<td>250</td><td>USD</td>" not in report
