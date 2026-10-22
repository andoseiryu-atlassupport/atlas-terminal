"""Run a reproducible research contract evaluation through one official MCP connection."""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx2
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from atlas_terminal.remote_mcp import validated_origin
from atlas_terminal.research_views import EvidenceResult, ResearchResult

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_TOOLS = {
    "atlas_research_catalog",
    "atlas_get_research",
    "atlas_get_research_batch",
    "atlas_get_research_preset",
    "atlas_get_evidence",
    "atlas_research_diagnostics",
}


def evidence_ids(value):
    if isinstance(value, dict):
        if "evidence_id" in value:
            identifier = value["evidence_id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
                raise ValueError("Invalid evidence identifier.")
            yield identifier
        for child in value.values():
            yield from evidence_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_ids(child)


async def evaluate(session, *, synthetic, identifier=None, min_fx_date=None):
    cases, calls = [], []
    initialized = await session.initialize()
    tools = (await session.list_tools()).tools

    def record(name, passed):
        cases.append({"id": name, "status": "PASS" if passed else "FAIL"})

    async def call(name, arguments):
        start = time.monotonic()
        response = await session.call_tool(name, arguments)
        calls.append(
            {
                "tool": name,
                "elapsed_ms": round((time.monotonic() - start) * 1000),
                "response_bytes": len(response.model_dump_json().encode()),
                "is_error": bool(response.is_error),
            }
        )
        if not isinstance(response.structured_content, dict):
            raise ValueError("Structured tool output required.")
        return response.structured_content, bool(response.is_error)

    record("research_only_tools", {tool.name for tool in tools} == RESEARCH_TOOLS)
    if cases[-1]["status"] == "FAIL":
        # Do not proceed with research against a server with an unexpected tool surface.
        return {"status": "FAIL", "cases": cases, "calls": calls}
    catalog, error = await call("atlas_research_catalog", {"view": "compact"})
    record(
        "dataset_discovery",
        not error
        and {"statements", "financial_analytics", "economics", "fx_rates"}
        <= {item["id"] for item in catalog["datasets"]},
    )
    diagnostics, error = await call("atlas_research_diagnostics", {"query": {}})
    record(
        "configuration_without_probes",
        not error
        and diagnostics["is_synthetic"] is synthetic
        and diagnostics["network_checks_executed"] is False,
    )
    recipe = json.loads((ROOT / "examples/company_macro_fx.json").read_text())
    recipe = deepcopy(recipe)
    for item in recipe["items"]:
        if "identifier" in item["query"]:
            item["query"]["identifier"] = identifier or ("ATLASDEMO" if synthetic else "AAPL")
        if item["id"] == "usd_jpy" and min_fx_date:
            item["query"]["requirements"] = {
                "min_observation_date": min_fx_date,
                "allow_missing": False,
            }
    batch, error = await call("atlas_get_research_batch", {"query": recipe})
    successful = []
    if not error:
        for item in batch["items"]:
            if item["status"] == "ok":
                result = ResearchResult.model_validate(item["result"])
                if result.is_synthetic is not synthetic:
                    raise ValueError(
                        "Server data mode does not match the selected evaluation mode."
                    )
                successful.append(item)
    record(
        "company_macro_fx_batch",
        not error
        and len(successful) == 4
        and [item["id"] for item in successful] == [item["id"] for item in recipe["items"]]
        and all(
            item["result"]["records"]
            and item["result"].get("quality", {}).get("meets_requirements") is True
            for item in successful
        ),
    )
    ids = sorted(set(evidence_ids(successful)))
    if len(ids) > 64:
        raise ValueError("Evidence resolution exceeds this workflow's bound.")
    evidence = {}
    for evidence_id in ids:
        data, failed = await call("atlas_get_evidence", {"evidence_id": evidence_id})
        if not failed:
            resolved = EvidenceResult.model_validate(data)
            if resolved.evidence_id != evidence_id:
                raise ValueError("Evidence response does not match its request.")
            evidence[evidence_id] = resolved.evidence.model_dump(mode="json", exclude_none=True)
    record("resolve_original_evidence", bool(ids) and len(evidence) == len(ids))
    quality, failed = await call(
        "atlas_get_research",
        {
            "query": {
                "dataset": "fx_rates",
                "base_currency": "USD",
                "quote_currency": "JPY",
                "limit": 1,
                "requirements": {"allow_derived": False},
            }
        },
    )
    record(
        "explicit_quality_rejection",
        failed and quality.get("error", {}).get("code") == "QUALITY_NOT_MET",
    )
    invalid, failed = await call(
        "atlas_get_research",
        {"query": {"dataset": "real_time_equity_prices", "identifier": "AAPL"}},
    )
    record(
        "unsupported_dataset_rejection",
        failed and invalid.get("error", {}).get("code") == "INVALID_INPUT",
    )
    return {
        "status": "PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL",
        "server_version": initialized.server_info.version,
        "cases": cases,
        "tool_call_count": len(calls),
        "calls": calls,
        "workflow": {
            "atomic_snapshot": False,
            "query": recipe,
            "batch": batch,
            "resolved_evidence": evidence,
        },
    }


def remote_url(value):
    parts = urlsplit(value)
    if parts.path != "/mcp" or parts.query or parts.fragment:
        raise ValueError("Use the exact HTTPS /mcp endpoint without query parameters.")
    origin = validated_origin(f"{parts.scheme}://{parts.netloc}")
    if not origin.startswith("https://"):
        raise ValueError("Remote verification requires HTTPS.")
    return origin + "/mcp"


@asynccontextmanager
async def connection(provider, directory, url=None):
    if url:
        token = os.environ.get("ATLAS_MCP_AUTH_TOKEN")
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with (
            httpx2.AsyncClient(headers=headers, follow_redirects=False, timeout=120) as client,
        ):
            endpoint = remote_url(url)
            health = await client.get(endpoint.removesuffix("/mcp") + "/healthz")
            health.raise_for_status()
            health_data = health.json()
            if health_data.get("is_synthetic") is not False:
                raise ValueError(
                    "A public remote evaluation requires non-synthetic health metadata."
                )
            async with (
                streamable_http_client(endpoint, http_client=client) as streams,
                ClientSession(*streams, read_timeout_seconds=120) as session,
            ):
                yield (
                    session,
                    {
                        "status": "PASS",
                        "is_synthetic": False,
                        "source_readiness_implied": False,
                    },
                )
    else:
        allowed = {
            "SEC_USER_AGENT",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment["ATLAS_STATE_DIR"] = str(directory)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "atlas_terminal",
                "mcp",
                "serve",
                "--provider",
                provider,
                "--research-only",
            ],
            env=environment,
        )
        with open(os.devnull, "w") as errors:
            async with (
                stdio_client(parameters, errlog=errors) as streams,
                ClientSession(*streams, read_timeout_seconds=120) as session,
            ):
                yield session, None


