"""Opt-in BLS HTML verification; retain actual bytes/results only under private build/."""

import argparse
import csv
import io
import json
import re
from decimal import Decimal, localcontext
from html.parser import HTMLParser
from pathlib import Path

from verify_g20_live import DiagnosticTransport, private_write

from atlas_terminal.client import Atlas
from atlas_terminal.cpi_catalog import ARCHIVE_URL
from atlas_terminal.cpi_sources import cpi_identity, decode_html
from atlas_terminal.errors import AtlasError
from atlas_terminal.util import canonical, digest, stamp, state_dir


class Capture(DiagnosticTransport):
    def __init__(self, directory):
        # Reuse the ordinary private cache without deleting or resetting BLS usage.
        super().__init__(cache_path=state_dir() / "public-cache.sqlite3")
        self.directory = directory
        self.snapshots = {}

    def get(self, url, ttl, **kwargs):
        assert cpi_identity(url), "Only the fixed official BLS CPI document routes are allowed"
        snapshot = super().get(url, ttl, **kwargs)
        self.snapshots[url] = snapshot
        private_write(self.directory / (snapshot.sha256 + ".html"), snapshot.body)
        return snapshot


class CellText(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(text)

    def handle_data(self, data):
        self.parts.append(data)


def check_cell(cell, capture):
    evidence = cell["source"]
    if evidence is None:
        return 0
    snapshot = capture.snapshots[evidence["raw_source_url"]]
    assert digest(snapshot.body) == evidence["raw_sha256"]
    assert snapshot.retrieved_at == evidence["retrieved_at"]
    text = decode_html(snapshot.body)
    fragment = text[evidence["html_start_char"] : evidence["html_end_char"]]
    assert re.match(r"<t[dh](?:\s|>)", fragment, re.I)
    plain = "".join(CellText(fragment).parts)
    assert " ".join(plain.split()) == " ".join(cell["raw_value"].split())
    if cell["value"] is not None:
        numeric = re.sub(r"\(\d+\)", "", plain).replace(",", "").replace("−", "-").strip()
        assert Decimal(numeric) == Decimal(cell["value"])
    return 1


def verify(result, capture):
    assert not result["is_synthetic"]
    checked = 0
    effects = 0
    for row in result["records"]:
        for item in row["records"]:
            for cell in item["reported_inputs"].values():
                checked += check_cell(cell, capture)
            if item["effect_pt"] is not None:
                effects += 1
            calc = item["calculation"]
            if not calc or calc["effect_pt"] is None:
                continue
            with localcontext() as context:
                context.prec = 50
                total, origin = Decimal(0), None
                for step in calc["inputs"]:
                    operands = {
                        key: result["calculation_inputs"][ref["input_id"]]
                        for key, ref in step["items"].items()
                    }
                    own, core = operands[item["category"]], operands["core_cpi"]
                    start = Decimal(core["index_start"]["value"])
                    origin = start if origin is None else origin
                    total += (
                        Decimal(own["relative_importance_pct"]["value"])
                        / Decimal(core["relative_importance_pct"]["value"])
                        * (
                            Decimal(own["index_end"]["value"])
                            / Decimal(own["index_start"]["value"])
                            - 1
                        )
                        * 100
                        * (start / origin)
                    )
                assert total == Decimal(item["effect_pt"])
    for identity, operands in result["calculation_inputs"].items():
        assert digest(canonical(operands)) == identity
        for cell in operands.values():
            checked += check_cell(cell, capture)
    return {
        "verified_numeric_cells": checked,
        "usable_effects": effects,
        "success": effects > 0 and result["status"] == "available",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Explicit --allow-network is required.")
    directory = args.output.resolve()
    if not directory.is_relative_to(Path("build").resolve()):
        parser.error("Actual data and connection logs must stay under private build/.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    raw = directory / "raw"
    raw.mkdir(exist_ok=True, mode=0o700)
    raw.chmod(0o700)
    cases = {
        "latest_mom_sa": {},
        "latest_yoy_nsa": {"change": "yoy", "seasonal_adjustment": "nsa"},
        "historical_mom_sa": {"start_month": "2024-08", "end_month": "2024-08"},
        "reweighting_yoy_nsa": {
            "start_month": "2024-12",
            "end_month": "2025-02",
            "change": "yoy",
            "seasonal_adjustment": "nsa",
        },
        "core_reweighting_mom_nsa": {
            "start_month": "2024-12",
            "end_month": "2025-02",
            "target": "core",
            "seasonal_adjustment": "nsa",
        },
        "core_reweighting_yoy_nsa": {
            "start_month": "2025-02",
            "end_month": "2025-02",
            "target": "core",
            "seasonal_adjustment": "nsa",
            "change": "yoy",
        },
    }
    summary = {
        "started_at": stamp(),
        "is_synthetic": False,
        "cases": {},
        "api_v1_requests": 0,
        "actual_source_verification_success": False,
    }
    rows = []
    with Atlas("public", directory=directory / "views") as atlas:
        capture = Capture(raw)
        atlas._research.public = capture
        try:
            capture.get(ARCHIVE_URL, 21600)
        except AtlasError as exc:
            summary["discovery_failure"] = exc.envelope()["error"]
            summary["cases"] = {
                name: {
                    "query": {"dataset": "cpi_effects", **options},
                    "status": "not_attempted_archive_unavailable",
                }
                for name, options in cases.items()
            }
        else:
            for name, options in cases.items():
                query = {"dataset": "cpi_effects", **options}
                try:
                    result = atlas.get_research(query)
                    private_write(directory / (name + ".json"), canonical(result))
                    checks = verify(result, capture)
                    summary["cases"][name] = {
                        "query": query,
                        "status": result["status"],
                        **checks,
                        "coverage": result["coverage"],
                    }
                    for row in result["records"]:
                        for item in row["records"]:
                            rows.append(
                                {
                                    key: item[key]
                                    for key in (
                                        "month",
                                        "category",
                                        "target",
                                        "change",
                                        "seasonal_adjustment",
                                        "rate_pct",
                                        "effect_pt",
                                        "relative_importance_pct",
                                        "weight_base_month",
                                    )
                                }
                            )
                except (AtlasError, AssertionError) as exc:
                    summary["cases"][name] = {
                        "query": query,
                        "status": "verification_failed",
                        "error": exc.envelope()
                        if isinstance(exc, AtlasError)
                        else "Original-cell or independent formula check failed.",
                    }
        summary["diagnostics"] = capture.diagnostics
        summary["source_manifest"] = [
            {"url": s.url, "sha256": s.sha256, "retrieved_at": s.retrieved_at}
            for s in capture.snapshots.values()
        ]
    if rows:
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        private_write(directory / "observations.csv", stream.getvalue().encode())
    summary["actual_source_verification_success"] = all(
        item.get("success", False) for item in summary["cases"].values()
    )
    summary["finished_at"] = stamp()
    private_write(directory / "summary.json", canonical(summary))
    print(
        json.dumps(
            {
                "success": summary["actual_source_verification_success"],
                "cases": {key: value["status"] for key, value in summary["cases"].items()},
                "summary": str(directory / "summary.json"),
            }
        )
    )
    return 0 if summary["actual_source_verification_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
