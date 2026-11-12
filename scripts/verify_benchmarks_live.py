"""Opt-in benchmark source-cell and FX audits; private files, no paid data or transfers."""

import argparse
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

from verify_g20_live import DiagnosticTransport, private_write

from atlas_terminal.benchmark_catalog import BENCHMARKS
from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import yahoo_identity
from atlas_terminal.errors import AtlasError
from atlas_terminal.public_sources import ECB_FX_URL, local_name, xml_root
from atlas_terminal.util import canonical, digest, stamp


class Capture(DiagnosticTransport):
    def __init__(self, output):
        super().__init__(cache_path=output / "state" / "public-cache.sqlite3")
        self.output = output
        self.snapshots = {}
        self.calls = []

    def get(self, url, ttl, **kwargs):
        from atlas_terminal.benchmark_sources import topix_identity

        assert url == ECB_FX_URL or topix_identity(url) or yahoo_identity(url)
        self.calls.append(url)
        result = super().get(url, ttl, **kwargs)
        self.snapshots[result.sha256] = result
        private_write(self.output / "raw" / (result.sha256 + ".bin"), result.body)
        return result


def resolve(evidence, capture):
    snapshot = capture.snapshots[evidence["raw_sha256"]]
    assert digest(snapshot.body) == evidence["raw_sha256"]
    assert snapshot.url == evidence["raw_source_url"]
    if "text_line" in evidence:
        return snapshot.body.decode().splitlines()[evidence["text_line"] - 1]
    if "json_pointer" in evidence:
        value = snapshot.data
        for part in evidence["json_pointer"].split("/")[1:]:
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    node = xml_root(snapshot.body)
    parts = evidence["xml_local_path"].split("/")[1:]
    assert parts.pop(0) == local_name(node)
    for part in parts:
        if part.startswith("@"):
            return node.get(part[1:])
        match = re.fullmatch(r"([A-Za-z]+)(?:\[([0-9]+)\])?", part)
        assert match
        children = [child for child in node if local_name(child) == match[1]]
        node = children[int(match[2] or "1") - 1]
    return node


