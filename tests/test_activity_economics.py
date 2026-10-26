"""Fictional activity source contracts, exact evidence and shared research interfaces."""

import json
import subprocess
import sys
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.activity_catalog import ACTIVITY_SERIES
from atlas_terminal.activity_sources import (
    G17_URLS,
    STATCAN_BASE,
    activity_identity,
    census_url,
    fred_url,
    statcan_url,
    world_bank_url,
)
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import source_shapes
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
DATES = {"start_date": "2023-01-01", "end_date": "2026-12-31"}
PRESETS = (
    "us_activity_extended",
    "japan_external_corporate",
    "australia_macro_overview",
    "canada_macro_overview",
    "china_macro_overview",
)


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)


def query(series="us_retail_sales", **extra):
    return {"dataset": "activity_economics", "series": series, **DATES, **extra}


def raw(name):
    return json.loads(RESOURCE.joinpath(name).read_bytes())


class Changed(PublicFixtures):
    def __init__(self, name, transform):
        self.name, self.transform = name, transform

    def get(self, url, ttl):
        snapshot = super().get(url, ttl)
        if snapshot.url.endswith("/" + self.name):
            return Snapshot(self.transform(snapshot.body), snapshot.url, snapshot.retrieved_at)
        return snapshot


def changed(series, name, transform, **extra):
    with Atlas("fixture") as atlas:
        atlas._research.public = Changed(name, transform)
        return atlas.get_research(query(series, **extra))


@pytest.mark.parametrize("series", ACTIVITY_SERIES)
def test_every_curated_source_keeps_frequency_scale_and_exact_source_identity(series):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(series))
        compact = atlas.get_research(
            query(series, response={"view": "compact", "fields": ["value"]})
        )
        for row, short in zip(result["records"], compact["records"], strict=True):
            assert row["value"] is None or isinstance(row["value"], str)
            assert row["country"] == ACTIVITY_SERIES[series]["country"]
            assert row["frequency"] == ACTIVITY_SERIES[series]["frequency"]
            assert row["annualized"] == ACTIVITY_SERIES[series]["annualized"]
            assert row["published_at"] is None
            assert short["value"] == row["value"] and short["unit"] == row["unit"]
            assert short["annualized"] == row["annualized"]
            evidence = atlas.get_evidence(short["source"]["evidence_id"])["evidence"]
            assert evidence == row["source"]
            name = evidence["raw_source_url"].rsplit("/", 1)[-1]
            assert evidence["raw_sha256"] == digest(RESOURCE.joinpath(name).read_bytes())
    assert result["is_synthetic"] and len(result["records"]) == 3
    assert result["source_id"] == ACTIVITY_SERIES[series]["source_id"]
    assert result["attributions"][0]["terms_url"].startswith("https://")


def test_monthly_sales_and_annual_rate_housing_are_not_interchanged():
    with Atlas("fixture") as atlas:
        sales = atlas.get_research(query())["records"]
        housing = atlas.get_research(query("us_housing_starts"))["records"]
    assert [row["value"] for row in sales] == [None, "0", "100.125"]
    assert sales[0]["source_value"] == "D"
    assert sales[0]["date"] == "2026-09-30" and sales[0]["unit"] == "million_USD"
    assert not sales[0]["annualized"] and housing[0]["annualized"]
    assert housing[0]["unit"] == "thousand_dwellings"


