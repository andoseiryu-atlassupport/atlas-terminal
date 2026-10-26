"""Numerical, timing, persistence and interface tests with fictional observations only."""

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from random import Random

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.remote_mcp import create_remote_mcp
from atlas_terminal.risk_demo import fictional_panel
from atlas_terminal.risk_engine import (
    Exposures,
    RiskPanel,
    exposures,
    fit_model,
    regress,
)
from atlas_terminal.risk_math import (
    ONE,
    ZERO,
    covariance,
    dot,
    ewma_weights,
    matvec,
    solve,
    standardize,
    wls,
)
from atlas_terminal.risk_models import (
    MAX_RISK_BYTES,
    RiskPanelInput,
    RiskQuery,
    RiskResult,
    parse_risk,
)
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest


def parsed_panel(data):
    value = RiskPanelInput.model_validate_json(canonical(data))
    result = RiskPanel(value.manifest, value.fundamentals, value.sessions)
    result.validate()
    return result


def fit_query(data, **options):
    return parse_risk(
        {
            "action": "fit",
            "head_id": "0" * 64,
            "as_of": data["sessions"][64]["end_at"],
        }
        | options
    ).root


@pytest.fixture(scope="module")
def fictional():
    return fictional_panel()


@pytest.fixture(scope="module")
def fitted(tmp_path_factory, fictional):
    directory = tmp_path_factory.mktemp("risk")
    atlas = Atlas("public", directory=directory)
    imported = atlas.import_risk_panel(fictional)
    query = {
        "action": "fit",
        "head_id": imported["head_id"],
        "as_of": fictional["sessions"][64]["end_at"],
    }
    model = atlas.risk(query)
    yield atlas, directory, imported, query, model
    atlas.close()


def test_weighted_regression_recovers_known_coefficients():
    with localcontext() as ctx:
        ctx.prec = 42
        x = [[ONE, Decimal(i), Decimal(i % 3)] for i in range(12)]
        truth = [Decimal("0.003"), Decimal("-0.0004"), Decimal("0.001")]
        returns = matvec(x, truth)
        weights = [Decimal(i + 1) / 78 for i in range(12)]
        factors, residuals, pivot = wls(x, returns, weights)
        assert max(abs(a - b) for a, b in zip(factors, truth, strict=True)) < Decimal("1e-36")
        assert max(map(abs, residuals)) < Decimal("1e-36")
        assert pivot > Decimal("1e-10")
        mean, _, _ = wls([[ONE], [ONE]], [ZERO, ONE], [ONE, Decimal(3)])
        assert mean == [Decimal("0.75")]


def test_identified_industries_recover_full_returns_and_capitalization_constraint():
    with localcontext() as ctx:
        ctx.prec = 42
        caps = [Decimal(i + 1) for i in range(60)]
        industries = ["a"] * 30 + ["b"] * 30
        weights = [c.sqrt() for c in caps]
        weights = [w / sum(weights) for w in weights]
        x = [
            [ONE, ONE if i < 30 else ZERO, ZERO if i < 30 else ONE, Decimal(i % 7) - 3]
            for i in range(60)
        ]
        truth = [
            Decimal("0.002"),
            Decimal("0.004"),
            -sum(caps[:30]) / sum(caps[30:]) * Decimal("0.004"),
            Decimal("0.003"),
        ]
        design = Exposures(
            ["market", "industry:a", "industry:b", "style:size"],
            x,
            weights,
            caps,
            industries,
            {},
            [],
        )
        factors, residuals, stats = regress(design, matvec(x, truth))
        assert max(abs(a - b) for a, b in zip(factors, truth, strict=True)) < Decimal("1e-36")
        assert abs(sum(caps[:30]) * factors[1] + sum(caps[30:]) * factors[2]) < Decimal("1e-36")
        assert max(map(abs, residuals)) < Decimal("1e-36")
        assert stats["free_parameters"] == 3


