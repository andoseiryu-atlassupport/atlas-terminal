"""Explicit, private international Yahoo capture through the shared Atlas service."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import TOKYO, require_yahoo_enabled, yahoo_url
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
    assert sha256(body).hexdigest() == result["sources"][0]["raw_sha256"]
    assert len(result["records"]) == result["coverage"]["returned_records"]
    chart = original["chart"]["result"][0]
    retrieved = datetime.fromisoformat(result["sources"][0]["retrieved_at"])
    today = retrieved.astimezone(TOKYO).date()
    regular_end = chart["meta"].get("currentTradingPeriod", {}).get("regular", {}).get("end")
    end = datetime.fromtimestamp(regular_end, TOKYO) if regular_end is not None else None
    coverage = result["coverage"]
    expected_dates = set()
    excluded = 0
    for timestamp in chart.get("timestamp", []):
        day = datetime.fromtimestamp(timestamp, TOKYO).date()
        date = day.isoformat()
        if not (
            (coverage["requested_start_date"] or date)
            <= date
            <= (coverage["requested_end_date"] or date)
        ):
            continue
        if day >= today and not (
            day == today and end is not None and end.date() == day and retrieved >= end
        ):
            excluded += 1
            continue
        expected_dates.add(date)
    assert not coverage["truncated"]
    assert {row["date"] for row in result["records"]} == expected_dates
    assert coverage["excluded_unconfirmed_session_rows"] == excluded
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
                assert Decimal(row[field]) == cell
        timestamp = locate(original, row["field_sources"]["date"]["json_pointer"])
        assert datetime.fromtimestamp(timestamp, TOKYO).date().isoformat() == row["date"]


def verify_actions(result, body, bounds):
    original = decode_json(body)
    expected = set()
    today = (
        datetime.fromisoformat(result["sources"][0]["retrieved_at"])
        .astimezone(TOKYO)
        .date()
        .isoformat()
    )
    for kind, cells in original["chart"]["result"][0].get("events", {}).items():
        if kind not in {"dividends", "splits"}:
            continue
        for key, cell in cells.items():
            day = datetime.fromtimestamp(cell["date"], TOKYO).date().isoformat()
            if day <= today and bounds.get("start_date", day) <= day <= bounds.get("end_date", day):
                expected.add(f"/chart/result/0/events/{kind}/{key}")
    actual = set()
    for row in result["records"]:
        pointer = row["source"]["json_pointer"]
        actual.add(pointer)
        cell = locate(original, pointer)
        assert row["date"] == datetime.fromtimestamp(cell["date"], TOKYO).date().isoformat()
        assert row["published_at"] is None
        if row["action_type"] == "dividend":
            assert Decimal(row["value"]) == cell["amount"]
        else:
            assert Decimal(row["numerator"]) == cell["numerator"]
            assert Decimal(row["denominator"]) == cell["denominator"]
            assert row["split_ratio"] == cell.get("splitRatio")
    assert actual == expected


async def capture(output):
    require_yahoo_enabled()
    output.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TOKYO).date()
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
                verify_actions(actions, snapshot.body, bounds)
                assert all(
                    bounds.get("start_date", row["date"])
                    <= row["date"]
                    <= bounds.get("end_date", row["date"])
                    for row in result["records"]
                )
                assert actions["sources"] == result["sources"]
                entry.update(
                    status="ok",
                    records=len(result["records"]),
                    actions=len(actions["records"]),
                    coverage=result["coverage"],
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
        for item in preset["batch"]["items"]:
            for row in item["result"]["records"]:
                eid = row["source"]["evidence_id"]
                evidence[eid] = atlas.get_evidence(eid)["evidence"]
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
                resolved = source.structured_content["evidence"]
                assert resolved == evidence[eid]
                evidence[eid] = resolved
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
