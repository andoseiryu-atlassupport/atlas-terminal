"""Explicit private JPX cell audit and local history replay; no EDINET key required."""

import argparse
import io
import json
import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from verify_data_workflows import CaptureTransport

from atlas_terminal.client import Atlas
from atlas_terminal.japan_company_sources import JPX_LIST
from atlas_terminal.research_japan_companies import jpx_rows
from atlas_terminal.util import canonical, stamp


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    transport = CaptureTransport(output)
    with Atlas("public") as atlas:
        atlas._research.public = transport
        sample = atlas.get_research({"dataset": "japan_securities", "identifier": "7203.T"})
        assert len(sample["records"]) == 1 and not sample["is_synthetic"]
        snapshot = transport.snapshots[JPX_LIST]
        complete = jpx_rows(snapshot)
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with zipfile.ZipFile(io.BytesIO(snapshot.body)) as archive:
            strings = [
                "".join(n.itertext()) for n in ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
            tree = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            cells = {c.get("r"): c for c in tree.iter(ns + "c")}
            matches = 0
            for row in complete:
                for field, source in row["field_sources"].items():
                    coordinate = chr(64 + source["cell_column"]) + str(source["cell_row"])
                    cell = cells[coordinate]
                    raw = cell.findtext(ns + "v")
                    value = strings[int(raw)] if cell.get("t") == "s" else raw
                    normalized = {
                        "日付": row["reference_date"].replace("-", ""),
                        "コード": row["security_code"],
                        "銘柄名": row["name"],
                        "市場・商品区分": row["market_product"],
                        "33業種コード": row["industry_code"],
                        "33業種区分": row["industry_name"],
                    }[field]
                    assert value == normalized
                    matches += 1
        letters = [row for row in complete if not row["security_code"].isdigit()]
        classes = [row for row in complete if len(row["security_code"]) == 5]
        for row in [letters[0], classes[0]]:
            selected = atlas.get_research(
                {"dataset": "japan_securities", "identifier": row["identifier"]}
            )
            assert selected["records"] == [row]
        query = {"dataset": "source_history", "source_url": JPX_LIST}
        history = atlas.get_research(query)
        assert any(v["sha256"] == snapshot.sha256 for v in history["records"])
        captured = stamp()
        replay = atlas.get_research(query | {"history_action": "as_of", "as_of": captured})
        assert replay["history"]["version"]["sha256"] == snapshot.sha256
        by_hash = atlas.get_research(query | {"history_action": "get", "sha256": snapshot.sha256})
        assert by_hash["history"]["body_encoding"] == "binary_not_rendered"
        diff = atlas.get_research(
            query
            | {
                "history_action": "diff",
                "sha256": snapshot.sha256,
                "before_sha256": snapshot.sha256,
            }
        )
        assert not diff["history"]["bytes_changed"]
        report = {
            "verified_at": captured,
            "is_synthetic": False,
            "jpx": {
                "status": "PASS",
                "rows": len(complete),
                "raw_sha256": snapshot.sha256,
                "reference_dates": sorted({r["reference_date"] for r in complete}),
                "alphanumeric_codes": len(letters),
                "five_character_codes": len(classes),
                "exact_source_cell_matches": matches,
            },
            "source_history": {
                "status": "PASS",
                "actions": ["list", "get", "as_of", "diff"],
                "versions": len(history["records"]),
                "observed_revision": len(history["records"]) > 1,
                "point_in_time": False,
            },
            "edinet": {
                "status": "NOT_LIVE_VERIFIED",
                "key_configured": bool(os.environ.get("EDINET_API_KEY")),
            },
        }
        (output / "report.json").write_bytes(canonical(report))
        (output / "jpx-sample.json").write_bytes(canonical(sample))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/japan-company-live"))
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("--allow-network is required")
    run(args.output)
