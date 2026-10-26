"""ECB/RBA identity, economic conventions and original-cell research contracts."""

import csv
import io
import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.regional_bond_sources import (
    RBA_URL,
    ecb_curve_identity,
    ecb_curve_url,
    parse_ecb_curve,
    parse_rba_curve,
)
from atlas_terminal.research_models import parse_research
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import source_shapes
from atlas_terminal.util import canonical, digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def snapshot(name):
    return Snapshot(
        RESOURCE.joinpath(name).read_bytes(),
        "https://fixtures.atlas.invalid/" + name,
        "2026-10-20T00:00:00Z",
    )


def changed_csv(name, change):
    rows = list(csv.reader(io.StringIO(RESOURCE.joinpath(name).read_text())))
    change(rows)
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\n").writerows(rows)
    return Snapshot(
        out.getvalue().encode(),
        "https://fixtures.atlas.invalid/changed.csv",
        "2026-10-20T00:00:00Z",
    )


@pytest.mark.parametrize(
    "series,expected",
    [
        ("euro_aaa_spread_10y_2y", "60"),
        ("euro_aaa_spread_30y_5y", "90"),
        ("euro_aaa_spread_30y_10y", "60"),
        ("euro_aaa_butterfly_2y_5y_10y", "0"),
        ("euro_aaa_yield_change_2y", "10"),
        ("euro_aaa_yield_change_5y", "10"),
        ("euro_aaa_yield_change_10y", "20"),
        ("euro_aaa_yield_change_30y", "20"),
        ("euro_all_spread_10y_2y", "70"),
        ("euro_all_spread_30y_5y", "80"),
        ("euro_all_spread_30y_10y", "60"),
        ("euro_all_butterfly_2y_5y_10y", "30"),
        ("euro_all_yield_change_2y", "10"),
        ("euro_all_yield_change_5y", "30"),
        ("euro_all_yield_change_10y", "20"),
        ("euro_all_yield_change_30y", "30"),
        ("australia_spread_10y_2y", "60"),
        ("australia_spread_10y_3y", "50"),
        ("australia_butterfly_2y_5y_10y", "20"),
        ("australia_yield_change_2y", "10"),
        ("australia_yield_change_3y", "10"),
        ("australia_yield_change_5y", None),
        ("australia_yield_change_10y", "30"),
    ],
)
def test_source_local_curve_math_matches_hand_calculations_and_preserves_conventions(
    series, expected
):
    with Atlas("fixture") as atlas:
        row = atlas.get_research(
            {"dataset": "macro_analytics", "series": series, "year": 2026, "limit": 1}
        )["records"][0]
    assert row["value"] is None if expected is None else Decimal(row["value"]) == Decimal(expected)
    assert row["date"] == "2026-10-19" and row["unit"] == "basis_points"
    assert row["currency"] == ("AUD" if series.startswith("australia") else "EUR")
    assert row["compounding"] == (
        "source_published_convention" if series.startswith("australia") else "continuous"
    )
    assert row["source_estimated"]
    if "change" in series:
        assert row["comparison_date"] == "2026-10-16" and row["elapsed_calendar_days"] == 3
    else:
        assert {item["date"] for item in row["inputs"]} == {row["date"]}


def test_ecb_rating_universes_units_nulls_and_original_csv_cells_are_distinct():
    for curve in ("ecb_aaa", "ecb_all"):
        original = snapshot(curve + "_2026.csv")
        parsed, document = parse_ecb_curve(original, curve, 2026)
        rows = list(csv.reader(io.StringIO(original.body.decode())))
        assert document["csv_header"] == "KEY" and document["csv_row"] == 1
        for daily in parsed.values():
            for record in daily.values():
                source = record["source"]
                raw = rows[source["csv_row"] - 1][source["csv_column"] - 1]
                assert raw == record["source_value"]
                assert source["raw_sha256"] == digest(original.body)
                assert record["rate_type"] == "zero_coupon_spot"
                assert record["compounding"] == "continuous"
        assert parsed["2026-10-19"]["10YEAR"]["value"] == ("2.30" if curve == "ecb_aaa" else "2.90")
        assert parsed["2026-10-15"]["3MONTH"]["value"] == "-0.10"
        if curve == "ecb_aaa":
            assert parsed["2026-10-16"]["7YEAR"]["value"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("KEY", "YC.B.U2.EUR.4F.G_N_C.SV_C_YM.SR_3M"),
        ("CURRENCY", "USD"),
        ("FREQ", "M"),
        ("REF_AREA", "US"),
        ("UNIT", "PC"),
        ("UNIT_MULT", "2"),
        ("OBS_VALUE", "NaN"),
        ("TIME_PERIOD", "2026-02-30"),
        ("TIME_PERIOD", "2025-10-15"),
    ],
)
def test_ecb_rejects_wrong_identity_scale_frequency_dates_and_nonfinite_numbers(field, value):
    body = changed_csv(
        "ecb_aaa_2026.csv", lambda rows: rows[1].__setitem__(rows[0].index(field), value)
    )
    with pytest.raises(AtlasError) as caught:
        parse_ecb_curve(body, "ecb_aaa", 2026)
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_ecb_duplicate_rows_and_forecasts_do_not_become_observed_rates():
    body = changed_csv("ecb_aaa_2026.csv", lambda rows: rows.append(deepcopy(rows[1])))
    with pytest.raises(AtlasError):
        parse_ecb_curve(body, "ecb_aaa", 2026)
    body = changed_csv(
        "ecb_aaa_2026.csv", lambda rows: rows[1].__setitem__(rows[0].index("OBS_STATUS"), "F")
    )
    rows, _ = parse_ecb_curve(body, "ecb_aaa", 2026)
    assert rows["2026-10-15"]["3MONTH"]["value"] is None
    assert rows["2026-10-15"]["3MONTH"]["observation_status"] == "F"


