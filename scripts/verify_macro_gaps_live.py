"""Opt-in current ABS CPI frequency and availability checks; private output only."""

import argparse
from pathlib import Path

from verify_data_workflows import CaptureTransport
from verify_source_cells import verify_source_cells

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.util import canonical, stamp


def save(path: Path, value: object) -> None:
    path.write_bytes(canonical(value))
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/macro-gap-live"))
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Pass --allow-network for explicit public-source reads.")
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    transport = CaptureTransport(output)
    report = {"started_at": stamp(), "checks": [], "status": "in_progress"}
    try:
        with Atlas("public") as atlas:
            atlas._research.public = transport
            for series, frequency in (
                ("au_cpi_quarterly", "quarterly"),
                ("au_cpi_monthly", "monthly"),
            ):
                query = {
                    "dataset": "activity_economics",
                    "series": series,
                    "start_date": "2024-01-01",
                    "end_date": "2026-09-30",
                    "limit": 500,
                }
                result = atlas.get_research(query)
                assert not result["is_synthetic"] and result["records"]
                assert all(row["frequency"] == frequency for row in result["records"])
                assert all(row["dataflow"] == "ABS,CPI,2.0.0" for row in result["records"])
                verified = verify_source_cells(result, transport.snapshots)
                assert verified["matched_reported_cells"] == len(result["records"])
                save(output / f"{series}.json", result)
                report["checks"].append(
                    {
                        "query": query,
                        "status": "PASS",
                        "records": len(result["records"]),
                        "first_date": min(row["date"] for row in result["records"]),
                        "last_date": max(row["date"] for row in result["records"]),
                        **verified,
                    }
                )
            catalog = atlas.get_research(
                {
                    "dataset": "sdmx_catalog",
                    "source_id": "abs",
                    "dataflow": "ABS,CPI_Q,1.0.0",
                    "dimension": "FREQ",
                }
            )
            save(output / "quarterly_component_availability.json", catalog)
            before = set(transport.snapshots)
            try:
                atlas.get_research(
                    {
                        "dataset": "international_economics",
                        "source_id": "abs",
                        "dataflow": "ABS,CPI_Q,1.0.0",
                        "dimensions": {
                            "MEASURE": "1",
                            "INDEX": "10001",
                            "TSEST": "10",
                            "REGION": "50",
                            "FREQ": "Q",
                        },
                        "start_date": "2024-01-01",
                        "end_date": "2026-09-30",
                    }
                )
            except AtlasError as error:
                assert error.code == "INCOMPLETE_COVERAGE"
                assert set(transport.snapshots) == before
                report["checks"].append(
                    {"name": "excluded_component_key", "status": "PASS", "error": error.envelope()}
                )
            else:
                raise AssertionError("Source availability changed; review the expected exclusion")
        report.update(status="PASS", finished_at=stamp(), requests=transport.requests)
    finally:
        save(output / "report.json", report)
        for path in output.glob("*.bin"):
            path.chmod(0o600)
        transport.client.close()
    print(canonical(report).decode())


if __name__ == "__main__":
    main()
