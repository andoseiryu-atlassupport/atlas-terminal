"""Opt-in private IMF commodity and Yahoo future cell verification; no rating feed."""

import argparse
import json
from decimal import Decimal, localcontext
from pathlib import Path
from xml.etree import ElementTree as ET

from verify_market_prices_live import locate, verify_cells

from atlas_terminal.client import Atlas
from atlas_terminal.commodity_catalog import COMMODITIES
from atlas_terminal.equity_sources import require_yahoo_enabled, yahoo_url
from atlas_terminal.futures_catalog import FUTURES
from atlas_terminal.util import canonical, stamp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/commodities-futures-live"))
    args = parser.parse_args()
    require_yahoo_enabled()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if not out.is_relative_to(root / "build"):
        parser.error("Actual observations must remain under the ignored build directory.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    checks = []
    report = {"started_at": stamp(), "checks": checks, "credit_agency_live_verified": False}

    def save(name, body):
        path = out / name
        path.write_bytes(body)
        path.chmod(0o600)

    def record(row):
        checks.append(row)
        print(json.dumps(row), flush=True)
        save("report.json", canonical(report))

    with Atlas("public") as atlas:
        for series, (code, label, unit) in COMMODITIES.items():
            try:
                result = atlas.get_research(
                    {
                        "dataset": "commodity_prices",
                        "series": series,
                        "start_date": "2026-01-01",
                        "end_date": "2026-09-30",
                        "limit": 500,
                    }
                )
                assert not result["is_synthetic"] and not result["coverage"]["truncated"]
                assert result["records"] and atlas._research.public is not None
                snapshots = {}
                for ref in result["sources"]:
                    source = atlas._research.public.get(ref["raw_source_url"], 86400)
                    assert source.sha256 == ref["raw_sha256"]
                    snapshots[source.sha256] = source
                    save(source.sha256 + ".xml", source.body)
                checked = 0
                for row in result["records"]:
                    source = snapshots[row["source"]["raw_sha256"]]
                    xml = ET.fromstring(source.body)
                    for node in xml.iter():
                        node.tag = node.tag.rsplit("}", 1)[-1]
                    path = row["source"]["xml_local_path"].removeprefix("/" + xml.tag + "/")
                    obs = xml.find(path)
                    parent = xml.find(path.rsplit("/", 1)[0])
                    assert obs is not None and parent is not None
                    assert parent.get("INDICATOR") == code and parent.get("SCALE") == "0"
                    assert obs.get("TIME_PERIOD") == row["period"]
                    raw = obs.get("OBS_VALUE")
                    assert (raw in {None, ""}) == (row["value"] is None)
                    if row["value"] is not None:
                        assert Decimal(raw) == Decimal(row["value"])
                    assert row["unit"] == unit and row["source_unit_label"] == label
                    assert row["published_at"] is None
                    checked += 1
                save(series + "-normalized.json", canonical(result))
                record(
                    {
                        "selection": series,
                        "status": "verified",
                        "rows": len(result["records"]),
                        "original_cells_checked": checked,
                        "unit": unit,
                        "unavailable_rows": sum(r["value"] is None for r in result["records"]),
                    }
                )
            except Exception as error:
                record(
                    {
                        "selection": series,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
        windows = [(root + "=F", "2026-10-01", "2026-10-30") for root in FUTURES]
        windows += [
            (s, "2026-10-01", "2026-10-30")
            for s in ("GCZ26.CMX", "GCG27.CMX", "GCJ27.CMX", "CLZ26.NYM", "ESZ26.CME")
        ]
        windows.append(("CL=F", "2020-04-17", "2020-04-23"))
        for symbol, start, end in windows:
            try:
                result = atlas.get_research(
                    {
                        "dataset": "futures_prices",
                        "identifier": symbol,
                        "start_date": start,
                        "end_date": end,
                        "limit": 500,
                    }
                )
                assert atlas._research.public is not None
                source = atlas._research.public.get(yahoo_url(symbol, start, end), 900)
                checked = verify_cells(result, source.body)
                negative = sum(
                    r["close"] is not None and Decimal(r["close"]) < 0 for r in result["records"]
                )
                if start == "2020-04-17":
                    assert negative > 0
                name = symbol.replace("=", "_") + "-" + start
                save(name + "-raw.json", source.body)
                save(name + "-normalized.json", canonical(result))
                record(
                    {
                        "selection": symbol + "_" + start,
                        "status": "verified",
                        "rows": len(result["records"]),
                        "original_cells_checked": checked,
                        "negative_close_rows": negative,
                        "unavailable_rows": result["coverage"]["unavailable_records"],
                    }
                )
            except Exception as error:
                record(
                    {
                        "selection": symbol + "_" + start,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
        try:
            result = atlas.get_research(
                {
                    "dataset": "futures_curve",
                    "identifiers": ["GCZ26.CMX", "GCG27.CMX", "GCJ27.CMX"],
                    "observation_date": "2026-10-30",
                }
            )
            for row in result["records"]:
                if row["record_type"] == "calendar_spread":
                    near, far = row["inputs"]
                    with localcontext() as context:
                        context.prec = 200
                        assert Decimal(row["value"]) == Decimal(far["value"]) - Decimal(
                            near["value"]
                        )
            save("gold-curve-normalized.json", canonical(result))
            assert atlas._research.public is not None
            original_contracts = {}
            for ref in result["sources"]:
                source = atlas._research.public.get(ref["raw_source_url"], 900)
                assert source.sha256 == ref["raw_sha256"]
                save(source.sha256 + ".json", source.body)
                original_contracts[source.sha256] = source.data
            for point in result["records"][:3]:
                ref = point["source"]
                raw_close = locate(original_contracts[ref["raw_sha256"]], ref["json_pointer"])
                assert raw_close is not None and Decimal(point["value"]) == raw_close
            record(
                {
                    "selection": "gold_dated_curve_2026-10-30",
                    "status": "verified",
                    "contract_points": 3,
                    "exact_calendar_spreads": 2,
                }
            )
        except Exception as error:
            record(
                {
                    "selection": "gold_dated_curve_2026-10-30",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    report["completed_at"] = stamp()
    report["status"] = "PASS" if all(r["status"] == "verified" for r in checks) else "INCOMPLETE"
    save("report.json", canonical(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
