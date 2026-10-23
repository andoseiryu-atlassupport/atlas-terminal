"""Native input normalization and distinct time-series/cross-sectional model workflows."""

import copy
import csv
import json
import sqlite3
import sys
from decimal import ROUND_UP, Context, Decimal, Inexact, localcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_connections import (
    NativeRiskConnector,
    RiskConnections,
    RiskConnectionSpec,
    price_returns,
)
from atlas_terminal.risk_demo import fictional_panel, fictional_time_series_panel
from atlas_terminal.risk_models import RiskQuery, parse_risk
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest


@pytest.fixture(scope="module")
def panels():
    return {"cross_sectional": fictional_panel(), "time_series": fictional_time_series_panel()}


def relational(panel):
    tables = {"sessions": [], "returns": []}
    for session in panel["sessions"]:
        row = {k: v for k, v in session.items() if k not in {"observations", "factor_returns"}}
        tables["sessions"].append(row)
        tables["returns"] += [
            {"end_at": session["end_at"]} | obs for obs in session["observations"]
        ]
        if "factor_returns" in session:
            tables.setdefault("factors", []).extend(
                {"end_at": session["end_at"], "factor_id": name, "return": value}
                for name, value in session["factor_returns"].items()
            )
    if "fundamentals" in panel:
        tables["fundamentals"] = panel["fundamentals"]
    return tables


def write_connection(directory, panel, method, kind, table_data=None, **options):
    directory.mkdir(parents=True, exist_ok=True)
    tables = table_data if table_data is not None else relational(panel)
    if kind == "json":
        path = directory / "private-panel.json"
        path.write_bytes(canonical(panel))
    elif kind == "csv":
        path = directory
        (path / "manifest.json").write_bytes(canonical(panel["manifest"]))
        for name, rows in tables.items():
            with (path / f"{name}.csv").open("w", newline="") as stream:
                fields = list(dict.fromkeys(key for row in rows for key in row))
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    else:
        path = directory / "private-source.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE manifest(body TEXT)")
            conn.execute("INSERT INTO manifest VALUES(?)", (canonical(panel["manifest"]).decode(),))
            for name, rows in tables.items():
                fields = list(dict.fromkeys(key for row in rows for key in row))
                conn.execute(f'CREATE TABLE "{name}" ({", ".join(f"{f} TEXT" for f in fields)})')
                placeholders = ",".join("?" for _ in fields)
                conn.executemany(
                    f'INSERT INTO "{name}" VALUES({placeholders})',
                    [[row.get(field) for field in fields] for row in rows],
                )
    spec = RiskConnectionSpec.model_validate_json(
        canonical(
            {
                "connection_id": kind + "_" + method,
                "kind": kind,
                "method": method,
                "path": str(path),
                **options,
            }
        )
    )
    return spec


def run_query(panel, connection_id, method, **options):
    return {
        "action": "run",
        "connection_id": connection_id,
        "fit": {"method": method, "as_of": panel["sessions"][64]["end_at"], **options},
        "positions": [{"asset_id": panel["manifest"]["universe"][0]["asset_id"], "weight": "1"}],
    }


