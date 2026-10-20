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


async def mcp_smoke(root):
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
        assert len((await session.list_tools()).tools) == 6
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
    mcp_status = asyncio.run(mcp_smoke(root))
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
        client = HostedClient(
            base,
            directory=root / "client",
            client=httpx.Client(trust_env=False, timeout=10),
            policy_path=policy,
        )
        q = client.quote(QUERY)
        first = client.purchase(q.quote_id)
        assert client.purchase(q.quote_id) == first
        assert client.status(q.quote_id)["state"] == "SETTLED"
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
        assert client.purchase(q.quote_id) == first
        assert digest(canonical(first.data)) == q.result_sha256
        client.close()
    finally:
        stop(proc)
    print(
        json.dumps(
            {
                "wheel_demo": "PASS",
                "wheel_mcp_subprocess": mcp_status,
                "real_loopback_http_mock_purchase": "PASS",
                "server_restart_recovery": "PASS",
                "result_sha256": q.result_sha256,
                "external_network": False,
                "real_transfer": False,
            }
        )
    )