async def run(provider="fixture", url=None, identifier=None, min_fx_date=None):
    synthetic = provider == "fixture" and not url
    with TemporaryDirectory(prefix="atlas-research-workflow-") as directory:
        async with connection(provider, directory, url) as (session, health):
            result = await evaluate(
                session, synthetic=synthetic, identifier=identifier, min_fx_date=min_fx_date
            )
    return {
        "schema_version": "1.0.0",
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "installed_mcp_contract_evaluation",
        "transport": "streamable_http" if url else "stdio",
        "is_synthetic": synthetic,
        "external_source_requests_allowed": not synthetic,
        "llm_used": False,
        "model_task_success_measured": False,
        "search_discovery_measured": False,
        "transfers": False,
        "remote_health": health,
        **result,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--provider", choices=["fixture", "public"], default="fixture")
    cli.add_argument(
        "--url",
        help="Actual HTTPS /mcp endpoint; bearer token is read only from ATLAS_MCP_AUTH_TOKEN",
    )
    cli.add_argument("--identifier", help="SEC issuer identifier; public data defaults to AAPL")
    freshness = cli.add_mutually_exclusive_group()
    freshness.add_argument(
        "--min-fx-date", help="Optional explicit minimum usable FX observation date, YYYY-MM-DD"
    )
    freshness.add_argument(
        "--max-fx-age-days", type=int, help="Maximum calendar age of usable FX observations, 0-365"
    )
    cli.add_argument("--allow-network", action="store_true")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    if (args.provider == "public" or args.url) and not args.allow_network:
        cli.error("Public sources or remote endpoints require --allow-network.")
    if args.max_fx_age_days is not None and not 0 <= args.max_fx_age_days <= 365:
        cli.error("FX observation age must be between 0 and 365 calendar days.")
    try:
        if args.url:
            remote_url(args.url)
        if args.min_fx_date:
            datetime.strptime(args.min_fx_date, "%Y-%m-%d")
        if args.max_fx_age_days is not None:
            args.min_fx_date = (
                datetime.now(UTC).date() - timedelta(days=args.max_fx_age_days)
            ).isoformat()
        result = asyncio.run(run(args.provider, args.url, args.identifier, args.min_fx_date))
    except Exception:
        # SDK/network exceptions may contain authentication headers or operator settings.
        result = {
            "status": "FAIL",
            "code": "WORKFLOW_STOPPED",
            "llm_used": False,
            "transfers": False,
        }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"workflow", "calls"}}
        )
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
