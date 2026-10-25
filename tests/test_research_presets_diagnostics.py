"""One-connection recipes, time/missingness summaries and opt-in diagnosis."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import SecTransport
from atlas_terminal.public_sources import ECB_FX_URL, NYFED_RATES, PublicTransport
from atlas_terminal.regional_bond_sources import ecb_curve_identity
from atlas_terminal.research_diagnostics import (
    ResearchDiagnosticsQuery,
    ResearchDiagnosticsResult,
)
from atlas_terminal.research_presets import (
    PRESETS,
    PresetPlanItem,
    ResearchPresetQuery,
    ResearchPresetResult,
    item_overview,
)
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import RecordingTransport, check_sources

FX = {
    "preset": "fx_context",
    "base_currency": "USD",
    "quote_currency": "JPY",
    "year": 2026,
    "view": "full",
}
NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr("atlas_terminal.util.utcnow", lambda: NOW)
    monkeypatch.setattr("atlas_terminal.research_quality.utcnow", lambda: NOW)
    monkeypatch.setattr("atlas_terminal.research_presets.utcnow", lambda: NOW)
    monkeypatch.setattr("atlas_terminal.research_fixed_income.utcnow", lambda: NOW)


def normalized(data):
    """Ignore transport-time metadata, while comparing all data and quality decisions."""
    if isinstance(data, dict):
        return {
            key: normalized(value)
            for key, value in data.items()
            if key
            not in {
                "generated_at",
                "checked_at",
                "evaluated_at",
                "max_source_age_seconds",
                "request_id",
            }
        }
    if isinstance(data, list):
        return [normalized(value) for value in data]
    return data


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected source, payment or diagnostic operation")


@pytest.mark.parametrize(
    "query",
    [
        {"preset": "company_overview", "identifier": "ATLASDEMO", "view": "full"},
        {"preset": "us_macro_overview", "year": 2026, "view": "full"},
        FX,
    ],
)
def test_presets_execute_the_previewed_queries_through_batch_and_keep_original_data(
    query, monkeypatch
):
    with Atlas("fixture") as atlas:
        monkeypatch.setattr(atlas, "quote", forbidden)
        monkeypatch.setattr(atlas, "purchase", forbidden)
        preview = atlas.get_research_preset(query | {"preview": True})
        result = atlas.get_research_preset(query)
        assert preview["plan"] == result["plan"]
        assert preview["status"] == "preview" and preview["batch"] is None
        assert result["status"] == "ok" and result["is_synthetic"]
        assert not result["requires_wallet"] and not result["atomic_snapshot"]
        assert not result["point_in_time"] and result["upstream_data_cost"] == "0"
        for item, planned in zip(result["batch"]["items"], result["plan"]["items"], strict=True):
            assert item["id"] == planned["id"] and item["status"] == "ok"
            assert item["result"] == atlas.get_research(planned["query"])
        ResearchPresetResult.model_validate(result)


def test_preview_and_config_only_diagnostics_make_no_clients_requests_or_stores(
    tmp_path, monkeypatch
):
    root = tmp_path / "never-created"
    with Atlas("public", directory=root) as atlas:
        monkeypatch.setattr(atlas, "get_research", forbidden)
        monkeypatch.setattr("atlas_terminal.source_checks.check_sources", forbidden)
        result = atlas.get_research_preset(FX | {"preview": True, "include": ["us_sofr", "fx"]})
        assert [item["id"] for item in result["plan"]["items"]] == ["us_sofr", "fx"]
        assert result["plan"]["year_basis"] is None
        assert result["overview"] == [] and atlas.source is None
        assert atlas.research_diagnostics()["status"] == "action_required"
        assert atlas._research.public is None
    assert not root.exists()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"preset": "unknown"},
        {"preset": "company_overview"},
        {"preset": "company_overview", "identifier": None},
        {"preset": "company_overview", "identifier": "https://arbitrary.example"},
        FX | {"quote_currency": "USD"},
        FX | {"base_currency": None},
        FX | {"year": True},
        FX | {"identifier": "AAPL"},
        FX | {"count": 1},
        FX | {"include": []},
        FX | {"include": ["fx", "fx"]},
        FX | {"include": ["unknown"]},
        FX | {"preview": "false"},
        FX | {"recipient": "SECRET_SENTINEL"},
        FX | {"include": ["fx"], "requirements_by_item": {"us_sofr": {}}},
        {"preset": "us_macro_overview", "period": "annual"},
        {"preset": "company_overview", "identifier": "ATLASDEMO", "count": 0},
    ],
)
def test_invalid_recipes_fail_before_execution_and_do_not_echo_inputs(data, monkeypatch):
    with Atlas("public") as atlas:
        monkeypatch.setattr(atlas, "get_research", forbidden)
        with pytest.raises(AtlasError) as error:
            atlas.get_research_preset(data)
        assert error.value.code == "INVALID_INPUT"
        assert "SECRET_SENTINEL" not in json.dumps(error.value.envelope())


def test_catalog_and_json_schema_describe_the_actual_recipe_parameters_and_item_ids():
    catalog = Atlas.research_catalog(view="compact")
    assert {row["id"] for row in catalog["presets"]["items"]} == set(PRESETS)
    assert "query_schema" not in catalog["presets"]
    assert catalog["diagnostics"]["network_default"] is False
    validator = Draft202012Validator(ResearchPresetQuery.model_json_schema())
    validator.check_schema(validator.schema)
    for data in [
        FX,
        {"preset": "company_overview", "identifier": "ATLASDEMO"},
        {"preset": "us_macro_overview", "include": ["cpi"]},
    ]:
        validator.validate(data)
    for data in [
        FX | {"quote_currency": "USD"},
        FX | {"include": ["profile"]},
        {"preset": "us_macro_overview", "identifier": "AAPL"},
        {"preset": "company_overview", "identifier": None},
    ]:
        with pytest.raises(ValidationError):
            validator.validate(data)


def test_overview_does_not_confuse_dates_or_hide_missing_values_and_can_retrieve_evidence():
    with Atlas("fixture") as atlas:
        fx = atlas.get_research_preset(FX | {"view": "compact", "include": ["fx", "us_cpi"]})
        overview = fx["overview"][0]
        assert overview["availability"] == "partial"
        assert overview["usable_records"] < overview["returned_records"]
        assert overview["observation_dates"] == {"earliest": "2026-10-16", "latest": "2026-10-19"}
        assert overview["source_retrieval_times"]["latest"] == "2026-10-20T00:00:00Z"
        assert overview["publication_times"] == {"earliest": None, "latest": None}
        assert overview["publication_time_unknown_records"] == overview["returned_records"]
        assert overview["units"] == ["JPY_per_USD"]
        evidence_id = fx["batch"]["items"][0]["result"]["records"][0]["inputs"][0]["source"][
            "evidence_id"
        ]
        assert atlas.get_evidence(evidence_id)["evidence"]["raw_source_url"]
        cpi = fx["overview"][1]
        assert cpi["observation_dates"]["latest"] == "2026-09-30"
        result = atlas.get_research_preset({"preset": "us_macro_overview", "include": ["cpi_yoy"]})
        assert result["status"] == "ok"  # Successful retrieval with unavailable observations.
        assert result["overview"][0]["availability"] == "unavailable"
        assert result["overview"][0]["quality"]["missing_value_count"] > 0


def test_explicit_publication_times_are_normalized_and_unknowns_are_not_inferred():
    with Atlas("fixture") as atlas:
        result = atlas.get_research_preset(FX | {"include": ["fx"]})
    item = deepcopy(result["batch"]["items"][0])
    item["result"]["records"][0]["published_at"] = "2026-10-19T18:00:00+09:00"
    item["result"]["records"][1]["accepted_at"] = "2026-10-20T00:00:00Z"
    overview = item_overview(item, PresetPlanItem.model_validate(result["plan"]["items"][0]))
    assert overview.publication_times.latest == "2026-10-19T09:00:00Z"
    assert overview.publication_time_known_records == 1
    assert overview.publication_time_unknown_records == 2


def test_per_item_requirements_override_defaults_and_failed_items_do_not_stop_the_set():
    with Atlas("fixture") as atlas:
        result = atlas.get_research_preset(
            FX
            | {
                "include": ["fx", "us_sofr"],
                "requirements": {"min_observation_date": "2026-10-19"},
                "requirements_by_item": {"fx": {"allow_missing": False}},
            }
        )
    assert result["status"] == "partial"
    assert [row["status"] for row in result["batch"]["items"]] == ["error", "ok"]
    failure = result["overview"][0]
    assert failure["availability"] == "error" and failure["error_code"] == "QUALITY_NOT_MET"
    assert failure["quality"]["meets_requirements"] is False
    assert failure["usable_records"] is None
    assert result["plan"]["items"][1]["query"]["requirements"] == {
        "min_observation_date": "2026-10-19",
    }


def test_source_failures_are_isolated_and_only_selected_items_are_called(monkeypatch):
    with Atlas("fixture") as atlas:
        original = atlas.get_research
        called = []

        def read(query):
            called.append(query["dataset"])
            if query["dataset"] == "fx_rates":
                raise AtlasError("SOURCE_RATE_LIMITED", "Wait before reading.", retryable=True)
            return original(query)

        monkeypatch.setattr(atlas, "get_research", read)
        result = atlas.get_research_preset(FX | {"include": ["fx", "us_sofr"]})
    assert called == ["fx_rates", "reference_rates"]
    assert result["batch"]["summary"]["retryable_read_ids"] == ["fx"]
    assert result["overview"][1]["availability"] == "available"


def test_configuration_checks_share_sec_validation_and_never_expose_contact_values(monkeypatch):
    with Atlas("public") as atlas:
        initial = atlas.research_diagnostics({"sources": ["sec", "ecb"]})
        assert initial["status"] == "action_required"
        assert initial["sources"][0]["configuration"] == "configuration_required"
        assert not initial["network_checks_executed"]
        monkeypatch.setenv("SEC_USER_AGENT", "Atlas SECRET_SENTINEL@contact.example")
        configured = atlas.research_diagnostics({"sources": ["sec"]})
        assert configured["status"] == "configured"
        assert configured["sources"][0]["connectivity"] == "not_checked"
        assert "SECRET_SENTINEL" not in json.dumps(configured)
        for value in [
            "missing-contact",
            "Atlas person@fixtures.invalid",
            "Atlas a@b.example\r\nX: bad",
        ]:
            monkeypatch.setenv("SEC_USER_AGENT", value)
            assert atlas.research_diagnostics({"sources": ["sec"]})["status"] == "action_required"
            with pytest.raises(AtlasError):
                SecTransport(value)


@pytest.mark.parametrize(
    "data",
    [
        {"sources": []},
        {"sources": ["ecb", "ecb"]},
        {"sources": ["invented"]},
        {"allow_network": "false"},
        {"allow_network": 1},
        {"SEC_USER_AGENT": "SECRET_SENTINEL"},
        {"url": "https://arbitrary.example"},
    ],
)
def test_invalid_diagnostics_do_not_probe_or_echo_arguments(data, monkeypatch):
    monkeypatch.setattr("atlas_terminal.source_checks.check_sources", forbidden)
    with Atlas("public") as atlas, pytest.raises(AtlasError) as failed:
        atlas.research_diagnostics(data)
    assert failed.value.code == "INVALID_INPUT"
    assert "SECRET_SENTINEL" not in json.dumps(failed.value.envelope())


def test_fixture_diagnostics_never_run_probes_even_with_network_opt_in(monkeypatch):
    monkeypatch.setattr("atlas_terminal.source_checks.check_sources", forbidden)
    with Atlas("fixture") as atlas:
        result = atlas.research_diagnostics({"allow_network": True})
    assert result["status"] == "synthetic" and not result["network_checks_executed"]
    assert all(row["connectivity"] == "not_run" for row in result["sources"])


def test_opted_in_diagnostics_reuse_real_probe_logic_with_mock_http(tmp_path, monkeypatch):
    requests = []
    resource = files("atlas_terminal").joinpath("resources", "research")

    def handler(request):
        requests.append(request)
        if selected := ecb_curve_identity(str(request.url)):
            return httpx.Response(
                200, content=resource.joinpath(selected[0] + "_2026.csv").read_bytes()
            )
        name = (
            "fx.xml"
            if str(request.url) == ECB_FX_URL
            else ("effr.json" if str(request.url) == NYFED_RATES["effr"] else "sofr.json")
        )
        return httpx.Response(200, content=resource.joinpath(name).read_bytes())

    def factory(directory, snapshots):
        atlas = Atlas("public", directory=directory)
        atlas._research.public = RecordingTransport(
            PublicTransport(
                cache_path=directory / "public-cache.sqlite3",
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                sleep=lambda seconds: None,
                interval=0.5,
            ),
            snapshots,
        )
        return atlas

    def probe(sources, *, allow_network, directory):
        return check_sources(
            sources, allow_network=allow_network, directory=directory, client_factory=factory
        )

    monkeypatch.setattr("atlas_terminal.source_checks.check_sources", probe)
    root = tmp_path / "shared"
    with Atlas("public", directory=root) as atlas:
        offline = atlas.research_diagnostics({"sources": ["ecb", "new_york_fed"]})
        assert offline["status"] == "configured" and not requests
        result = atlas.research_diagnostics(
            {"sources": ["ecb", "new_york_fed"], "allow_network": True}
        )
    assert result["status"] == "verified" and result["network_checks_executed"]
    assert len(requests) == 5 and all(row["connectivity"] == "pass" for row in result["sources"])
    assert (root / "source-checks.sqlite3").exists()
    assert all("@" not in request.headers["user-agent"] for request in requests)
    ResearchDiagnosticsResult.model_validate(result)


def test_sdk_http_and_published_contracts_agree_for_presets_and_diagnostics(tmp_path):
    data = FX | {"include": ["fx", "us_sofr"]}
    with Atlas("fixture") as atlas:
        expected = atlas.get_research_preset(data)
        diagnostics = atlas.research_diagnostics()
    with TestClient(
        create_app(provider="fixture", payments="mock", directory=tmp_path / "http")
    ) as http:
        preset = http.post("/v1/research/preset", json=data)
        doctor = http.post("/v1/research/diagnostics", json={})
        assert preset.status_code == doctor.status_code == 200
        assert normalized(preset.json()) == normalized(expected)
        assert normalized(doctor.json()) == normalized(diagnostics)
        assert "PAYMENT-REQUIRED" not in preset.headers
        assert http.post("/v1/research/preset", json=FX | {"identifier": "AAPL"}).status_code == 400
        assert (
            http.post("/v1/research/diagnostics", json={"allow_network": "yes"}).status_code == 400
        )
        assert http.post("/v1/research/preset", content=b" " * 16385).status_code == 413
    schema = json.loads((Path(__file__).parents[1] / "schemas/research.schema.json").read_bytes())
    for name, value in [
        ("ResearchPresetResult", preset.json()),
        ("ResearchDiagnosticsResult", doctor.json()),
    ]:
        Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"}).validate(value)
    Draft202012Validator(ResearchDiagnosticsQuery.model_json_schema()).validate({})


def test_openapi_shared_query_models_and_every_local_reference_are_resolvable(tmp_path):
    app = create_app(provider="fixture", directory=tmp_path)
    try:
        document = app.openapi()
        assert document is app.openapi()

        def check(value):
            if isinstance(value, dict):
                ref = value.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/"):
                    target = document
                    for part in ref[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(document)
        request_schema = document["paths"]["/v1/research/preset"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        validator = Draft202012Validator(request_schema | {"components": document["components"]})
        validator.validate(FX)
        with pytest.raises(ValidationError):
            validator.validate(FX | {"identifier": "AAPL"})
    finally:
        app.state.orders.close()


def test_cli_presets_support_saved_queries_preview_and_partial_exit_and_doctor(tmp_path):
    path = tmp_path / "preset.json"
    path.write_text(json.dumps(FX | {"include": ["fx", "us_sofr"]}))
    base = [sys.executable, "-m", "atlas_terminal", "research"]
    preview = subprocess.run(
        base + ["preset", "--input", str(path), "--preview", "--provider", "fixture"],
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0 and not preview.stderr
    assert json.loads(preview.stdout)["mode"] == "preview"
    data = FX | {
        "include": ["fx", "us_sofr"],
        "requirements_by_item": {"fx": {"allow_missing": False}},
    }
    partial = subprocess.run(
        base + ["preset", "--input", "-", "--provider", "fixture", "--json"],
        input=json.dumps(data),
        capture_output=True,
        text=True,
    )
    assert partial.returncode == 1 and not partial.stderr
    assert json.loads(partial.stdout)["status"] == "partial"
    doctor = subprocess.run(
        base + ["doctor", "--sources", "ecb", "--json"], capture_output=True, text=True
    )
    assert doctor.returncode == 0 and json.loads(doctor.stdout)["status"] == "configured"
    assert not json.loads(doctor.stdout)["network_checks_executed"]


async def test_real_stdio_mcp_exposes_typed_presets_diagnostics_and_safe_errors(tmp_path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams, read_timeout_seconds=20) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert len(tools) == 14
        for name in ["atlas_get_research_preset", "atlas_research_diagnostics"]:
            assert tools[name].annotations.read_only_hint and tools[name].output_schema
        data = FX | {"include": ["fx", "us_sofr"]}
        result = await session.call_tool("atlas_get_research_preset", {"query": data})
        assert not result.is_error
        with Atlas("fixture") as atlas:
            assert normalized(result.structured_content) == normalized(
                atlas.get_research_preset(data)
            )
        Draft202012Validator(tools["atlas_get_research_preset"].output_schema).validate(
            result.structured_content
        )
        doctor = await session.call_tool("atlas_research_diagnostics", {"query": {}})
        assert not doctor.is_error and doctor.structured_content["status"] == "synthetic"
        invalid = await session.call_tool(
            "atlas_get_research_preset", {"query": FX, "wallet": "SECRET_SENTINEL"}
        )
        assert invalid.is_error and "SECRET_SENTINEL" not in invalid.model_dump_json()
        invalid_doctor = await session.call_tool(
            "atlas_research_diagnostics", {"query": {"allow_network": "no"}}
        )
        assert invalid_doctor.is_error
        assert invalid_doctor.structured_content["error"]["code"] == "INVALID_INPUT"