@pytest.mark.parametrize("shrink", ["0", "0.15", "1"])
def test_covariance_is_symmetric_and_positive_semidefinite(shrink):
    rng = Random(42)
    with localcontext() as ctx:
        ctx.prec = 42
        rows = [[Decimal(rng.randrange(-10, 11)) for _ in range(5)] for _ in range(60)]
        cov = covariance(rows, ewma_weights(60, Decimal("0.97")), Decimal(shrink))
        assert all(cov[i][j] == cov[j][i] for i in range(5) for j in range(5))
        for _ in range(40):
            vector = [Decimal(rng.randrange(-10, 11)) for _ in range(5)]
            assert dot(vector, matvec(cov, vector)) >= -Decimal("1e-34")
        sample = covariance([[Decimal(1)], [Decimal(2)], [Decimal(3)]], [ONE / 3] * 3)
        assert abs(sample[0][0] - ONE) < Decimal("1e-38")


def test_clipping_standardization_and_rank_rejection():
    with localcontext() as ctx:
        ctx.prec = 42
        values = [Decimal(i) for i in range(39)] + [Decimal(1000000)]
        weights = [ONE / 40] * 40
        z, transform = standardize(values, weights)
        assert Decimal(transform["clip_p95"]) < 40
        assert abs(dot(z, weights)) < Decimal("1e-37")
        assert abs(dot([v * v for v in z], weights) - ONE) < Decimal("1e-37")
        with pytest.raises(AtlasError, match="no usable"):
            standardize([ONE] * 40, weights)
        with pytest.raises(AtlasError, match="collinear"):
            solve([[ONE, ONE], [ONE, ONE]], [ONE, ONE])


def test_real_shared_workflow_persists_hashes_and_deterministic_model(fitted):
    atlas, directory, imported, query, model = fitted
    assert model["is_synthetic"] is True
    assert model["data"]["historical_availability"] == "caller_asserted_unverified"
    assert model["data"]["assets"] == 60
    assert model["data"]["estimation_sessions"] == 60
    assert model["data"]["minimum_relative_pivot"]
    assert atlas.risk(query) == model
    with Atlas("public", directory=directory) as restarted:
        loaded = restarted.risk({"action": "get", "model_id": model["data"]["model_id"]})
        assert loaded["data"] == model["data"]
        assert restarted.import_risk_panel(fictional_panel())["head_id"] == imported["head_id"]
    if os.name != "nt":
        assert (directory / "risk-models.sqlite3").stat().st_mode & 0o077 == 0
    artifact = atlas._risk._read(model["data"]["model_id"], "model")
    assert digest(canonical(artifact)) == model["data"]["model_id"]
    assert all(Decimal(v) >= Decimal("0.00000001") for v in artifact["specific_variances"])
    assert not list(directory.glob("*orders*"))


def test_caller_decimal_context_cannot_change_a_model_or_analysis(fitted):
    from decimal import ROUND_UP, Inexact

    atlas, _, _, query, expected = fitted
    analysis = {
        "action": "analyze",
        "model_id": expected["data"]["model_id"],
        "positions": [{"asset_id": "USDEMO000", "weight": "1"}],
    }
    baseline = atlas.risk(analysis)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert atlas.risk(query) == expected
        assert atlas.risk(analysis) == baseline


def test_factor_history_constraint_covariance_and_selected_vintages(fitted):
    atlas, _, _, _, model = fitted
    model_id = model["data"]["model_id"]
    history = atlas.risk({"action": "get", "model_id": model_id, "view": "history"})["data"]
    groups = [name[9:] for name in history["factor_order"] if name.startswith("industry:")]
    for session in history["sessions"]:
        caps = session["diagnostics"]["industry_capitalizations"]
        with localcontext() as ctx:
            ctx.prec = 42
            constraint = sum(
                Decimal(caps[g]) * Decimal(session["factor_returns"][i + 1])
                for i, g in enumerate(groups)
            )
        assert abs(constraint) < Decimal("1e-21")
    selected = atlas.risk(
        {"action": "get", "model_id": model_id, "view": "assets", "asset_ids": ["USDEMO000"]}
    )["data"]["assets"]
    assert len(selected) == 1
    assert selected[0]["vintage"]["available_at"] < model["data"]["as_of"]
    factor_view = atlas.risk({"action": "get", "model_id": model_id, "view": "factors"})["data"]
    f = [[Decimal(v) for v in row] for row in factor_view["daily_factor_covariance"]]
    for i in range(len(f)):
        assert f[i][i] >= ZERO
        assert all(f[i][j] == f[j][i] for j in range(len(f)))
    assert "training_returns" not in canonical(factor_view).decode()


