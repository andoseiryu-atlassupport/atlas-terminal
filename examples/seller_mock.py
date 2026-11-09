"""Fictional BLS purchase and recovery with an in-memory seller; no network or wallet."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.seller_client import SellerClient
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_server import create_seller_app

with TemporaryDirectory(prefix="atlas-seller-") as directory:
    root = Path(directory)
    ProductLibrary(root / "server").refresh("cpi", fixture=True)
    app = create_seller_app(directory=root / "server", payments="mock")
    origin = "http://127.0.0.1:8000"
    with TestClient(app, base_url=origin) as http:
        seller = SellerClient(origin, directory=root / "client", client=http)
        quote = seller.quote({"series": "cpi"}, intent_id="fictional-example")
        assert seller.preflight(quote.quote_id).decision == "ready"
        proof = MockAdapter.sign(quote, origin)
        paid = http.get(quote.resource_path, headers={"Atlas-Mock-Payment": proof})
        assert paid.status_code == 200
        result = seller.recover(quote.quote_id)
        assert result.data.is_synthetic and result.receipt.simulated
        assert result.receipt.transaction is None
        assert result.model_dump(mode="json") == paid.json()
        print(
            json.dumps(
                {
                    "quote": quote.model_dump(mode="json"),
                    "purchase": result.model_dump(mode="json"),
                },
                indent=2,
            )
        )
