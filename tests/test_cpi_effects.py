"""Fictional BLS-layout tests, never a claim of live official-value verification."""

import copy
import json
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.cpi_calculations import core_effect, reconciliation
from atlas_terminal.cpi_catalog import (
    ARCHIVE_URL,
    CATEGORIES,
    SETS,
    months_between,
    shift_month,
    stacking,
)
from atlas_terminal.cpi_sources import (
    CPIHTML,
    annual_weights,
    archive_releases,
    cpi_identity,
    release_tables,
)
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_cpi_effects import cpi_effects
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest

ROOT = files("atlas_terminal").joinpath("resources", "research", "cpi")
URL = "https://www.bls.gov/news.release/archives/cpi_02122025.htm"


def snapshot(name="cpi_02122025.htm", body=None):
    return Snapshot(
        body if body is not None else ROOT.joinpath(name).read_bytes(), URL, "2026-11-11T00:00:00Z"
    )


class Capture(PublicFixtures):
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []
        self.snapshots = {}

    def get(self, url, ttl):
        self.calls.append(url)
        raw = super().get(url, ttl)
        result = Snapshot(self.overrides.get(url, raw.body), raw.url, raw.retrieved_at)
        self.snapshots[result.url] = result
        return result


def run(transport=None, **options):
    return cpi_effects(
        transport or Capture(),
        ResearchQuery.model_validate(
            {"dataset": "cpi_effects", "start_month": "2025-01", "end_month": "2025-01", **options}
        ),
    )


def test_reported_cells_decimal_spelling_hash_and_exact_html_positions():
    capture = Capture()
    result = run(capture)
    month = result["records"][0]
    assert month["rate_pct"] == "0.8"
    assert month["reconciliation"]["sum_effect_pt"] == "0.800"
    assert month["stacking"]["complete"]
    assert [c["effect_pt"] for c in month["records"]] == ["0.200", "-0.200", "0.200", "0.600"]
    for item in month["records"]:
        assert item["effect_status"] == "reported" and item["weight_base_month"] == "2024-12"
        for field, evidence in item["field_sources"].items():
            raw = capture.snapshots[evidence["raw_source_url"]]
            assert digest(raw.body) == evidence["raw_sha256"]
            cell = raw.body.decode()[evidence["html_start_char"] : evidence["html_end_char"]]
            assert cell == "<td>" + item[field] + "</td>"
            assert evidence["html_row"] > 1 and evidence["html_column"] > 1
            assert evidence["published_at"] == "2025-02-12"
        assert item["relative_importance_pct"] != item["effect_pt"]
    assert month["records"][1]["contribution_share_pct"] == "-25.00"
    assert Counter(capture.calls) == {ARCHIVE_URL: 1, URL: 1}


def test_core_monthly_uses_own_denominator_and_verified_january_vintage():
    result = run(target="core", seasonal_adjustment="nsa")
    month = result["records"][0]
    assert month["target"] == "core" and month["rate_pct"] == "1.0"
    goods, services = month["records"]
    assert goods["effect_pt"] == "0.300"  # 24/80 * 1%, not the all-items table's 0.200.
    assert services["effect_pt"] == "0.700"
    assert goods["relative_importance_pct"] == "24.000"  # Never relabel a normalized core share.
    assert goods["effect_status"] == "calculated"
    assert goods["field_sources"]["effect_pt"] is None
    ref = goods["calculation"]["inputs"][0]["items"]["core_goods_cpi"]["input_id"]
    original = result["calculation_inputs"][ref]
    assert digest(canonical(original)) == ref
    assert original["annual_new_vintage_check"]["expenditure_weight_year"] == 2023
    assert "2023 Weights" in original["annual_new_vintage_check"]["source"]["html_table"]
    assert month["reconciliation"]["status"] == "within_input_rounding_bounds"


