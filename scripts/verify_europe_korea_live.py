"""Explicit personal-use overseas market checks; actual captures stay private."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from atlas_terminal.client import Atlas
from atlas_terminal.equity_intraday import intraday_url
from atlas_terminal.equity_sources import require_yahoo_enabled, yahoo_url
from atlas_terminal.market_catalog import SECURITY_MARKETS
from atlas_terminal.yahoo_risk import YahooRiskSpec, panel_from_prices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/europe-korea-live"))
    args = parser.parse_args()
    require_yahoo_enabled()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if not out.is_relative_to(root / "build"):
        parser.error("Actual data output must stay under the ignored build directory.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    results = []

    def save(name: str, body: bytes) -> None:
        path = out / name
        path.write_bytes(body)
        path.chmod(0o600)

    def record(row: dict) -> None:
        results.append(row)
        print(json.dumps(row), flush=True)

    def selected_pointer(raw: object, path: str) -> object:
        value = raw
        for part in path.split("/")[1:]:
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value

    with Atlas("public") as atlas:
        selections = [
            (spec.example, "2026-10-01", "2026-10-31")
            for key, spec in SECURITY_MARKETS.items()
            if key not in {"us", "tokyo"}
        ]
        selections += [(s, "2026-10-01", "2026-10-31") for s in ("069500.KS", "ISF.L", "EXS1.DE")]
        selections += [
            ("005930.KS", "2018-04-01", "2018-05-31"),
            ("NOVO-B.CO", "2023-09-01", "2023-09-30"),
        ]
        for symbol, start, end in selections:
            name = symbol + "_" + start
            try:
                query = {"identifier": symbol, "start_date": start, "end_date": end, "limit": 500}
                prices = atlas.get_research({"dataset": "equity_prices", **query})
                actions = atlas.get_research({"dataset": "equity_actions", **query})
                assert atlas._research.public is not None
                source = atlas._research.public.get(yahoo_url(symbol, start, end), 900)
                raw = source.data
                checked = 0
                for row in prices["records"]:
                    for field in ("open", "high", "low", "close", "volume", "adjusted_close"):
                        pointer = row["field_sources"].get(field)
                        if pointer is None:
                            if row[field] is not None:
                                raise ValueError("A value has no original source cell")
                            continue
                        original = selected_pointer(raw, pointer["json_pointer"])
                        normalized = row[field]
                        if (original is None) != (normalized is None) or (
                            original is not None and Decimal(str(original)) != Decimal(normalized)
                        ):
                            raise ValueError("Normalized value differs from original source cell")
                        checked += 1
                for row in actions["records"]:
                    event = selected_pointer(raw, row["source"]["json_pointer"])
                    for field, source_field in (
                        (("value", "amount"),)
                        if row["action_type"] == "dividend"
                        else (("numerator", "numerator"), ("denominator", "denominator"))
                    ):
                        if Decimal(str(event[source_field])) != Decimal(row[field]):
                            raise ValueError("Action term differs from source")
                        checked += 1
                save(name + ".json", source.body)
                save(name + "-prices.json", json.dumps(prices, indent=2).encode())
                save(name + "-actions.json", json.dumps(actions, indent=2).encode())
                record(
                    {
                        "selection": name,
                        "status": "verified",
                        "symbol": symbol,
                        "currency": prices["instrument"]["currency"],
                        "price_rows": len(prices["records"]),
                        "unavailable_rows": sum(
                            r["status"] != "reported" for r in prices["records"]
                        ),
                        "action_rows": len(actions["records"]),
                        "splits": sum(r["action_type"] == "split" for r in actions["records"]),
                        "original_cells_checked": checked,
                        "sha256": source.sha256,
                    }
                )
            except Exception as error:
                record(
                    {
                        "selection": name,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
        for symbol in ("005930.KS", "VOD.L", "SAP.DE"):
            try:
                result = atlas.get_research(
                    {
                        "dataset": "intraday_prices",
                        "identifier": symbol,
                        "start_date": "2026-10-28",
                        "end_date": "2026-10-28",
                        "interval": "5m",
                        "limit": 500,
                    }
                )
                assert atlas._research.public is not None
                source = atlas._research.public.get(
                    intraday_url(symbol, "2026-10-28", "2026-10-28", "5m"), 900
                )
                checked = 0
                for row in result["records"]:
                    for field in ("open", "high", "low", "close", "volume"):
                        original = selected_pointer(
                            source.data, row["field_sources"][field]["json_pointer"]
                        )
                        if (original is None) != (row[field] is None) or (
                            original is not None and Decimal(str(original)) != Decimal(row[field])
                        ):
                            raise ValueError("Intraday value differs from original source cell")
                        checked += 1
                    original = selected_pointer(
                        source.data, row["field_sources"]["observation_at"]["json_pointer"]
                    )
                    if datetime.fromisoformat(row["observation_at"]).timestamp() != original:
                        raise ValueError("Intraday timestamp differs from original source")
                    checked += 1
                save(symbol + "-intraday-raw.json", source.body)
                save(symbol + "-intraday.json", json.dumps(result, indent=2).encode())
                record(
                    {
                        "selection": symbol + "_intraday_5m",
                        "status": "verified",
                        "original_cells_checked": checked,
                        "rows": len(result["records"]),
                        "unavailable_rows": sum(
                            r["status"] != "reported" for r in result["records"]
                        ),
                    }
                )
            except Exception as error:
                record(
                    {
                        "selection": symbol + "_intraday_5m",
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
        for asset, factor in (
            ("005930.KS", "069500.KS"),
            ("VOD.L", "ISF.L"),
            ("SAP.DE", "EXS1.DE"),
        ):
            try:
                spec = YahooRiskSpec(
                    connection_id="verification",
                    kind="yahoo_finance",
                    symbols=[asset],
                    factor_symbol=factor,
                    start_date="2026-08-01" if asset == "VOD.L" else "2026-07-01",
                    end_date="2026-10-30",
                    personal_use_acknowledged=True,
                )
                prices = [
                    atlas.get_research(
                        {
                            "dataset": "equity_prices",
                            "identifier": symbol,
                            "start_date": spec.start_date,
                            "end_date": spec.end_date,
                            "limit": 500,
                        }
                    )
                    for symbol in (asset, factor)
                ]
                loaded = panel_from_prices(spec, prices)
                imported = atlas.import_risk_panel(loaded.panel)
                head = imported["head_id"]
                fitted = atlas.risk(
                    {
                        "action": "fit",
                        "method": "time_series",
                        "head_id": head,
                        "as_of": datetime.now(UTC).isoformat(),
                        "lookback": 60,
                    }
                )
                save(asset + "-risk-inputs.json", json.dumps(prices, indent=2).encode())
                save(asset + "-risk-fit.json", json.dumps(fitted, indent=2).encode())
                record(
                    {
                        "selection": asset + "_risk",
                        "status": "verified",
                        "currency": loaded.panel["manifest"]["currency"],
                        "sessions": len(loaded.panel["sessions"]),
                        "start_date": spec.start_date,
                        "end_date": spec.end_date,
                        "model_id": fitted["data"].get("model_id"),
                    }
                )
            except Exception as error:
                record(
                    {
                        "selection": asset + "_risk",
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
    save(
        "summary.json",
        json.dumps(
            {
                "verified_at": datetime.now(UTC).isoformat(),
                "results": results,
                "limitations": [
                    "Personal-use Yahoo data; no redistribution license or "
                    "exchange-calendar guarantee.",
                    "Current captures are not publisher PIT.",
                    "Financial statements from local European/Korean filing "
                    "sources are not verified.",
                ],
            },
            indent=2,
        ).encode(),
    )
    if any(row["status"] == "failed" for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
