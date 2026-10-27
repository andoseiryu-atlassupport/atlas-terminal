"""Explicit, private international Yahoo capture through the shared Atlas service."""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import require_yahoo_enabled, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import decode_json
from atlas_terminal.research_equities import PRICE_FIELDS
from atlas_terminal.util import stamp, state_dir


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def locate(document, pointer):
    for part in pointer.split("/")[1:]:
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def verify_prices(result, body):
    original = decode_json(body)
    assert not result["is_synthetic"] and result["instrument"]["currency"] == "JPY"
    for row in result["records"]:
        assert row["price_basis"] == "yahoo_split_adjusted"
        for field in PRICE_FIELDS:
            if field not in row["field_sources"]:
                assert field == "adjusted_close" and row[field] is None
                continue
            cell = locate(original, row["field_sources"][field]["json_pointer"])
            if cell is None:
                assert row[field] is None
            else:
                from decimal import Decimal

                assert Decimal(row[field]) == cell
        timestamp = locate(original, row["field_sources"]["date"]["json_pointer"])
        from atlas_terminal.equity_sources import TOKYO

        assert datetime.fromtimestamp(timestamp, TOKYO).date().isoformat() == row["date"]


async def capture(output):
    require_yahoo_enabled()
    output.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).date()
    dates = {"start_date": f"{today.year - 1}-01-01", "end_date": today.isoformat()}
    report = {"started_at": stamp(), "status": "in_progress", "reads": []}
    evidence = {}
    with Atlas("public") as atlas:
        for symbol in ("7203.T", "6758.T"):
            for mode, bounds in (("normal", {}), ("dated", dates)):
                query = {"dataset": "equity_prices", "identifier": symbol, "limit": 500, **bounds}
                entry = {"query": query, "status": "error"}
                try:
                    result = atlas.get_research(query)
                except AtlasError as error:
                    entry["error"] = error.envelope()["error"]
                    report["reads"].append(entry)
                    report["status"] = "error"
                    save(output / "report.json", report)
                    print(json.dumps(report, indent=2))
                    # Stop after the first failure; never rotate hosts or reset state.
                    return 1
                source = result["sources"][0]
                url = yahoo_url(symbol, bounds.get("start_date"), bounds.get("end_date"))
                snapshot = atlas._research.public.get(url, 900)
                assert snapshot.sha256 == source["raw_sha256"]
                (output / (snapshot.sha256 + ".json")).write_bytes(snapshot.body)
                verify_prices(result, snapshot.body)
                compact = atlas.get_research(query | {"response": {"view": "compact"}})
                for row, full in zip(compact["records"], result["records"], strict=True):
                    for reference in [row["source"], *row["field_sources"].values()]:
                        eid = reference["evidence_id"]
                        evidence[eid] = atlas.get_evidence(eid)["evidence"]
                    assert evidence[row["source"]["evidence_id"]] == full["source"]
                actions = atlas.get_research(query | {"dataset": "equity_actions"})
                for row in actions["records"]:
                    assert locate(decode_json(snapshot.body), row["source"]["json_pointer"])
                assert actions["sources"] == result["sources"]
                entry.update(
                    status="ok",
                    records=len(result["records"]),
                    latest_date=result["records"][0]["date"],
                    source=source,
                )
                save(
                    output / f"{symbol}-{mode}.json",
                    {"query": query, "result": result, "actions": actions},
                )
                report["reads"].append(entry)
        preset_query = {"preset": "japan_equity_history", "identifier": "7203.T", **dates}
        preset = atlas.get_research_preset(preset_query)
        assert preset["status"] == "ok" and not preset["is_synthetic"]
        save(output / "sdk-preset.json", preset)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "public", "--research-only"],
        env={**os.environ, "ATLAS_STATE_DIR": str(state_dir())},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        response = await session.call_tool("atlas_get_research_preset", {"query": preset_query})
        assert not response.is_error and response.structured_content["status"] == "ok"
        actual = response.structured_content
        for item, expected in zip(actual["batch"]["items"], preset["batch"]["items"], strict=True):
            assert item["result"]["records"] == expected["result"]["records"]
            for row in item["result"]["records"]:
                eid = row["source"]["evidence_id"]
                source = await session.call_tool("atlas_get_evidence", {"evidence_id": eid})
                assert not source.is_error
                evidence[eid] = source.structured_content["evidence"]
        save(output / "mcp-preset.json", actual)
    report.update(
        status="pass", completed_at=stamp(), mcp="pass", sdk="pass", resolved_evidence=evidence
    )
    save(output / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "resolved_evidence"}, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/yahoo-live"))
    arguments = parser.parse_args()
    if not arguments.allow_network:
        parser.error("--allow-network is required; this script never runs in default tests")
    raise SystemExit(asyncio.run(capture(arguments.output)))


if __name__ == "__main__":
    main()
