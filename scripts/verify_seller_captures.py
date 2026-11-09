"""Replay retained real BLS captures through the seller with fictional payment, never network."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from atlas_terminal.errors import AtlasError
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import BLS_SERIES
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_models import ProductQuery, ProductQuote
from atlas_terminal.seller_server import create_seller_app
from atlas_terminal.util import canonical, digest

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_bytes(canonical(value))
    path.chmod(0o600)


def verify(capture, output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        raise ValueError("Private output must be under ignored build/")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    before = {
        str(p.relative_to(capture)): digest(p.read_bytes())
        for p in capture.rglob("*")
        if p.is_file()
    }
    manifest = json.loads((capture / "manifest.json").read_bytes())
    checks, total = [], 0
    for index, name in enumerate(manifest["captures"]):
        saved = json.loads((capture / name).read_bytes())
        query = saved["query"]
        source = saved["result"]["sources"][0]
        raw = (capture / "raw" / (source["raw_sha256"] + ".json")).read_bytes()
        assert digest(raw) == source["raw_sha256"]
        original = json.loads(raw)
        library = ProductLibrary(output / f"case-{index:02d}")
        data = library.publish(
            query["series"],
            Snapshot(raw, source["raw_source_url"], source["retrieved_at"]),
            source_access="retained_capture",
            start_date=query.get("start_date"),
            end_date=query.get("end_date"),
        )
        matches = 0
        for row in data.research.records:
            value = original
            for part in row["source"]["json_pointer"].split("/")[1:]:
                value = value[int(part)] if isinstance(value, list) else value[part]
            expected = None if value["value"] in ("-", "", None) else Decimal(value["value"])
            assert (None if row["value"] is None else Decimal(row["value"])) == expected
            assert row["year"] == value["year"] and row["period"] == value["period"]
            assert row["unit"] == BLS_SERIES[query["series"]][2]
            assert row["source"]["raw_sha256"] == source["raw_sha256"]
            assert row["source"]["retrieved_at"] == source["retrieved_at"]
            matches += 1
        default_age = "available"
        try:
            library.get(
                ProductQuery(series=query["series"]), max_age_seconds=172800, allow_synthetic=False
            )
        except AtlasError as exc:
            default_age = exc.code
        # Replay old captures offline without changing production's 48-hour default.
        app = create_seller_app(
            directory=library.directory, payments="mock", max_source_age_seconds=366 * 86400
        )
        with TestClient(app, base_url="http://127.0.0.1:8000") as http:
            response = http.post("/v1/seller/quotes", json={"series": query["series"]})
            assert response.status_code == 200
            quote = ProductQuote.model_validate(response.json())
            token = response.headers["Atlas-Recovery-Token"]
            proof = MockAdapter.sign(quote, "http://127.0.0.1:8000")
            purchased = http.get(quote.resource_path, headers={"Atlas-Mock-Payment": proof})
            assert purchased.status_code == 200
            result = purchased.json()
            assert result["receipt"]["simulated"] and result["receipt"]["transaction"] is None
            assert not result["data"]["is_synthetic"]
            assert digest(canonical(result["data"])) == quote.result_sha256
            assert (
                http.get(quote.resource_path, headers={"Authorization": "Bearer " + token}).json()
                == result
            )
            assert http.get(quote.resource_path).status_code == 401
            assert http.get("/v1/research").status_code == 404
            assert app.state.orders.adapter.settlements == 1
            write(output / f"result-{index:02d}.json", result)
        total += matches
        checks.append(
            {
                "series": query["series"],
                "source_capture": name,
                "captured_at": data.captured_at,
                "source_sha256": source["raw_sha256"],
                "result_sha256": quote.result_sha256,
                "matched_records": matches,
                "default_freshness_check": default_age,
            }
        )
    after = {
        str(p.relative_to(capture)): digest(p.read_bytes())
        for p in capture.rglob("*")
        if p.is_file()
    }
    assert before == after
    report = {
        "status": "PASS",
        "scope": "offline retained-source replay with fictional settlement",
        "captures": len(checks),
        "matched_records": total,
        "source_files_preserved": len(before),
        "new_source_requests": 0,
        "real_transfers": 0,
        "live_facilitator_verified": False,
        "checks": checks,
    }
    write(output / "report.json", report)
    return {key: value for key, value in report.items() if key != "checks"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=ROOT / "docs/evidence/bls-2026-10-27")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.capture, args.output)))


if __name__ == "__main__":
    main()
