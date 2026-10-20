import json
import os
import subprocess
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas

QUERY = {"identifier": "ATLASDEMO", "metrics": ["revenue", "net_income"], "count": 3}


def cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "atlas_terminal", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_cli_matches_sdk_and_uses_single_json_without_keys():
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(x in k for x in ("API_KEY", "PRIVATE_KEY", "SEC_USER_AGENT"))
    }
    result = cli("demo", "--json", env=env)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == Atlas("fixture").get_financials(QUERY).model_dump(
        mode="json"
    )
    error = cli("financials", "get", "AAPL", "--provider", "sec", "--json", env=env)
    assert error.returncode == 4 and json.loads(error.stdout)["error"]["code"] == "CONFIG_REQUIRED"
    for args in [
        ("financials", "get", "AAPL", "--count", "bad", "--json"),
        ("financials", "get", "AAPL", "--count", "100", "--json"),
    ]:
        error = cli(*args, env=env)
        assert (
            error.returncode == 2 and json.loads(error.stdout)["error"]["code"] == "INVALID_INPUT"
        )


async def test_real_mcp_subprocess_initialize_list_call_and_structured_content(tmp_path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={
            "ATLAS_STATE_DIR": str(tmp_path),
            "ATLAS_TESTNET_PRIVATE_KEY": "SECRET_SENTINEL_NOT_A_KEY",
        },
    )
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        initialized = await session.initialize()
        assert initialized.server_info.name == "Atlas Terminal"
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == {
            "atlas_resolve_company",
            "atlas_get_financials",
            "atlas_list_filings",
            "atlas_quote",
            "atlas_purchase",
            "atlas_purchase_status",
        }
        purchase = next(t for t in tools if t.name == "atlas_purchase")
        assert set(purchase.input_schema["properties"]) == {"quote_id"}
        assert "SPENDS" in purchase.description
        result = await session.call_tool("atlas_get_financials", QUERY)
        assert not result.is_error
        assert result.structured_content == Atlas("fixture").get_financials(QUERY).model_dump(
            mode="json"
        )
        company = await session.call_tool("atlas_resolve_company", {"query": "ATLASDEMO"})
        assert company.structured_content["cik"] == "9999999999"
        invalid = await session.call_tool("atlas_get_financials", QUERY | {"count": 100})
        assert invalid.is_error
        historical = await session.call_tool(
            "atlas_get_financials", QUERY | {"as_of": "2020-01-01"}
        )
        assert historical.is_error
        assert historical.structured_content["error"]["code"] == "UNSUPPORTED_HISTORICAL_QUERY"
        unknown = await session.call_tool(
            "atlas_purchase", {"quote_id": "sample", "policy": "overwrite"}
        )
        assert unknown.is_error and unknown.structured_content["error"]["code"] == "INVALID_INPUT"
        wrong_type = await session.call_tool("atlas_get_financials", QUERY | {"count": True})
        assert wrong_type.is_error
        assert "SECRET_SENTINEL" not in result.model_dump_json()