def test_monthly_history_preserves_weight_changes_and_fetches_each_release_once():
    capture = Capture()
    result = run(
        capture,
        target="core",
        seasonal_adjustment="nsa",
        start_month="2024-12",
        end_month="2025-02",
    )
    assert [r["records"][0]["relative_importance_pct"] for r in result["records"]] == [
        "20.000",
        "24.000",
        "24.000",
    ]
    assert [r["records"][0]["weight_base_month"] for r in result["records"]] == [
        "2024-11",
        "2024-12",
        "2025-01",
    ]
    assert all(count == 1 for count in Counter(capture.calls).values())
    assert result["coverage"]["advertised_first_month"] == "2024-12"
    assert result["coverage"]["available_effect_last_month"] == "2025-02"


@pytest.mark.parametrize(
    "combo",
    [
        {"change": "mom", "seasonal_adjustment": "nsa"},
        {"change": "yoy", "seasonal_adjustment": "sa"},
        {"target": "core", "change": "mom", "seasonal_adjustment": "sa"},
        {"target": "core", "change": "yoy", "seasonal_adjustment": "sa"},
    ],
)
def test_unsupported_combinations_are_explicit_without_substitution_or_network(combo):
    capture = Capture()
    result = run(capture, **combo)
    assert result["status"] == "unsupported" and not result["records"]
    assert capture.calls == []


def test_yoy_uses_table_seven_not_table_six():
    body = snapshot().body.replace(b"<td>0.200</td>", b"<td>9.200</td>", 1)
    capture = Capture({URL: body})
    result = run(capture, change="yoy", seasonal_adjustment="nsa")
    food = result["records"][0]["records"][0]
    assert food["effect_pt"] == "0.200"
    assert "Table 7." in food["field_sources"]["effect_pt"]["html_table"]


@pytest.mark.parametrize(
    "categories,overlap,complete",
    [
        (SETS["all_items_basic"], False, True),
        (["shelter_cpi", "rent_cpi"], True, False),
        (["rent_cpi", "owners_equivalent_rent_cpi"], False, False),
        (["energy_cpi", "gasoline_cpi"], True, False),
        (["education_cpi", "core_services_cpi"], True, False),
        (["food_at_home_cpi", "food_away_from_home_cpi"], False, False),
    ],
)
def test_classification_overlap_and_incomplete_sets(categories, overlap, complete):
    result = run(categories=categories)
    meta = result["records"][0]["stacking"]
    assert bool(meta["overlaps"]) == overlap
    assert meta["covers_target"] == complete
    assert meta["ready"] != overlap
    assert not complete or meta["complete"]
    assert CATEGORIES["rent_cpi"]["parent"] == "shelter_cpi"


def test_core_rejects_food_and_energy_effects():
    result = run(target="core", seasonal_adjustment="nsa", categories=["food_cpi"])
    assert result["records"][0]["records"][0]["reason"] == "category_outside_target"
    assert not result["records"][0]["stacking"]["ready"]
    assert stacking(["core_goods_cpi", "core_services_cpi"], "core")["covers_target"]


def test_missing_effect_is_not_zero_or_an_other_component():
    body = snapshot().body.replace(b"<td>-0.200</td>", b"<td>-</td>", 1)
    result = run(Capture({URL: body}))
    month = result["records"][0]
    assert month["records"][1]["effect_pt"] is None
    assert month["records"][1]["reason"] == "source_value_missing"
    assert month["stacking"]["missing_categories"] == ["energy_cpi"]
    assert not month["stacking"]["complete"]
    assert month["reconciliation"]["sum_effect_pt"] is None
    assert result["status"] == "partial"


def test_zero_target_change_keeps_offsetting_effects_without_division_by_zero():
    body = snapshot().body.replace(b"<td>0.8</td>", b"<td>0.0</td>")
    result = run(Capture({URL: body}))
    row = result["records"][0]
    assert row["records"][1]["effect_pt"] == "-0.200"
    assert all(c["contribution_share_pct"] is None for c in row["records"])
    assert row["records"][0]["contribution_share_reason"] == "target_change_is_zero"
    assert row["reconciliation"]["status"] == "difference_exceeds_input_rounding_bounds"
    assert row["reconciliation"]["residual_pt"] == "-0.800"
    assert not row["reconciliation"]["adjusted_to_match"]