@pytest.mark.parametrize("method", ["cross_sectional", "time_series"])
def test_native_json_csv_sqlite_have_identical_numeric_results_and_saved_evidence(
    tmp_path, panels, method
):
    panel = panels[method]
    results = []
    with Atlas("public", directory=tmp_path / "state") as atlas:
        for kind in ("json", "csv", "sqlite"):
            spec = write_connection(tmp_path / kind, panel, method, kind)
            atlas.register_risk_connection(spec.connection_id, NativeRiskConnector(spec))
            raw_db_hash = digest(Path(spec.path).read_bytes()) if kind == "sqlite" else None
            query = run_query(panel, spec.connection_id, method)
            result = atlas.risk(query)
            assert result["action"] == "run" and result["is_synthetic"] is True
            assert result["data"]["model"]["method"] == method
            assert (
                result["data"]["plan"]["estimation_universe"]
                == "from_connection_not_from_portfolio"
            )
            assert str(tmp_path) not in json.dumps(result)
            results.append(result["data"]["analysis"])
            model_id = result["data"]["model"]["model_id"]
            evidence = atlas.risk({"action": "get", "model_id": model_id, "view": "inputs"})["data"]
            assert evidence["input_evidence_id"] == result["data"]["model"]["input_evidence_id"]
            assert evidence["input_evidence"] == result["data"]["input_evidence"]
            assert evidence["input_evidence"]["documents"]
            assert evidence["input_evidence"]["snapshot_atomic"] == (kind == "sqlite")
            assert evidence["input_evidence"]["normalization"]["return_input"] == "returns"
            if kind == "json":
                assert evidence["input_evidence"]["documents"][0]["sha256"] == digest(
                    Path(spec.path).read_bytes()
                )
            if kind == "sqlite":
                assert digest(Path(spec.path).read_bytes()) == raw_db_hash
                assert all(
                    d["hash_encoding"] == "canonical_rows"
                    for d in evidence["input_evidence"]["documents"]
                )
            assert atlas.risk(query) == result
            with Atlas("public", directory=tmp_path / "state") as restarted:
                assert (
                    restarted.risk({"action": "get", "model_id": model_id, "view": "inputs"})[
                        "data"
                    ]
                    == evidence
                )
    assert results[0]["portfolio"] == results[1]["portfolio"] == results[2]["portfolio"]


def exact_series_panel(excess=False):
    panel = fictional_time_series_panel()
    panel["manifest"]["universe"] = [
        {"asset_id": "EXACT", "currency": "USD", "security_type": "common_stock"}
    ]
    panel["manifest"]["factors"] = ["market", "value"]
    panel["manifest"]["factor_convention"] = "excess_return" if excess else "total_return"
    with localcontext(Context(prec=42)):
        for i, row in enumerate(panel["sessions"]):
            market = Decimal(i % 13 - 6) / 1000
            value = Decimal((i * 7) % 17 - 8) / 1000
            rf = Decimal(i % 4 + 1) / 100000 if excess else Decimal(0)
            row["factor_returns"] = {"market": str(market), "value": str(value)}
            row["observations"] = [
                {
                    "asset_id": "EXACT",
                    "total_return": str(
                        rf + Decimal("0.0002") + Decimal("1.7") * market - Decimal("0.3") * value
                    ),
                }
            ]
            if excess:
                row["risk_free_return"] = str(rf)
    return panel


@pytest.mark.parametrize("excess", [False, True])
@pytest.mark.parametrize("weighting", ["equal", "ewma"])
def test_time_series_recovers_known_alpha_betas_without_using_cross_sectional_styles(
    tmp_path, excess, weighting
):
    panel = exact_series_panel(excess)
    with Atlas("public", directory=tmp_path) as atlas, localcontext(Context(prec=42)):
        head = atlas.import_risk_panel(panel)["head_id"]
        fitted = atlas.risk(
            {
                "action": "fit",
                "head_id": head,
                "method": "time_series",
                "as_of": panel["sessions"][64]["end_at"],
                "regression_weights": weighting,
            }
        )
        model_id = fitted["data"]["model_id"]
        assert fitted["methodology_version"] == "atlas-time-series-v1"
        asset = atlas.risk(
            {"action": "get", "model_id": model_id, "view": "assets", "asset_ids": ["EXACT"]}
        )["data"]["assets"][0]
        assert abs(Decimal(asset["alpha"]) - Decimal("0.0002")) < Decimal("1e-34")
        assert abs(Decimal(asset["exposures"][0]) - Decimal("1.7")) < Decimal("1e-34")
        assert abs(Decimal(asset["exposures"][1]) + Decimal("0.3")) < Decimal("1e-34")
        analysis = atlas.risk(
            {
                "action": "analyze",
                "model_id": model_id,
                "positions": [{"asset_id": "EXACT", "weight": "1"}],
                "shocks": [{"factor": "market", "return_shock": "-0.03"}],
            }
        )["data"]
        assert abs(Decimal(analysis["scenario_common_return"]) - Decimal("-0.051")) < Decimal(
            "1e-34"
        )
        assert analysis["modeled_return"] == ("excess_return" if excess else "total_return")
        holdout = atlas.risk(
            {
                "action": "validate",
                "model_id": model_id,
                "head_id": head,
                "positions": [{"asset_id": "EXACT", "weight": "1"}],
            }
        )["data"]
        assert holdout["sessions"] == 20
        returns = [
            Decimal(s["observations"][0]["total_return"]) - Decimal(s.get("risk_free_return", "0"))
            for s in panel["sessions"][65:]
        ]
        mean = sum(returns) / 20
        expected = sum((r - mean) ** 2 for r in returns) / 19
        assert abs(Decimal(holdout["realized_daily_sample_variance"]) - expected) < Decimal("1e-37")