def test_ecb_multi_year_reads_preserve_missing_tenor_evidence_from_the_correct_file():
    old = changed_csv(
        "ecb_aaa_2026.csv",
        lambda rows: [
            row.__setitem__(
                rows[0].index("TIME_PERIOD"),
                row[rows[0].index("TIME_PERIOD")].replace("2026-10", "2025-12"),
            )
            for row in rows[1:]
        ],
    )
    current = changed_csv(
        "ecb_aaa_2026.csv",
        lambda rows: rows.__setitem__(
            slice(None),
            [row for row in rows if not (row[0].endswith("SR_7Y") and "2026-10-19" in row)],
        ),
    )
    calls = []

    class Sources(PublicFixtures):
        def get(self, url, ttl):
            selection = ecb_curve_identity(url)
            assert selection is not None
            calls.append(selection[1])
            return old if selection[1] == 2025 else current

    with Atlas("fixture") as atlas:
        atlas._research.public = Sources()
        result = atlas.get_research(
            {
                "dataset": "euro_area_yields",
                "series": "aaa_spot",
                "start_date": "2025-12-01",
                "end_date": "2026-10-31",
                "limit": 11,
            }
        )
    missing = next(row for row in result["records"] if row["maturity"] == "7YEAR")
    assert missing["value"] is None and missing["source"]["selected_year"] == 2026
    assert missing["source"]["raw_sha256"] == current.sha256
    assert calls == [2025, 2026]


def test_rba_maps_series_ids_instead_of_positions_and_excludes_indexed_bonds():
    original = snapshot("rba_yields.csv")
    parsed, _ = parse_rba_curve(original)
    assert set(parsed["2026-10-19"]) == {"2YEAR", "3YEAR", "5YEAR", "10YEAR"}
    assert parsed["2026-10-19"]["10YEAR"]["value"] == "3.80"
    assert parsed["2026-10-16"]["5YEAR"]["value"] is None
    assert parsed["2026-10-19"]["10YEAR"]["source"]["csv_header"] == "FCMYGBAG10D"
    assert parsed["2026-10-19"]["10YEAR"]["source_provider"] == "RBA"
    cells = list(csv.reader(io.StringIO(original.body.decode())))
    for daily in parsed.values():
        for record in daily.values():
            source = record["source"]
            assert cells[source["csv_row"] - 1][source["csv_column"] - 1] == record["source_value"]
    shape = source_shapes([original])[0]
    assert shape["format"] == "csv"