def test_published_rounding_residual_is_kept():
    check = reconciliation(["0.123", "0.123", "0.123", "0.123"], "0.5", complete=True)
    assert check["status"] == "within_input_rounding_bounds"
    assert check["residual_pt"] == "0.008"


def test_nsa_footnote_does_not_silently_change_requested_sa_rate():
    body = snapshot().body
    marker = b"Table 6."
    start = body.index(marker)
    before, after = body[:start], body[start:]
    after = after.replace(b"<th>Food</th>", b"<th>Food(4)</th>", 1)
    after = after.replace(
        b"</tbody></table>", b"</tbody></table><p>(4) Not seasonally adjusted.</p>", 1
    )
    result = run(Capture({URL: before + after}))
    food = result["records"][0]["records"][0]
    assert food["rate_pct"] is None and food["effect_pt"] == "0.200"
    assert food["source_rate_adjustment"] == "nsa"
    assert food["reported_inputs"]["rate"]["value"] == "2.0"
    assert food["missing_reasons"]["rate_pct"] == "source_item_rate_is_not_seasonally_adjusted"


@pytest.mark.parametrize(
    "options",
    [
        {"start_month": "2025-01", "end_month": None},
        {"start_month": "2025-13"},
        {"start_month": "2025-02", "end_month": "2025-01"},
        {"start_month": "2020-01", "end_month": "2025-01"},
        {"categories": ["median_cpi_mom"]},
        {"categories": ["trimmed_mean_cpi_mom"]},
        {"categories": ["rent_cpi", "rent_cpi"]},
        {"categories": ["rent_cpi"], "category_set": "all_items_basic"},
        {"series": "cpi"},
        {"seasonal_adjustment": "adjusted"},
    ],
)
def test_invalid_queries_fail_before_transport(options):
    capture = Capture()
    with pytest.raises((AtlasError, ValidationError)):
        run(capture, **options)
    assert capture.calls == []


@pytest.mark.parametrize(
    "old,new,reason",
    [
        (b"January 2025", b"January 2026", "release_table_period_mismatch"),
        (
            b"Relative importance Dec. 2024",
            b"Relative importance Nov. 2024",
            "weight_base_month_mismatch",
        ),
        (b"<td>24.000</td>", b"<td>124.000</td>", "relative_importance_outside_percent_bounds"),
        (b"<td>102.010</td>", b"<td>NaN</td>", "unrecognized_numeric_cell"),
    ],
)
def test_source_period_unit_and_numeric_shape_fail_closed(old, new, reason):
    with pytest.raises(AtlasError) as caught:
        release_tables(snapshot(body=snapshot().body.replace(old, new, 1)), "2025-01", "2025-02-12")
    assert caught.value.details["reason"] == reason


def test_duplicate_rows_are_not_selected_by_order():
    body = snapshot().body
    start = body.index(b"<tr><th>Food</th>")
    end = body.index(b"</tr>", start) + 5
    with pytest.raises(AtlasError, match="Unsupported") as caught:
        release_tables(
            snapshot(body=body[:end] + body[start:end] + body[end:]), "2025-01", "2025-02-12"
        )
    assert caught.value.details["reason"] == "duplicate_expenditure_row"


def test_old_annual_weight_vintage_is_not_selected_by_order():
    body = ROOT.joinpath("2024.htm").read_bytes()
    old = body.replace(b"2023 Weights", b"2022 Weights").replace(b"24.000", b"20.000")
    combined = old + body
    assert annual_weights(snapshot(body=combined), 2024)["core_goods_cpi"]["value"] == "24.000"
    with pytest.raises(AtlasError) as caught:
        annual_weights(snapshot(body=old), 2024)
    assert caught.value.details["reason"] == "new_annual_weight_vintage_not_identified"
    with pytest.raises(AtlasError):
        annual_weights(snapshot(body=b"<h2>Table 1 (2023 Weights).</h2>" + old), 2024)