@pytest.mark.parametrize(
    "positions",
    [
        [{"asset_id": "USDEMO000", "weight": "1"}],
        [
            {"asset_id": "USDEMO000", "weight": "0.6"},
            {"asset_id": "USDEMO001", "weight": "0.5"},
            {"asset_id": "USDEMO002", "weight": "-0.1"},
        ],
        [{"asset_id": "USDEMO000", "weight": "0"}],
    ],
)
def test_risk_contributions_add_up_tracking_error_and_scenarios(fitted, positions):
    atlas, _, _, _, model = fitted
    response = atlas.risk(
        {
            "action": "analyze",
            "model_id": model["data"]["model_id"],
            "positions": positions,
            "benchmark": positions,
            "shocks": [{"factor": "market", "return_shock": "-0.05"}],
        }
    )
    result = response["data"]
    risk = result["portfolio"]
    with localcontext() as ctx:
        ctx.prec = 42
        volatility = Decimal(risk["daily_volatility"])
        asset_sum = sum(
            Decimal(a["daily_volatility_contribution"]) for a in risk["asset_contributions"]
        )
        factor_sum = sum(
            Decimal(v) for v in risk["common_factor_daily_volatility_contributions"].values()
        )
        specific_sum = sum(
            Decimal(a["daily_specific_volatility_contribution"])
            for a in risk["asset_contributions"]
        )
        assert abs(asset_sum - volatility) < Decimal("1e-36")
        assert abs(factor_sum + specific_sum - volatility) < Decimal("1e-36")
        assert abs(
            Decimal(risk["common_variance"])
            + Decimal(risk["specific_variance"])
            - Decimal(risk["daily_variance"])
        ) < Decimal("1e-38")
        assert Decimal(risk["annualized_volatility"]) == volatility * Decimal(252).sqrt()
        assert Decimal(result["active"]["daily_volatility"]) == ZERO
        assert Decimal(result["scenario_common_return"]) == Decimal("-0.05") * Decimal(
            risk["net_weight"]
        )


def test_future_returns_caps_and_later_filings_cannot_change_forecast(fictional):
    modified = copy.deepcopy(fictional)
    for session in modified["sessions"][65:]:
        for row in session["observations"]:
            row["total_return"] = "0.7"
            row["market_cap"] = "999999999999999"
    for row in fictional["fundamentals"]:
        future = row | {
            "available_at": fictional["sessions"][70]["end_at"],
            "book_equity": "99999999999",
        }
        modified["fundamentals"].append(future)
    with localcontext() as ctx:
        ctx.prec = 42
        query = fit_query(fictional)
        before = fit_model(parsed_panel(fictional), query)
        after = fit_model(parsed_panel(modified), query)
        assert before == after


def test_current_session_cap_and_unavailable_filing_are_excluded(fictional):
    altered = copy.deepcopy(fictional)
    cutoff = datetime.fromisoformat(fictional["sessions"][10]["start_at"])
    for row in altered["sessions"][10]["observations"]:
        row["market_cap"] = "999999999999"
    for row in fictional["fundamentals"]:
        altered["fundamentals"].append(
            row | {"available_at": fictional["sessions"][10]["end_at"], "book_equity": "99999999"}
        )
    with localcontext() as ctx:
        ctx.prec = 42
        assert exposures(parsed_panel(fictional), cutoff, ["size", "value"]) == exposures(
            parsed_panel(altered), cutoff, ["size", "value"]
        )