def test_time_series_context_currency_and_future_data_isolation(tmp_path):
    panel = exact_series_panel()
    panel["manifest"]["currency"] = "JPY"
    panel["manifest"]["universe"][0]["currency"] = "JPY"
    changed = copy.deepcopy(panel)
    for row in changed["sessions"][65:]:
        row["factor_returns"] = {"market": "0.5", "value": "0.7"}
        row["observations"][0]["total_return"] = "0.8"
    with Atlas("public", directory=tmp_path) as atlas:
        first = atlas.import_risk_panel(panel)["head_id"]
        second = atlas.import_risk_panel(changed)["head_id"]
        models = [
            atlas.risk(
                {
                    "action": "fit",
                    "method": "time_series",
                    "head_id": h,
                    "as_of": panel["sessions"][64]["end_at"],
                }
            )
            for h in (first, second)
        ]
        values = [
            atlas.risk(
                {
                    "action": "analyze",
                    "model_id": m["data"]["model_id"],
                    "positions": [{"asset_id": "EXACT", "weight": "1"}],
                }
            )["data"]
            for m in models
        ]
        assert values[0]["portfolio"] == values[1]["portfolio"]
        assert values[0]["currency"] == "JPY"
        history = atlas.risk(
            {"action": "get", "model_id": models[0]["data"]["model_id"], "view": "history"}
        )["data"]
        assert history["factor_return_origin"] == "supplied_observed_factor_returns"
        assert history["asset_regressions"][0]["asset_id"] == "EXACT"


@pytest.mark.parametrize(
    "mutation",
    [
        "constant",
        "collinear",
        "missing_factor",
        "missing_asset",
        "wrong_unit",
        "missing_rf",
        "mixed_currency",
        "gap",
        "duplicate",
    ],
)
def test_time_series_rejects_unusable_inputs(tmp_path, mutation):
    panel = exact_series_panel()
    if mutation == "constant":
        for row in panel["sessions"]:
            row["factor_returns"]["value"] = "0"
    elif mutation == "collinear":
        for row in panel["sessions"]:
            row["factor_returns"]["value"] = row["factor_returns"]["market"]
    elif mutation == "missing_factor":
        panel["sessions"][10]["factor_returns"].pop("value")
    elif mutation == "missing_asset":
        panel["sessions"][10]["observations"].clear()
    elif mutation == "wrong_unit":
        panel["manifest"]["return_unit"] = "percent"
    elif mutation == "missing_rf":
        panel["manifest"]["factor_convention"] = "excess_return"
    elif mutation == "mixed_currency":
        panel["manifest"]["universe"][0]["currency"] = "JPY"
    elif mutation == "gap":
        panel["sessions"][10]["start_at"] = panel["sessions"][9]["start_at"]
    else:
        panel["sessions"].append(panel["sessions"][10])
    with Atlas("public", directory=tmp_path) as atlas, pytest.raises(AtlasError) as caught:
        head = atlas.import_risk_panel(panel)["head_id"]
        atlas.risk(
            {
                "action": "fit",
                "method": "time_series",
                "head_id": head,
                "as_of": panel["sessions"][64]["end_at"],
            }
        )
    assert caught.value.code in {"INVALID_INPUT", "RISK_INPUT_INCOMPLETE", "RISK_RANK_DEFICIENT"}


