"""Small, executable walkthroughs using an actual official MCP client session."""

import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal import __version__
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_views import EvidenceResult, ResearchResult
from atlas_terminal.risk_demo import fictional_panel
from atlas_terminal.util import canonical

Scenario = Literal["research", "japan-risk"]
Provider = Literal["fixture", "public"]


def evidence_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        return ({value["evidence_id"]} if "evidence_id" in value else set()).union(
            *(evidence_ids(child) for child in value.values())
        )
    if isinstance(value, list):
        return set().union(*(evidence_ids(child) for child in value))
    return set()


async def execute(scenario: Scenario, provider: Provider, directory: Path) -> dict[str, Any]:
    allowed = {
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
    environment["ATLAS_STATE_DIR"] = str(directory / "state")
    args = ["-m", "atlas_terminal", "mcp", "serve", "--provider", provider, "--research-only"]
    positions: list[dict[str, str]] = []
    risk_query: dict[str, Any] = {}
    if scenario == "japan-risk":
        panel = fictional_panel(country="JP")
        path = directory / "fictional-panel.json"
        path.write_bytes(canonical(panel))
        path.chmod(0o600)
        config = directory / "connections.json"
        config.write_bytes(
            canonical(
                {
                    "schema_version": "0.1.0",
                    "connections": [
                        {
                            "connection_id": "fictional_japan",
                            "kind": "json",
                            "method": "cross_sectional",
                            "path": str(path),
                        }
                    ],
                }
            )
        )
        config.chmod(0o600)
        environment["ATLAS_RISK_CONNECTIONS_FILE"] = str(config)
        args.append("--enable-risk")
        positions = [
            {"asset_id": row["asset_id"], "weight": "0.05"}
            for row in panel["manifest"]["universe"][:20]
        ]
        risk_query = {
            "action": "run",
            "connection_id": "fictional_japan",
            "fit": {"method": "cross_sectional", "as_of": panel["sessions"][64]["end_at"]},
            "positions": positions,
        }
    steps: list[dict[str, Any]] = []
    parameters = StdioServerParameters(command=sys.executable, args=args, env=environment)
    with open(os.devnull, "w") as errors:
        async with (
            stdio_client(parameters, errlog=errors) as streams,
            ClientSession(*streams, read_timeout_seconds=120) as session,
        ):
            initialized = await session.initialize()

            async def call(name: str, query: dict[str, Any]) -> dict[str, Any]:
                started = time.monotonic()
                result = await session.call_tool(name, {"query": query})
                data = result.structured_content
                if result.is_error or not isinstance(data, dict):
                    raise AtlasError(
                        "SHOWCASE_FAILED", "A showcase tool failed; inspect source readiness."
                    )
                steps.append(
                    {"tool": name, "elapsed_ms": round(1000 * (time.monotonic() - started))}
                )
                return data

            if scenario == "research":
                query = {
                    "preset": "fx_context",
                    "base_currency": "USD",
                    "quote_currency": "JPY",
                    "include": ["fx", "us_sofr"],
                    "view": "compact",
                }
                result = await call("atlas_get_research_preset", query)
                if result["batch"]["summary"]["failed"]:
                    raise AtlasError(
                        "SHOWCASE_FAILED", "The selected research sources did not complete."
                    )
                for item in result["batch"]["items"]:
                    parsed = ResearchResult.model_validate(item["result"])
                    if parsed.is_synthetic is not (provider == "fixture") or not parsed.records:
                        raise AtlasError(
                            "SHOWCASE_FAILED", "Missing records or unexpected data mode."
                        )
                    if parsed.response is None or parsed.response.next_cursor:
                        raise AtlasError("SHOWCASE_FAILED", "The selected result is incomplete.")
                    if parsed.records[0].get("value") is None:
                        raise AtlasError("SHOWCASE_FAILED", "The latest selected value is missing.")
                evidence = {}
                for identifier in sorted(evidence_ids(result["batch"])):
                    response = await session.call_tool(
                        "atlas_get_evidence", {"evidence_id": identifier}
                    )
                    if response.is_error:
                        raise AtlasError(
                            "SHOWCASE_FAILED", "Original evidence could not be resolved."
                        )
                    resolved = EvidenceResult.model_validate(response.structured_content)
                    if resolved.evidence_id != identifier:
                        raise AtlasError(
                            "SHOWCASE_FAILED", "Original evidence identity mismatched."
                        )
                    evidence[identifier] = resolved.model_dump(mode="json")
                    steps.append({"tool": "atlas_get_evidence", "evidence_id": identifier})
                if not evidence:
                    raise AtlasError("SHOWCASE_FAILED", "The result has no original evidence.")
                payload = {"query": query, "preset": result, "evidence": evidence}
            else:
                result = await call("atlas_risk", risk_query)
                model = result["data"]["model"]
                validation = await call(
                    "atlas_risk",
                    {
                        "action": "validate",
                        "model_id": model["model_id"],
                        "head_id": model["input_head_id"],
                        "positions": positions,
                    },
                )
                payload = {"query": risk_query, "run": result, "holdout": validation}
    return {
        "schema_version": "1.0.0",
        "atlas_version": __version__,
        "status": "PASS",
        "scope": "executed_workflow_not_source_or_model_accuracy_benchmark",
        "scenario": scenario,
        "provider": provider,
        "is_synthetic": provider == "fixture",
        "recorded_at": datetime.now(UTC).isoformat(),
        "transport": "stdio",
        "mcp_sessions": 1,
        "server": initialized.server_info.model_dump(mode="json"),
        "state": "isolated_temporary_removed_on_exit",
        "llm_used": False,
        "transfers": False,
        "steps": steps,
        "result": payload,
    }


def run_showcase(
    scenario: Scenario, provider: Provider = "fixture", *, allow_network: bool = False
) -> dict[str, Any]:
    """Run from an installed wheel; public research requires explicit source-request opt-in."""
    if scenario not in {"research", "japan-risk"} or provider not in {"fixture", "public"}:
        raise AtlasError("INVALID_INPUT", "Select research or japan-risk and fixture or public.")
    if provider == "public" and (not allow_network or scenario != "research"):
        raise AtlasError(
            "INVALID_INPUT", "Only research supports public mode; add --allow-network to opt in."
        )
    try:
        with TemporaryDirectory(prefix="atlas-showcase-") as temporary:
            return asyncio.run(execute(scenario, provider, Path(temporary)))
    except AtlasError:
        raise
    except Exception:
        # SDK/transport exceptions may contain process settings; never echo them.
        raise AtlasError(
            "SHOWCASE_FAILED", "The MCP walkthrough stopped before completion."
        ) from None


def transcript(report: dict[str, Any]) -> str:
    """Present executed observations without generated research claims or market advice."""
    lines = [
        f"Atlas Terminal {report['atlas_version']} | {report['scenario']}",
        "FICTIONAL DATA - not market observations"
        if report["is_synthetic"]
        else "PUBLIC SOURCE DATA",
        "Official MCP client | one stdio session | no LLM | no payment",
        "",
    ]
    if report["scenario"] == "research":
        for item in report["result"]["preset"]["batch"]["items"]:
            row = item["result"]["records"][0]
            lines.append(f"{item['id']}: {row['value']} {row['unit']} | observation {row['date']}")
        lines += [
            f"Resolved original evidence: {len(report['result']['evidence'])} references",
            "USD/JPY = JPY per EUR / USD per EUR, using the same observation date.",
            "SOFR and FX retain their own observation dates; they are not an atomic snapshot.",
        ]
    else:
        data = report["result"]["run"]["data"]
        model, portfolio = data["model"], data["analysis"]["portfolio"]
        lines += [
            f"Market: {model['country']} / {model['currency']} / "
            f"{model['industry_classification']}",
            f"Estimation universe: {model['assets']} fictional stocks; holdings: 20",
            f"Estimation sessions: {model['estimation_sessions']}; "
            f"factors: {len(model['factor_order'])}",
            f"Annualized volatility (decimal return): {portfolio['annualized_volatility']}",
            "Style exposures (standardized model units):",
            *[
                f"  {key}: {Decimal(value):.6f}"
                for key, value in portfolio["factor_exposures"].items()
                if key.startswith("style:")
            ],
            "A subsequent holdout was evaluated; synthetic results do not measure market accuracy.",
            "Bring a broad estimation universe and rights-cleared history for your own portfolio.",
        ]
    return "\n".join(
        [*lines, "", "PASS | Re-run with --json for complete queries, results and evidence."]
    )