def test_delayed_market_data_uses_only_available_prior_sessions(fictional):
    data = copy.deepcopy(fictional)
    for session in data["sessions"]:
        session["available_at"] = (
            datetime.fromisoformat(session["end_at"]) + timedelta(minutes=5)
        ).isoformat()
    panel = parsed_panel(data)
    cutoff = panel.sessions[10].start_at
    result = exposures(panel, cutoff, ["size"])
    assert all(
        datetime.fromisoformat(v["market_cap_available_at"]) <= cutoff for v in result.vintages
    )
    assert (
        datetime.fromisoformat(result.vintages[0]["market_cap_end_at"]) == panel.sessions[8].end_at
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing_return", "RISK_INPUT_INCOMPLETE"),
        ("duplicate_return", "INVALID_INPUT"),
        ("unknown_source", "INVALID_INPUT"),
        ("gap", "INVALID_INPUT"),
        ("duplicate_fundamental", "INVALID_INPUT"),
        ("zero_assets", "INVALID_INPUT"),
        ("return_minus_one", "INVALID_INPUT"),
        ("unadjusted", "INVALID_INPUT"),
        ("float", "INVALID_INPUT"),
        ("return_from_future", "INVALID_INPUT"),
    ],
)
def test_invalid_panel_never_imports_or_fills_data(fictional, tmp_path, mutation, code):
    data = copy.deepcopy(fictional)
    if mutation == "missing_return":
        data["sessions"][10]["observations"].pop()
    elif mutation == "duplicate_return":
        data["sessions"][10]["observations"][0] = data["sessions"][10]["observations"][1]
    elif mutation == "unknown_source":
        data["sessions"][10]["source_id"] = "unknown"
    elif mutation == "gap":
        data["sessions"][10]["start_at"] = data["sessions"][9]["start_at"]
    elif mutation == "duplicate_fundamental":
        data["fundamentals"].append(data["fundamentals"][0])
    elif mutation == "zero_assets":
        data["fundamentals"][0]["total_assets"] = "0"
    elif mutation == "return_minus_one":
        data["sessions"][10]["observations"][0]["total_return"] = "-1"
    elif mutation == "unadjusted":
        data["manifest"]["return_convention"] = "price_only"
    elif mutation == "float":
        data["sessions"][10]["observations"][0]["total_return"] = 0.1
    else:
        data["sessions"][10]["available_at"] = data["sessions"][10]["start_at"]
    with Atlas("public", directory=tmp_path) as atlas, pytest.raises(AtlasError) as caught:
        atlas.import_risk_panel(data)
    assert caught.value.code == code
    assert not (tmp_path / "risk-models.sqlite3").exists()


@pytest.mark.parametrize(
    "mutation",
    ["late_filing", "stale_filing", "unsupported_sic", "flat_style", "changing_industry"],
)
def test_model_rejects_unavailable_stale_and_unidentified_inputs(fictional, mutation):
    data = copy.deepcopy(fictional)
    if mutation == "late_filing":
        for row in data["fundamentals"]:
            row["available_at"] = data["sessions"][40]["end_at"]
    elif mutation == "stale_filing":
        for row in data["fundamentals"]:
            row["period_end"] = "2020-01-01T21:00:00Z"
    elif mutation == "unsupported_sic":
        data["fundamentals"][0]["sic"] = "0000"
    elif mutation == "flat_style":
        for row in data["fundamentals"]:
            row["total_debt"] = "0"
    else:
        data["fundamentals"].append(
            data["fundamentals"][0]
            | {"available_at": data["sessions"][40]["end_at"], "sic": "6021"}
        )
    with localcontext() as ctx, pytest.raises(AtlasError) as caught:
        ctx.prec = 42
        fit_model(parsed_panel(data), fit_query(data))
    assert caught.value.code in {"RISK_INPUT_INCOMPLETE", "RISK_RANK_DEFICIENT"}


def test_all_seven_styles_with_sufficient_prior_sessions():
    data = fictional_panel(sessions=325)
    query = fit_query(
        data,
        as_of=data["sessions"][-1]["end_at"],
        styles=[
            "size",
            "value",
            "profitability",
            "leverage",
            "momentum",
            "beta",
            "residual_volatility",
        ],
    )
    with localcontext() as ctx:
        ctx.prec = 42
        artifact = fit_model(parsed_panel(data), query)
    assert len(artifact["summary"]["styles"]) == 7
    assert len(artifact["factor_covariance"]) == 12
    assert all(Decimal(v) >= Decimal("0.00000001") for v in artifact["specific_variances"])


@pytest.mark.parametrize("style", ["momentum", "beta", "residual_volatility"])
def test_history_dependent_styles_never_use_short_history(fictional, style):
    with pytest.raises(AtlasError, match="requires|require"):
        fit_model(parsed_panel(fictional), fit_query(fictional, styles=["size", style]))