def test_preview_and_connections_do_not_read_datasets_or_create_state(tmp_path):
    class Provider:
        method = "cross_sectional"
        kind = "local_test"
        uses_network = False

        def read(self):
            pytest.fail("Preview read data")

    with Atlas("public", directory=tmp_path) as atlas:
        atlas.register_risk_connection("test", Provider())
        assert (
            atlas.risk({"action": "connections"})["data"]["connections"][0]["connection_id"]
            == "test"
        )
        result = atlas.risk(
            {
                "action": "run",
                "connection_id": "test",
                "preview": True,
                "fit": {"as_of": "2024-01-01T00:00:00Z"},
                "positions": [{"asset_id": "ANY", "weight": "1"}],
            }
        )
        assert result["is_synthetic"] is None
        assert result["data"]["data_reads_executed"] is False
        assert result["data"]["plan"]["estimation_universe"] == "from_connection_not_from_portfolio"
    assert not (tmp_path / "risk-models.sqlite3").exists()


def test_trusted_api_provider_requires_explicit_opt_in_and_errors_are_sanitized(tmp_path, panels):
    class Provider:
        method = "time_series"
        kind = "api_test"
        uses_network = True
        calls = 0

        def read(self):
            self.calls += 1
            raise RuntimeError("SECRET_SENTINEL and a private endpoint")

    provider = Provider()
    with Atlas("public", directory=tmp_path) as atlas:
        atlas.register_risk_connection("api", provider)
        query = run_query(panels["time_series"], "api", "time_series")
        with pytest.raises(AtlasError) as denied:
            atlas.risk(query)
        assert denied.value.code == "CONFIG_REQUIRED" and provider.calls == 0
        assert atlas.risk(query | {"preview": True})["data"]["data_reads_executed"] is False
        with pytest.raises(AtlasError) as failed:
            atlas.risk(query | {"allow_network": True})
        assert failed.value.code == "SOURCE_UNAVAILABLE" and provider.calls == 1
        assert "SECRET_SENTINEL" not in json.dumps(failed.value.envelope())


def test_connector_method_and_portfolio_universe_are_explicit(tmp_path, panels):
    panel = panels["cross_sectional"]
    spec = write_connection(tmp_path / "input", panel, "cross_sectional", "json")
    with Atlas("public", directory=tmp_path / "state") as atlas:
        atlas.register_risk_connection(spec.connection_id, NativeRiskConnector(spec))
        bad_method = run_query(panel, spec.connection_id, "time_series")
        with pytest.raises(AtlasError, match="methods differ"):
            atlas.risk(bad_method)
        query = run_query(panel, spec.connection_id, "cross_sectional")
        query["positions"][0]["asset_id"] = "OUTSIDE"
        with pytest.raises(AtlasError, match="outside"):
            atlas.risk(query)
    assert not (tmp_path / "state" / "risk-models.sqlite3").exists()