def audit(result, capture):
    assert not result["is_synthetic"] and not result["coverage"]["truncated"]
    matches, conversions, usable = 0, 0, 0
    for row in result["records"]:
        original = row["inputs"][0]
        raw = resolve(original["source"], capture)
        normalized = Decimal(str(raw).replace(",", "")) if raw not in {None, "--"} else None
        assert normalized == (
            Decimal(row["index_value"]) if row["index_value"] is not None else None
        )
        matches += 1
        for source in original.get("fact_sources", []):
            assert Decimal(str(resolve(source, capture)).replace(",", "")) == normalized
            matches += 1
        for evidence in original["date_sources"]:
            cell = resolve(evidence, capture)
            if "text_line" in evidence:
                day = datetime.strptime(cell.strip(), "%Y%m%d%H%M").date().isoformat()
            else:
                day = (
                    datetime.fromtimestamp(cell, UTC)
                    .astimezone(ZoneInfo(original["timezone"]))
                    .date()
                    .isoformat()
                )
            assert day == row["date"]
            matches += 1
        expected = normalized
        if row["derived"]:
            fx = row["inputs"][1]
            operands = {}
            for item in fx["inputs"]:
                raw_fx = resolve(item["source"], capture)
                assert (Decimal(raw_fx) if raw_fx is not None else None) == (
                    Decimal(item["value"]) if item["value"] is not None else None
                )
                assert item["date"] == row["date"]
                operands[item["quote_currency"]] = item["value"]
                matches += 1
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                rate = (
                    Decimal(operands[row["currency"]]) / Decimal(operands[row["native_currency"]])
                    if operands and all(v is not None for v in operands.values())
                    else None
                )
            assert rate == (Decimal(row["fx_value"]) if row["fx_value"] is not None else None)
            with localcontext(Context(prec=256, rounding=ROUND_HALF_EVEN)):
                expected = expected * rate if expected is not None and rate is not None else None
            conversions += expected is not None
        assert expected == (Decimal(row["value"]) if row["value"] is not None else None)
        usable += expected is not None
    return {"source_cells": matches, "converted_levels": conversions, "usable_levels": usable}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Explicit --allow-network is required; Yahoo also needs operator enablement.")
    build = Path("build").resolve()
    output = args.output.resolve()
    if output == build or not output.is_relative_to(build) or (output.exists() and not args.resume):
        parser.error("Use a new private directory below build/.")
    output.mkdir(parents=True, exist_ok=args.resume, mode=0o700)
    output.chmod(0o700)
    (output / "raw").mkdir(exist_ok=args.resume, mode=0o700)
    report = {"started_at": stamp(), "is_synthetic": False, "cases": [], "status": "INCOMPLETE"}
    capture = Capture(output)
    saved = {}
    with Atlas("public", directory=output / "state") as atlas:
        atlas._research.public = capture
        for key, spec in BENCHMARKS.items():
            currencies = [spec["native_currency"]]
            if spec["native_currency"] != "JPY":
                currencies.append("JPY")
            for currency in currencies:
                case = {"benchmark_id": key, "currency": currency}
                try:
                    result = atlas.benchmark_prices(
                        key,
                        start_date=args.start_date,
                        end_date=args.end_date,
                        benchmark_currency=currency,
                    )
                    private_write(output / f"{key}-{currency}.json", canonical(result))
                    checked = audit(result, capture)
                    saved[key, currency] = result
                    case.update(
                        status="PASS" if checked["usable_levels"] else "INCOMPLETE",
                        **checked,
                        comparison_period=result["coverage"]["comparison_period"],
                        missing_dates=result["coverage"]["missing_dates"],
                    )
                except AtlasError as exc:
                    case.update(status="FAIL", error_code=exc.code, details=exc.details)
                except (AssertionError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    case.update(
                        status="FAIL",
                        error_code="SOURCE_AUDIT_MISMATCH",
                        audit_exception=type(exc).__name__,
                    )
                report["cases"].append(case)
                print(canonical(case).decode(), flush=True)
        if ("sp500", "JPY") in saved and ("nasdaq_composite", "JPY") in saved:
            observations = [
                {"date": r["date"], "value": r["value"]}
                for r in reversed(saved["sp500", "JPY"]["records"])
                if r["value"] is not None
            ]
            if len(observations) >= 3:
                portfolio = {
                    "currency": "JPY",
                    "dividend_treatment": "price_return",
                    "valuation_basis": "utc_day_end",
                    "value_basis": "unitized_nav_external_flows_removed",
                    "risk_free_rate_annual": "0",
                    "observations": observations,
                }
                compared = []
                for key in ["sp500", "nasdaq_composite"]:
                    result = atlas.benchmark_prices(
                        key, start_date=args.start_date, end_date=args.end_date, portfolio=portfolio
                    )
                    audit(result, capture)
                    private_write(output / f"reference-comparison-{key}.json", canonical(result))
                    compared.append(result["comparison"])
                assert compared[0]["portfolio_metrics"] == compared[1]["portfolio_metrics"]
                assert Decimal(compared[0]["relative_metrics"]["beta"]["value"]) == 1
                report["reference_comparison"] = {
                    "status": "PASS",
                    "basis": "S&P500_JPY_reference_levels_not_an_actual_investor_portfolio",
                    "risk_free_rate_annual_assumption": "0",
                    "portfolio_metrics_unchanged": True,
                    "relative_metrics": [r["relative_metrics"] for r in compared],
                }
    report["finished_at"] = stamp()
    report["http_diagnostics"] = capture.diagnostics
    report["status"] = (
        "PASS" if all(c["status"] == "PASS" for c in report["cases"]) else "INCOMPLETE"
    )
    private_write(output / "report.json", canonical(report))
    print(
        canonical(
            {"status": report["status"], "cases": len(report["cases"]), "output": str(output)}
        ).decode()
    )
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