def test_holdout_excludes_training_and_reports_a_baseline_without_certification(fitted):
    atlas, _, imported, _, model = fitted
    value = atlas.risk(
        {
            "action": "validate",
            "model_id": model["data"]["model_id"],
            "head_id": imported["head_id"],
            "positions": [{"asset_id": "USDEMO000", "weight": "1"}],
        }
    )
    assert value["data"]["sessions"] == 20
    assert value["data"]["start_at"] >= model["data"]["as_of"]
    assert Decimal(value["data"]["historical_portfolio_ewma_baseline_variance"]) > ZERO
    assert Decimal(value["data"]["realized_daily_sample_variance"]) > ZERO
    assert "passed" not in value["data"]


def test_corrupt_state_and_unknown_object_fail_closed(tmp_path, fictional):
    with Atlas("public", directory=tmp_path) as atlas:
        with pytest.raises(AtlasError) as missing:
            atlas.risk({"action": "get", "model_id": "0" * 64})
        assert missing.value.code == "RISK_OBJECT_NOT_FOUND"
        receipt = atlas.risk({"action": "put", "chunk": fictional["manifest"]})
        with atlas._risk.db.connect(write=True) as conn:
            conn.execute(
                "UPDATE risk_objects SET body=? WHERE id=?", (b"{}", receipt["data"]["chunk_id"])
            )
        with pytest.raises(AtlasError) as corrupt:
            atlas.risk({"action": "put", "chunk": fictional["manifest"]})
        assert corrupt.value.code == "RISK_STATE_CORRUPT"


@pytest.mark.parametrize(
    "query",
    [
        {"action": "fit", "head_id": "0" * 64, "as_of": "2024-01-01"},
        {"action": "fit", "head_id": "0" * 64, "as_of": "2024-01-01T00:00:00Z", "lookback": True},
        {"action": "fit", "head_id": "0" * 64, "as_of": "2024-01-01T00:00:00Z", "decay": 0.97},
        {
            "action": "analyze",
            "model_id": "0" * 64,
            "positions": [{"asset_id": "a", "weight": "11"}],
        },
        {"action": "catalog", "extra": "SECRET_SENTINEL"},
    ],
)
def test_invalid_queries_are_sanitized(query):
    with pytest.raises(AtlasError) as caught:
        parse_risk(query)
    assert caught.value.code == "INVALID_INPUT"
    assert "SECRET_SENTINEL" not in json.dumps(caught.value.envelope())


def test_catalog_is_offline_and_does_not_create_private_store(tmp_path):
    with Atlas("public", directory=tmp_path) as atlas:
        result = atlas.risk({"action": "catalog"})
        assert result["is_synthetic"] is None
        assert result["data"]["limits"]["request_bytes"] == MAX_RISK_BYTES
        RiskResult.model_validate(result)
    assert not (tmp_path / "risk-models.sqlite3").exists()


def test_loopback_http_is_opt_in_and_shares_saved_model(fitted):
    _, directory, _, _, model = fitted
    app = create_app(provider="fixture", directory=directory, enable_risk=True)
    with TestClient(app) as http:
        result = http.post(
            "/v1/risk", json={"action": "get", "model_id": model["data"]["model_id"]}
        )
        assert result.status_code == 200
        assert result.json()["is_synthetic"] is True
        assert result.json()["data"]["assets"] == 60
        schema = app.openapi()
        Draft202012Validator.check_schema(RiskQuery.model_json_schema())
        assert "RiskQuery" in schema["components"]["schemas"]
        bad = http.post("/v1/risk", json={"action": "catalog", "extra": "SECRET_SENTINEL"})
        assert bad.status_code == 400 and "SECRET_SENTINEL" not in bad.text
        assert http.post("/v1/risk", content=b"x" * (MAX_RISK_BYTES + 1)).status_code == 413
    with TestClient(create_app(provider="fixture", directory=directory)) as http:
        assert http.post("/v1/risk", json={"action": "catalog"}).status_code == 404


