"""Quality gates, snapshot aging and independent free research batches."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_batch import ResearchBatchQuery, research_batch
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_quality import (
    ResearchRequirements,
    assess_quality,
    record_date,
)
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical

NOW = datetime(2026, 10, 21, tzinfo=UTC)
FX = {"dataset": "fx_rates", "base_currency": "USD", "quote_currency": "JPY"}


@pytest.fixture(autouse=True)
def private_state_and_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("atlas_terminal.research_quality.utcnow", lambda: NOW)


def test_retrieval_age_is_distinct_from_observation_date_and_has_an_exact_boundary(monkeypatch):
    with Atlas("fixture") as atlas:
        query = FX | {"requirements": {"max_source_age_seconds": 86400}}
        result = atlas.get_research(query)
        assert result["quality"]["max_source_age_seconds"] == 86400
        assert result["quality"]["latest_observation_date"] == "2026-10-19"
        with pytest.raises(AtlasError) as failed:
            atlas.get_research(FX | {"requirements": {"min_observation_date": "2026-10-20"}})
        assert failed.value.code == "QUALITY_NOT_MET"
        assert failed.value.next_action.action == "review_requirements"
        monkeypatch.setattr(
            "atlas_terminal.research_quality.utcnow", lambda: NOW + timedelta(seconds=1)
        )
        with pytest.raises(AtlasError):
            atlas.get_research(query)


def test_age_checks_include_older_calculation_inputs_and_reject_unknown_or_future_source_times():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(FX | {"limit": 1})
    result["records"][0]["inputs"][0]["source"]["retrieved_at"] = "2026-10-01T00:00:00Z"
    report = assess_quality(result, ResearchRequirements(max_source_age_seconds=86400), now=NOW)
    assert report.oldest_source_retrieved_at == "2026-10-01T00:00:00Z"
    assert not report.meets_requirements and report.max_source_age_seconds == 20 * 86400
    for value in [
        [],
        [
            {
                "retrieved_at": "2026-10-22T00:00:00Z",
                "raw_source_url": "https://fixtures.atlas.invalid/future",
                "raw_sha256": "0" * 64,
            }
        ],
    ]:
        candidate = deepcopy(result)
        candidate["records"] = []
        candidate["sources"] = value
        assert not assess_quality(
            candidate, ResearchRequirements(max_source_age_seconds=86400), now=NOW
        ).meets_requirements


def test_financial_observation_dates_use_actual_periods_instead_of_later_filing_dates():
    with Atlas("fixture") as atlas:
        annual = atlas.get_research(
            {
                "dataset": "statements",
                "identifier": "ATLASDEMO",
                "metrics": ["revenue"],
                "count": 1,
                "requirements": {"min_observation_date": "2025-12-31"},
            }
        )
        assert annual["quality"]["latest_observation_date"] == "2025-12-31"
        holdings = atlas.get_research(
            {
                "dataset": "institutional_holdings",
                "identifier": "ATLASDEMO",
                "requirements": {"min_observation_date": "2026-09-30"},
            }
        )
        assert holdings["quality"]["latest_observation_date"] == "2026-09-30"
        debt = atlas.get_research({"dataset": "federal_debt"})
        debt["records"][0]["tot_pub_debt_out_amt"] = None
        assert not assess_quality(
            debt, ResearchRequirements(allow_missing=False)
        ).meets_requirements
        with pytest.raises(AtlasError):
            atlas.get_research(
                annual["query"] | {"requirements": {"min_observation_date": "2026-01-01"}}
            )
    assert (
        record_date(
            {"reported_context": {"end": "2025-12-31", "filed": "2026-10-21"}}, "company_facts"
        )
        == "2025-12-31"
    )
    assert record_date({"year": "2024", "period": "M02"}, "economics") == "2024-02-29"
    assert record_date({"year": "2024", "period": "M13"}, "economics") == "2024-12-31"


def test_undated_metadata_and_unavailable_calculations_cannot_satisfy_observation_requirements():
    with Atlas("fixture") as atlas:
        for query in [
            {"dataset": "profile", "identifier": "ATLASDEMO"},
            {"dataset": "macro_analytics", "series": "cpi_yoy"},
        ]:
            with pytest.raises(AtlasError) as failed:
                atlas.get_research(query | {"requirements": {"min_observation_date": "2020-01-01"}})
            assert failed.value.details["quality"]["latest_observation_date"] is None


def test_missing_derived_and_truncated_conditions_are_explicit_and_independent():
    with Atlas("fixture") as atlas:
        for query, requirements in [
            (FX, {"allow_missing": False}),
            (FX | {"limit": 1}, {"allow_derived": False}),
            (FX | {"limit": 1}, {"allow_truncated": False}),
            (
                {
                    "dataset": "statements",
                    "identifier": "ATLASDEMO",
                    "metrics": ["revenue"],
                    "count": 20,
                    "allow_partial": True,
                },
                {"allow_missing": False},
            ),
        ]:
            with pytest.raises(AtlasError) as failed:
                atlas.get_research(query | {"requirements": requirements})
            assert failed.value.code == "QUALITY_NOT_MET"
            assert not failed.value.details["quality"]["meets_requirements"]
        direct = atlas.get_research(
            FX
            | {
                "base_currency": "EUR",
                "limit": 1,
                "requirements": {"allow_derived": False, "allow_missing": False},
            }
        )
        assert direct["quality"]["derived_record_count"] == 0
        whole = atlas.get_research(FX | {"requirements": {"allow_truncated": False}})
        assert (
            whole["coverage"]["upstream_window_limited"] and whole["quality"]["meets_requirements"]
        )


def test_quality_gates_apply_before_projection_and_all_pages_and_recheck_time(monkeypatch):
    query = FX | {
        "response": {"view": "compact", "fields": ["value"], "page_size": 1},
        "requirements": {"max_source_age_seconds": 86401},
    }
    with Atlas("fixture") as atlas:
        with pytest.raises(AtlasError):
            atlas.get_research(query | {"requirements": {"allow_missing": False}})
        page = atlas.get_research(query)
        assert page["quality"]["missing_value_count"] > 0
        assert page["quality"]["scope"] == "complete_selected_result"
        assert page["attributions"] and page["records"][0]["inputs"]
        query["response"]["cursor"] = page["response"]["next_cursor"]
        monkeypatch.setattr(
            atlas._research.public, "get", lambda *args: pytest.fail("Cursor fetched upstream")
        )
        monkeypatch.setattr(
            "atlas_terminal.research_quality.utcnow", lambda: NOW + timedelta(seconds=2)
        )
        with pytest.raises(AtlasError) as failed:
            atlas.get_research(query)
        assert failed.value.code == "QUALITY_NOT_MET"
        query["requirements"]["max_source_age_seconds"] = 999999
        with pytest.raises(AtlasError) as changed:
            atlas.get_research(query)
        assert changed.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize(
    "requirements",
    [
        {"max_source_age_seconds": True},
        {"max_source_age_seconds": -1},
        {"max_source_age_seconds": 31536001},
        {"allow_missing": "false"},
        {"allow_derived": 0},
        {"min_observation_date": "2026-02-30"},
        {"min_observation_date": "20261019"},
        {"unexpected": "SECRET_SENTINEL"},
    ],
)
def test_requirement_schema_and_runtime_reject_invalid_values_before_fetch(
    requirements, monkeypatch
):
    with pytest.raises(ValidationError):
        Draft202012Validator(
            ResearchQuery.model_json_schema(), format_checker=FormatChecker()
        ).validate(FX | {"requirements": requirements})
    with Atlas("fixture") as atlas:
        monkeypatch.setattr(
            atlas._research, "public_data", lambda *args: pytest.fail("Invalid query fetched")
        )
        with pytest.raises(AtlasError) as failed:
            atlas.get_research(FX | {"requirements": requirements})
    assert "SECRET_SENTINEL" not in json.dumps(failed.value.envelope())


def test_batch_preserves_order_isolates_invalid_quality_and_transient_failures_without_retry():
    with Atlas("fixture") as atlas:
        calls = []

        def read(query):
            calls.append(query)
            if query.get("series") == "transient":
                raise AtlasError(
                    "SOURCE_RATE_LIMITED",
                    "Wait for source quota.",
                    retryable=True,
                    retry_after_seconds=60,
                )
            return atlas.get_research(query)

        items = [
            {"id": "fx", "query": FX | {"limit": 1}},
            {"id": "invalid", "query": {"dataset": "purchase", "secret": "SECRET_SENTINEL"}},
            {"id": "quality", "query": FX | {"requirements": {"allow_derived": False}}},
            {"id": "later", "query": {"dataset": "economics", "series": "transient"}},
            {"id": "sofr", "query": {"dataset": "reference_rates", "series": "sofr"}},
        ]
        batch = research_batch({"items": items}, read)
    assert len(calls) == 5 and [row["id"] for row in batch["items"]] == [row["id"] for row in items]
    assert batch["status"] == "partial" and batch["summary"] == {
        "requested": 5,
        "successful": 2,
        "failed": 3,
        "retryable_read_ids": ["later"],
    }
    assert not batch["requires_wallet"] and batch["upstream_data_cost"] == "0"
    assert batch["items"][2]["error"]["next_action"]["action"] == "review_requirements"
    assert "SECRET_SENTINEL" not in json.dumps(batch)


@pytest.mark.parametrize(
    "data",
    [
        {"items": []},
        {"items": [{"id": "same", "query": FX}] * 2},
        {"items": [{"id": str(i), "query": FX} for i in range(11)]},
        {"items": [{"id": "bad/id", "query": FX}]},
        {"items": [{"id": "good", "query": []}]},
        {"items": [{"id": "good", "query": FX}], "provider": "hosted"},
        {"items": [{"id": "large", "query": {"unknown": "x" * 16384}}]},
    ],
)
def test_invalid_batch_envelopes_do_not_execute_any_item(data):
    with pytest.raises(AtlasError) as failed:
        research_batch(data, lambda query: pytest.fail("Invalid batch executed an item"))
    assert failed.value.code == "INVALID_INPUT"


def test_batch_bounds_output_and_sanitizes_unexpected_item_failures(monkeypatch):
    monkeypatch.setattr("atlas_terminal.research_batch.MAX_BATCH_RESPONSE_BYTES", 16384 + 15000)
    with Atlas("fixture") as atlas:
        small = atlas.get_research(FX | {"limit": 1})

    def read(query):
        if query["dataset"] == "huge_error":
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Oversized error details.", details={"source": "x" * 20000}
            )
        if query["dataset"] == "crash":
            raise RuntimeError("SECRET_SENTINEL")
        return small | ({"extra": "x" * 20000} if query["dataset"] == "large" else {})

    result = research_batch(
        {
            "items": [
                {"id": value, "query": {"dataset": value}}
                for value in ["large", "huge_error", "crash", "small"]
            ]
        },
        read,
    )
    assert [row["status"] for row in result["items"]] == ["error", "error", "error", "ok"]
    assert result["items"][1]["error"]["code"] == "RESPONSE_TOO_LARGE"
    assert result["items"][0]["error"]["code"] == "RESPONSE_TOO_LARGE"
    assert result["items"][0]["error"]["next_action"]["action"] == "fix_query"
    assert "SECRET_SENTINEL" not in json.dumps(result) and len(canonical(result)) < 31384


def test_sdk_http_and_published_batch_contract_agree_and_remain_free(tmp_path, monkeypatch):
    body = {
        "items": [
            {"id": "fx", "query": FX | {"limit": 1, "requirements": {"allow_missing": False}}},
            {"id": "bad", "query": {"dataset": "economics", "series": "unknown"}},
        ]
    }
    with TestClient(
        create_app(provider="fixture", payments="mock", directory=tmp_path / "server")
    ) as http:
        monkeypatch.setattr(
            http.app.state.orders.atlas, "quote", lambda *args: pytest.fail("Batch quoted")
        )
        monkeypatch.setattr(
            http.app.state.orders.atlas, "purchase", lambda *args: pytest.fail("Batch purchased")
        )
        response = http.post("/v1/research/batch", json=body)
        assert response.status_code == 200 and response.json()["status"] == "partial"
        assert (
            "PAYMENT-REQUIRED" not in response.headers
            and "Atlas-Recovery-Token" not in response.headers
        )
        with Atlas("fixture") as atlas:
            sdk = atlas.get_research_batch(body)
        sdk["items"][1]["error"].pop("request_id")
        received = response.json()
        received["items"][1]["error"].pop("request_id")
        assert received == sdk
        invalid = http.post("/v1/research/batch", json={"items": []})
        assert invalid.status_code == 400
        oversized = http.post("/v1/research/batch", content=b" " * 16385)
        assert oversized.status_code == 413
    schema = json.loads((Path(__file__).parents[1] / "schemas/research.schema.json").read_bytes())
    Draft202012Validator({**schema, "$ref": "#/$defs/ResearchBatchResult"}).validate(
        response.json()
    )
    Draft202012Validator(ResearchBatchQuery.model_json_schema()).validate(body)


def test_cli_batch_stdin_has_one_json_document_and_nonzero_partial_exit_and_quality_flags():
    body = {"items": [{"id": "fx", "query": FX}, {"id": "bad", "query": {"dataset": "unknown"}}]}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "batch",
            "--input",
            "-",
            "--provider",
            "fixture",
            "--json",
        ],
        input=json.dumps(body),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout)["status"] == "partial"
    quality = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "fx_rates",
            "--base-currency",
            "USD",
            "--quote-currency",
            "JPY",
            "--no-allow-derived",
            "--provider",
            "fixture",
        ],
        text=True,
        capture_output=True,
    )
    assert (
        quality.returncode == 2 and json.loads(quality.stdout)["error"]["code"] == "QUALITY_NOT_MET"
    )


async def test_real_mcp_batch_tool_contract_and_partial_results(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        tool = next(
            tool
            for tool in (await session.list_tools()).tools
            if tool.name == "atlas_get_research_batch"
        )
        assert tool.annotations.read_only_hint and tool.output_schema
        result = await session.call_tool(
            "atlas_get_research_batch",
            {
                "query": {
                    "items": [
                        {
                            "id": "fx",
                            "query": FX | {"requirements": {"min_observation_date": "2026-10-19"}},
                        },
                        {"id": "invalid", "query": {"dataset": "economics", "series": "unknown"}},
                    ]
                }
            },
        )
        assert not result.is_error and result.structured_content["status"] == "partial"
        assert result.structured_content["items"][0]["result"]["quality"]["meets_requirements"]
        assert result.structured_content["items"][1]["error"]["code"] == "INVALID_INPUT"
        bad = await session.call_tool(
            "atlas_get_research_batch", {"query": {"items": []}, "secret": "SECRET_SENTINEL"}
        )
        assert bad.is_error and "SECRET_SENTINEL" not in bad.model_dump_json()