def test_january_unverified_vintage_keeps_effect_missing():
    weights_url = "https://www.bls.gov/cpi/tables/relative-importance/2024.htm"
    body = ROOT.joinpath("2024.htm").read_bytes().replace(b"24.000", b"23.000")
    result = run(Capture({weights_url: body}), target="core", seasonal_adjustment="nsa")
    assert result["status"] == "unavailable"
    assert all(
        c["reason"] == "new_january_weight_vintage_not_verified"
        for c in result["records"][0]["records"]
    )


def test_core_target_reconciliation_failure_does_not_publish_an_estimate():
    body = snapshot().body
    start = body.index(b"<tr><th>Services less energy services</th>")
    end = body.index(b"</tr>", start)
    body = body[:start] + body[start:end].replace(b"102.010", b"105.000") + body[end:]
    result = run(Capture({URL: body}), target="core", seasonal_adjustment="nsa")
    row = result["records"][0]["records"][0]
    assert row["effect_pt"] is None
    assert row["reason"] == "core_components_do_not_reconcile_with_target_indexes"
    assert result["calculation_inputs"] and result["calculation_checks"]


def synthetic_chain():
    """Independent invented monthly history crossing the January 2025 reweighting."""
    base = release_tables(snapshot(), "2025-01", "2025-02-12")
    releases = {}
    for offset, month in enumerate(months_between("2024-03", "2025-02")):
        doc = copy.deepcopy(base)
        table = doc["tables"][1]
        table["weight_base_month"] = shift_month(month, -1)
        for key in ("core_cpi", "core_goods_cpi", "core_services_cpi"):
            row = table["rows"][key]
            operand = copy.deepcopy(row["index:2025-01"])
            for name in list(row):
                if name.startswith("index:"):
                    del row[name]
            row["index:" + shift_month(month, -1)] = {**operand, "value": f"{100 + offset}.000"}
            row["index:" + month] = {**operand, "value": f"{101 + offset}.000"}
            if month == "2025-02":
                row["index:2024-02"] = {**operand, "value": "100.000"}
            weight = (
                "80.000"
                if key == "core_cpi"
                else (
                    ("20.000" if month < "2025-01" else "24.000")
                    if key == "core_goods_cpi"
                    else ("60.000" if month < "2025-01" else "56.000")
                )
            )
            row["relative_importance_pct"]["value"] = weight
        releases[month] = doc
    weights = {2024: annual_weights(snapshot(body=ROOT.joinpath("2024.htm").read_bytes()), 2024)}
    return releases, weights


def test_yoy_chains_old_and_new_weights_instead_of_latest_weight_times_growth():
    releases, weights = synthetic_chain()
    goods = core_effect("2025-02", "yoy", "core_goods_cpi", releases, weights)
    services = core_effect("2025-02", "yoy", "core_services_cpi", releases, weights)
    # Ten increments at 25/75, two at 30/70, each equals one original index point.
    assert abs(Decimal(goods["effect_pt"]) - Decimal("3.1")) < Decimal("1e-45")
    assert abs(Decimal(services["effect_pt"]) - Decimal("8.9")) < Decimal("1e-45")
    assert Decimal(goods["effect_pt"]) != Decimal("12") * Decimal(".3")
    assert len(goods["monthly_reconciliation"]) == 12


def test_yoy_conflicting_index_vintages_and_missing_months_remain_unavailable():
    releases, weights = synthetic_chain()
    releases["2024-08"]["tables"][1]["rows"]["core_goods_cpi"]["index:2024-07"]["value"] = "999.000"
    assert (
        core_effect("2025-02", "yoy", "core_goods_cpi", releases, weights)["reason"]
        == "index_vintage_conflict_across_releases"
    )
    del releases["2024-04"]
    assert (
        core_effect("2025-02", "yoy", "core_goods_cpi", releases, weights)["missing_month"]
        == "2024-04"
    )