@pytest.mark.parametrize(
    "fault",
    [
        "category_code",
        "data_type_code",
        "seasonally_adj",
        "error_data",
        "us",
        "duplicate",
        "width",
        "period",
        "numeric",
        "header",
    ],
)
def test_census_rejects_wrong_dimensions_error_estimates_and_malformed_cells(fault):
    name = "activity_us_retail_sales_data.json"
    body = raw(name)
    if fault in body[0]:
        body[1][body[0].index(fault)] = "wrong"
    elif fault == "duplicate":
        body.append(body[1])
    elif fault == "width":
        body[1].append("unexpected")
    elif fault == "period":
        body[1][body[0].index("time")] = "2026-13"
    elif fault == "numeric":
        body[1][body[0].index("cell_value")] = "NaN"
    else:
        body[0].append("time")
    with pytest.raises(AtlasError) as error:
        changed("us_retail_sales", name, lambda _: json.dumps(body).encode())
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "fault",
    [
        "frequency",
        "units",
        "adjustment",
        "date",
        "count",
        "transform",
        "offset",
        "fractional",
        "negative",
    ],
)
def test_claims_require_documented_week_convention_and_untransformed_complete_counts(fault):
    meta = fault in {"frequency", "units", "adjustment"}
    name = f"activity_us_initial_claims_{'metadata' if meta else 'data'}.json"
    body = raw(name)
    if meta:
        key = {
            "frequency": "frequency",
            "units": "units",
            "adjustment": "seasonal_adjustment_short",
        }[fault]
        body["seriess"][0][key] = "wrong"
    elif fault in {"count", "offset"}:
        body[fault] += 1
    elif fault == "transform":
        body["units"] = "pch"
    elif fault == "date":
        body["observations"][0]["date"] = "2026-10-04"
    else:
        body["observations"][0]["value"] = "0.5" if fault == "fractional" else "-1"
    with pytest.raises(AtlasError) as error:
        changed("us_initial_claims", name, lambda _: json.dumps(body).encode())
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "status,usable",
    [
        (0, True),
        (1, False),
        (2, False),
        (3, True),
        (4, True),
        (5, True),
        (6, True),
        (7, True),
        (8, False),
        (9, False),
        (10, False),
    ],
)
def test_statcan_documented_status_flags_distinguish_true_zero_from_rounded_or_missing(
    status, usable
):
    name = "activity_ca_cpi_data.json"
    body = raw(name)
    body[0]["object"]["vectorDataPoint"][-1]["statusCode"] = status
    result = changed("ca_cpi", name, lambda _: json.dumps(body).encode())
    row = result["records"][0]
    assert row["value"] == ("0" if usable else None)
    assert row["source_value"] == 0 and row["source_status"] == status
    assert row["source_release_time"] == "2026-10-20T08:30"
    assert row["published_at"] is None
    assert result["records"][1]["value"] == "0"


def test_statcan_suppression_and_scale_are_retained_without_rescaling():
    name = "activity_ca_gdp_real_data.json"
    body = raw(name)
    body[0]["object"]["vectorDataPoint"][-1].update(statusCode=0, securityLevelCode=1, value=123)
    result = changed("ca_gdp_real", name, lambda _: json.dumps(body).encode())
    row = result["records"][0]
    assert row["value"] is None and row["source_value"] == 123
    assert row["source_security_level"] == 1 and row["annualized"]
    assert row["unit_multiplier"] == 6 and row["unit"] == "Canadian dollars"
    assert result["records"][-1]["value"] == "103.12345678901234567890"
    assert row["metadata_source"]["request_method"] == "POST"


@pytest.mark.parametrize(
    "fault",
    [
        "vector",
        "table",
        "coordinate",
        "frequency",
        "scalar",
        "status",
        "symbol",
        "security",
        "period",
        "unit",
        "terminated",
    ],
)
def test_statcan_identity_metadata_and_unknown_flags_fail_closed(fault):
    meta = fault in {"unit", "terminated"}
    name = f"activity_ca_cpi_{'metadata' if meta else 'data'}.json"
    body = raw(name)
    obj = body[0]["object"]
    if fault in {"vector", "table", "coordinate", "unit", "terminated"}:
        key = {
            "vector": "vectorId",
            "table": "productId",
            "coordinate": "coordinate",
            "unit": "memberUomCode",
            "terminated": "terminated",
        }[fault]
        obj[key] = 999
    else:
        key = {
            "frequency": "frequencyCode",
            "scalar": "scalarFactorCode",
            "status": "statusCode",
            "symbol": "symbolCode",
            "security": "securityLevelCode",
            "period": "refPer",
        }[fault]
        obj["vectorDataPoint"][0][key] = "2026-Q1" if fault == "period" else 999
    with pytest.raises(AtlasError) as error:
        changed("ca_cpi", name, lambda _: json.dumps(body).encode())
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "fault",
    [
        "country",
        "indicator",
        "period",
        "duplicate",
        "pages",
        "total",
        "source",
        "metadata",
        "status",
        "numeric",
    ],
)
def test_world_bank_does_not_mix_countries_frequencies_pages_or_undocumented_status(fault):
    name = f"activity_cn_gdp_growth_annual_{'metadata' if fault == 'metadata' else 'data'}.json"
    body = raw(name)
    if fault == "metadata":
        body[1][0]["source"] = None
    elif fault == "country":
        body[1][0]["countryiso3code"] = "USA"
    elif fault == "indicator":
        body[1][0]["indicator"]["id"] = "wrong"
    elif fault == "period":
        body[1][0]["date"] = "2023-Q1"
    elif fault == "duplicate":
        body[1][1]["date"] = body[1][0]["date"]
    elif fault in {"pages", "total"}:
        body[0][fault] += 1
    elif fault == "source":
        body[0]["sourceid"] = "1"
    elif fault == "status":
        body[1][0].pop("obs_status")
    else:
        body[1][0]["value"] = "Infinity"
    with pytest.raises(AtlasError) as error:
        changed("cn_gdp_growth_annual", name, lambda _: json.dumps(body).encode())
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_world_bank_annual_growth_and_flagged_cells_keep_notes_and_source_values():
    name = "activity_cn_gdp_growth_annual_data.json"
    body = raw(name)
    body[1][-1]["obs_status"] = "F"
    result = changed("cn_gdp_growth_annual", name, lambda _: json.dumps(body).encode())
    row = result["records"][0]
    assert row["value"] is None and row["source_value"] == "103.12345678901234567890"
    assert row["observation_status"] == "F" and row["frequency"] == "annual"
    assert row["date"] == "2025-12-31" and not row["annualized"]
    assert row["unit"] == "percent" and row["original_producers"]
    assert result["records"][-1]["value"] == "-1.125"


