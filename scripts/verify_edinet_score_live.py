"""Opt-in EDINET scoring audit. Real sources and results remain private in build/."""

import argparse
import json
import re
from decimal import Decimal, localcontext
from pathlib import Path
from xml.etree import ElementTree as ET

from verify_g20_live import private_write

from atlas_terminal.buffett_models import ScoreValuation
from atlas_terminal.client import Atlas
from atlas_terminal.edinet_extensions import exact_text
from atlas_terminal.errors import AtlasError
from atlas_terminal.filing_html import FilingHTML
from atlas_terminal.japan_company_sources import JPX_LIST
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_buffett import CONTEXT
from atlas_terminal.source_archives import bounded_zip, member_bytes
from atlas_terminal.source_deadline import source_deadline
from atlas_terminal.util import canonical, digest, stamp


class Capture(PublicTransport):
    def __init__(self, output):
        super().__init__(cache_path=output / "state/public-cache.sqlite3")
        self.output, self.snapshots = output, {}

    def get(self, url, ttl, **kwargs):
        assert url == JPX_LIST or url.startswith("https://api.edinet-fsa.go.jp/api/v2/documents")
        snapshot = super().get(url, ttl, **kwargs)
        self.snapshots[url] = snapshot
        private_write(self.output / "raw" / (snapshot.sha256 + ".bin"), snapshot.body)
        return snapshot


def resolve_xml(source, capture):
    snapshot = capture.snapshots[source["raw_source_url"]]
    assert snapshot.sha256 == source["raw_sha256"]
    with bounded_zip(snapshot.body) as archive:
        body = member_bytes(archive, source["archive_member"])
    assert digest(body) == source["member_sha256"]
    root = ET.fromstring(body)
    node = root
    for index in re.findall(r"/\*\[(\d+)\]", source["xml_local_path"]):
        node = node[int(index) - 1]
    if source.get("expanded_element"):
        assert node.tag == source["expanded_element"]
    return node


def audit(result, capture):
    assert not result["is_synthetic"] and result["input_origin"] == "edinet_primary_annual_facts"
    checked, numeric, notes = 0, 0, 0
    for cell in result["records"][0]["reported_inputs"].values():
        if cell["value"] is None:
            continue
        checked += 1
        for fact in cell["fact_inputs"]:
            node = resolve_xml(fact["source"], capture)
            assert node.text == fact["raw_value"] and Decimal(node.text) == Decimal(fact["value"])
            assert (
                node.get("contextRef") == fact["context_id"]
                and node.get("unitRef") == fact["unit_id"]
            )
            for mapping in fact.get("mapping_evidence", []):
                label = resolve_xml(mapping, capture)
                assert "".join(label.itertext()) == mapping["label"]
                resolve_xml(mapping["schema"], capture)
            numeric += 1
        if cell["evidence_type"] == "numeric_xbrl":
            values = []
            for operand in cell["normalization"]["operands"]:
                originals = {
                    Decimal(resolve_xml(source, capture).text)
                    for source in operand["source_locations"]
                }
                assert len(originals) == 1 and next(iter(originals)) == Decimal(operand["value"])
                values.append(next(iter(originals)))
            total = sum(values, Decimal(0))
            if cell["metric"] == "capital_expenditure":
                assert all(value <= 0 for value in values)
                total = -total
            assert total == Decimal(cell["value"])
        else:
            for note in cell["note_inputs"]:
                node = resolve_xml(note["source"], capture)
                html = node.text or ""
                assert digest(html.encode()) == note["source"]["text_block_sha256"]
                parser = FilingHTML(html)
                parser.feed(html)
                parser.close()
                if note["evidence_type"] == "xbrl_note_text":
                    text = exact_text("".join(part[0] for part in parser.parts))
                    source = note["source"]
                    assert (
                        text[source["normalized_text_start"] : source["normalized_text_end"]]
                        == source["quote"]
                    )
                    assert Decimal(note["raw_value"].replace(",", "")) * 1_000_000 == Decimal(
                        cell["value"]
                    )
                else:
                    values = []
                    for operand in note["operands"]:
                        source = operand["source"]
                        table = parser.tables[source["table_index"] - 1]
                        cells = [
                            item
                            for item in table["cells"]
                            if item["row_index"] == source["row_index"]
                            and item["cell_index"] == source["cell_index"]
                        ]
                        assert len(cells) == 1 and cells[0]["text"] == operand["raw_value"]
                        raw = exact_text(cells[0]["text"])
                        values.append(Decimal(raw.removeprefix("△").replace(",", "")) * 1_000_000)
                    assert sum(values) == Decimal(cell["value"])
                notes += 1
    return {"matched_inputs": checked, "numeric_fact_locations": numeric, "note_locations": notes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--years", type=int, choices=range(3, 11), default=3)
    parser.add_argument("--submission-date", action="append", default=[])
    parser.add_argument("--valuation-input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    if not output.is_relative_to(root / "build") or len(set(args.submission_date)) > 31:
        parser.error("Use private build/ output and at most 31 explicit submission dates.")
    if output.exists():
        parser.error("Use a new output directory; existing source caches are never reset.")
    for path in (output, output / "raw", output / "state"):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    valuation = None
    if args.valuation_input:
        if args.valuation_input.stat().st_size > 1024 * 1024:
            parser.error("Valuation input exceeds 1 MiB.")
        valuation = ScoreValuation.model_validate_json(
            args.valuation_input.read_bytes()
        ).model_dump()
    report = {
        "status": "NOT_RUN",
        "checked_at": stamp(),
        "identifier": args.identifier,
        "years": args.years,
        "index": [],
        "passes": [],
        "point_in_time": False,
    }
    capture = Capture(output)
    try:
        with Atlas("public", directory=output / "state") as atlas:
            atlas._research.public = capture
            with source_deadline(90):
                for day in sorted(set(args.submission_date)):
                    report["index"].append(atlas.sync_edinet_history(day, day))
            for name in ("cold_financial_cache", "warm_financial_cache"):
                result = atlas.get_buffett_score(args.identifier, country="JP", years=args.years)
                private_write(output / (name + ".json"), canonical(result))
                with localcontext(CONTEXT):
                    matched = audit(result, capture)
                report["passes"].append(
                    {
                        "pass": name,
                        **matched,
                        "source_coverage": result["source_coverage"],
                        "quality_score": result["records"][0]["quality_score"],
                    }
                )
            if valuation is not None:
                result = atlas.get_buffett_score(
                    args.identifier, country="JP", years=args.years, valuation=valuation
                )
                private_write(output / "valued.json", canonical(result))
            score = result["records"][0]
            report["status"] = (
                "PASS"
                if (
                    matched["matched_inputs"] > 0
                    and score["quality_score"] is not None
                    and (valuation is None or score["score"] is not None)
                )
                else "INCOMPLETE_INPUTS"
            )
            report["score"] = score["score"]
            report["missing_inputs"] = score["missing_inputs"]
            report["latest_amendment_guaranteed"] = False
    except AtlasError as error:
        report.update(status="BLOCKED", error_code=error.code, reason=error.details.get("reason"))
    except (AssertionError, ValueError, KeyError):
        report.update(status="AUDIT_FAILED")
    finally:
        capture.close()
    private_write(output / "report.json", canonical(report))
    print(
        json.dumps(
            {key: report.get(key) for key in ("status", "identifier", "error_code", "score")}
        )
    )
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
