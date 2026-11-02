"""Opt-in government spread reads with independent raw-cell and exact-math checks."""

import argparse
import json
from decimal import Context, Decimal, localcontext
from pathlib import Path

from verify_g20_live import CaptureTransport, private_write
from verify_source_cells import SourceInspector

from atlas_terminal.client import Atlas
from atlas_terminal.spread_catalog import SPREADS
from atlas_terminal.util import canonical, stamp, state_dir


def verify_spread(result, capture, raw_directory):
    assert not result["is_synthetic"] and len(result["records"]) == 1
    inspector = SourceInspector(capture.snapshots)
    for source in result["sources"]:
        snapshot = capture.snapshots[source["raw_source_url"]]
        assert snapshot.sha256 == source["raw_sha256"]
        private_write(raw_directory / (snapshot.sha256 + ".bin"), snapshot.body)
    row = result["records"][0]
    assert row["unit"] == "basis_points" and row["published_at"] is None
    assert {part["date"] for part in row["inputs"]} == {row["date"]}
    assert {part["curve"] for part in row["inputs"]} == {row["curve"]}
    assert all(part["unit"] == "percent_per_annum" for part in row["inputs"])
    inspector.record(row)
    assert inspector.matches == sum(part["value"] is not None for part in row["inputs"])
    if any(part["value"] is None for part in row["inputs"]):
        assert row["value"] is None and row["percentage_points"] is None
    else:
        with localcontext(Context(prec=200)):
            expected = (
                Decimal(row["inputs"][0]["value"]) - Decimal(row["inputs"][1]["value"])
            ) * 100
            assert Decimal(row["value"]) == expected
            assert Decimal(row["percentage_points"]) == expected / 100
    return inspector.matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/spreads-live"))
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(Path(__file__).resolve().parents[1] / "build"):
        parser.error("Actual observations must stay under the ignored build directory.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    raw = out / "raw"
    raw.mkdir(exist_ok=True, mode=0o700)
    raw.chmod(0o700)
    checks = []
    report = {"started_at": stamp(), "checks": checks}
    with Atlas("public") as atlas:
        capture = CaptureTransport(state_dir() / "public-cache.sqlite3")
        atlas._research.public = capture
        for series in SPREADS:
            entry = {"series": series, "status": "failed"}
            capture.last_failure = None
            try:
                result = atlas.get_spread(series)
                checked = verify_spread(result, capture, raw)
                private_write(out / (series + ".json"), canonical(result))
                row = result["records"][0]
                entry.update(
                    status="verified",
                    source_id=result["source_id"],
                    observation_date=row["date"],
                    basis_points=row["value"],
                    original_numeric_cells_checked=checked,
                    unavailable=row["value"] is None,
                )
            except Exception as error:
                entry.update(error_type=type(error).__name__, error=str(error))
                if capture.last_failure is not None:
                    entry["network_diagnostic"] = capture.last_failure
            checks.append(entry)
            print(json.dumps(entry), flush=True)
            private_write(out / "report.json", canonical(report))
        report["requests"] = capture.transport.diagnostics
    report.update(
        completed_at=stamp(),
        status="PASS" if all(c["status"] == "verified" for c in checks) else "INCOMPLETE",
    )
    private_write(out / "report.json", canonical(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
