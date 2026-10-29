"""New research families keep one contract across actual shared interfaces."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_models import parse_research
from atlas_terminal.server import create_app

QUERIES = [
    {"dataset": "price_analytics", "identifier": "AAPL", "metrics": ["rsi_14"], "limit": 3},
    {
        "dataset": "price_comparison",
        "identifiers": ["AAPL", "MSFT"],
        "start_date": "2026-04-01",
        "end_date": "2026-10-26",
    },
    {
        "dataset": "company_screen",
        "identifiers": ["ATLASDEMO"],
        "filters": [{"metric": "revenue", "operator": "gt", "value": "0", "unit": "USD"}],
    },
    {"dataset": "filing_events", "identifier": "ATLASDEMO", "series": "earnings_results"},
    {
        "dataset": "fund_holdings",
        "identifier": "ATLASDEMO",
        "accession": "9999999999-26-000008",
    },
    {
        "dataset": "fred_series",
        "series": "DEMO_INDEX",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    },
]


@pytest.mark.parametrize("symbol", ["^GSPC", "EURUSD=X", "BTC-USD", "CL=F"])
def test_security_only_queries_match_advertised_schema(symbol):
    schema = Draft202012Validator(Atlas.research_catalog()["query_schema"])
    for query in [
        {"dataset": "equity_actions", "identifier": symbol},
        {
            "dataset": "price_comparison",
            "identifiers": ["AAPL", symbol],
            "start_date": "2026-04-01",
            "end_date": "2026-10-26",
        },
    ]:
        with pytest.raises(ValidationError):
            schema.validate(query)
        with pytest.raises(AtlasError, match="stock|security"):
            parse_research(query)


@pytest.mark.parametrize("query", QUERIES, ids=lambda query: query["dataset"])
def test_new_market_queries_cli_http_match_sdk(tmp_path, query):
    with Atlas("fixture", directory=tmp_path / "sdk") as atlas:
        expected = atlas.get_research(query)
    request = tmp_path / "query.json"
    request.write_text(json.dumps(query))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "run",
            "--input",
            str(request),
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout
    assert json.loads(proc.stdout) == expected
    with TestClient(
        create_app(provider="fixture", payments="off", directory=tmp_path / "http")
    ) as client:
        response = client.post("/v1/research", json=query)
    assert response.status_code == 200
    assert response.json() == expected
    assert "PAYMENT-REQUIRED" not in response.headers


async def test_new_market_queries_actual_official_stdio_mcp(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=30) as session,
    ):
        await session.initialize()
        with Atlas("fixture", directory=tmp_path / "sdk") as atlas:
            for query in QUERIES:
                expected = atlas.get_research(query)
                result = await session.call_tool("atlas_get_research", {"query": query})
                assert not result.is_error
                assert result.structured_content == expected