def test_cli_bulk_import_and_saved_analysis(fitted, tmp_path):
    _, directory, _, _, model = fitted
    query = tmp_path / "query.json"
    query.write_bytes(canonical({"action": "get", "model_id": model["data"]["model_id"]}))
    result = subprocess.run(
        [sys.executable, "-m", "atlas_terminal", "risk", "run", str(query)],
        env={**os.environ, "ATLAS_STATE_DIR": str(directory)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["assets"] == 60
    assert not result.stderr


async def test_actual_stdio_connection_has_research_plus_opt_in_risk(fitted, tmp_path):
    _, directory, _, _, model = fitted
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "atlas_terminal",
            "mcp",
            "serve",
            "--provider",
            "public",
            "--research-only",
            "--enable-risk",
        ],
        env={"ATLAS_STATE_DIR": str(directory)},
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=30) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert len(tools) == 7
        tool = next(t for t in tools if t.name == "atlas_risk")
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.idempotent_hint is True
        query = {
            "action": "analyze",
            "model_id": model["data"]["model_id"],
            "positions": [{"asset_id": "USDEMO000", "weight": "1"}],
        }
        Draft202012Validator(tool.input_schema).validate({"query": query})
        result = await session.call_tool("atlas_risk", {"query": query})
        assert not result.is_error
        Draft202012Validator(tool.output_schema).validate(result.structured_content)
        assert Decimal(result.structured_content["data"]["portfolio"]["daily_volatility"]) > ZERO
        research = await session.call_tool("atlas_research_catalog", {"view": "compact"})
        assert not research.is_error
        assert len(research.structured_content["datasets"]) == 32
        invalid = await session.call_tool(
            "atlas_risk", {"query": {"action": "catalog", "secret": "SECRET_SENTINEL"}}
        )
        assert invalid.is_error and "SECRET_SENTINEL" not in invalid.content[0].text


async def test_agent_can_import_fit_and_analyze_through_one_mcp_connection(tmp_path, fictional):
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "atlas_terminal",
            "mcp",
            "serve",
            "--provider",
            "public",
            "--research-only",
            "--enable-risk",
        ],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=30) as session,
    ):
        await session.initialize()

        async def call(query):
            result = await session.call_tool("atlas_risk", {"query": query})
            assert not result.is_error, result.content
            return result.structured_content["data"]

        receipt = await call({"action": "put", "chunk": fictional["manifest"]})
        head = receipt["chunk_id"]
        for kind, records, step in (
            ("fundamentals", fictional["fundamentals"], 20),
            ("sessions", fictional["sessions"], 5),
        ):
            for i in range(0, len(records), step):
                receipt = await call(
                    {
                        "action": "put",
                        "chunk": {"kind": kind, "previous": head, "records": records[i : i + step]},
                    }
                )
                head = receipt["chunk_id"]
        fitted = await call(
            {"action": "fit", "head_id": head, "as_of": fictional["sessions"][64]["end_at"]}
        )
        analysis = await call(
            {
                "action": "analyze",
                "model_id": fitted["model_id"],
                "positions": [{"asset_id": "USDEMO000", "weight": "1"}],
            }
        )
        assert Decimal(analysis["portfolio"]["daily_volatility"]) > ZERO
        assert fitted["historical_availability"] == "caller_asserted_unverified"
        assert not list(tmp_path.glob("*orders*"))


@pytest.mark.parametrize(
    "operation", ["unknown_asset", "duplicate_weights", "unknown_shock", "asset_selection"]
)
def test_portfolio_and_artifact_queries_reject_invalid_inputs(fitted, operation):
    atlas, _, _, _, model = fitted
    query = {
        "action": "analyze",
        "model_id": model["data"]["model_id"],
        "positions": [{"asset_id": "USDEMO000", "weight": "1"}],
    }
    if operation == "unknown_asset":
        query["positions"][0]["asset_id"] = "OUTSIDE"
    elif operation == "duplicate_weights":
        query["positions"].append(query["positions"][0])
    elif operation == "unknown_shock":
        query["shocks"] = [{"factor": "unknown", "return_shock": "0.1"}]
    else:
        query = {"action": "get", "model_id": model["data"]["model_id"], "view": "assets"}
    with pytest.raises(AtlasError) as caught:
        atlas.risk(query)
    assert caught.value.code == "INVALID_INPUT"