def test_adjusted_returns_and_raw_capitalization_use_separate_bases():
    spec = RiskConnectionSpec(
        connection_id="prices",
        kind="csv",
        method="cross_sectional",
        path="/operator/data",
        return_input="adjusted_prices",
    )
    sessions = {
        "2024-01-03T21:00:00+00:00": {
            "start_at": "2024-01-02T21:00:00Z",
            "available_at": "2024-01-03T21:00:00Z",
        }
    }
    rows = [
        {
            "asset_id": "A",
            "end_at": "2024-01-02T21:00:00Z",
            "available_at": "2024-01-02T21:05:00Z",
            "adjusted_close": "100",
            "close": "100",
            "shares_outstanding": "1000",
        },
        {
            "asset_id": "A",
            "end_at": "2024-01-03T21:00:00Z",
            "available_at": "2024-01-03T21:05:00Z",
            "adjusted_close": "110",
            "close": "55",
            "shares_outstanding": "2000",
        },
    ]
    result = price_returns(spec, sessions, rows)
    assert result == [
        {
            "end_at": "2024-01-03T21:00:00+00:00",
            "asset_id": "A",
            "total_return": "0.1",
            "market_cap": "110000",
        }
    ]
    assert sessions["2024-01-03T21:00:00+00:00"]["available_at"] == "2024-01-03T21:05:00+00:00"


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing_start",
        "zero_price",
        "float",
        "fractional_shares",
        "cap_and_close",
        "unmatched",
        "before_close",
    ],
)
def test_price_normalization_rejects_ambiguous_or_invalid_inputs(mutation):
    spec = RiskConnectionSpec(
        connection_id="prices",
        kind="csv",
        method="cross_sectional",
        path="/operator/data",
        return_input="adjusted_prices",
    )
    sessions = {
        "2024-01-03T21:00:00+00:00": {
            "start_at": "2024-01-02T21:00:00Z",
            "available_at": "2024-01-03T21:00:00Z",
        }
    }
    rows = [
        {
            "asset_id": "A",
            "end_at": f"2024-01-0{i}T21:00:00Z",
            "available_at": f"2024-01-0{i}T21:05:00Z",
            "adjusted_close": "100",
            "close": "100",
            "shares_outstanding": "1000",
        }
        for i in (2, 3)
    ]
    if mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "missing_start":
        rows.pop(0)
    elif mutation == "zero_price":
        rows[0]["adjusted_close"] = "0"
    elif mutation == "float":
        rows[0]["adjusted_close"] = 100.0
    elif mutation == "fractional_shares":
        rows[1]["shares_outstanding"] = "1000.5"
    elif mutation == "cap_and_close":
        rows[1]["market_cap"] = "100000"
    elif mutation == "unmatched":
        rows.append(
            rows[0] | {"end_at": "2024-01-01T21:00:00Z", "available_at": "2024-01-01T21:05:00Z"}
        )
    else:
        rows[1]["available_at"] = "2024-01-03T20:59:00Z"
    with pytest.raises((AtlasError, ValueError, TypeError)):
        price_returns(spec, sessions, rows)


def test_explicit_csv_column_and_asset_mapping(tmp_path):
    panel = exact_series_panel()
    spec = write_connection(tmp_path / "input", panel, "time_series", "csv")
    path = Path(spec.path) / "returns.csv"
    rows = list(csv.DictReader(path.open()))
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["Date", "Ticker", "Total Return", "Unused"])
        writer.writeheader()
        writer.writerows(
            {
                "Date": r["end_at"],
                "Ticker": "SOURCE/A",
                "Total Return": r["total_return"],
                "Unused": "ignored explicitly",
            }
            for r in rows
        )
    spec = spec.model_copy(
        update={
            "columns": {
                "returns": {"end_at": "Date", "asset_id": "Ticker", "total_return": "Total Return"}
            },
            "asset_map": {"SOURCE/A": "EXACT"},
        }
    )
    with Atlas("public", directory=tmp_path / "state") as atlas:
        atlas.register_risk_connection(spec.connection_id, NativeRiskConnector(spec))
        result = atlas.risk(run_query(panel, spec.connection_id, "time_series"))
    assert result["data"]["model"]["assets"] == 1


