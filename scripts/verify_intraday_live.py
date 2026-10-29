"""Opt-in recent Yahoo intraday capture and exact original-cell verification."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas_terminal.client import Atlas
from atlas_terminal.equity_intraday import intraday_url
from atlas_terminal.equity_sources import require_yahoo_enabled
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import decode_json
from atlas_terminal.util import canonical, digest, stamp


def locate(document, pointer):
    for part in pointer.split("/")[1:]:
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def private_write(path, body):
    with path.open("wb") as stream:
        path.chmod(0o600)
        stream.write(body)


def verify(result, snapshot):
    document = decode_json(snapshot.body)
    assert not result["is_synthetic"]
    assert result["sources"][0]["raw_sha256"] == digest(snapshot.body)
    timezone = ZoneInfo(result["instrument"]["exchange_timezone"])
    checked = 0
    for row in result["records"]:
        for field in ("open", "high", "low", "close", "volume", "observation_at"):
            source = row["field_sources"][field]
            assert source["raw_sha256"] == snapshot.sha256
            value = locate(document, source["json_pointer"])
            if field == "observation_at":
                observed = datetime.fromtimestamp(value, UTC)
                assert observed.isoformat().replace("+00:00", "Z") == row[field]
                assert observed.astimezone(timezone).isoformat() == row["observation_local"]
                assert observed.astimezone(timezone).date().isoformat() == row["date"]
                assert datetime.fromisoformat(row["bar_end_at"]) <= datetime.fromisoformat(
                    snapshot.retrieved_at
                )
            elif value is None:
                assert row[field] is None
            else:
                assert Decimal(row[field]) == value
            checked += 1
    return checked


def run(output, day):
    require_yahoo_enabled()
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    report = {"started_at": stamp(), "data_usage": "private_personal_use", "checks": []}
    selections = [
        ("AAPL", "1m"),
        ("AAPL", "5m"),
        ("AAPL", "15m"),
        ("AAPL", "30m"),
        ("AAPL", "60m"),
        ("7203.T", "1m"),
        ("7203.T", "5m"),
        ("7203.T", "60m"),
        ("1306.T", "5m"),
        ("SPY", "5m"),
        ("BTC-USD", "60m"),
        ("EURUSD=X", "60m"),
    ]
    with Atlas("public") as atlas:
        for symbol, interval in selections:
            query = {
                "dataset": "intraday_prices",
                "identifier": symbol,
                "start_date": day,
                "end_date": day,
                "interval": interval,
                "limit": 500,
            }
            entry = {"query": query, "status": "FAIL"}
            try:
                result = atlas.get_research(query)
                source = atlas._research.public.get(intraday_url(symbol, day, day, interval), 900)
                label = symbol.replace("=", "_") + "-" + interval
                private_write(output / (label + "-raw.json"), source.body)
                private_write(output / (label + "-normalized.json"), canonical(result))
                entry.update(
                    status="PARTIAL"
                    if result["coverage"]["partial"] or result["coverage"]["truncated"]
                    else "PASS",
                    exact_source_cells=verify(result, source),
                    coverage=result["coverage"],
                    source_interval=result["instrument"]["source_interval"],
                )
            except AtlasError as error:
                entry["error"] = error.envelope()["error"]
            report["checks"].append(entry)
            private_write(output / "report.json", canonical(report))
            print(json.dumps(entry), flush=True)
    report["completed_at"] = stamp()
    private_write(output / "report.json", canonical(report))
    return 0 if all(item["status"] == "PASS" for item in report["checks"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/intraday-live"))
    parser.add_argument(
        "--date", default=(datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    )
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Live source access requires --allow-network")
    try:
        day = date.fromisoformat(args.date).isoformat()
    except ValueError:
        parser.error("Use an ISO --date")
    raise SystemExit(run(args.output, day))


if __name__ == "__main__":
    main()
