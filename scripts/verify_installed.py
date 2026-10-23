"""Verify installed demo, MCP and loopback mock recovery. No external network."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.hosted import HostedClient
from atlas_terminal.models import parse_query
from atlas_terminal.util import canonical, digest

QUERY = parse_query({"identifier": "ATLASDEMO", "metrics": ["revenue", "net_income"], "count": 3})
expected = Atlas("fixture").get_financials(QUERY).model_dump(mode="json")
MACRO_QUERIES = [
    {"dataset": "sdmx_catalog", "source_id": "imf"},
    {
        "dataset": "international_economics",
        "source_id": "imf",
        "dataflow": "IMF.DEMO,ATLAS_MACRO,1.0",
        "dimensions": {"FREQUENCY": "M", "COUNTRY": "USA", "INDICATOR": "CPI"},
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
    },
    json.loads(
        (Path(__file__).resolve().parents[1] / "examples/research_panels/us_japan.json").read_text()
    ),
    {"dataset": "fx_rates", "base_currency": "USD", "quote_currency": "JPY"},
    {"dataset": "reference_rates", "series": "sofr"},
    {"dataset": "treasury_real_yields", "year": 2026},
    {"dataset": "macro_analytics", "series": "yield_spread_10y_2y", "year": 2026},
    {
        "dataset": "national_accounts",
        "series": "gdp_real",
        "start_date": "2025-01-01",
        "end_date": "2026-12-31",
    },
    {
        "dataset": "japan_economics",
        "series": "cpi",
        "start_date": "2025-01-01",
        "end_date": "2026-12-31",
    },
]
BATCH_QUERY = {
    "items": [
        {
            "id": "fx",
            "query": {
                "dataset": "fx_rates",
                "base_currency": "USD",
                "quote_currency": "JPY",
                "limit": 2,
                "requirements": {"min_observation_date": "2026-10-19", "allow_missing": False},
            },
        },
        {"id": "sofr", "query": {"dataset": "reference_rates", "series": "sofr"}},
        {"id": "invalid", "query": {"dataset": "economics", "series": "unknown"}},
    ]
}
PRESET_QUERY = {
    "preset": "fx_context",
    "base_currency": "USD",
    "quote_currency": "JPY",
    "include": ["fx", "us_sofr"],
    "view": "full",
}


def verify_preset_result(result):
    assert result["status"] == "ok" and result["mode"] == "executed"
    assert not result["requires_wallet"] and not result["atomic_snapshot"]
    assert result["batch"]["summary"]["successful"] == 2
    assert [row["id"] for row in result["overview"]] == ["fx", "us_sofr"]
    assert result["overview"][0]["availability"] == "partial"
    assert result["overview"][0]["publication_times"]["latest"] is None
    assert result["batch"]["items"][0]["result"]["records"][0]["value"] == "120"
    assert result["overview"][1]["observation_dates"]["latest"] == "2026-10-19"


def verify_batch_result(result):
    assert result["status"] == "partial"
    assert result["summary"] == {
        "requested": 3,
        "successful": 2,
        "failed": 1,
        "retryable_read_ids": [],
    }
    assert [row["id"] for row in result["items"]] == ["fx", "sofr", "invalid"]
    assert result["items"][0]["result"]["records"][0]["value"] == "120"
    assert result["items"][0]["result"]["quality"]["meets_requirements"]
    assert result["items"][1]["result"]["records"][0]["value"] == "1.30"
    assert result["items"][2]["error"]["code"] == "INVALID_INPUT"


async def payment_planning_mcp(root, env, quote_id):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "hosted"],
        cwd=root,
        env=env,
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        quote = await session.call_tool(
            "atlas_quote", QUERY.model_dump(mode="json") | {"intent_id": "installed-workflow"}
        )
        assert not quote.is_error and quote.structured_content["quote_id"] == quote_id
        budget = await session.call_tool("atlas_budget_status", {})
        assert not budget.is_error and budget.structured_content["day"]["spent_atomic"] == "0"
        checked = await session.call_tool("atlas_purchase_preflight", {"quote_id": quote_id})
        assert not checked.is_error and checked.structured_content["decision"] == "ready"
        denied = await session.call_tool(
            "atlas_purchase", {"quote_id": quote_id, "max_cost": "9999"}
        )
        assert denied.is_error and denied.structured_content["error"]["code"] == "BUDGET_EXCEEDED"


async def mcp_smoke(root, macro_expected):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        cwd=root,
        env={"ATLAS_STATE_DIR": str(root / "mcp")},
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        assert len((await session.list_tools()).tools) == 14
        preset = await session.call_tool("atlas_get_research_preset", {"query": PRESET_QUERY})
        assert not preset.is_error
        verify_preset_result(preset.structured_content)
        doctor = await session.call_tool("atlas_research_diagnostics", {"query": {}})
        assert not doctor.is_error and doctor.structured_content["status"] == "synthetic"
        batch = await session.call_tool("atlas_get_research_batch", {"query": BATCH_QUERY})
        assert not batch.is_error
        verify_batch_result(batch.structured_content)
        for query in MACRO_QUERIES:
            macro = await session.call_tool("atlas_get_research", {"query": query})
            assert not macro.is_error
            assert macro.structured_content == macro_expected[query["dataset"]]
        research = await session.call_tool(
            "atlas_get_research",
            {"query": {"dataset": "institutional_holdings", "identifier": "ATLASDEMO"}},
        )
        assert (
            not research.is_error
            and research.structured_content["records"][0]["cusip"] == "999999AA1"
        )
        agent_query = {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "metrics": ["revenue"],
            "response": {"view": "compact", "page_size": 1},
        }
        compact = await session.call_tool("atlas_get_research", {"query": agent_query})
        assert not compact.is_error
        evidence_id = compact.structured_content["records"][0]["source"]["evidence_id"]
        evidence = await session.call_tool("atlas_get_evidence", {"evidence_id": evidence_id})
        assert not evidence.is_error
        assert digest(canonical(evidence.structured_content["evidence"])) == evidence_id
        agent_query["response"]["cursor"] = compact.structured_content["response"]["next_cursor"]
        next_page = await session.call_tool("atlas_get_research", {"query": agent_query})
        assert not next_page.is_error and next_page.structured_content["response"]["offset"] == 1
        data = await session.call_tool("atlas_get_financials", QUERY.model_dump(mode="json"))
        assert data.structured_content == expected
    return "PASS"


with TemporaryDirectory(prefix="atlas-wheel-e2e-") as temporary:
    root = Path(temporary)
    demo = subprocess.run(
        [sys.executable, "-m", "atlas_terminal", "demo", "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(demo.stdout) == expected and not demo.stderr
    research_demo = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "economics",
            "--series",
            "cpi",
            "--provider",
            "fixture",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(research_demo.stdout)["records"][0]["value"] == "321.500"
    with Atlas("fixture", directory=root / "sdk") as atlas:
        macro_expected = {query["dataset"]: atlas.get_research(query) for query in MACRO_QUERIES}
        verify_batch_result(atlas.get_research_batch(BATCH_QUERY))
        verify_preset_result(atlas.get_research_preset(PRESET_QUERY))
        assert atlas.get_research_preset(PRESET_QUERY | {"preview": True})["batch"] is None
        assert not atlas.research_diagnostics()["network_checks_executed"]
    preset_cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "preset",
            "--input",
            "-",
            "--provider",
            "fixture",
        ],
        cwd=root,
        input=json.dumps(PRESET_QUERY),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    verify_preset_result(json.loads(preset_cli.stdout))
    doctor_cli = subprocess.run(
        [sys.executable, "-m", "atlas_terminal", "research", "doctor", "--provider", "fixture"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(doctor_cli.stdout)["status"] == "synthetic" and not doctor_cli.stderr
    batch_cli = subprocess.run(
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
        ],
        cwd=root,
        input=json.dumps(BATCH_QUERY),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert batch_cli.returncode == 1 and not batch_cli.stderr
    verify_batch_result(json.loads(batch_cli.stdout))
    source_check = subprocess.run(
        [sys.executable, "-m", "atlas_terminal", "research", "check-sources", "--sources", "ecb"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert source_check.returncode == 4 and json.loads(source_check.stdout)["status"] == "NOT_RUN"
    assert macro_expected["fx_rates"]["records"][0]["value"] == "120"
    assert macro_expected["reference_rates"]["records"][0]["value"] == "1.30"
    assert any(row["value"] == "-0.10" for row in macro_expected["treasury_real_yields"]["records"])
    assert macro_expected["macro_analytics"]["records"][0]["value"] == "25.00"
    for query in MACRO_QUERIES:
        saved_query = any(isinstance(value, (dict, list)) for value in query.values())
        args = [sys.executable, "-m", "atlas_terminal", "research"]
        if saved_query:
            args.extend(["run", "--input", "-"])
        else:
            args.extend(["get", query["dataset"]])
            for key, value in query.items():
                if key != "dataset":
                    args.extend(["--" + key.replace("_", "-"), str(value)])
        macro_cli = subprocess.run(
            [*args, "--provider", "fixture"],
            cwd=root,
            capture_output=True,
            text=True,
            input=json.dumps(query) if saved_query else None,
            timeout=20,
            check=True,
        )
        assert json.loads(macro_cli.stdout) == macro_expected[query["dataset"]]
        assert macro_expected[query["dataset"]]["is_synthetic"]
    mcp_status = asyncio.run(mcp_smoke(root, macro_expected))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    policy = root / "policy.json"
    policy.write_bytes(
        canonical(
            {
                "enabled": True,
                "mode": "mock",
                "allowed_origins": [base],
                "network": "mock:0",
                "asset": "fixture:USDC",
                "pay_to": "fixture:merchant",
                "per_purchase_atomic": "10000",
                "per_session_atomic": "20000",
                "per_day_atomic": "20000",
            }
        )
    )
    server_env = dict(os.environ, ATLAS_STATE_DIR=str(root / "server"))
    for name in ["ATLAS_PUBLIC_ORIGIN", "WEB_CONCURRENCY", "UVICORN_WORKERS"]:
        server_env.pop(name, None)

    def start_server():
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "atlas_terminal",
                "serve",
                "--provider",
                "fixture",
                "--payments",
                "mock",
                "--port",
                str(port),
            ],
            cwd=root,
            env=server_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Loopback server exited during startup")
            try:
                with httpx.Client(trust_env=False, timeout=0.3) as probe:
                    if probe.get(base + "/healthz").status_code == 200:
                        return proc
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        proc.terminate()
        proc.wait(timeout=5)
        raise RuntimeError("Loopback server startup timeout")

    def stop(proc):
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    proc = start_server()
    try:
        with httpx.Client(trust_env=False) as probe:
            document = probe.get(base + "/openapi.json").json()
            preset_schema = document["paths"]["/v1/research/preset"]["post"]["requestBody"][
                "content"
            ]["application/json"]["schema"]
            assert preset_schema["$ref"] == "#/components/schemas/ResearchPresetQuery"
            assert "ResearchPresetQuery" in document["components"]["schemas"]
            batch_http = probe.post(base + "/v1/research/batch", json=BATCH_QUERY)
            assert batch_http.status_code == 200
            verify_batch_result(batch_http.json())
            preset_http = probe.post(base + "/v1/research/preset", json=PRESET_QUERY)
            assert preset_http.status_code == 200
            verify_preset_result(preset_http.json())
            assert "PAYMENT-REQUIRED" not in preset_http.headers
            doctor_http = probe.post(base + "/v1/research/diagnostics", json={})
            assert doctor_http.status_code == 200 and doctor_http.json()["status"] == "synthetic"
            for query in MACRO_QUERIES:
                macro_http = probe.post(base + "/v1/research", json=query)
                assert macro_http.status_code == 200
                assert macro_http.json() == macro_expected[query["dataset"]]
            research_http = probe.post(
                base + "/v1/research",
                json={"dataset": "insider_transactions", "identifier": "ATLASDEMO"},
            )
            assert (
                research_http.status_code == 200
                and research_http.json()["records"][0]["shares"] == "100.5"
            )
            compact_http = probe.post(
                base + "/v1/research",
                json={
                    "dataset": "statements",
                    "identifier": "ATLASDEMO",
                    "metrics": ["revenue"],
                    "response": {"view": "compact", "page_size": 1},
                },
            )
            assert compact_http.status_code == 200
            evidence_id = compact_http.json()["records"][0]["source"]["evidence_id"]
            evidence_http = probe.get(base + "/v1/research/evidence/" + evidence_id)
            assert evidence_http.status_code == 200
            assert digest(canonical(evidence_http.json()["evidence"])) == evidence_id
        client = HostedClient(
            base,
            directory=root / "client",
            client=httpx.Client(trust_env=False, timeout=10),
            policy_path=policy,
        )
        q = client.quote(QUERY, intent_id="installed-workflow")
        assert client.quote(QUERY, intent_id="installed-workflow") == q
        assert client.budget_status().day.spent_atomic == "0"
        assert client.preflight(q.quote_id).decision == "ready"
        assert client.preflight(q.quote_id, max_cost="9999").code == "BUDGET_EXCEEDED"
        planning_env = dict(
            os.environ,
            ATLAS_HOSTED_ORIGIN=base,
            ATLAS_STATE_DIR=str(root / "client"),
            ATLAS_POLICY_FILE=str(policy),
            ATLAS_SESSION_ID="default",
            NO_PROXY=os.environ.get("NO_PROXY", "") + ",127.0.0.1,localhost",
        )
        for command, key, value in (
            (
                ["quote", "financials", "ATLASDEMO", "--intent-id", "installed-workflow"],
                "quote_id",
                q.quote_id,
            ),
            (["purchase-preflight", q.quote_id, "--max-cost", "9999"], "code", "BUDGET_EXCEEDED"),
        ):
            cli_result = subprocess.run(
                [sys.executable, "-m", "atlas_terminal", *command, "--json"],
                cwd=root,
                env=planning_env,
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            assert not cli_result.stderr and json.loads(cli_result.stdout)[key] == value
        asyncio.run(payment_planning_mcp(root, planning_env, q.quote_id))
        first = client.purchase(q.quote_id)
        assert client.purchase(q.quote_id) == first
        assert client.status(q.quote_id)["state"] == "SETTLED"
        assert client.budget_status().day.spent_atomic == "10000"
        assert client.preflight(q.quote_id).decision == "recover"
        assert first.data.model_dump(mode="json") == expected
        assert first.receipt.simulated and first.receipt.transaction is None
        client.close()
    finally:
        stop(proc)
    proc = start_server()
    try:
        client = HostedClient(
            base,
            directory=root / "client",
            client=httpx.Client(trust_env=False, timeout=10),
            policy_path=policy,
        )
        assert client.quote(QUERY, intent_id="installed-workflow") == q
        assert client.purchase(q.quote_id) == first
        assert digest(canonical(first.data)) == q.result_sha256
        client.close()
    finally:
        stop(proc)
    print(
        json.dumps(
            {
                "wheel_demo": "PASS",
                "wheel_research_cli_mcp_http": "PASS",
                "wheel_macro_fx_cli_sdk_mcp_http": "PASS",
                "wheel_quality_batch_cli_sdk_mcp_http": "PASS",
                "wheel_presets_cli_sdk_mcp_http": "PASS",
                "wheel_openapi_contracts": "PASS",
                "wheel_diagnostics_cli_sdk_mcp_http": "PASS",
                "wheel_source_check_optin_guard": "PASS",
                "wheel_mcp_subprocess": mcp_status,
                "wheel_agent_compact_paging_evidence": "PASS",
                "real_loopback_http_mock_purchase": "PASS",
                "wheel_payment_preflight_budget_intent": "PASS",
                "server_restart_recovery": "PASS",
                "result_sha256": q.result_sha256,
                "external_network": False,
                "real_transfer": False,
            }
        )
    )