@pytest.mark.parametrize("method", ["cross_sectional", "time_series"])
@pytest.mark.parametrize("kind", ["csv", "sqlite"])
def test_adjusted_price_connections_match_supplied_returns(tmp_path, panels, method, kind):
    panel = panels[method]
    tables = relational(panel)
    prices = []
    with localcontext(Context(prec=80)):
        for asset in panel["manifest"]["universe"]:
            price = Decimal("100")
            asset_id = asset["asset_id"]
            prices.append(
                {
                    "asset_id": asset_id,
                    "end_at": panel["sessions"][0]["start_at"],
                    "available_at": panel["sessions"][0]["start_at"],
                    "adjusted_close": "100",
                    **({"market_cap": "1000000"} if method == "cross_sectional" else {}),
                }
            )
            for session in panel["sessions"]:
                obs = next(r for r in session["observations"] if r["asset_id"] == asset_id)
                price = (price * (1 + Decimal(obs["total_return"]))).quantize(
                    Decimal("0.000000000000000001")
                )
                prices.append(
                    {
                        "asset_id": asset_id,
                        "end_at": session["end_at"],
                        "available_at": session["available_at"],
                        "adjusted_close": format(price, "f"),
                        **(
                            {"market_cap": obs["market_cap"]} if method == "cross_sectional" else {}
                        ),
                    }
                )
    del tables["returns"]
    tables["prices"] = prices
    spec = write_connection(
        tmp_path / "prices",
        panel,
        method,
        kind,
        table_data=tables,
        return_input="adjusted_prices",
    )
    supplied = write_connection(tmp_path / "returns", panel, method, "json")
    with Atlas("public", directory=tmp_path / "state") as atlas:
        results = []
        for connection in (spec, supplied):
            atlas.register_risk_connection(
                connection.connection_id, NativeRiskConnector(connection)
            )
            results.append(atlas.risk(run_query(panel, connection.connection_id, method))["data"])
    assert results[0]["input_evidence"]["normalization"]["return_input"] == "adjusted_prices"
    assert results[0]["input_evidence"]["normalization"]["mapping_sha256"]
    with localcontext(Context(prec=42)):
        for metric in ("daily_volatility", "annualized_volatility"):
            assert abs(
                Decimal(results[0]["analysis"]["portfolio"][metric])
                - Decimal(results[1]["analysis"]["portfolio"][metric])
            ) < Decimal("1e-16")


def test_sqlite_does_not_silently_convert_binary_floats_to_decimal_strings(tmp_path):
    panel = exact_series_panel()
    spec = write_connection(tmp_path / "input", panel, "time_series", "sqlite")
    with sqlite3.connect(spec.path) as conn:
        conn.execute(
            "CREATE VIEW numeric_returns AS SELECT end_at, asset_id, "
            "CAST(total_return AS REAL) AS total_return FROM returns"
        )
    spec = spec.model_copy(update={"tables": {"returns": "numeric_returns"}})
    with Atlas("public", directory=tmp_path / "state") as atlas:
        atlas.register_risk_connection(spec.connection_id, NativeRiskConnector(spec))
        with pytest.raises(AtlasError) as caught:
            atlas.risk(run_query(panel, spec.connection_id, "time_series"))
    assert caught.value.code == "INVALID_INPUT"
    assert not (tmp_path / "state" / "risk-models.sqlite3").exists()


def test_time_series_import_and_fit_ignore_caller_decimal_traps(tmp_path):
    panel = exact_series_panel()
    results = []
    for precision, rounding, trap in ((42, "ROUND_HALF_EVEN", False), (3, ROUND_UP, True)):
        with (
            Atlas("public", directory=tmp_path / str(precision)) as atlas,
            localcontext(Context(prec=precision, rounding=rounding)) as context,
        ):
            context.traps[Inexact] = trap
            head = atlas.import_risk_panel(panel)["head_id"]
            results.append(
                atlas.risk(
                    {
                        "action": "fit",
                        "head_id": head,
                        "method": "time_series",
                        "as_of": panel["sessions"][64]["end_at"],
                    }
                )
            )
    assert results[0] == results[1]


def configure_file(path, specs):
    path.write_bytes(
        canonical(
            {"schema_version": "0.1.0", "connections": [s.model_dump(mode="json") for s in specs]}
        )
    )
    path.chmod(0o600)


