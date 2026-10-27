"""Agent contracts, lossless compact provenance, paging and recovery behavior."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError, ErrorEnvelope
from atlas_terminal.hosted import HostedClient
from atlas_terminal.public_sources import BLS_SERIES, PublicTransport
from atlas_terminal.research import DATASETS
from atlas_terminal.research_analytics import ANALYTICS_METRICS
from atlas_terminal.research_financials import STATEMENT_METRICS
from atlas_terminal.research_models import ResearchQuery, dataset_required
from atlas_terminal.research_views import ResearchResult, ResearchViews
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def query(dataset="statements", **extra):
    if dataset in {"equity_prices", "equity_actions"}:
        return {"dataset": dataset, "identifier": "7203.T", **extra}
    if dataset == "sdmx_catalog":
        return {"dataset": dataset, "source_id": "imf", **extra}
    if dataset == "international_economics":
        return {
            "dataset": dataset,
            "source_id": "imf",
            "dataflow": "IMF.DEMO,ATLAS_MACRO,1.0",
            "dimensions": {"FREQUENCY": "M", "COUNTRY": "USA", "INDICATOR": "CPI"},
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            **extra,
        }
    if dataset == "macro_panel":
        return (
            json.loads(
                (Path(__file__).parents[1] / "examples/research_panels/us_japan.json").read_text()
            )
            | extra
        )
    result = {"dataset": dataset}
    for key in dataset_required(dataset):
        result[key] = (
            ["ATLASDEMO"] if key == "identifiers" else "cpi" if key == "series" else "ATLASDEMO"
        )
    if dataset == "national_accounts":
        result["series"] = "gdp_real"
    if dataset == "euro_area_yields":
        result.update(series="aaa_spot", year=2026)
    if dataset == "europe_economics":
        result["series"] = "ea_hicp_yoy"
    if dataset == "activity_economics":
        result["series"] = "us_industrial_production"
    if dataset == "fx_rates":
        result.update(base_currency="USD", quote_currency="JPY")
    if dataset in {"reference_rates", "macro_analytics"}:
        result["series"] = "sofr" if dataset == "reference_rates" else "cpi_yoy"
    if dataset in {"statements", "ratios", "financial_analytics", "compare"}:
        result["allow_partial"] = True
    if dataset in {"treasury_yields", "treasury_real_yields"}:
        result["year"] = 2026
    return result | extra


def expand(value, atlas):
    if isinstance(value, dict):
        if set(value) == {"evidence_id"}:
            evidence = atlas.get_evidence(value["evidence_id"])
            assert digest(canonical(evidence["evidence"])) == value["evidence_id"]
            return evidence["evidence"]
        return {key: expand(item, atlas) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, atlas) for item in value]
    return value


@pytest.mark.parametrize("dataset", DATASETS)
def test_compact_provenance_reconstructs_every_dataset_without_losing_values(dataset):
    with Atlas("fixture") as atlas:
        full = atlas.get_research(query(dataset))
        compact = atlas.get_research(query(dataset, response={"view": "compact"}))
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas/research.schema.json").read_bytes()
        )
        Draft202012Validator({**schema, "$ref": "#/$defs/ResearchResult"}).validate(compact)
        metadata = compact.pop("response")
        assert metadata["result_sha256"] == digest(canonical(full))
        assert metadata["evidence_count"] > 0
        assert metadata["total_records"] == len(full["records"])
        assert expand(compact, atlas) == full
        if dataset in {"statements", "ratios", "financial_analytics", "compare"}:
            assert len(canonical(compact | {"response": metadata})) < len(canonical(full))


def test_query_schema_discriminates_datasets_and_enumerates_actual_metrics():
    schema = ResearchQuery.model_json_schema()
    Draft202012Validator.check_schema(schema)
    variants = {branch["properties"]["dataset"]["const"]: branch for branch in schema["oneOf"]}
    assert set(variants) == set(DATASETS)
    assert set(variants["statements"]["properties"]["metrics"]["items"]["enum"]) == set(
        STATEMENT_METRICS
    )
    assert set(variants["financial_analytics"]["properties"]["metrics"]["items"]["enum"]) == set(
        ANALYTICS_METRICS
    )
    assert set(variants["compare"]["properties"]["metrics"]["items"]["enum"]) == set(
        STATEMENT_METRICS
    ) | set(ANALYTICS_METRICS)
    assert set(variants["economics"]["properties"]["series"]["enum"]) == set(BLS_SERIES) | {
        v[0] for v in BLS_SERIES.values()
    }
    validator = Draft202012Validator(schema)
    for dataset in DATASETS:
        validator.validate(query(dataset, response={"view": "compact", "page_size": 2}))
    for bad in [
        {"dataset": "profile", "identifier": None},
        query("economics", series="invented"),
        query(metrics=["made_up_metric"]),
        query("profile", metrics=["revenue"]),
        query("compare", identifiers=[]),
        query(count=True),
    ]:
        with pytest.raises(ValidationError):
            validator.validate(bad)
        with Atlas("fixture") as atlas, pytest.raises(AtlasError):
            atlas.get_research(bad)


def test_filtered_compact_catalog_keeps_discovery_small_and_dataset_schema_complete():
    compact = Atlas.research_catalog("statements", view="compact")
    full = Atlas.research_catalog("statements")
    assert [item["id"] for item in compact["datasets"]] == ["statements"]
    assert not compact["schema_included"] and "query_schema" not in compact
    assert len(canonical(compact)) < len(canonical(full))
    assert set(compact["statement_metrics"]) == set(STATEMENT_METRICS)
    assert not compact["requires_wallet"] and not compact["point_in_time"]
    validator = Draft202012Validator(full["query_schema"])
    validator.validate(query())
    with pytest.raises(ValidationError):
        validator.validate(query("economics"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "catalog",
            "--dataset",
            "statements",
            "--view",
            "compact",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == compact
    with TestClient(create_app(provider="fixture")) as http:
        assert http.get("/v1/research/catalog?dataset=statements&view=compact").json() == compact


def test_projection_keeps_units_periods_missingness_and_derived_inputs():
    with Atlas("fixture") as atlas:
        full = atlas.get_research(query("financial_analytics"))
        result = atlas.get_research(query("financial_analytics", response={"fields": ["value"]}))
        for before, after in zip(full["records"], result["records"], strict=True):
            for key in ["value", "unit", "status", "reason", "formula", "period", "inputs"]:
                assert after[key] == before[key]
        subset = atlas.get_research(
            query("institutional_holdings", response={"fields": ["reported_value"]})
        )
        assert "reported_value_unit" in subset["records"][0]
        assert "cusip" in subset["records"][0] and "source" in subset["records"][0]
        assert "voting_shared" not in subset["records"][0]
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(query(response={"fields": ["typo"]}))
        assert caught.value.next_action.fields == ["response.fields"]


def test_paging_survives_restart_and_does_not_refetch_or_hide_upstream_coverage(monkeypatch):
    q = query(metrics=["revenue", "net_income"], response={"view": "compact", "page_size": 2})
    with Atlas("fixture") as atlas:
        full = atlas.get_research(query(metrics=["revenue", "net_income"]))
        first = atlas.get_research(q)
        assert first["coverage"] == full["coverage"]
        rows = expand(first["records"], atlas)
    with Atlas("fixture") as restarted:
        monkeypatch.setattr(
            restarted._research, "sec_factory", lambda: pytest.fail("Page refetched SEC")
        )
        next_page = first["response"]["next_cursor"]
        while next_page:
            page = restarted.get_research(q | {"response": q["response"] | {"cursor": next_page}})
            assert page["response"]["snapshot_id"] == first["response"]["snapshot_id"]
            assert page["coverage"] == full["coverage"]
            rows.extend(expand(page["records"], restarted))
            next_page = page["response"]["next_cursor"]
    assert rows == full["records"]


@pytest.mark.parametrize(
    "change", ["dataset", "metrics", "period", "view", "fields", "page_size", "offset"]
)
def test_cursor_rejects_query_or_presentation_changes_before_any_source_read(change, monkeypatch):
    q = query(metrics=["revenue"], response={"view": "compact", "page_size": 1})
    with Atlas("fixture") as atlas:
        page = atlas.get_research(q)
        altered = q | {"response": q["response"] | {"cursor": page["response"]["next_cursor"]}}
        if change in {"view", "fields", "page_size"}:
            altered["response"][change] = {"view": "full", "fields": ["value"], "page_size": 2}[
                change
            ]
        elif change == "offset":
            altered["response"]["cursor"] = f"r1_{page['response']['snapshot_id']}_999999"
        else:
            altered[change] = {
                "dataset": "financial_analytics",
                "metrics": ["net_income"],
                "period": "quarterly",
            }[change]
            if change == "dataset":
                altered["metrics"] = ["free_cash_flow"]
        monkeypatch.setattr(
            atlas._research, "sec_factory", lambda: pytest.fail("Changed cursor fetched a source")
        )
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(altered)
        assert caught.value.code == "INVALID_CURSOR"


def test_expiry_integrity_and_bounded_cache_fail_without_partial_persistence(tmp_path):
    moment = [datetime(2026, 10, 21, tzinfo=UTC)]
    views = ResearchViews(
        tmp_path / "views.sqlite3", synthetic=True, clock=lambda: moment[0], ttl_seconds=60
    )
    with Atlas("fixture") as atlas:
        atlas._research._views = views
        q = query(response={"view": "compact", "page_size": 1})
        page = atlas.get_research(q)
        evidence_id = page["records"][0]["source"]["evidence_id"]
        with views.db.connect(write=True) as conn:
            conn.execute("UPDATE research_evidence SET body=? WHERE id=?", (b"{}", evidence_id))
        with pytest.raises(AtlasError) as corrupted:
            atlas.get_evidence(evidence_id)
        assert corrupted.value.code == "LEDGER_UNAVAILABLE"
        moment[0] += timedelta(seconds=61)
        with pytest.raises(AtlasError) as expired:
            atlas.get_research(
                q | {"response": q["response"] | {"cursor": page["response"]["next_cursor"]}}
            )
        assert (
            expired.value.code == "RESULT_EXPIRED"
            and expired.value.next_action.action == "restart_query"
        )
        with pytest.raises(AtlasError) as gone:
            atlas.get_evidence(evidence_id)
        assert gone.value.code == "EVIDENCE_NOT_FOUND"
        limited = ResearchViews(tmp_path / "limited.sqlite3", synthetic=True, max_bytes=1)
        atlas._research._views = limited
        with pytest.raises(AtlasError) as full:
            atlas.get_research(q)
        assert full.value.code == "CACHE_FULL"
        with limited.db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM research_snapshots").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM research_evidence").fetchone()[0] == 0


def test_corrupt_snapshot_cannot_be_used_for_paging():
    with Atlas("fixture") as atlas:
        q = query(response={"page_size": 1})
        first = atlas.get_research(q)
        with atlas._research.views.db.connect(write=True) as conn:
            conn.execute("UPDATE research_snapshots SET body=?", (b"{}",))
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(
                q | {"response": {"page_size": 1, "cursor": first["response"]["next_cursor"]}}
            )
        assert caught.value.code == "LEDGER_UNAVAILABLE"


def test_default_view_remains_full_without_creating_a_presentation_database(tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        result = atlas.get_research(query())
        assert "response" not in result and "raw_sha256" in result["records"][0]["source"]
        assert not (tmp_path / "research-responses.sqlite3").exists()


def test_provider_scope_prevents_synthetic_evidence_being_used_as_public_data(tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        page = atlas.get_research(query(response={"view": "compact"}))
        evidence_id = page["records"][0]["source"]["evidence_id"]
    with Atlas("public", directory=tmp_path) as public, pytest.raises(AtlasError) as caught:
        public.get_evidence(evidence_id)
    assert caught.value.code == "EVIDENCE_NOT_FOUND"


def test_error_fields_are_safe_stable_and_do_not_relax_partial_or_payment_policy():
    with Atlas("fixture") as atlas:
        with pytest.raises(AtlasError) as malformed:
            atlas.get_research(query("treasury_yields", year="SECRET_SENTINEL"))
        error = malformed.value.envelope()
        ErrorEnvelope.model_validate(error)
        schema = json.loads((Path(__file__).parents[1] / "schemas/atlas.schema.json").read_bytes())
        Draft202012Validator({**schema, "$ref": "#/$defs/Error"}).validate(error)
        assert error == malformed.value.envelope()
        assert "SECRET_SENTINEL" not in json.dumps(error)
        assert error["error"]["next_action"]["fields"] == ["year"]
        with pytest.raises(AtlasError) as partial:
            atlas.get_research(query("financial_analytics", allow_partial=False))
        assert partial.value.next_action.action == "review_coverage"
        for code in ["SETTLEMENT_UNKNOWN", "BUDGET_EXCEEDED", "PAYMENT_FAILED", "POLICY_DENIED"]:
            action = AtlasError(code, "Bound operation").next_action
            assert not action.automatic and action.action != "retry_read"


def test_hosted_transport_failure_directs_the_agent_to_status_not_payment_retry(tmp_path):
    def unavailable(request):
        raise httpx.ReadTimeout("PRIVATE_PROVIDER_PATH")

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as http:
        hosted = HostedClient("https://atlas.invalid", directory=tmp_path, client=http)
        with pytest.raises(AtlasError) as caught:
            hosted.request("GET", "/v1/quotes/public-id/result")
        assert caught.value.next_action.action == "check_purchase_status"
        assert "PRIVATE_PROVIDER_PATH" not in json.dumps(caught.value.envelope())


def test_http_compact_evidence_and_rate_limit_guidance_share_the_core_service(tmp_path):
    app = create_app(provider="fixture", directory=tmp_path)
    with TestClient(app) as http:
        result = http.post("/v1/research", json=query(response={"view": "compact", "page_size": 1}))
        assert result.status_code == 200
        payload = result.json()
        ResearchResult.model_validate(payload)
        evidence_id = payload["records"][0]["source"]["evidence_id"]
        evidence = http.get(f"/v1/research/evidence/{evidence_id}")
        assert (
            evidence.status_code == 200
            and digest(canonical(evidence.json()["evidence"])) == evidence_id
        )
        invalid = http.get("/v1/research/evidence/SECRET_SENTINEL")
        assert invalid.status_code == 400 and "SECRET_SENTINEL" not in invalid.text
        moment = [10000.0]
        transport = PublicTransport(
            cache_path=tmp_path / "rate.sqlite3",
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(429, headers={"Retry-After": "90"})
                )
            ),
            clock=lambda: moment[0],
            sleep=lambda seconds: moment.__setitem__(0, moment[0] + seconds),
        )
        app.state.orders.atlas._research.public = transport
        limited = http.post("/v1/research", json=query("economics"))
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "90"
        assert limited.json()["error"]["next_action"]["action"] == "wait"
        assert limited.json()["error"]["retry_after_seconds"] == 90
        with transport.db.connect(write=True) as conn:
            conn.execute("INSERT OR REPLACE INTO source_daily_usage VALUES ('bls','1970-01-01',25)")
        quota = http.post("/v1/research", json=query("economics"))
        assert quota.status_code == 429 and quota.json()["error"][
            "retry_after_seconds"
        ] == 86400 - int(moment[0])


def test_cli_compact_paging_and_evidence_across_separate_processes():
    args = [sys.executable, "-m", "atlas_terminal", "research"]
    first = subprocess.run(
        args
        + [
            "get",
            "statements",
            "--identifier",
            "ATLASDEMO",
            "--metrics",
            "revenue",
            "--view",
            "compact",
            "--page-size",
            "1",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(first.stdout)
    second = subprocess.run(
        args
        + [
            "get",
            "statements",
            "--identifier",
            "ATLASDEMO",
            "--metrics",
            "revenue",
            "--view",
            "compact",
            "--page-size",
            "1",
            "--cursor",
            result["response"]["next_cursor"],
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(second.stdout)["response"]["offset"] == 1
    evidence_id = result["records"][0]["source"]["evidence_id"]
    saved = subprocess.run(
        args + ["evidence", evidence_id, "--provider", "fixture", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert digest(canonical(json.loads(saved.stdout)["evidence"])) == evidence_id


async def test_real_mcp_typed_schemas_compact_paging_evidence_and_sanitized_errors(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={
            "ATLAS_STATE_DIR": str(tmp_path / "mcp"),
            "ATLAS_TESTNET_PRIVATE_KEY": "SECRET_SENTINEL",
        },
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        catalog = await session.call_tool(
            "atlas_research_catalog", {"dataset": "statements", "view": "compact"}
        )
        assert not catalog.is_error and not catalog.structured_content["schema_included"]
        tool = next(t for t in tools if t.name == "atlas_get_research")
        assert tool.output_schema["properties"]["records"]["type"] == "array"
        input_validator = Draft202012Validator(tool.input_schema)
        q = query(response={"view": "compact", "page_size": 1})
        input_validator.validate({"query": q})
        with pytest.raises(ValidationError):
            input_validator.validate({"query": query(metrics=["fake_metric"])})
        first = await session.call_tool("atlas_get_research", {"query": q})
        assert not first.is_error
        Draft202012Validator(tool.output_schema).validate(first.structured_content)
        assert json.loads(first.content[0].text) == first.structured_content
        evidence_id = first.structured_content["records"][0]["source"]["evidence_id"]
        evidence = await session.call_tool("atlas_get_evidence", {"evidence_id": evidence_id})
        assert (
            not evidence.is_error
            and digest(canonical(evidence.structured_content["evidence"])) == evidence_id
        )
        second = await session.call_tool(
            "atlas_get_research",
            {
                "query": q
                | {
                    "response": q["response"]
                    | {"cursor": first.structured_content["response"]["next_cursor"]}
                }
            },
        )
        assert not second.is_error and second.structured_content["response"]["offset"] == 1
        for name, arguments in [
            ("atlas_get_research", {"query": query(count="SECRET_SENTINEL")}),
            ("atlas_get_research", {"query": query(response={"view": "SECRET_SENTINEL"})}),
            ("atlas_get_evidence", {"evidence_id": "SECRET_SENTINEL"}),
            ("atlas_research_catalog", {"view": "SECRET_SENTINEL"}),
        ]:
            failed = await session.call_tool(name, arguments)
            assert failed.is_error and "SECRET_SENTINEL" not in failed.model_dump_json()
            assert failed.structured_content["error"]["next_action"]["action"] == "fix_query"