def test_yoy_checks_year_ago_baseline_in_final_release_against_the_monthly_chain():
    releases, weights = synthetic_chain()
    row = releases["2025-02"]["tables"][1]["rows"]["core_cpi"]
    row["index:2024-02"]["value"] = "100.100"
    result = core_effect("2025-02", "yoy", "core_goods_cpi", releases, weights)
    assert result["effect_pt"] is None and result["reason"] == "annual_baseline_vintage_conflict"
    assert result["conflict"]["final_release_baseline"]["source"]["raw_sha256"]
    row["index:2024-02"]["value"] = None
    result = core_effect("2025-02", "yoy", "core_goods_cpi", releases, weights)
    assert result["effect_pt"] is None
    assert result["reason"] == "annual_baseline_not_verified_in_final_release"


def test_missing_archive_months_are_not_inferred_from_advertised_bounds():
    result = run(start_month="2024-10", end_month="2025-01")
    coverage = result["coverage"]
    assert coverage["months_not_linked_as_html"] == ["2024-10", "2024-11"]
    assert coverage["missing_effect_months"] == ["2024-10", "2024-11"]
    assert coverage["available_effect_first_month"] == "2024-12"
    assert result["status"] == "partial"


def test_archive_does_not_follow_external_links_or_guess_release_dates():
    body = (
        ROOT.joinpath("archive.html")
        .read_bytes()
        .replace(b"/news.release/archives/", b"https://evil.invalid/")
    )
    with pytest.raises(AtlasError):
        archive_releases(snapshot(body=body))
    for url in [
        URL + "?x=1",
        URL.replace("https:", "http:"),
        URL.replace("www.bls.gov", "example.com"),
        URL.replace("02122025", "02302025"),
    ]:
        assert cpi_identity(url) is None
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(url)


def test_archive_failure_has_no_false_success_or_guessed_coverage():
    class Failing:
        def get(self, *args):
            raise AtlasError("SOURCE_UNAVAILABLE", "Failed")

    result = run(Failing())
    assert result["status"] == "unavailable"
    assert result["coverage"]["advertised_first_month"] is None
    assert result["coverage"]["months_not_linked_as_html"] == []
    assert result["coverage"]["unretrieved_months"] == ["2025-01"]
    assert result["coverage"]["missing_effect_months"] == ["2025-01"]
    assert result["coverage"]["failures"][0]["stage"] == "archive"


def test_transport_warm_cache_reuses_html_without_charging_bls_api_quota(tmp_path):
    called = []

    def handler(request):
        called.append(str(request.url))
        return httpx.Response(200, content=ROOT.joinpath("archive.html").read_bytes())

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        first = transport.get(ARCHIVE_URL, 3600)
        second = transport.get(ARCHIVE_URL, 3600)
        assert first == second and called == [ARCHIVE_URL]
        with transport.db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM source_daily_usage").fetchone()[0] == 0
    finally:
        transport.close()


def test_sdk_cli_http_catalog_and_compact_evidence_share_contract(tmp_path):
    query = {"dataset": "cpi_effects", "target": "core", "seasonal_adjustment": "nsa"}
    with Atlas("fixture") as atlas, TestClient(create_app(provider="fixture")) as client:
        expected = atlas.get_research(query)
        assert client.post("/v1/research", json=query).json() == expected
        assert (
            atlas.get_cpi_effects(target="core", seasonal_adjustment="nsa")["records"]
            == expected["records"]
        )
        compact = atlas.get_research(
            {**query, "response": {"view": "compact", "fields": ["effect_pt"]}}
        )
        assert compact["records"][0]["records"][0]["weight_base_month"] == "2025-01"
        assert compact["calculation_inputs"]
        catalog = Atlas.research_catalog(dataset="cpi_effects")
        assert len(catalog["cpi_effects"]["supported_combinations"]) == 4
        assert catalog["series_search"]["matched"] == 4
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "cpi-effects",
            "--target",
            "core",
            "--seasonal-adjustment",
            "nsa",
            "--provider",
            "fixture",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["records"] == expected["records"]