@pytest.mark.parametrize("series", ["ca_cpi", "cn_gdp_growth_annual"])
def test_json_numeric_decimal_cells_and_preserved_source_values_are_lossless(series):
    name = f"activity_{series}_data.json"
    exact = b"103.12345678901234567890"
    result = changed(
        series,
        name,
        lambda b: b.replace(b'"' + exact + b'"', exact),
        response={"view": "compact", "fields": ["value"]},
    )
    matched = [row for row in result["records"] if row["value"] == exact.decode()]
    assert len(matched) == 1 and matched[0]["source_value"] == exact.decode()
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "series,original",
    [
        ("us_retail_sales", b"2026-07"),
        ("ca_cpi", b"2026-07"),
        ("cn_gdp_growth_annual", b"2023"),
    ],
)
def test_zero_years_are_sanitized_source_errors(series, original):
    with pytest.raises(AtlasError) as error:
        changed(
            series,
            f"activity_{series}_data.json",
            lambda b: b.replace(original, b"0000" + original[4:]),
        )
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_g17_fixed_width_evidence_resolves_the_original_cell_and_omits_blank_future_tail():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query("us_industrial_production"))
    assert [row["date"] for row in result["records"]] == ["2026-03-31", "2026-02-28", "2026-01-31"]
    assert [row["value"] for row in result["records"]] == [None, "0.0000", "101.1234"]
    source = result["records"][-1]["source"]
    body = RESOURCE.joinpath("g17_ip_sa.txt").read_bytes()
    line = body.decode().splitlines()[source["text_line"] - 1]
    assert line[source["text_column_start"] - 1 : source["text_column_end"]].strip() == "101.1234"
    assert source["raw_sha256"] == digest(body)
    shape = source_shapes([Snapshot(body, G17_URLS[0], source["retrieved_at"])])
    assert shape[0]["shape_sha256"]


@pytest.mark.parametrize(
    "fault", ["duplicate", "year", "description", "column", "numeric", "binary"]
)
def test_g17_rejects_ambiguous_or_changed_fixed_width_formats(fault):
    body = RESOURCE.joinpath("g17_ip_sa.txt").read_bytes()
    if fault == "duplicate":
        body += body.splitlines(keepends=True)[1]
    elif fault == "year":
        body = body.replace(b"2026", b"1899")
    elif fault == "description":
        body = body.split(b"\n", 1)[1]
    elif fault == "column":
        body = body.replace(b"2026     ", b"2026bad  ")
    elif fault == "numeric":
        body = body.replace(b"101.1234", b"101,1234")
    else:
        body += b"\x00"
    with pytest.raises(AtlasError) as error:
        changed("us_industrial_production", "g17_ip_sa.txt", lambda _: body)
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_curated_sdmx_selector_drift_is_coverage_failure_and_forecasts_are_missing():
    name = "activity_au_gdp_real_structure.xml"
    with pytest.raises(AtlasError) as error:
        changed("au_gdp_real", name, lambda b: b.replace(b'id="GPM"', b'id="REMOVED"'))
    assert error.value.code == "INCOMPLETE_COVERAGE"
    result = changed(
        "au_gdp_real",
        "activity_au_gdp_real_data.xml",
        lambda b: b.replace(b'id="OBS_STATUS" value="A"', b'id="OBS_STATUS" value="F"'),
    )
    assert all(row["value"] is None for row in result["records"])
    assert result["records"][-1]["source_value"] == "101.12345678901234567890"
    assert result["records"][-1]["unit_multiplier"] == 6


