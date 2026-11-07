"""Opt-in private JPX daily margin reads, original layout cells and exact ratio checks."""

import argparse
import csv
import io
from fractions import Fraction
from pathlib import Path

from verify_g20_live import DiagnosticTransport, private_write

from atlas_terminal.client import Atlas
from atlas_terminal.japan_company_sources import jpx_identifier
from atlas_terminal.margin_sources import MARGIN_INDEX, margin_identity, margin_reports
from atlas_terminal.research_margin import extract_margin_pages
from atlas_terminal.util import canonical, stamp, state_dir


class Capture(DiagnosticTransport):
    def __init__(self, *, raw_directory):
        super().__init__(cache_path=state_dir() / "public-cache.sqlite3")
        self.raw_directory, self.snapshots = raw_directory, {}

    def get(self, url, ttl, **kwargs):
        assert margin_identity(url) is not None, "Only fixed JPX margin endpoints are allowed"
        snapshot = super().get(url, ttl, **kwargs)
        self.snapshots[url] = snapshot
        extension = ".html" if url == MARGIN_INDEX else ".pdf"
        private_write(self.raw_directory / (snapshot.sha256 + extension), snapshot.body)
        return snapshot


def verify(result, capture):
    assert result["dataset"] == "margin_balances" and not result["is_synthetic"]
    assert result["source_id"] == "jpx" and not result["coverage"]["partial"]
    assert not result["coverage"]["point_in_time"] and len(result["records"]) == 1
    row = result["records"][0]
    assert row["published_at"] is None and row["frequency"] == "daily"
    assert row["publication_date"] >= row["date"]
    count = 0
    for kind in ("shares", "amount_jpy"):
        table = row[kind]
        assert table["unit"] == ("share" if kind == "shares" else "JPY")
        for field, locator in table["field_sources"].items():
            snapshot = capture.snapshots[locator["raw_source_url"]]
            assert snapshot.sha256 == locator["raw_sha256"]
            assert snapshot.retrieved_at == locator["retrieved_at"]
            pages = extract_margin_pages(snapshot.body)
            line = pages[locator["pdf_page"] - 1]["text"].splitlines()[locator["text_line"] - 1]
            text = line[locator["text_column_start"] - 1 : locator["text_column_end"]]
            assert text == locator["reported_text"]
            if field.endswith("_pct"):
                expected = None if text in {"-", "*"} else text.removesuffix("%")
            else:
                expected = (
                    None
                    if text in {"-", "*"}
                    else str(int(text.replace(",", "").replace("▲", "-").replace(" ", "")))
                )
            assert table[field] == expected
            count += 1
        for side in ("sell", "buy"):
            for measure in ("balance", "change"):
                assert int(table[f"{side}_{measure}"]) == (
                    int(table[f"general_{side}_{measure}"])
                    + int(table[f"standardized_{side}_{measure}"])
                )
    ratio = row["margin_ratio"]
    assert ratio["formula"] == "buy_balance_shares / sell_balance_shares"
    for field, operand in ratio["inputs"].items():
        assert operand["value"] == row["shares"][field] and operand["unit"] == "share"
        assert operand["source"] == row["shares"]["field_sources"][field]
    buy, sell = (int(row["shares"][field]) for field in ("buy_balance", "sell_balance"))
    if sell == 0:
        assert ratio["value"] is None and ratio["unavailable_reason"] == "zero_sell_balance"
    else:
        exact = Fraction(buy, sell)
        numerator, remainder = divmod(exact.numerator * 10**18, exact.denominator)
        if 2 * remainder > exact.denominator or (
            2 * remainder == exact.denominator and numerator % 2 == 1
        ):
            numerator += 1
        expected = f"{numerator // 10**18}.{numerator % 10**18:018}"
        assert ratio["value"] == expected and ratio["unavailable_reason"] is None
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/margin-live"))
    parser.add_argument("--identifiers", default="7203.T,7267.T,6758.T")
    parser.add_argument("--report-count", type=int, default=1)
    args = parser.parse_args()
    identifiers = args.identifiers.split(",")
    if not 1 <= args.report_count <= 5 or not 1 <= len(identifiers) <= 10:
        parser.error("Choose one to five linked reports and one to ten exact JPX identifiers.")
    for identifier in identifiers:
        jpx_identifier(identifier)
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "build"):
        parser.error("Actual output must stay in ignored private build/.")
    raw = output / "raw"
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    raw.chmod(0o700)
    capture = Capture(raw_directory=raw)
    checked, records = [], []
    with Atlas("public") as atlas:
        atlas._research.public = capture
        available = margin_reports(capture.get(MARGIN_INDEX, 300))
        for report in available[: args.report_count]:
            day = str(report["date"])
            for identifier in identifiers:
                result = atlas.get_margin_status(identifier, observation_date=day)
                checked.append(
                    {"identifier": identifier, "date": day, "source_cells": verify(result, capture)}
                )
                private_write(output / f"{day}-{identifier}.json", canonical(result))
                row = result["records"][0]
                records.append(
                    {
                        "identifier": identifier,
                        "application_date": day,
                        "publication_date": row["publication_date"],
                        **{
                            field: row["shares"][field]
                            for field in (
                                "buy_balance",
                                "buy_change",
                                "sell_balance",
                                "sell_change",
                            )
                        },
                        "share_unit": "share",
                        "margin_ratio": row["margin_ratio"]["value"],
                        "source_url": row["source"]["raw_source_url"],
                        "raw_sha256": row["source"]["raw_sha256"],
                        "pdf_page": row["source"]["pdf_page"],
                    }
                )
                print(f"PASS {identifier} {day}: 28 original cells and exact ratio", flush=True)
    csv_output = io.StringIO(newline="")
    writer = csv.DictWriter(csv_output, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    private_write(output / "margin-balances.csv", csv_output.getvalue().encode("utf-8"))
    report = {
        "status": "PASS",
        "verified_at": stamp(),
        "checks": checked,
        "available_application_dates": [item["date"] for item in available],
        "records": len(records),
        "source_cells": sum(item["source_cells"] for item in checked),
        "http_diagnostics": capture.diagnostics,
        "limitations": (
            "Dated selected issues/reports only; no JSF or broker data or PIT guarantee."
        ),
    }
    private_write(output / "report.json", canonical(report))
    print(f"PASS: {len(records)} issue-date records; private CSV and evidence in {output}")


if __name__ == "__main__":
    main()
