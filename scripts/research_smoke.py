"""Opt-in, read-only live source check. This script never purchases or signs."""

import argparse
import json
from typing import Any

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.research import DATASETS
from atlas_terminal.util import canonical, digest, stamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=sorted(set(DATASETS) - {"compare"}))
    parser.add_argument("--identifier")
    parser.add_argument("--series")
    parser.add_argument("--year", type=int)
    parser.add_argument("--base-currency")
    parser.add_argument("--quote-currency")
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if not args.allow_network:
        print(json.dumps({"status": "NOT RUN", "reason": "Explicit --allow-network is required."}))
        raise SystemExit(4)
    query: dict[str, Any] = {"dataset": args.dataset}
    for name in ("identifier", "series", "year", "base_currency", "quote_currency"):
        if getattr(args, name) is not None:
            query[name] = getattr(args, name)
    if args.dataset in {"statements", "ratios", "financial_analytics"}:
        query.update(count=1, allow_partial=True)
    elif args.dataset != "profile":
        query["limit"] = 5
    try:
        with Atlas("public") as atlas:
            result = atlas.get_research(query)
        if not result["records"]:
            raise AtlasError("INCOMPLETE_COVERAGE", "Live check returned no records.")
        print(
            canonical(
                {
                    "status": "PASS",
                    "scope": "read-only live source",
                    "dataset": args.dataset,
                    "checked_at": stamp(),
                    "is_synthetic": result["is_synthetic"],
                    "returned_records": len(result["records"]),
                    "result_sha256": digest(canonical(result)),
                    "sources": result["sources"],
                }
            ).decode()
        )
    except AtlasError as exc:
        print(
            json.dumps(
                {
                    "status": "NOT RUN" if exc.code == "CONFIG_REQUIRED" else "FAIL",
                    "dataset": args.dataset,
                    "error": exc.code,
                }
            )
        )
        raise SystemExit(exc.exit_code) from None


if __name__ == "__main__":
    main()
