"""Opt-in dated NY Fed reads, original-field checks and a historical calendar panel."""

import argparse
from decimal import Context, Decimal, localcontext
from pathlib import Path

from verify_g20_live import DiagnosticTransport, private_write
from verify_source_cells import SourceInspector

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.reference_rate_sources import nyfed_identity
from atlas_terminal.util import canonical, stamp, state_dir


class Capture(DiagnosticTransport):
    def __init__(self, *, raw_directory):
        super().__init__(cache_path=state_dir() / "public-cache.sqlite3")
        self.raw_directory, self.snapshots = raw_directory, {}

    def get(self, url, ttl, **kwargs):
        assert nyfed_identity(url) is not None, "Only fixed NY Fed reference-rate reads are allowed"
        snapshot = super().get(url, ttl, **kwargs)
        self.snapshots[url] = snapshot
        private_write(self.raw_directory / (snapshot.sha256 + ".json"), snapshot.body)
        return snapshot


def verify_fields(result, capture):
    inspector = SourceInspector(capture.snapshots)
    for row in result["records"]:
        source = row["source"]
        raw_value = inspector.cell(source)
        expected = None if raw_value in (None, "") else format(Decimal(str(raw_value)), "f")
        assert row["value"] == expected
        snapshot = capture.snapshots[source["raw_source_url"]]
        index = int(source["json_pointer"].split("/")[2])
        original = snapshot.data["refRates"][index]
        assert original["effectiveDate"] == row["date"]
        assert original.get("type", original.get("rateType")) == row["source_series"]
        assert original.get("revisionIndicator") == row["revision_indicator"]
        assert row["unit"] == "percent_per_annum" and row["published_at"] is None
    return 4 * len(result["records"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/reference-history-live"))
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(Path(__file__).resolve().parents[1] / "build"):
        parser.error("Actual observations must stay in ignored build output.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    raw = out / "raw"
    raw.mkdir(exist_ok=True, mode=0o700)
    raw.chmod(0o700)
    checks = []
    with Atlas("public") as atlas:
        capture = Capture(raw_directory=raw)
        atlas._research.public = capture
        windows = [
            ("effr-2000", "effr", "2000-01-01", "2000-12-31"),
            ("effr-2010", "effr", "2010-01-01", "2010-12-31"),
            ("effr-2020", "effr", "2020-01-01", "2020-12-31"),
            ("sofr-2018", "sofr", "2018-01-01", "2018-12-31"),
            ("sofr-2020", "sofr", "2020-01-01", "2020-12-31"),
            ("effr-year-boundary", "effr", "2010-12-29", "2011-01-04"),
        ]
        for name, series, start, end in windows:
            item = {"selection": name, "status": "FAIL"}
            try:
                result = atlas.get_research(
                    {
                        "dataset": "reference_rates",
                        "series": series,
                        "start_date": start,
                        "end_date": end,
                        "limit": 500,
                    }
                )
                assert not result["is_synthetic"] and not result["coverage"]["truncated"]
                assert all(start <= row["date"] <= end for row in result["records"])
                matched = verify_fields(result, capture)
                private_write(out / (name + ".json"), canonical(result))
                item.update(
                    status="PASS",
                    records=len(result["records"]),
                    original_field_matches=matched,
                    first=result["coverage"]["first_observation_date"],
                    last=result["coverage"]["last_observation_date"],
                )
            except Exception as error:
                item.update(error_type=type(error).__name__, error=str(error)[:250])
            checks.append(item)
            print(canonical(item).decode(), flush=True)
        item = {"selection": "historical-annual-panel", "status": "FAIL"}
        try:
            panel = atlas.get_research(
                {
                    "dataset": "macro_panel",
                    "start_date": "2001-01-01",
                    "end_date": "2002-12-31",
                    "frequency": "annual",
                    "items": [
                        {
                            "id": "effr",
                            "query": {"dataset": "reference_rates", "series": "effr"},
                            "aggregation": "mean",
                        }
                    ],
                }
            )
            assert not panel["is_synthetic"] and not panel["coverage"]["partial"]
            matched = 0
            for period in panel["records"]:
                value = period["records"][0]
                inputs = value["inputs"]
                assert len(inputs) > 90
                assert all(
                    period["period_start"] <= r["date"] <= period["period_end"] for r in inputs
                )
                matched += verify_fields({"records": inputs}, capture)
                with localcontext(Context(prec=100)):
                    total = sum((Decimal(r["value"]) for r in inputs), Decimal(0))
                with localcontext(Context(prec=28)):
                    expected = total / Decimal(len(inputs))
                assert Decimal(value["value"]) == expected
            private_write(out / "annual-panel.json", canonical(panel))
            item.update(
                status="PASS",
                calendar_periods=len(panel["records"]),
                original_field_matches=matched,
                means_recomputed=2,
            )
        except Exception as error:
            item.update(error_type=type(error).__name__, error=str(error)[:250])
        checks.append(item)
        print(canonical(item).decode(), flush=True)
        item = {"selection": "effr-1999-empty-window", "status": "FAIL"}
        try:
            atlas.get_research(
                {
                    "dataset": "reference_rates",
                    "series": "effr",
                    "start_date": "1999-01-01",
                    "end_date": "1999-12-31",
                    "limit": 500,
                }
            )
        except AtlasError as error:
            item.update(
                status="PASS" if error.code == "INCOMPLETE_COVERAGE" else "FAIL",
                expected_error=error.code,
            )
        checks.append(item)
        print(canonical(item).decode(), flush=True)
        report = {
            "checked_at": stamp(),
            "checks": checks,
            "passed": all(row["status"] == "PASS" for row in checks),
            "requests": capture.diagnostics,
            "source_urls": list(capture.snapshots),
            "fresh_bls_requests": False,
            "rates_filled": False,
        }
        private_write(out / "report.json", canonical(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