@pytest.mark.parametrize(
    "series", ["au_gdp_real", "au_unemployment", "au_cpi_quarterly", "cn_cpi", "cn_cpi_yoy"]
)
def test_curated_sdmx_rejects_other_geographies(series):
    spec = ACTIVITY_SERIES[series]
    geo = (
        b'"CHN"'
        if spec["country"] == "CN"
        else b'"50"'
        if series == "au_cpi_quarterly"
        else b'"AUS"'
    )
    with pytest.raises(AtlasError) as error:
        changed(series, f"activity_{series}_data.xml", lambda b: b.replace(geo, b'"OTHER"'))
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "url",
    [
        census_url("us_retail_sales", "2023-01-01", "2026-12-31"),
        fred_url("us_continued_claims"),
        fred_url("us_initial_claims", "2023-01-01", "2026-12-31"),
        statcan_url("ca_gdp_real"),
        statcan_url("ca_cpi", "2023-01-01", "2026-12-31"),
        world_bank_url("cn_gdp_nominal_annual"),
        world_bank_url("cn_gdp_growth_annual", "2023-01-01", "2026-12-31"),
        *G17_URLS,
    ],
)
def test_canonical_endpoints_reject_extra_credentials_fragments_hosts_and_unbounded_windows(url):
    assert activity_identity(url) is not None
    PublicTransport.validate_url(url)
    for bad in (
        url + "&api_key=unexpected",
        url + "#fragment",
        url.replace("https://", "https://evil.invalid/"),
        url.replace("2023", "1800"),
        url.replace("2023", "1900"),
    ):
        if bad == url:
            continue
        assert activity_identity(bad) is None
        with pytest.raises(AtlasError, match="endpoint catalog"):
            PublicTransport.validate_url(bad)


@pytest.mark.parametrize(
    "source,env,series,builder,size,param",
    [
        ("census", "CENSUS_API_KEY", "us_retail_sales", census_url, 40, "key"),
        ("fred", "FRED_API_KEY", "us_initial_claims", fred_url, 32, "api_key"),
    ],
)
def test_api_credentials_are_outbound_only_and_required_even_for_cache_hits(
    source, env, series, builder, size, param, tmp_path, monkeypatch, caplog
):
    private = "a" * size
    requests = []
    body = RESOURCE.joinpath(f"activity_{series}_data.json").read_bytes()

    def handler(request):
        requests.append(request)
        assert request.url.params[param] == private
        return httpx.Response(200, content=body)

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    url = builder(series, "2023-01-01", "2026-12-31")
    with pytest.raises(AtlasError) as error:
        transport.get(url, 3600)
    assert error.value.code == "CONFIG_REQUIRED" and not requests
    monkeypatch.setenv(env, private)
    snapshot = transport.get(url, 3600)
    assert snapshot.url == url and private not in snapshot.url
    assert snapshot.body == body and len(requests) == 1
    assert transport.get(url, 3600).sha256 == snapshot.sha256 and len(requests) == 1
    monkeypatch.delenv(env)
    with pytest.raises(AtlasError) as error:
        transport.get(url, 3600)
    assert error.value.code == "CONFIG_REQUIRED"
    assert private not in caplog.text
    assert all(
        private.encode() not in path.read_bytes() for path in tmp_path.glob("cache.sqlite3*")
    )


def test_statcan_metadata_uses_a_read_only_post_with_reproducible_canonical_identity(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, content=RESOURCE.joinpath("activity_ca_cpi_metadata.json").read_bytes()
        )

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    snapshot = transport.get(statcan_url("ca_cpi"), 3600)
    assert snapshot.url == statcan_url("ca_cpi")
    assert requests[0].method == "POST"
    assert str(requests[0].url) == STATCAN_BASE + "getSeriesInfoFromVector"
    assert json.loads(requests[0].content) == [{"vectorId": 41690973}]


@pytest.mark.parametrize(
    "source,env,size,builder,series",
    [
        ("census", "CENSUS_API_KEY", 40, census_url, "us_retail_sales"),
        ("fred", "FRED_API_KEY", 32, fred_url, "us_initial_claims"),
    ],
)
def test_source_echoed_credentials_never_enter_cache_or_errors(
    source, env, size, builder, series, tmp_path, monkeypatch, caplog
):
    private = "b" * size
    monkeypatch.setenv(env, private)
    transport = PublicTransport(
        cache_path=tmp_path / "echo.sqlite3",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"echo": private})
            )
        ),
    )
    with pytest.raises(AtlasError) as error:
        transport.get(builder(series, "2023-01-01", "2026-12-31"), 3600)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert private not in str(error.value) and private not in caplog.text
    assert all(private.encode() not in path.read_bytes() for path in tmp_path.glob("echo.sqlite3*"))


