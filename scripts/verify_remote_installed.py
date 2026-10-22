"""Exercise an installed wheel over actual loopback Streamable HTTP; fixtures only."""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from atlas_terminal import __version__


@contextmanager
def running(directory, token, port):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "mcp",
            "http",
            "--provider",
            "fixture",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=os.environ
        | {
            "ATLAS_STATE_DIR": str(directory),
            "ATLAS_MCP_AUTH_TOKEN": token,
            "ATLAS_MCP_PUBLIC_ORIGIN": f"http://127.0.0.1:{port}",
        },
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("Remote MCP process exited during startup.")
            try:
                result = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=0.2)
                if result.status_code == 200:
                    assert result.json()["is_synthetic"]
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("Remote MCP did not become ready.")
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def exercise(origin, token, evidence_id=None):
    async with (
        httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as client,
        streamable_http_client(origin + "/mcp", http_client=client) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        initialized = await session.initialize()
        assert initialized.server_info.version == __version__
        tools = (await session.list_tools()).tools
        assert len(tools) == 6 and not any("purchase" in tool.name for tool in tools)
        if evidence_id is None:
            response = await session.call_tool(
                "atlas_get_research_preset",
                {
                    "query": {
                        "preset": "fx_context",
                        "base_currency": "USD",
                        "quote_currency": "JPY",
                        "include": ["fx", "us_sofr"],
                    }
                },
            )
            assert not response.is_error and response.structured_content["is_synthetic"]
            assert response.structured_content["batch"]["summary"]["successful"] == 2
            result = response.structured_content["batch"]["items"][0]["result"]
            assert result["records"][0]["value"] == "120"
            assert result["documentation"]["citation_guidance"].endswith("/docs/citations.md")
            evidence_id = result["sources"][0]["evidence_id"]
        evidence = await session.call_tool("atlas_get_evidence", {"evidence_id": evidence_id})
        assert not evidence.is_error
        assert evidence.structured_content["evidence"]["raw_sha256"]
        assert token not in evidence.model_dump_json()
        return evidence_id


def main():
    with TemporaryDirectory(prefix="atlas-remote-wheel-") as temporary:
        directory = Path(temporary)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_urlsafe(32)
        with running(directory, token, port) as service:
            assert httpx.post(service + "/mcp", json={}).status_code == 401
            headers = {"Authorization": "Bearer " + token}
            assert (
                httpx.post(
                    service + "/mcp", headers=headers | {"Host": "evil.invalid"}, json={}
                ).status_code
                == 421
            )
            assert (
                httpx.post(
                    service + "/mcp", headers=headers | {"Origin": "https://evil.invalid"}, json={}
                ).status_code
                == 403
            )
            evidence_id = asyncio.run(exercise(service, token))
        with running(directory, token, port) as service:
            assert asyncio.run(exercise(service, token, evidence_id)) == evidence_id
        assert not list(directory.glob("*orders*"))
    print(
        json.dumps(
            {
                "status": "PASS",
                "installed_wheel_remote_mcp": True,
                "official_client": True,
                "restart_evidence": True,
                "external_source_requests": False,
                "transfers": False,
            }
        )
    )


if __name__ == "__main__":
    main()
