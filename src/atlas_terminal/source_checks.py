"""Opt-in official-source probes with bounded, private last-success baselines."""

import json
import os
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.fixed_income_sources import JGB_URLS, parse_jgb_csv
from atlas_terminal.providers import (
    CachedHTTPTransport,
    SECProvider,
    SecTransport,
    Snapshot,
    Transport,
)
from atlas_terminal.public_sources import SOURCE_CATALOG, PublicTransport, xml_root
from atlas_terminal.regional_bond_sources import ECB_CURVE_BASE, source_csv
from atlas_terminal.research_quality import (
    ResearchRequirements,
    assess_quality,
    enforce_requirements,
    leaf_records,
)
from atlas_terminal.util import Database, canonical, digest, stamp, state_dir

PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "ecb_aaa_curve": ("ecb", {"dataset": "euro_area_yields", "series": "aaa_spot", "limit": 11}),
    "ecb_all_curve": ("ecb", {"dataset": "euro_area_yields", "series": "all_spot", "limit": 11}),
    "rba_curve": ("rba", {"dataset": "australia_yields", "limit": 4}),
    "jgb_curve": ("japan_mof", {"dataset": "jgb_yields", "limit": 15}),
    "imf_catalog": ("imf", {"dataset": "sdmx_catalog", "source_id": "imf", "limit": 1}),
    "oecd_catalog": ("oecd", {"dataset": "sdmx_catalog", "source_id": "oecd", "limit": 1}),
    "sec_profile": ("sec", {"dataset": "profile", "identifier": "AAPL"}),
    "bls_cpi": ("bls", {"dataset": "economics", "series": "cpi", "limit": 5}),
    "ecb_fx": (
        "ecb",
        {
            "dataset": "fx_rates",
            "base_currency": "USD",
            "quote_currency": "JPY",
            "limit": 1,
        },
    ),
    "nyfed_effr": ("new_york_fed", {"dataset": "reference_rates", "series": "effr", "limit": 1}),
    "nyfed_sofr": ("new_york_fed", {"dataset": "reference_rates", "series": "sofr", "limit": 1}),
    "treasury_nominal": ("treasury", {"dataset": "treasury_yields", "limit": 20}),
    "treasury_real": ("treasury", {"dataset": "treasury_real_yields", "limit": 10}),
    "treasury_spread": (
        "treasury",
        {
            "dataset": "macro_analytics",
            "series": "yield_spread_10y_2y",
            "limit": 2,
        },
    ),
    "federal_debt": ("fiscaldata", {"dataset": "federal_debt", "limit": 5}),
    "fed_releases": ("federal_reserve", {"dataset": "policy_news", "limit": 5}),
    "bea_gdp": ("bea", {"dataset": "national_accounts", "series": "gdp_real", "limit": 1}),
    "bea_pce": (
        "bea",
        {"dataset": "national_accounts", "series": "core_pce_price_index", "limit": 1},
    ),
    "boj_call_rate": ("boj", {"dataset": "japan_economics", "series": "call_rate", "limit": 1}),
    "boj_tankan": (
        "boj",
        {"dataset": "japan_economics", "series": "tankan_manufacturing", "limit": 1},
    ),
    "estat_cpi": ("estat", {"dataset": "japan_economics", "series": "cpi", "limit": 1}),
    "estat_unemployment": (
        "estat",
        {"dataset": "japan_economics", "series": "unemployment", "limit": 1},
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_check_baselines (
 probe_id TEXT PRIMARY KEY, body BLOB NOT NULL, sha256 TEXT NOT NULL
);
"""


class RecordingTransport:
    def __init__(self, transport: Transport, snapshots: list[Snapshot]) -> None:
        self.transport, self.snapshots = transport, snapshots
        self.synthetic = transport.synthetic
        self.run_cache: dict[str, Snapshot] = {}

    def get(self, url: str, ttl: int) -> Snapshot:
        if url not in self.run_cache:
            self.run_cache[url] = (
                self.transport.get(url, ttl, force_refresh=True)
                if isinstance(self.transport, CachedHTTPTransport)
                else self.transport.get(url, ttl)
            )
        snapshot = self.run_cache[url]
        self.snapshots.append(snapshot)
        return snapshot

    def close(self) -> None:
        self.transport.close()


def check_client(directory: Path, snapshots: list[Snapshot]) -> Atlas:
    atlas = Atlas("public", directory=directory)
    atlas._research.public = RecordingTransport(
        PublicTransport(cache_path=directory / "public-cache.sqlite3"),
        snapshots,
    )

    def sec_source() -> SECProvider:
        if atlas.research_source is None:
            atlas.research_source = SECProvider(
                RecordingTransport(
                    SecTransport(
                        os.environ.get("SEC_USER_AGENT", ""),
                        cache_path=directory / "sec-cache.sqlite3",
                    ),
                    snapshots,
                )
            )
        return atlas.research_source

    atlas._research.sec_factory = sec_source
    return atlas


def distinct_shapes(values: list[Any]) -> list[Any]:
    return [value for _, value in sorted({canonical(value): value for value in values}.items())]


def json_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 8:
        return "bounded_depth"
    if isinstance(value, dict):
        if value and all(key.isdigit() for key in value):
            return {
                "record_map": distinct_shapes(
                    [json_shape(item, depth + 1) for item in value.values()]
                )
            }
        keys = sorted(value)[:256]
        return {
            "object": {key: json_shape(value[key], depth + 1) for key in keys},
            "additional_fields": len(value) > len(keys),
        }
    if isinstance(value, list):
        return {"array": distinct_shapes([json_shape(item, depth + 1) for item in value])}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return "string" if isinstance(value, str) else "number"


def xml_shape(node: Any, depth: int = 0) -> dict[str, Any]:
    return {
        "tag": node.tag,
        "attributes": sorted(node.attrib),
        "children": distinct_shapes([xml_shape(child, depth + 1) for child in node])
        if depth < 8
        else "bounded_depth",
    }


def source_shapes(snapshots: list[Snapshot]) -> list[dict[str, Any]]:
    rows = {}
    for snapshot in snapshots:
        is_xml = snapshot.body.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"<")
        is_csv = snapshot.url.endswith(".csv") or snapshot.url.startswith(ECB_CURVE_BASE)
        if is_csv:
            if snapshot.url in JGB_URLS or snapshot.url.endswith(
                ("jgb_current.csv", "jgb_history.csv")
            ):
                observations, _ = parse_jgb_csv(snapshot)
                first = next(iter(observations.values()), None)
                header = list(first.headers) if first else []
            else:
                header = next(
                    (row for row in source_csv(snapshot) if row and row[0] in {"KEY", "Series ID"}),
                    [],
                )
            structure = {"columns": header, "units": "percent"}
        else:
            structure = xml_shape(xml_root(snapshot.body)) if is_xml else json_shape(snapshot.data)
        rows[snapshot.url] = {
            "raw_source_url": snapshot.url,
            "raw_sha256": snapshot.sha256,
            "retrieved_at": snapshot.retrieved_at,
            "format": "csv" if is_csv else "xml" if is_xml else "json",
            "shape_sha256": digest(canonical(structure)),
        }
    return [rows[url] for url in sorted(rows)]


def compare_baseline(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, bool | None]:
    def source_values(record: dict[str, Any], field: str) -> dict[str, str]:
        return {row["raw_source_url"]: row[field] for row in record["sources"]}

    previous = old["latest_observation_date"] if old else None
    latest = new["latest_observation_date"]
    return {
        "raw_bytes_changed": source_values(old, "raw_sha256") != source_values(new, "raw_sha256")
        if old
        else None,
        "shape_changed": source_values(old, "shape_sha256") != source_values(new, "shape_sha256")
        if old
        else None,
        "latest_observation_changed": previous != latest if old else None,
        "latest_observation_regressed": latest < previous if latest and previous else None,
    }


def check_sources(
    sources: list[str] | None = None,
    *,
    allow_network: bool = False,
    directory: Path | None = None,
    max_observation_lag_days: int | None = None,
    client_factory: Callable[[Path, list[Snapshot]], Atlas] = check_client,
) -> dict[str, Any]:
    if not isinstance(allow_network, bool):
        raise AtlasError("INVALID_INPUT", "Network opt-in must be a boolean.")
    selected = list(SOURCE_CATALOG) if sources is None else sources
    if not selected or len(set(selected)) != len(selected) or set(selected) - SOURCE_CATALOG.keys():
        raise AtlasError("INVALID_INPUT", "Choose unique source IDs from the research catalog.")
    if max_observation_lag_days is not None and (
        isinstance(max_observation_lag_days, bool)
        or not isinstance(max_observation_lag_days, int)
        or not 0 <= max_observation_lag_days <= 3650
    ):
        raise AtlasError("INVALID_INPUT", "Observation lag must be 0–3650 calendar days.")
    probes = {key: value for key, value in PROBES.items() if value[0] in selected}
    results: list[dict[str, Any]] = []
    if not allow_network:
        results = [
            {
                "probe_id": key,
                "source_id": source,
                "status": "NOT_RUN",
                "reason": "explicit_allow_network_required",
            }
            for key, (source, _) in probes.items()
        ]
    else:
        root = directory or state_dir()
        store = Database((root / "source-checks.sqlite3").resolve(), SCHEMA)
        snapshots: list[Snapshot] = []
        with client_factory(root, snapshots) as atlas:
            for key, (source, query) in probes.items():
                snapshots.clear()
                started = time.monotonic()
                record: dict[str, Any] = {
                    "probe_id": key,
                    "source_id": source,
                    "checked_at": stamp(),
                    "source_terms_url": SOURCE_CATALOG[source]["terms"],
                }
                try:
                    data = atlas.get_research(query)
                    if data["is_synthetic"]:
                        record.update(status="NOT_RUN", reason="synthetic_result")
                    else:
                        values = [
                            row["value"] for row in leaf_records(data["records"]) if "value" in row
                        ]
                        if not data["records"] or (
                            values and all(value is None for value in values)
                        ):
                            raise AtlasError(
                                "INCOMPLETE_COVERAGE",
                                "Probe returned no available observations.",
                            )
                        requirements = ResearchRequirements(
                            max_source_age_seconds=3600,
                            min_observation_date=(
                                date.today() - timedelta(days=max_observation_lag_days)
                            ).isoformat()
                            if max_observation_lag_days is not None
                            else None,
                        )
                        enforce_requirements(data, requirements)
                        quality = assess_quality(data)
                        record.update(
                            status="PASS",
                            is_synthetic=False,
                            latest_observation_date=quality.latest_observation_date,
                            returned_records=len(data["records"]),
                            sources=source_shapes(snapshots),
                        )
                        if not record["sources"]:
                            raise AtlasError(
                                "SOURCE_UNAVAILABLE", "Probe did not capture source bytes."
                            )
                        with store.connect(write=True) as conn:
                            row = conn.execute(
                                "SELECT * FROM source_check_baselines WHERE probe_id=?", (key,)
                            ).fetchone()
                            if row and digest(row["body"]) != row["sha256"]:
                                raise AtlasError(
                                    "LEDGER_UNAVAILABLE",
                                    "Source-check baseline failed integrity verification.",
                                )
                            old = json.loads(row["body"]) if row else None
                            record["changes"] = compare_baseline(old, record)
                            body = canonical(record)
                            conn.execute(
                                "INSERT OR REPLACE INTO source_check_baselines VALUES (?,?,?)",
                                (key, body, digest(body)),
                            )
                except AtlasError as exc:
                    record = {
                        key_: value
                        for key_, value in record.items()
                        if key_
                        in {
                            "probe_id",
                            "source_id",
                            "checked_at",
                            "source_terms_url",
                        }
                    }
                    record.update(
                        status="NOT_RUN" if exc.code == "CONFIG_REQUIRED" else "FAIL",
                        error=exc.envelope()["error"],
                    )
                except Exception:
                    failure = AtlasError(
                        "INTERNAL_ERROR", "Source probe stopped due to an internal error."
                    )
                    record.update(status="FAIL", error=failure.envelope()["error"])
                record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                results.append(record)
    statuses = [row["status"] for row in results]
    return {
        "schema_version": "0.2.0",
        "checked_at": stamp(),
        "scope": "opt_in_read_only_source_checks",
        "status": "FAIL" if "FAIL" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "PASS",
        "checks": results,
        "real_transfer": False,
        "limitations": [
            "PASS validates only the selected current responses, "
            "not continuous availability or source terms.",
            "Shape changes are signals, not proof of a breaking change; "
            "values and list lengths are excluded.",
            "Observation lag is an optional calendar-day condition, "
            "without a release/holiday calendar.",
        ],
    }