def test_activity_schema_discovery_source_requirements_and_weekly_filter():
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    validator.validate(query())
    for bad in ({"dataset": "activity_economics"}, query("invented"), query(identifier="AAPL")):
        with pytest.raises(ValidationError):
            validator.validate(bad)
        with pytest.raises(AtlasError):
            parse_research(bad)
    catalog = Atlas.research_catalog(
        dataset="activity_economics", country="CA", frequency="monthly"
    )
    assert {r["query"]["series"] for r in catalog["series_search"]["records"]} == {
        "ca_cpi",
        "ca_unemployment",
        "ca_gdp_real",
    }
    assert len(Atlas.research_catalog(frequency="weekly")["series_search"]["records"]) == 2
    with Atlas("public") as atlas:
        diagnosis = atlas.research_diagnostics(
            {"sources": ["census", "fred", "abs", "statcan", "world_bank"]}
        )
    assert [s["configuration"] for s in diagnosis["sources"]] == ["configuration_required"] * 2 + [
        "ready"
    ] * 3
    assert not diagnosis["network_checks_executed"]


@pytest.mark.parametrize("preset", PRESETS)
def test_new_presets_preview_without_network_then_execute_the_same_plan(preset, monkeypatch):
    with Atlas("public") as atlas:

        def forbidden(*args, **kwargs):
            pytest.fail("Preview attempted a data read")

        monkeypatch.setattr(atlas, "get_research", forbidden)
        preview = atlas.get_research_preset({"preset": preset, **DATES, "preview": True})
        assert preview["status"] == "preview" and atlas._research.public is None
    with Atlas("fixture") as atlas:
        result = atlas.get_research_preset({"preset": preset, **DATES})
    assert result["status"] == "ok" and preview["plan"] == result["plan"]
    catalog = next(p for p in Atlas.research_catalog()["presets"]["items"] if p["id"] == preset)
    for planned, advertised, item, overview in zip(
        preview["plan"]["items"],
        catalog["items"],
        result["batch"]["items"],
        result["overview"],
        strict=True,
    ):
        expected_source = item["result"]["source_id"]
        assert advertised["source_id"] == expected_source
        assert planned["source_ids"] == overview["source_ids"] == [expected_source]


def test_weekly_panel_is_observed_only_and_does_not_hide_missing_claims():
    with Atlas("fixture") as atlas:
        panel = atlas.get_research(
            {
                "dataset": "macro_panel",
                "frequency": "monthly",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
                "allow_partial": True,
                "items": [
                    {
                        "id": "claims",
                        "query": {"dataset": "activity_economics", "series": "us_initial_claims"},
                        "aggregation": "mean",
                    }
                ],
            }
        )
    row = panel["records"][0]["records"][0]
    assert row["value"] is None and row["input_count"] == 3
    assert row["input_frequency"] == "weekly"
    assert row["coverage_basis"] == "observed_dates_only_no_business_calendar"


def test_cli_http_and_sdk_share_the_activity_result(tmp_path):
    q = query("ca_cpi")
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(q)
    with TestClient(create_app(provider="fixture", directory=tmp_path / "http")) as http:
        result = http.post("/v1/research", json=q)
        assert result.status_code == 200 and result.json() == expected
        weekly = http.get("/v1/research/catalog", params={"frequency": "weekly"})
        assert weekly.status_code == 200 and len(weekly.json()["series_search"]["records"]) == 2
    command = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "activity_economics",
            "--series",
            "ca_cpi",
            "--start-date",
            DATES["start_date"],
            "--end-date",
            DATES["end_date"],
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(command.stdout) == expected


@pytest.mark.asyncio
async def test_one_actual_mcp_session_executes_new_presets_and_resolves_text_evidence(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--research-only", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp-state")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        assert len((await session.list_tools()).tools) == 6
        for name in PRESETS:
            response = await session.call_tool(
                "atlas_get_research_preset", {"query": {"preset": name, **DATES}}
            )
            assert not response.is_error and response.structured_content["status"] == "ok", name
        response = await session.call_tool(
            "atlas_get_research",
            {
                "query": query(
                    "us_industrial_production", response={"view": "compact", "page_size": 1}
                )
            },
        )
        assert not response.is_error
        source = response.structured_content["records"][0]["source"]
        resolved = await session.call_tool(
            "atlas_get_evidence", {"evidence_id": source["evidence_id"]}
        )
        assert not resolved.is_error and resolved.structured_content["evidence"]["text_line"] == 2