def test_insufficient_estimation_universe_and_holdout_fail(fictional, fitted):
    panel = parsed_panel(fictional)
    # A full-rank reduced regression still needs three assets per free parameter.
    design = exposures(panel, panel.sessions[10].end_at, ["size"])
    design.rows = [
        row + [Decimal(i % n) for n in range(2, 20)] for i, row in enumerate(design.rows)
    ]
    with pytest.raises(AtlasError, match="three per"):
        regress(design, [ONE] * 60)
    atlas, _, _, _, model = fitted
    receipt = atlas.import_risk_panel(fictional | {"sessions": fictional["sessions"][:70]})
    with pytest.raises(AtlasError, match="20 strictly"):
        atlas.risk(
            {
                "action": "validate",
                "model_id": model["data"]["model_id"],
                "head_id": receipt["head_id"],
                "positions": [{"asset_id": "USDEMO000", "weight": "1"}],
            }
        )


def test_quota_failure_is_atomic_and_repeated_imports_reuse_objects(
    fictional, tmp_path, monkeypatch
):
    import atlas_terminal.risk_service as service

    with Atlas("public", directory=tmp_path) as atlas:
        receipt = atlas.risk({"action": "put", "chunk": fictional["manifest"]})
        with atlas._risk.db.connect() as conn:
            count = conn.execute("SELECT count(*) FROM risk_objects").fetchone()[0]
        assert atlas.risk({"action": "put", "chunk": fictional["manifest"]}) == receipt
        monkeypatch.setattr(service, "MAX_STATE_BYTES", 1)
        with pytest.raises(AtlasError) as caught:
            atlas.risk(
                {
                    "action": "put",
                    "chunk": {
                        "kind": "fundamentals",
                        "previous": receipt["data"]["chunk_id"],
                        "records": fictional["fundamentals"][:1],
                    },
                }
            )
        assert caught.value.code == "CACHE_FULL"
        with atlas._risk.db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM risk_objects").fetchone()[0] == count


class RiskSDKTransport(httpx2.AsyncBaseTransport):
    def __init__(self, app):
        self.transport = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request):
        response = await self.transport.handle_async_request(
            httpx.Request(
                request.method,
                str(request.url),
                headers=request.headers.raw,
                content=await request.aread(),
            )
        )
        return httpx2.Response(
            response.status_code,
            headers=response.headers.raw,
            content=await response.aread(),
            request=request,
        )

    async def aclose(self):
        await self.transport.aclose()


def test_remote_risk_rejects_anonymous_before_startup(tmp_path):
    with pytest.raises(AtlasError, match="bearer"):
        create_remote_mcp(
            provider="fixture", directory=tmp_path, allow_anonymous=True, enable_risk=True
        )
    assert not (tmp_path / "risk-models.sqlite3").exists()


async def test_official_remote_mcp_risk_reuses_private_model(fitted, fictional):
    _, directory, _, _, model = fitted
    token = "fictional-risk-bearer-value-for-offline-tests"
    root = "https://atlas.example"
    app = create_remote_mcp(
        provider="fixture", directory=directory, public_origin=root, token=token, enable_risk=True
    )
    async with (
        app.app.router.lifespan_context(app.app),
        httpx2.AsyncClient(
            transport=RiskSDKTransport(app), headers={"Authorization": "Bearer " + token}
        ) as client,
        streamable_http_client(root + "/mcp", http_client=client) as streams,
        ClientSession(*streams, read_timeout_seconds=30) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert len(tools) == 7
        result = await session.call_tool(
            "atlas_risk", {"query": {"action": "get", "model_id": model["data"]["model_id"]}}
        )
        assert not result.is_error
        assert result.structured_content["data"]["assets"] == 60
        assert token not in result.content[0].text
        manifest = await session.call_tool(
            "atlas_risk", {"query": {"action": "put", "chunk": fictional["manifest"]}}
        )
        assert not manifest.is_error
        upload = {
            "action": "put",
            "chunk": {
                "kind": "sessions",
                "previous": manifest.structured_content["data"]["chunk_id"],
                "records": fictional["sessions"][:5],
            },
        }
        assert 16384 < len(canonical(upload)) < MAX_RISK_BYTES
        uploaded = await session.call_tool("atlas_risk", {"query": upload})
        assert not uploaded.is_error
        assert uploaded.structured_content["data"]["kind"] == "sessions"