@pytest.mark.asyncio
async def test_mcp_exposes_same_effects_and_catalog(tmp_path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(parameters) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool(
            "atlas_get_research", {"query": {"dataset": "cpi_effects"}}
        )
        assert not result.is_error
        assert result.structured_content["records"][0]["records"][1]["effect_pt"] == "-0.200"
        catalog = await session.call_tool("atlas_research_catalog", {"dataset": "cpi_effects"})
        assert not catalog.is_error and catalog.structured_content["cpi_effects"]["mapping_version"]


def test_malformed_html_and_grid_bounds():
    for body in [
        b"<table><table>",
        b'<table><tr><td colspan="1000">1</td></tr></table>',
        b"<table>",
    ]:
        with pytest.raises(AtlasError):
            CPIHTML(body)


@pytest.mark.parametrize(
    "old,new",
    [
        (b"(CPI-U)", b"(CPI-W)"),
        (b"U.S. city average", b"Los Angeles-Long Beach-Anaheim"),
        (b"Table 1.", b"Table 3."),
    ],
)
def test_caption_scope_and_table_number_cannot_inherit_a_preceding_heading(old, new):
    original = snapshot().body
    first_end = original.index(b"</table>") + len(b"</table>")
    first_table = original[original.index(b"<table>") : first_end]
    foreign = first_table.replace(old, new)
    preceding = b"<h2>Table 1. CPI-U (CPI-U): U.S. city average</h2>"
    # Keep the valid Table 1 followed by an unrelated table under a stale heading.
    body = original[:first_end] + preceding + foreign + original[first_end:]
    result = release_tables(snapshot(body=body), "2025-01", "2025-02-12")
    assert set(result["tables"]) == {1, 6, 7}


def test_core_zero_aggregate_keeps_positive_and_negative_effects():
    releases, weights = synthetic_chain()
    rows = releases["2025-02"]["tables"][1]["rows"]
    for key, end, weight in [
        ("core_cpi", "100.000", "80.000"),
        ("core_goods_cpi", "103.000", "20.000"),
        ("core_services_cpi", "99.000", "60.000"),
    ]:
        rows[key]["index:2025-01"]["value"] = "100.000"
        rows[key]["index:2025-02"]["value"] = end
        rows[key]["relative_importance_pct"]["value"] = weight
    goods = core_effect("2025-02", "mom", "core_goods_cpi", releases, weights)
    services = core_effect("2025-02", "mom", "core_services_cpi", releases, weights)
    assert Decimal(goods["effect_pt"]) == Decimal("0.75")
    assert Decimal(services["effect_pt"]) == Decimal("-0.75")
    assert goods["monthly_reconciliation"][0]["target_index_change_pct"] == "0"


def test_annual_vintage_in_separate_heading_and_html_header_line_breaks():
    body = (
        ROOT.joinpath("2024.htm")
        .read_bytes()
        .replace(
            b"<table><caption>Table 1 (2023 Weights). ",
            b"<h2>Table 1 (2023 Weights).</h2><table><caption>",
        )
    )
    assert annual_weights(snapshot(body=body), 2024)["core_cpi"]["value"] == "80.000"
    body = snapshot().body.replace(
        b"Relative importance Dec. 2024", b"Relative<br>importance<br>Dec.<br>2024"
    )
    parsed = release_tables(snapshot(body=body), "2025-01", "2025-02-12")
    assert parsed["tables"][6]["rows"]["food_cpi"]["effect_pt"]["value"] == "0.200"


def test_deadline_and_document_limits_return_resume_months(monkeypatch):
    from atlas_terminal import research_cpi_effects as module

    monkeypatch.setattr(module, "MAX_DOCUMENTS", 2)
    capture = Capture()
    result = run(capture, start_month="2024-12", end_month="2025-02")
    assert len(capture.calls) == 2
    assert result["coverage"]["retrieved_months"] == ["2024-12"]
    assert result["coverage"]["unretrieved_months"] == ["2025-01", "2025-02"]
    assert result["coverage"]["failures"][0]["reason"] == "document_limit_reached"

    def expired(*args):
        raise AtlasError(
            "SOURCE_UNAVAILABLE", "Expired", details={"reason": "source_deadline_exceeded"}
        )

    monkeypatch.setattr(module, "remaining_timeout", expired)
    capture = Capture()
    result = run(capture)
    assert capture.calls == []
    assert result["coverage"]["failures"][0]["reason"] == "source_deadline_exceeded"
