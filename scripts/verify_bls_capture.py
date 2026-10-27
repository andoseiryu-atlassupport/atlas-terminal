"""Audit saved BLS bytes and replay the shared service offline; never fetch a URL."""

import argparse
import calendar
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from atlas_terminal.client import Atlas
from atlas_terminal.providers import Snapshot
from atlas_terminal.util import digest

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "docs/evidence/bls-2026-10-27"
SERIES = {
    "employment_cost_compensation": "CIS1010000000000I",
    "employment_cost_wages": "CIS1020000000000I",
    "nonfarm_productivity_growth": "PRS85006092",
    "unit_labor_cost_growth": "PRS85006112",
    "import_prices": "EIUIR",
    "export_prices": "EIUIQ",
}


def read(path):
    return json.loads(path.read_bytes())


def pointer(document, locator):
    for part in locator.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


class SavedBLS:
    """Non-synthetic historical bytes, isolated from the operator's live cache/quota."""

    synthetic = False

    def __init__(self, directory, sources):
        self.snapshots = {
            s["raw_source_url"]: Snapshot(
                (directory / "raw" / (s["raw_sha256"] + ".json")).read_bytes(),
                s["raw_source_url"],
                s["retrieved_at"],
            )
            for s in sources
        }

    def get(self, url, ttl):
        # A missing archive entry raises; there is no network fallback.
        return self.snapshots[url]

    def close(self):
        pass


def verify_capture(directory=CAPTURE):
    manifest = read(directory / "manifest.json")
    expected_names = {f"results/{name}-{mode}.json" for name in SERIES for mode in ("get", "post")}
    assert set(manifest["captures"]) == expected_names and len(manifest["captures"]) == 12
    requests = manifest["requests"]
    assert len(requests) == 14 and Counter(r["method"] for r in requests) == {"GET": 6, "POST": 8}
    raw = {}
    for request in requests:
        body = (directory / request["raw_file"]).read_bytes()
        assert digest(body) == request["raw_sha256"], "Original response hash mismatch"
        assert request["http_status"] == 200
        value = json.loads(body)
        assert value["status"] == "REQUEST_SUCCEEDED" and value["message"] == []
        raw[request["raw_sha256"]] = value
    captures = [read(directory / name) for name in manifest["captures"]]
    interfaces = read(directory / manifest["interfaces"])
    evidence = interfaces["resolved_evidence"]
    counts = Counter()
    for capture in captures:
        assert capture["status"] == "ok" and len(capture["attempts"]) == 1
        query, result = capture["query"], capture["result"]
        name = query["series"]
        assert query["dataset"] == "economics" and not result["is_synthetic"]
        assert not result["coverage"]["truncated"]
        source = result["sources"][0]
        original = raw[source["raw_sha256"]]
        series = original["Results"]["series"][0]
        assert series["seriesID"] == SERIES[name]
        attempt = capture["attempts"][0]
        assert attempt in requests and attempt["raw_sha256"] == source["raw_sha256"]
        dated = "start_date" in query
        assert attempt["method"] == ("POST" if dated else "GET")
        if dated:
            assert {k: query[k] for k in ("start_date", "end_date")} == manifest[
                "observation_window"
            ]
            assert (
                attempt["request_body"]
                == source["request_body"]
                == {"seriesid": [SERIES[name]], "startyear": "2025", "endyear": "2026"}
            )
        else:
            assert attempt["url"].endswith("/" + SERIES[name])
        quarterly = name not in {"import_prices", "export_prices"}
        expected = []
        for cell in series["data"]:
            period = cell["period"]
            if quarterly and period == "Q05":
                counts["excluded_annual_summary_rows"] += 1
                continue
            year, part = int(cell["year"]), int(period[1:])
            month = part * 3 if quarterly else min(part, 12)
            date = f"{year:04}-{month:02}-{calendar.monthrange(year, month)[1]:02}"
            if dated and not query["start_date"] <= date <= query["end_date"]:
                continue
            expected.append((cell["year"], period, date, cell))
        expected.sort(key=lambda row: (row[2], row[1]), reverse=True)
        assert len(result["records"]) == len(expected)
        for row, (year, period, date, cell) in zip(result["records"], expected, strict=True):
            located = pointer(original, row["source"]["json_pointer"])
            assert located == cell and row["source"]["raw_sha256"] == source["raw_sha256"]
            assert row["source"]["retrieved_at"] == source["retrieved_at"]
            assert (row["year"], row["period"], row["date"]) == (year, period, date)
            assert row["value"] == cell["value"] and row["footnotes"] == cell["footnotes"]
            assert row["status"] == "reported" and row["published_at"] is None
            assert row["annualized"] == name.endswith("growth")
            counts["matched_records"] += 1
            counts["negative_records"] += row["value"].startswith("-")
        if quarterly:
            assert result["coverage"]["excluded_annual_summary_rows"] == sum(
                cell["period"] == "Q05" for cell in series["data"]
            )
    for name in SERIES:
        pair = [c for c in captures if c["query"]["series"] == name]
        normal = next(c for c in pair if "start_date" not in c["query"])
        dated = next(c for c in pair if "start_date" in c["query"])
        window = manifest["observation_window"]
        overlap = [
            row
            for row in normal["result"]["records"]
            if window["start_date"] <= row["date"] <= window["end_date"]
        ]
        assert [{k: v for k, v in row.items() if k != "source"} for row in overlap] == [
            {k: v for k, v in row.items() if k != "source"} for row in dated["result"]["records"]
        ]
    with TemporaryDirectory(prefix="atlas-bls-replay-") as temporary:
        with Atlas("public", directory=Path(temporary)) as atlas:
            atlas._research.public = SavedBLS(
                directory,
                [s for c in captures for s in c["result"]["sources"]] + list(evidence.values()),
            )
            for capture in captures:
                result = atlas.get_research(capture["query"])
                assert result["records"] == capture["result"]["records"]
                assert result["coverage"] == capture["result"]["coverage"]
            for filename in manifest["presets"]:
                saved = read(directory / filename)
                result = atlas.get_research_preset(saved["query"])
                assert result["status"] == saved["result"]["status"] == "ok"
                assert len(result["batch"]["items"]) == 8
                for item, old in zip(
                    result["batch"]["items"], saved["result"]["batch"]["items"], strict=True
                ):
                    assert item["result"]["records"] == old["result"]["records"]
                    for row in item["result"]["records"]:
                        eid = row["source"]["evidence_id"]
                        assert atlas.get_evidence(eid)["evidence"] == evidence[eid]
    for interface in ("sdk", "mcp"):
        checks = interfaces[interface]
        assert len(checks) == 12 and all(c["status"] == "pass" for c in checks)
        assert sum(c["records_resolved"] for c in checks) == counts["matched_records"]
    assert interfaces["attempt_counts"] == {
        "before": 12,
        "after_sdk_reads": 12,
        "after_sdk_preset": 14,
        "after_mcp": 14,
    }
    return {
        "status": "PASS",
        "mode": "offline_recorded_response_replay",
        "requests_archived": 14,
        **counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=CAPTURE)
    print(json.dumps(verify_capture(parser.parse_args().directory), indent=2))
