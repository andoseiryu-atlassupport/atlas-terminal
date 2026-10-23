"""Run with uv run python examples/mock_purchase.py. No external network or funds."""

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from atlas_terminal.hosted import HostedClient
from atlas_terminal.models import parse_query
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical


def main() -> None:
    with TemporaryDirectory(prefix="atlas-mock-") as temporary:
        root = Path(temporary)
        policy = root / "policy.json"
        policy.write_bytes(
            canonical(
                {
                    "enabled": True,
                    "mode": "mock",
                    "allowed_origins": ["http://127.0.0.1:8000"],
                    "network": "mock:0",
                    "asset": "fixture:USDC",
                    "pay_to": "fixture:merchant",
                    "per_purchase_atomic": "10000",
                    "per_session_atomic": "20000",
                    "per_day_atomic": "20000",
                }
            )
        )
        app = create_app(provider="fixture", payments="mock", directory=root / "server")
        with TestClient(app, base_url="http://127.0.0.1:8000") as http:
            client = HostedClient(
                "http://127.0.0.1:8000", directory=root / "client", client=http, policy_path=policy
            )
            query = parse_query(
                {"identifier": "ATLASDEMO", "metrics": ["revenue", "net_income"], "count": 3}
            )
            quote = client.quote(query, intent_id="mock-annual-review")
            assert client.quote(query, intent_id="mock-annual-review") == quote
            preflight = client.preflight(quote.quote_id, max_cost="10000")
            assert preflight.decision == "ready"
            result = client.purchase(quote.quote_id, max_cost="10000")
            recovered = client.purchase(quote.quote_id)
            assert recovered == result
            print(
                canonical(
                    {
                        "mode": "mock",
                        "external_network": False,
                        "simulated": result.receipt.simulated,
                        "transaction": result.receipt.transaction,
                        "result_sha256": result.receipt.result_sha256,
                        "recovery_same_artifact": True,
                        "intent_replay_same_quote": True,
                        "preflight_decision": preflight.decision,
                        "budget_after": client.budget_status().day.model_dump(mode="json"),
                    }
                ).decode()
            )


if __name__ == "__main__":
    main()
