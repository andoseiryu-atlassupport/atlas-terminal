"""Capture inspectable research examples using one official, research-only MCP session."""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from run_research_workflow import RESEARCH_TOOLS, connection, evidence_ids

from atlas_terminal import __version__
from atlas_terminal.research_views import EvidenceResult, ResearchResult

ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def validate_capture(capture, *, for_publication=False):
    if capture.get("schema_version") != "1.0.0" or capture.get("status") != "PASS":
        raise ValueError("Only completed captures can be exported.")
    synthetic = capture.get("is_synthetic")
    if not isinstance(synthetic, bool) or (for_publication and synthetic):
        raise ValueError("Public observation pages require actual public-source results.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", capture.get("id", "")):
        raise ValueError("Invalid case identifier.")
    items = capture.get("items", [])
    if not items or len(items) > 10:
        raise ValueError("Expected one to ten results.")
    required = set()
    for item in items:
        result = ResearchResult.model_validate(item["result"])
        if result.is_synthetic is not synthetic or not result.records:
            raise ValueError("Missing results or mismatched data mode.")
        if result.response is None or result.response.next_cursor:
            raise ValueError("Capture must include the complete selected result.")
        if result.quality is None or not result.quality.meets_requirements:
            raise ValueError("Result does not meet the selected quality requirements.")
        required.update(evidence_ids(item["result"]))
    evidence = capture.get("resolved_evidence", {})
    if not required or required != set(evidence) or len(required) > 128:
        raise ValueError("Every original evidence reference must be resolved.")
    for identifier, value in evidence.items():
        validated = EvidenceResult.model_validate(value)
        if validated.evidence_id != identifier:
            raise ValueError("Evidence identity mismatch.")
        if for_publication and ".invalid" in validated.evidence.raw_source_url:
            raise ValueError("Fictional evidence cannot substantiate public observations.")


async def capture_examples(session, provider, selected=None):
    initialized = await session.initialize()
    if {tool.name for tool in (await session.list_tools()).tools} != RESEARCH_TOOLS:
        raise ValueError("Expected only research tools.")
    recipes = json.loads((ROOT / "examples/citation_cases.json").read_text())["cases"]
    captures = []
    for recipe in recipes:
        if selected and recipe["id"] != selected:
            continue
        query = {"items": deepcopy(recipe["items"])}
        for item in query["items"]:
            item["query"]["response"] = {"view": "compact"}
            item["query"]["requirements"] = {}
            if provider == "fixture" and "identifier" in item["query"]:
                item["query"]["identifier"] = "ATLASDEMO"
        response = await session.call_tool("atlas_get_research_batch", {"query": query})
        batch = response.structured_content
        if response.is_error or not isinstance(batch, dict):
            raise ValueError("Research batch failed.")
        if any(item["status"] != "ok" for item in batch["items"]):
            raise ValueError("A research item failed.")
        if [item["id"] for item in batch["items"]] != [item["id"] for item in query["items"]]:
            raise ValueError("Incomplete batch.")
        evidence = {}
        identifiers = sorted(set(evidence_ids(batch["items"])))
        if len(identifiers) > 128:
            raise ValueError("Capture exceeds evidence bound.")
        for identifier in identifiers:
            result = await session.call_tool("atlas_get_evidence", {"evidence_id": identifier})
            if result.is_error:
                raise ValueError("Evidence resolution failed.")
            evidence[identifier] = result.structured_content
        capture = {
            "schema_version": "1.0.0",
            "status": "PASS",
            "id": recipe["id"],
            "title": recipe["title"],
            "captured_at": datetime.now(UTC).isoformat(),
            "atlas_version": __version__,
            "server_version": initialized.server_info.version,
            "transport": "stdio",
            "is_synthetic": provider == "fixture",
            "atomic_snapshot": False,
            "llm_used": False,
            "query": query,
            "items": batch["items"],
            "resolved_evidence": evidence,
        }
        validate_capture(capture)
        captures.append(capture)
    if not captures:
        raise ValueError("Unknown case.")
    return captures


def save_capture(capture, directory, *, publish=False):
    validate_capture(capture, for_publication=publish)
    payload = encoded(capture)
    digest = hashlib.sha256(payload).hexdigest()
    stem = capture["id"] + "-" + digest
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (stem + ".json")
    # Identical captures can be reused; existing content is never silently replaced.
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ValueError("Snapshot path already contains different bytes.")
    else:
        with destination.open("xb") as stream:
            stream.write(payload)
    if publish:
        lines = [
            "# " + capture["title"],
            "",
            "A completed public-source MCP capture. This is a dated observation, not a live feed.",
            "",
            f"Captured: {capture['captured_at']}. Atlas: {capture['atlas_version']}.",
            f"Transport: {capture['transport']}.",
            "Independent reads; observation periods may differ.",
            "",
            f"[Complete query, results and original evidence]({destination.name})",
            "",
            f"Snapshot SHA-256: `{digest}`.",
            "",
            "## Selected records",
            "",
        ]
        for item in capture["items"]:
            lines.extend(["### " + item["id"], "", "```json"])
            lines.extend(json.dumps(item["result"]["records"], indent=2).splitlines())
            lines.extend(["```", ""])
        lines.extend(["## Original sources", ""])
        urls = sorted(
            {
                EvidenceResult.model_validate(v).evidence.raw_source_url
                for v in capture["resolved_evidence"].values()
            }
        )
        lines.extend("- <" + url + ">" for url in urls)
        lines.extend(
            [
                "",
                "Cite original publishers for numerical facts. Retain the units, actual periods,",
                "source hashes and JSON/XML locators in the complete capture.",
                "",
                "[Reproduce these cases](research-examples.md) · [Citation rules](citations.md)",
                "",
            ]
        )
        page = directory / (stem + ".md")
        body = "\n".join(lines)
        if page.exists() and page.read_text() != body:
            raise ValueError("Published snapshot text already differs.")
        if not page.exists():
            page.write_text(body)
    return {"id": capture["id"], "sha256": digest, "path": str(destination)}


async def run(provider, selected):
    with TemporaryDirectory(prefix="atlas-citation-capture-") as directory:
        async with connection(provider, directory) as (session, _):
            return await capture_examples(session, provider, selected)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--provider", choices=["fixture", "public"], default="fixture")
    cli.add_argument(
        "--case", choices=["inflation-rates-fx", "reported-free-cash-flow", "calendar-alignment"]
    )
    cli.add_argument("--allow-network", action="store_true")
    cli.add_argument(
        "--publish", action="store_true", help="Export real observations as HTML inputs"
    )
    cli.add_argument("--output", type=Path, default=ROOT / "build/research-examples")
    args = cli.parse_args()
    if args.provider == "public" and not args.allow_network:
        cli.error("Public source reads require --allow-network.")
    if args.publish and args.provider != "public":
        cli.error("Publishing observation pages requires the public provider.")
    try:
        captures = asyncio.run(run(args.provider, args.case))
        saved = [save_capture(c, args.output, publish=args.publish) for c in captures]
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "is_synthetic": args.provider == "fixture",
                    "snapshots": saved,
                    "model_citation_measured": False,
                }
            )
        )
        return 0
    except Exception:
        # Do not echo SDK exceptions, source payloads or private operator settings.
        print(json.dumps({"status": "FAIL", "code": "CAPTURE_STOPPED"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
