"""Opt-in private Gamma search/detail verification; no wallet, orders or fallback hosts."""

import argparse
import csv
import io
from fractions import Fraction
from pathlib import Path

from verify_g20_live import DiagnosticTransport, private_write

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.prediction_sources import prediction_identity
from atlas_terminal.providers import decode_json
from atlas_terminal.util import canonical, stamp, state_dir


class Capture(DiagnosticTransport):
    def __init__(self, raw_directory):
        super().__init__(cache_path=state_dir() / "public-cache.sqlite3")
        self.raw_directory, self.snapshots = raw_directory, {}

    def get(self, url, ttl, **kwargs):
        assert prediction_identity(url) is not None, "Only fixed read-only Gamma URLs are allowed"
        snapshot = super().get(url, ttl, **kwargs)
        self.snapshots[url] = snapshot
        private_write(self.raw_directory / (snapshot.sha256 + ".json"), snapshot.body)
        return snapshot


def resolve(source, capture):
    snapshot = capture.snapshots[source["raw_source_url"]]
    assert snapshot.sha256 == source["raw_sha256"]
    assert snapshot.retrieved_at == source["retrieved_at"]
    value = snapshot.data
    for token in source["json_pointer"].split("/")[1:]:
        value = value[int(token)] if isinstance(value, list) else value[token]
    if "embedded_json_pointer" in source:
        value = decode_json(value.encode())
        for token in source["embedded_json_pointer"].split("/")[1:]:
            value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def verify(result, capture):
    assert result["dataset"] == "prediction_markets" and not result["is_synthetic"]
    assert result["source_id"] == "polymarket" and not result["coverage"]["point_in_time"]
    count = 0
    for event in result["records"]:
        raw_event = resolve(event["source"], capture)
        assert raw_event["id"] == event["event_id"] and raw_event["slug"] == event["event_slug"]
        assert raw_event["title"] == event["title"] and event["price_observed_at"] is None
        for market in event["markets"]:
            raw = resolve(market["source"], capture)
            assert raw["id"] == market["market_id"] and raw["question"] == market["question"]
            assert market["price_observed_at"] is None
            flags = [
                raw_event.get("closed"),
                raw.get("closed"),
                raw_event.get("archived"),
                raw.get("archived"),
            ]
            is_open = (
                raw_event.get("active") is True
                and raw_event.get("closed") is False
                and raw.get("active") is True
                and raw.get("closed") is False
                and raw.get("acceptingOrders") is True
                and not any(value is True for value in flags)
            )
            assert (market["lifecycle"] == "open") == is_open
            for outcome in market["outcomes"]:
                for field, source in outcome["field_sources"].items():
                    if source is None:
                        continue
                    reported = resolve(source, capture)
                    if field == "reported_price" and outcome[field] is None:
                        if isinstance(reported, str):
                            reported = decode_json(reported.encode())
                        assert reported is None or reported == []
                    elif field == "reported_price":
                        assert Fraction(str(reported)) == Fraction(outcome[field])
                    else:
                        assert reported == outcome[field]
                    count += 1
                if outcome["reported_price"] is not None and market["lifecycle"] == "open":
                    assert Fraction(outcome["probability_percent"]) == 100 * Fraction(
                        outcome["reported_price"]
                    )
                    assert Fraction(outcome["market_implied_probability"]) == Fraction(
                        outcome["reported_price"]
                    )
                else:
                    assert (
                        outcome["market_implied_probability"] is None
                        and outcome["probability_percent"] is None
                    )
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--search", default="Anthropic")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("build/predictions-live"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "build"):
        parser.error("Actual data must stay in ignored private build/.")
    raw = output / "raw"
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    raw.chmod(0o700)
    capture = Capture(raw)
    report = {"status": "NOT_RUN", "checked_at": stamp(), "search": args.search}
    try:
        with Atlas("public") as atlas:
            atlas._research.public = capture
            result = atlas.get_prediction_markets(args.search, limit=args.limit)
            matched = verify(result, capture)
            private_write(output / "result.json", canonical(result))
            rows = [
                {
                    "event_slug": event["event_slug"],
                    "market_id": market["market_id"],
                    "question": market["question"],
                    "outcome": outcome["outcome"],
                    "reported_price": outcome["reported_price"],
                    "probability_percent": outcome["probability_percent"],
                    "lifecycle": market["lifecycle"],
                    "source_url": market["source"]["raw_source_url"],
                    "raw_sha256": market["source"]["raw_sha256"],
                    "retrieved_at": market["source"]["retrieved_at"],
                }
                for event in result["records"]
                for market in event["markets"]
                for outcome in market["outcomes"]
            ]
            if rows:
                csv_output = io.StringIO(newline="")
                writer = csv.DictWriter(csv_output, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
                private_write(output / "outcomes.csv", csv_output.getvalue().encode("utf-8"))
            report.update(
                status="PASS"
                if any(row["probability_percent"] is not None for row in rows)
                else "INCOMPLETE",
                source_fields=matched,
                events=len(result["records"]),
                outcomes=len(rows),
                partial=result["coverage"]["partial"],
                truncated=result["coverage"]["truncated"],
            )
    except AtlasError as error:
        report.update(status="FAIL", error_code=error.code)
    finally:
        capture.close()
        report["http_diagnostics"] = capture.diagnostics
        private_write(output / "report.json", canonical(report))
    print(canonical(report).decode())
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
