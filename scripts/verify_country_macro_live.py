"""Opt-in seven-country macro/bond reads with private exact-cell verification."""

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from verify_g20_live import CaptureTransport, private_write, verify

from atlas_terminal.client import Atlas
from atlas_terminal.country_macro_catalog import COUNTRIES, country_series_catalog
from atlas_terminal.global_macro_sources import GLOBAL_INDICATORS
from atlas_terminal.research_macro import numeric
from atlas_terminal.util import canonical, stamp, state_dir


def verify_xml(result, transport, raw_directory):
    assert not result["is_synthetic"] and not result["coverage"]["truncated"]
    for ref in result["sources"]:
        snap = transport.snapshots[ref["raw_source_url"]]
        assert snap.sha256 == ref["raw_sha256"]
        private_write(raw_directory / (snap.sha256 + ".xml"), snap.body)
    for row in result["records"]:
        snap = transport.snapshots[row["source"]["raw_source_url"]]
        assert snap.sha256 == row["source"]["raw_sha256"]
        root = ET.fromstring(snap.body)
        for node in root.iter():
            node.tag = node.tag.rsplit("}", 1)[-1]
        path = row["source"]["xml_local_path"].removeprefix("/" + root.tag + "/")
        obs = root.find(path)
        assert obs is not None
        child = obs.find("ObsValue")
        raw = child.get("value") if child is not None else obs.get("OBS_VALUE")
        assert numeric(raw) == row.get("source_value", row["value"])
        period_node = obs.find("ObsDimension")
        period = period_node.get("value") if period_node is not None else obs.get("TIME_PERIOD")
        assert period == row["period"]
        assert row["unit_multiplier"] == 0 and row["published_at"] is None
    return len(result["records"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/country-macro-live"))
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(Path(__file__).resolve().parents[1] / "build"):
        parser.error("Actual observations must stay under the ignored build directory.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    raw = out / "raw"
    raw.mkdir(exist_ok=True, mode=0o700)
    checks = []
    report = {"started_at": stamp(), "checks": checks}
    queries = [
        {
            "dataset": "global_economics",
            "country": country,
            "series": series,
            "start_date": "2020-01-01",
            "end_date": "2025-12-31",
            "limit": 500,
        }
        for country in COUNTRIES
        for series in GLOBAL_INDICATORS
    ]
    queries += [
        {
            "dataset": spec["dataset"],
            "country": spec["country"],
            "series": spec["series"],
            "start_date": "2025-01-01",
            "end_date": "2026-09-30",
            "limit": 500,
        }
        for spec in country_series_catalog()
    ]
    with Atlas("public") as atlas:
        capture = CaptureTransport(state_dir() / "public-cache.sqlite3")
        atlas._research.public = capture
        for query in queries:
            name = query["country"] + "-" + query["series"]
            entry = {"country": query["country"], "series": query["series"], "status": "failed"}
            try:
                result = atlas.get_research(query)
                checked = (verify if query["dataset"] == "global_economics" else verify_xml)(
                    result, capture, raw
                )
                private_write(out / (name + ".json"), canonical(result))
                entry.update(
                    status="verified",
                    source_id=result["source_id"],
                    rows=len(result["records"]),
                    original_cells_checked=checked,
                    numeric=sum(r["value"] is not None for r in result["records"]),
                    unavailable=sum(r["value"] is None for r in result["records"]),
                    latest_date=result["records"][0]["date"],
                    frequency=result["records"][0]["frequency"],
                )
            except Exception as error:
                entry.update(error_type=type(error).__name__, error=str(error))
            checks.append(entry)
            print(json.dumps(entry), flush=True)
            private_write(out / "report.json", canonical(report))
    report.update(
        completed_at=stamp(),
        status="PASS" if all(c["status"] == "verified" for c in checks) else "INCOMPLETE",
    )
    private_write(out / "report.json", canonical(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