@pytest.mark.parametrize(
    "mode",
    ["units", "frequency", "monthly_id", "duplicate_id", "duplicate_date", "bad_date", "nonfinite"],
)
def test_rba_rejects_mismatched_metadata_and_ambiguous_observations(mode):
    def edit(rows):
        if mode == "units":
            rows[4][3] = "Dollars"
        elif mode == "frequency":
            rows[2][3] = "Monthly"
        elif mode == "monthly_id":
            rows[6][3] = "FCMYGBAG10"
        elif mode == "duplicate_id":
            rows[6][1] = "FCMYGBAG10D"
        elif mode == "duplicate_date":
            rows[9][0] = rows[8][0]
        elif mode == "bad_date":
            rows[9][0] = "30-Feb-2026"
        else:
            rows[9][3] = "Infinity"

    with pytest.raises(AtlasError) as caught:
        parse_rba_curve(changed_csv("rba_yields.csv", edit))
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_new_endpoints_use_fixed_requests_and_explicit_csv_media_type(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["accept"] == "text/csv"
        selection = ecb_curve_identity(str(request.url))
        name = selection[0] + "_2026.csv" if selection else "rba_yields.csv"
        return httpx.Response(200, content=RESOURCE.joinpath(name).read_bytes())

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    try:
        for url in (ecb_curve_url("ecb_aaa", 2026), ecb_curve_url("ecb_all", 2026), RBA_URL):
            assert transport.get(url, 21600).url == url
        for url in (
            ecb_curve_url("ecb_aaa", 2026).replace("G_N_A", "G_N_X"),
            ecb_curve_url("ecb_aaa", 2026) + "&redirect=evil",
            RBA_URL + "?table=f3",
        ):
            with pytest.raises(AtlasError):
                transport.get(url, 21600)
    finally:
        transport.close()
    assert len(requests) == 3


def test_regional_presets_preview_same_connection_and_preserve_cross_source_dates():
    with Atlas("fixture") as atlas:
        for name, count in [
            ("euro_area_bond_context", 8),
            ("australia_bond_context", 7),
            ("global_bond_context", 10),
        ]:
            preview = atlas.get_research_preset({"preset": name, "year": 2026, "preview": True})
            result = atlas.get_research_preset({"preset": name, "year": 2026})
            assert result["status"] == "ok" and len(result["overview"]) == count
            assert preview["plan"] == result["plan"]
            assert not result["atomic_snapshot"]
            for item in result["batch"]["items"]:
                assert item["status"] == "ok" and item["result"]["is_synthetic"]
        eu = atlas.research_catalog(search="ユーロ圏", country="EA", frequency="daily")
        au = atlas.research_catalog(search="豪州", country="AU", frequency="daily")
        assert eu["series_search"]["matched"] >= 2 and au["series_search"]["matched"] >= 2
        for row in eu["series_search"]["records"] + au["series_search"]["records"]:
            parse_research(row["query"])


def test_regional_quality_gates_filtered_baselines_and_calendar_panels():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(
            {
                "dataset": "macro_analytics",
                "series": "euro_aaa_yield_change_10y",
                "start_date": "2026-10-19",
                "end_date": "2026-10-19",
            }
        )
        assert result["records"][0]["comparison_date"] == "2026-10-16"
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(
                {
                    "dataset": "macro_analytics",
                    "series": "australia_yield_change_5y",
                    "year": 2026,
                    "requirements": {"allow_missing": False},
                }
            )
        assert caught.value.code == "QUALITY_NOT_MET"
        panel = atlas.get_research(
            {
                "dataset": "macro_panel",
                "frequency": "monthly",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
                "items": [
                    {
                        "id": "australia_slope",
                        "aggregation": "last",
                        "query": {
                            "dataset": "macro_analytics",
                            "series": "australia_spread_10y_2y",
                        },
                    }
                ],
            }
        )
        assert Decimal(panel["records"][0]["records"][0]["value"]) == 60


@pytest.mark.parametrize(
    "query",
    [
        {"dataset": "euro_area_yields"},
        {"dataset": "euro_area_yields", "series": "bund"},
        {"dataset": "euro_area_yields", "series": "aaa_spot", "year": 2003},
        {"dataset": "australia_yields", "year": 2012},
        {"dataset": "macro_analytics", "series": "euro_aaa_spread_10y_2y", "year": 2003},
        {"dataset": "macro_analytics", "series": "australia_spread_10y_2y", "year": 2012},
    ],
)
def test_invalid_regional_queries_fail_before_source_construction(query):
    with Atlas("public") as atlas, pytest.raises(AtlasError) as caught:
        atlas.get_research(query)
    assert caught.value.code == "INVALID_INPUT" and atlas._research.public is None


def test_regional_http_cli_and_csv_compact_evidence_keep_curve_basis(tmp_path):
    query = {"dataset": "euro_area_yields", "series": "aaa_spot", "year": 2026, "limit": 11}
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query)
    app = create_app(provider="fixture", directory=tmp_path / "http")
    try:
        with TestClient(app) as client:
            assert client.post("/v1/research", json=query).json() == expected
            result = client.post(
                "/v1/research", json={**query, "response": {"view": "compact", "fields": ["value"]}}
            ).json()
            row = result["records"][0]
            assert row["compounding"] == "continuous" and row["curve_universe"].endswith(
                "aaa_central_governments"
            )
            evidence = client.get("/v1/research/evidence/" + row["source"]["evidence_id"]).json()
            assert evidence["evidence"]["csv_header"] == "OBS_VALUE"
            assert digest(canonical(evidence["evidence"])) == row["source"]["evidence_id"]
    finally:
        app.state.orders.close()
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "euro_area_yields",
            "--series",
            "aaa_spot",
            "--year",
            "2026",
            "--limit",
            "11",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(run.stdout) == expected


async def test_one_official_mcp_session_reads_europe_australia_and_evidence(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp-state")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert len(tools.tools) == 6
        result = await session.call_tool(
            "atlas_get_research_preset",
            {
                "query": {
                    "preset": "global_bond_context",
                    "year": 2026,
                    "include": ["euro_aaa_curve", "australia_curve"],
                }
            },
        )
        assert not result.is_error and result.structured_content["status"] == "ok"
        items = result.structured_content["batch"]["items"]
        assert items[0]["result"]["records"][0]["currency"] == "EUR"
        assert items[1]["result"]["records"][0]["currency"] == "AUD"
        evidence_id = items[1]["result"]["records"][0]["source"]["evidence_id"]
        evidence = await session.call_tool("atlas_get_evidence", {"evidence_id": evidence_id})
        assert (
            not evidence.is_error
            and evidence.structured_content["evidence"]["csv_header"] == "FCMYGBAG2D"
        )
