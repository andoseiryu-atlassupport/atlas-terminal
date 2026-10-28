"""Explicit private live checks for macro coverage, Yahoo boundaries and real risk inputs."""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from verify_yahoo_live import verify_actions, verify_prices

from atlas_terminal.activity_catalog import ACTIVITY_SERIES
from atlas_terminal.client import Atlas
from atlas_terminal.economic_catalog import JAPAN_SERIES, NATIONAL_ACCOUNTS
from atlas_terminal.equity_sources import TOKYO, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.regional_economic_catalog import EUROPE_SERIES, UK_SERIES
from atlas_terminal.source_checks import PROBES
from atlas_terminal.util import canonical, stamp, state_dir


class CaptureTransport(PublicTransport):
    def __init__(self, output):
        super().__init__(cache_path=state_dir() / "public-cache.sqlite3")
        self.output, self.snapshots, self.failures, self.blocked_hosts = output, {}, [], set()

    def get(self, url, ttl):
        host = urlsplit(url).hostname
        if host in self.blocked_hosts:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Host already denied by the normal proxy in this run."
            )
        try:
            snapshot = super().get(url, ttl)
        except AtlasError:
            self.failures.append({"host": host, "at": stamp()})
            raise
        self.snapshots[url] = snapshot
        (self.output / (snapshot.sha256 + ".bin")).write_bytes(snapshot.body)
        return snapshot

    def request(self, url, headers):
        from contextlib import contextmanager

        @contextmanager
        def observed():
            try:
                with super(CaptureTransport, self).request(url, headers) as response:
                    yield response
            except httpx.ProxyError as error:
                host = urlsplit(url).hostname
                self.blocked_hosts.add(host)
                self.failures.append(
                    {"host": host, "type": "httpx.ProxyError", "error": str(error), "at": stamp()}
                )
                raise

        return observed()


def macro_queries():
    queries = {
        key: query
        for key, (source, query) in PROBES.items()
        if source not in {"bls", "sec", "yahoo_finance"}
    }
    for dataset, registry in (
        ("japan_economics", JAPAN_SERIES),
        ("national_accounts", NATIONAL_ACCOUNTS),
        ("europe_economics", EUROPE_SERIES),
        ("uk_economics", UK_SERIES),
        ("activity_economics", ACTIVITY_SERIES),
    ):
        for name in registry:
            queries[f"{dataset}_{name}"] = {"dataset": dataset, "series": name, "limit": 5}
    return queries


def run(output, selection):
    output.mkdir(parents=True, exist_ok=True)
    report = {"started_at": stamp(), "is_synthetic": False, "checks": [], "status": "in_progress"}
    today = datetime.now(TOKYO).date().isoformat()
    transport = CaptureTransport(output)
    with Atlas("public") as atlas:
        atlas._research.public = transport
        queries = macro_queries() if selection in {"all", "macro"} else {}
        if selection in {"all", "yahoo"}:
            queries.update(
                {
                    "sony_split_2024": {
                        "dataset": "equity_prices",
                        "identifier": "6758.T",
                        "start_date": "2024-09-01",
                        "end_date": "2024-10-31",
                        "limit": 500,
                    },
                    "toyota_split_2021": {
                        "dataset": "equity_prices",
                        "identifier": "7203.T",
                        "start_date": "2021-09-01",
                        "end_date": "2021-10-31",
                        "limit": 500,
                    },
                    "recent_equity_nulls": {
                        "dataset": "equity_prices",
                        "identifier": "7203.T",
                        "start_date": "2026-01-01",
                        "end_date": today,
                        "limit": 500,
                    },
                    "topix_etf": {"dataset": "equity_prices", "identifier": "1306.T", "limit": 500},
                    "less_active_etf": {
                        "dataset": "equity_prices",
                        "identifier": "2843.T",
                        "limit": 500,
                    },
                }
            )
        for name, query in queries.items():
            entry = {"name": name, "query": query, "status": "FAIL"}
            try:
                result = atlas.get_research(query)
                assert not result["is_synthetic"]
                if query["dataset"] == "equity_prices":
                    url = yahoo_url(
                        query["identifier"], query.get("start_date"), query.get("end_date")
                    )
                    snapshot = transport.get(url, 900)
                    verify_prices(result, snapshot.body)
                    actions = atlas.get_research(query | {"dataset": "equity_actions"})
                    verify_actions(actions, snapshot.body, query)
                    entry.update(
                        splits=sum(r["action_type"] == "split" for r in actions["records"]),
                        zero_volumes=sum(r["volume"] == "0" for r in result["records"]),
                        null_prices=sum(r["adjusted_close"] is None for r in result["records"]),
                    )
                    (output / f"{name}-actions.json").write_bytes(canonical(actions))
                entry.update(
                    status="PASS", records=len(result["records"]), sources=result["sources"]
                )
                (output / f"{name}.json").write_bytes(canonical(result))
            except AtlasError as error:
                entry.update(
                    status="CONFIG_REQUIRED" if error.code == "CONFIG_REQUIRED" else "FAIL",
                    error=error.envelope()["error"],
                )
            report["checks"].append(entry)
            (output / "report.json").write_bytes(canonical(report))
        if selection in {"all", "yahoo"}:
            from atlas_terminal.yahoo_risk import YahooRiskConnector, YahooRiskSpec

            spec = YahooRiskSpec(
                connection_id="yahoo_jp",
                kind="yahoo_finance",
                symbols=["7203.T", "6758.T"],
                factor_symbol="1306.T",
                start_date=f"{today[:4]}-01-01",
                end_date=(datetime.now(TOKYO).date() - timedelta(days=2)).isoformat(),
                personal_use_acknowledged=True,
            )
            connector = YahooRiskConnector(spec, state_dir())
            atlas.register_risk_connection("yahoo_jp", connector)
            entry = {"name": "yahoo_time_series_risk", "status": "FAIL"}
            try:
                # Capture first, then choose an as_of that can legitimately include the captures.
                connector.read()
                result = atlas.risk(
                    {
                        "action": "run",
                        "connection_id": "yahoo_jp",
                        "allow_network": True,
                        "fit": {"method": "time_series", "as_of": stamp(), "lookback": 120},
                        "positions": [
                            {"asset_id": "7203.T", "weight": "0.5"},
                            {"asset_id": "6758.T", "weight": "0.5"},
                        ],
                    }
                )
                (output / "yahoo-risk.json").write_bytes(canonical(result))
                entry.update(status="PASS", result=result)
            except AtlasError as error:
                entry["error"] = error.envelope()["error"]
            report["checks"].append(entry)
    report.update(
        completed_at=stamp(),
        transport_failures=transport.failures,
        blocked_hosts=sorted(transport.blocked_hosts),
        status="PASS" if all(e["status"] == "PASS" for e in report["checks"]) else "INCOMPLETE",
    )
    (output / "report.json").write_bytes(canonical(report))
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": [{"name": e["name"], "status": e["status"]} for e in report["checks"]],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--selection", choices=["all", "macro", "yahoo"], default="all")
    parser.add_argument("--output", type=Path, default=Path("build/data-live"))
    arguments = parser.parse_args()
    if not arguments.allow_network:
        parser.error("--allow-network is required")
    raise SystemExit(run(arguments.output, arguments.selection))
