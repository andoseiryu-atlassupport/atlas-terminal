"""Opt-in private cross-asset Yahoo verification, exact cells and US risk execution."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import REFERENCE_INSTRUMENTS, require_yahoo_enabled, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import decode_json
from atlas_terminal.util import canonical, digest, stamp, state_dir
from atlas_terminal.yahoo_risk import YahooRiskConnector, YahooRiskSpec


def locate(document, pointer):
    for part in pointer.split("/")[1:]:
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def verify_cells(result, body):
    document = decode_json(body)
    assert digest(body) == result["sources"][0]["raw_sha256"]
    assert not result["is_synthetic"] and not result["coverage"]["truncated"]
    timezone = ZoneInfo(result["instrument"]["exchange_timezone"])
    checked = 0
    for row in result["records"]:
        for field, source in row["field_sources"].items():
            original = locate(document, source["json_pointer"])
            assert source["raw_sha256"] == digest(body)
            if field == "date":
                assert (
                    datetime.fromtimestamp(original, UTC).astimezone(timezone).date().isoformat()
                    == row["date"]
                )
            elif original is None:
                assert row[field] is None
            else:
                assert Decimal(row[field]) == original
            checked += 1
    return checked


def run(output, start, end, risk_start):
    require_yahoo_enabled()
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    report = {"started_at": stamp(), "checks": checks, "data_usage": "private_personal_use"}
    with Atlas("public") as atlas:
        windows = [
            (symbol, start, end)
            for symbol in ["AAPL", "MSFT", "SPY", "BRK-B", *REFERENCE_INSTRUMENTS]
        ]
        windows.append(("CL=F", "2020-04-17", "2020-04-23"))
        for symbol, first, last in windows:
            query = {
                "dataset": "equity_prices",
                "identifier": symbol,
                "start_date": first,
                "end_date": last,
                "limit": 500,
            }
            entry = {"symbol": symbol, "query": query, "status": "FAIL"}
            label = symbol.replace("^", "index_").replace("=", "_") + "-" + first
            try:
                result = atlas.get_research(query)
                source = atlas._research.public.get(yahoo_url(symbol, first, last), 900)
                (output / (label + "-raw.json")).write_bytes(source.body)
                (output / (label + "-normalized.json")).write_bytes(canonical(result))
                entry.update(
                    status="PARTIAL" if result["coverage"]["partial"] else "PASS",
                    records=len(result["records"]),
                    exact_source_cells=verify_cells(result, source.body),
                    unavailable=result["coverage"]["unavailable_records"],
                    inconsistent_ohlc=result["coverage"]["inconsistent_ohlc_records"],
                    currency=result["instrument"]["currency"],
                    source_instrument_type=result["instrument"]["source_instrument_type"],
                    unit=result["records"][0]["unit"],
                )
                if symbol == "CL=F" and first == "2020-04-17":
                    assert any(Decimal(row["close"]) < 0 for row in result["records"])
            except AtlasError as error:
                entry["error"] = error.envelope()["error"]
            checks.append(entry)
            print(json.dumps(entry), flush=True)
            (output / "report.json").write_bytes(canonical(report))
        spec = YahooRiskSpec(
            connection_id="yahoo_us_verification",
            kind="yahoo_finance",
            symbols=["AAPL", "MSFT"],
            factor_symbol="SPY",
            start_date=risk_start,
            end_date=end,
            personal_use_acknowledged=True,
        )
        connector = YahooRiskConnector(spec, state_dir())
        entry = {"check": "us_time_series_risk", "status": "FAIL"}
        try:
            loaded = connector.read()
            # Capture every source before selecting availability for the fit.
            atlas.register_risk_connection(spec.connection_id, connector)
            result = atlas.risk(
                {
                    "action": "run",
                    "connection_id": spec.connection_id,
                    "allow_network": True,
                    "fit": {"method": "time_series", "as_of": stamp(), "lookback": 120},
                    "positions": [
                        {"asset_id": "AAPL", "weight": "0.5"},
                        {"asset_id": "MSFT", "weight": "0.5"},
                    ],
                }
            )
            (output / "us-risk.json").write_bytes(canonical(result))
            entry.update(
                status="PASS",
                input_sessions=len(loaded.panel["sessions"]),
                fit_sessions=120,
                currency=loaded.panel["manifest"]["currency"],
                factor_basis=loaded.normalization["factor_basis"],
            )
        except AtlasError as error:
            entry["error"] = error.envelope()["error"]
        checks.append(entry)
        print(json.dumps(entry), flush=True)
    report.update(
        completed_at=stamp(),
        status="INCOMPLETE" if any(x["status"] != "PASS" for x in checks) else "PASS",
    )
    (output / "report.json").write_bytes(canonical(report))
    return 1 if report["status"] == "INCOMPLETE" else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/market-prices-live"))
    parser.add_argument("--start-date", default="2026-09-01")
    parser.add_argument("--end-date", default="2026-10-23")
    parser.add_argument("--risk-start-date", default="2026-01-01")
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("--allow-network is required")
    raise SystemExit(run(args.output, args.start_date, args.end_date, args.risk_start_date))
