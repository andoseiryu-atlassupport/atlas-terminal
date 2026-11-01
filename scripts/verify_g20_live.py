"""Opt-in G20 WDI reads and exact original-cell checks; all output stays private."""

import argparse
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.global_macro_sources import G20_AREAS, GLOBAL_INDICATORS
from atlas_terminal.providers import Snapshot, decode_json
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_macro import numeric
from atlas_terminal.util import canonical, digest


class DiagnosticTransport(PublicTransport):
    """Retain safe request diagnostics while preserving the normal proxy and TLS path."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.diagnostics = {}

    @contextmanager
    def request(self, url, headers):
        diagnostic = {"http_status": None}
        self.diagnostics[url] = diagnostic
        try:
            with super().request(url, headers) as response:
                diagnostic["http_status"] = response.status_code
                yield response
        except httpx.HTTPError as error:
            diagnostic["network_exception"] = type(error).__name__
            raise


class CaptureTransport:
    synthetic = False

    def __init__(self, cache_path):
        self.transport = DiagnosticTransport(cache_path=cache_path)
        self.snapshots: dict[str, Snapshot] = {}
        self.last_failure = None

    def get(self, url, ttl):
        try:
            snapshot = self.transport.get(url, ttl)
        except AtlasError:
            self.last_failure = {"url": url, **self.transport.diagnostics.get(url, {})}
            raise
        self.snapshots[url] = snapshot
        return snapshot

    def close(self):
        self.transport.close()


def private_write(path, body):
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)


def verify(result, transport, raw_directory):
    assert not result["is_synthetic"] and not result["coverage"]["truncated"]
    for source in result["sources"]:
        snapshot = transport.snapshots[source["raw_source_url"]]
        assert digest(snapshot.body) == source["raw_sha256"]
        private_write(raw_directory / (snapshot.sha256 + ".json"), snapshot.body)
    for row in result["records"]:
        snapshot = transport.snapshots[row["source"]["raw_source_url"]]
        assert row["source"]["raw_sha256"] == digest(snapshot.body)
        raw = decode_json(snapshot.body)
        for token in row["source"]["json_pointer"].split("/")[1:]:
            raw = raw[int(token)] if isinstance(raw, list) else raw[token]
        assert numeric(raw) == numeric(row["source_value"])
        if row["value"] is not None:
            assert row["value"] == numeric(raw)
        assert row["published_at"] is None and row["frequency"] == "annual"
    return len(result["records"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/g20-live"))
    parser.add_argument("--countries", default=",".join(G20_AREAS))
    parser.add_argument("--series", default=",".join(GLOBAL_INDICATORS))
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=datetime.now(UTC).year - 1)
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Explicit --allow-network is required.")
    countries, indicators = args.countries.split(","), args.series.split(",")
    if (
        not countries
        or set(countries) - G20_AREAS.keys()
        or len(countries) != len(set(countries))
        or not indicators
        or set(indicators) - GLOBAL_INDICATORS.keys()
        or len(indicators) != len(set(indicators))
        or not 1900 <= args.start_year <= args.end_year <= datetime.now(UTC).year - 1
        or args.end_year - args.start_year > 29
    ):
        parser.error("Choose unique supported countries/indicators and up to 30 completed years.")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.chmod(0o700)
    raw_directory = args.output / "raw"
    raw_directory.mkdir(exist_ok=True, mode=0o700)
    raw_directory.chmod(0o700)
    results = []
    with Atlas("public", directory=args.output / "state") as atlas:
        transport = CaptureTransport(args.output / "state" / "public-cache.sqlite3")
        atlas._research.public = transport
        for country in countries:
            for series in indicators:
                query = {
                    "dataset": "global_economics",
                    "country": country,
                    "series": series,
                    "start_date": f"{args.start_year}-01-01",
                    "end_date": f"{args.end_year}-12-31",
                    "limit": 30,
                }
                try:
                    transport.last_failure = None
                    result = atlas.get_research(query)
                    checked = verify(result, transport, raw_directory)
                    private_write(args.output / f"{country}_{series}.json", canonical(result))
                    usable = [row for row in result["records"] if row["value"] is not None]
                    item = {
                        "country": country,
                        "series": series,
                        "status": "ok",
                        "records": checked,
                        "usable": len(usable),
                        "latest_usable": max((row["date"] for row in usable), default=None),
                        "missing_years": result["coverage"]["missing_years"],
                    }
                except AtlasError as error:
                    item = {
                        "country": country,
                        "series": series,
                        "status": "error",
                        "error": error.code,
                        "message": str(error),
                        "network_diagnostic": transport.last_failure,
                    }
                except AssertionError:
                    item = {
                        "country": country,
                        "series": series,
                        "status": "error",
                        "error": "SOURCE_CELL_MISMATCH",
                    }
                results.append(item)
                print(json.dumps(item), flush=True)
                private_write(args.output / "progress.json", canonical(results))
    summary = {
        "verified_at": datetime.now(UTC).isoformat(),
        "countries": countries,
        "indicators": indicators,
        "start_year": args.start_year,
        "end_year": args.end_year,
        "successful_reads": sum(item["status"] == "ok" for item in results),
        "failed_reads": sum(item["status"] == "error" for item in results),
        "all_null_selections": [
            {"country": item["country"], "series": item["series"]}
            for item in results
            if item["status"] == "ok" and item["usable"] == 0
        ],
        "checked_cells": sum(item.get("records", 0) for item in results),
        "results": results,
    }
    private_write(args.output / "summary.json", canonical(summary))
    print(
        json.dumps({key: value for key, value in summary.items() if key != "results"}), flush=True
    )
    return 1 if summary["failed_reads"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