async def test_actual_mcp_one_call_runs_each_native_model_without_data_upload(tmp_path, panels):
    specs = [
        write_connection(tmp_path / method, panel, method, "csv")
        for method, panel in panels.items()
    ]
    config = tmp_path / "operator-connections.json"
    configure_file(config, specs)
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
        env={
            "ATLAS_STATE_DIR": str(tmp_path / "state"),
            "ATLAS_RISK_CONNECTIONS_FILE": str(config),
        },
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=30) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert len(tools) == 7
        tool = next(t for t in tools if t.name == "atlas_risk")
        for spec in specs:
            query = run_query(panels[spec.method], spec.connection_id, spec.method)
            Draft202012Validator(tool.input_schema).validate({"query": query})
            result = await session.call_tool("atlas_risk", {"query": query})
            assert not result.is_error, result.content
            Draft202012Validator(tool.output_schema).validate(result.structured_content)
            assert result.structured_content["data"]["model"]["method"] == spec.method
            assert str(tmp_path) not in result.content[0].text
        bad = await session.call_tool("atlas_risk", {"query": query | {"path": "SECRET_SENTINEL"}})
        assert bad.is_error and "SECRET_SENTINEL" not in bad.content[0].text


def test_http_and_configuration_hide_paths_and_reject_mutable_source_arguments(
    tmp_path, panels, monkeypatch
):
    panel = panels["time_series"]
    spec = write_connection(tmp_path / "input", panel, "time_series", "sqlite")
    config = tmp_path / "operator-connections.json"
    configure_file(config, [spec])
    monkeypatch.setenv("ATLAS_RISK_CONNECTIONS_FILE", str(config))
    with TestClient(
        create_app(provider="fixture", directory=tmp_path / "state", enable_risk=True)
    ) as http:
        available = http.post("/v1/risk", json={"action": "connections"})
        assert available.status_code == 200 and str(tmp_path) not in available.text
        query = run_query(panel, spec.connection_id, "time_series")
        response = http.post("/v1/risk", json=query)
        assert response.status_code == 200
        assert response.json()["data"]["model"]["method"] == "time_series"
        for field in ("sql", "path", "url"):
            bad = http.post("/v1/risk", json=query | {field: "SECRET_SENTINEL"})
            assert bad.status_code == 400 and "SECRET_SENTINEL" not in bad.text


def test_invalid_private_configuration_and_query_options_fail_closed(tmp_path, panels, monkeypatch):
    config = tmp_path / "operator-connections.json"
    config.write_bytes(b"{}")
    config.chmod(0o600)
    monkeypatch.setenv("ATLAS_RISK_CONNECTIONS_FILE", str(config))
    with (
        Atlas("public", directory=tmp_path / "state") as atlas,
        pytest.raises(AtlasError) as caught,
    ):
        atlas.risk({"action": "connections"})
    assert caught.value.code == "CONFIG_REQUIRED"
    for value in (
        {
            "action": "fit",
            "method": "time_series",
            "head_id": "0" * 64,
            "as_of": "2024-01-01T00:00:00Z",
            "styles": ["size"],
        },
        {
            "action": "fit",
            "method": "cross_sectional",
            "head_id": "0" * 64,
            "as_of": "2024-01-01T00:00:00Z",
            "regression_weights": "equal",
        },
    ):
        with pytest.raises(AtlasError):
            parse_risk(value)
    Draft202012Validator.check_schema(RiskQuery.model_json_schema())
    assert not (tmp_path / "state" / "risk-models.sqlite3").exists()


def test_configuration_permissions_and_combined_quota_are_checked_before_registration(
    tmp_path, monkeypatch
):
    specs = [
        RiskConnectionSpec(
            connection_id=f"configured{i}",
            kind="json",
            method="time_series",
            path=str(tmp_path / "not-read.json"),
        )
        for i in range(2)
    ]
    config = tmp_path / "connections.json"
    configure_file(config, specs)
    monkeypatch.setenv("ATLAS_RISK_CONNECTIONS_FILE", str(config))
    registry = RiskConnections()
    for i in range(31):
        registry.register(f"registered{i}", NativeRiskConnector(specs[0]))
    with pytest.raises(AtlasError) as caught:
        registry.catalog()
    assert caught.value.code == "CONFIG_REQUIRED"
    assert len(registry._providers) == 31
    if sys.platform != "win32":
        config.chmod(0o644)
        with pytest.raises(AtlasError) as permissions:
            RiskConnections().catalog()
        assert permissions.value.code == "CONFIG_REQUIRED"
