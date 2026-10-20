"""Opt-in live smoke entrypoints; no network or signer is reached without flags."""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.models import parse_query
from atlas_terminal.payments.policy import SpendingPolicy
from atlas_terminal.util import canonical, digest, stamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["sec", "testnet"])
    parser.add_argument("--identifier", default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-testnet-transfer", action="store_true")
    args = parser.parse_args()
    if not args.allow_network:
        print(json.dumps({"status": "NOT RUN", "reason": "Explicit --allow-network is required."}))
        raise SystemExit(4)
    if args.provider == "testnet" and not args.allow_testnet_transfer:
        print(
            json.dumps(
                {"status": "NOT RUN", "reason": "Explicit --allow-testnet-transfer is required."}
            )
        )
        raise SystemExit(4)
    try:
        if args.provider == "sec":
            with Atlas("sec") as atlas:
                result = atlas.get_financials(
                    parse_query(
                        {
                            "identifier": args.identifier or "AAPL",
                            "metrics": ["revenue", "net_income"],
                            "count": 3,
                        }
                    )
                )
            print(
                canonical(
                    {
                        "status": "PASS",
                        "scope": "SEC live",
                        "checked_at": stamp(),
                        "result_sha256": digest(canonical(result)),
                        "data": result.model_dump(mode="json"),
                    }
                ).decode()
            )
        else:
            path = os.environ.get("ATLAS_POLICY_FILE")
            if not path or SpendingPolicy.load(Path(path)).mode != "testnet":
                raise AtlasError(
                    "CONFIG_REQUIRED", "A dedicated testnet spending policy is required."
                )
            client = HostedClient.from_env()
            try:
                quote = client.quote(
                    parse_query(
                        {
                            "identifier": args.identifier or "ATLASDEMO",
                            "metrics": ["revenue", "net_income"],
                            "count": 3,
                        }
                    )
                )
                if quote.pricing.mode != "testnet":
                    raise AtlasError(
                        "POLICY_DENIED", "Real testnet smoke cannot use off or mock mode."
                    )
                result = client.purchase(quote.quote_id)
                recovered = client.purchase(quote.quote_id)
                if recovered != result:
                    raise AtlasError("QUOTE_MISMATCH", "Recovered artifact differed.")
                print(
                    canonical(
                        {
                            "status": "PASS",
                            "scope": "real Base Sepolia",
                            "checked_at": stamp(),
                            "sdk": importlib.metadata.version("x402"),
                            "network": result.receipt.network,
                            "asset": result.receipt.asset,
                            "transaction": result.receipt.transaction,
                            "result_sha256": result.receipt.result_sha256,
                            "recovery_same_artifact": True,
                        }
                    ).decode()
                )
            finally:
                client.close()
    except AtlasError as exc:
        print(
            canonical(
                {
                    "status": "NOT RUN"
                    if exc.code in {"CONFIG_REQUIRED", "WALLET_NOT_CONFIGURED", "PAYMENTS_DISABLED"}
                    else "FAIL",
                    "scope": args.provider,
                    "error": exc.code,
                }
            ).decode()
        )
        raise SystemExit(exc.exit_code) from None


if __name__ == "__main__":
    main()
