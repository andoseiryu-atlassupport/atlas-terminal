"""Explicit live policy verification; captures remain private and outside distribution."""

import argparse
import csv
import io
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from atlas_terminal.policy_sources import POLICY_SERIES, parse_policy, policy_url
from atlas_terminal.public_sources import PublicTransport


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default="2026-10-31")
    parser.add_argument("--output", type=Path, default=Path("build/policy-live"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "build"):
        parser.error("Private output must be below the ignored repository build directory.")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    results = []
    transport = PublicTransport()
    try:
        for series in POLICY_SERIES:
            try:
                url = policy_url(series, args.start_date, args.end_date)
                snapshot = transport.get(url, 21600)
                rows = parse_policy(snapshot, series)
                raw = list(csv.reader(io.StringIO(snapshot.body.decode("utf-8-sig"), newline="")))
                for row in rows:
                    ref = row["source"]
                    cell = raw[ref["csv_row"] - 1][ref["csv_column"] - 1].strip()
                    if cell != row["source_value"] or (
                        row["value"] is not None and Decimal(cell) != Decimal(row["value"])
                    ):
                        raise ValueError("Original CSV cell differs from normalized observation")
                selected = [r for r in rows if args.start_date <= r["date"] <= args.end_date]
                for suffix, body in [
                    ("csv", snapshot.body),
                    ("json", json.dumps(selected, indent=2).encode()),
                ]:
                    path = output / (series + "." + suffix)
                    path.write_bytes(body)
                    path.chmod(0o600)
                result = {
                    "series": series,
                    "status": "verified",
                    "raw_source_url": url,
                    "raw_sha256": snapshot.sha256,
                    "retrieved_at": snapshot.retrieved_at,
                    "source_rows": len(rows),
                    "selected_rows": len(selected),
                    "original_cells_checked": len(rows),
                    "first_source_date": rows[0]["date"] if rows else None,
                    "last_source_date": rows[-1]["date"] if rows else None,
                }
            except Exception as error:
                result = {
                    "series": series,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            results.append(result)
            print(json.dumps(result), flush=True)
    finally:
        transport.close()
    report = {
        "verified_at": datetime.now(UTC).isoformat(),
        "sec_user_agent_configured": bool(os.getenv("SEC_USER_AGENT")),
        "results": results,
        "limitations": [
            "Current observations are not release-time PIT.",
            "No SEC filing live verification is performed by this script.",
        ],
    }
    target = output / "summary.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    target.chmod(0o600)
    if any(r["status"] != "verified" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
