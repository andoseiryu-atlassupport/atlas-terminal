"""Seller publication and delivery with fictional bytes and offline transports only."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from atlas_terminal.errors import AtlasError
from atlas_terminal.payments.common import make_pricing
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import BLS_BASE, BLS_SERIES, PublicFixtures
from atlas_terminal.seller import SellerOrders
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_models import ProductQuery, ProductQuote
from atlas_terminal.seller_server import create_seller_app
from atlas_terminal.util import canonical, digest, stamp, utcnow

ORIGIN = "http://127.0.0.1:8000"


def simulated_capture(series="cpi", **changes):
    """Fictional bytes with official-shaped metadata for branch tests, not live evidence."""
    url = BLS_BASE + BLS_SERIES[series][0]
    fixture = PublicFixtures().get(url, 0)
    return Snapshot(**({"body": fixture.body, "url": url, "retrieved_at": stamp()} | changes))


@pytest.mark.parametrize("series", list(BLS_SERIES))
def test_curated_series_use_existing_normalization_and_preserve_evidence(tmp_path, series):
    library = ProductLibrary(tmp_path)
    data = library.refresh(series, fixture=True)
    assert data.is_synthetic and data.source_access == "fictional_fixture"
    assert data.research.source_id == "bls"
    raw = PublicFixtures().get(BLS_BASE + BLS_SERIES[series][0], 0)
    document = raw.data
    for row in data.research.records:
        value = document
        for key in row["source"]["json_pointer"].split("/")[1:]:
            value = value[int(key)] if isinstance(value, list) else value[key]
        assert row["value"] == (None if value["value"] == "-" else value["value"])
        assert row["source"]["raw_sha256"] == raw.sha256
        assert row["source"]["retrieved_at"] == raw.retrieved_at
    assert not data.research.coverage["truncated"]


@pytest.mark.parametrize(
    "series", ["AAPL", "7203.T", "https://api.bls.gov", "fred_series", "polymarket"]
)
def test_non_bls_products_are_rejected(series):
    with pytest.raises(ValueError):
        ProductQuery(series=series)


def test_publication_has_no_implicit_network_and_refresh_is_opt_in(tmp_path, monkeypatch):
    from atlas_terminal.public_sources import PublicTransport

    def forbidden(*args, **kwargs):
        raise AssertionError("Network or live-cache constructor reached")

    monkeypatch.setattr(PublicTransport, "__init__", forbidden)
    library = ProductLibrary(tmp_path)
    with pytest.raises(AtlasError, match="allow-network"):
        library.refresh("cpi")
    data = library.publish("cpi", simulated_capture(), source_access="retained_capture")
    assert not data.is_synthetic
    assert "supplied by the operator" in data.limitations[-1]
    assert (
        library.get(ProductQuery(series="cpi"), max_age_seconds=60, allow_synthetic=False) == data
    )


@pytest.mark.parametrize(
    "mutation", ["url", "future", "naive", "oversized", "series", "duplicate", "empty", "missing"]
)
def test_bad_capture_cannot_replace_existing_product(tmp_path, mutation):
    library = ProductLibrary(tmp_path)
    previous = library.refresh("cpi", fixture=True)
    snap = simulated_capture()
    raw = json.loads(snap.body)
    changes = {}
    if mutation == "url":
        changes["url"] = "https://other.invalid/data"
    elif mutation == "future":
        changes["retrieved_at"] = stamp(utcnow() + timedelta(days=1))
    elif mutation == "naive":
        changes["retrieved_at"] = "2026-01-01T00:00:00"
    elif mutation == "oversized":
        changes["body"] = b" " * (2 * 1024 * 1024 + 1)
    else:
        series = raw["Results"]["series"][0]
        if mutation == "series":
            series["seriesID"] = "WRONG"
        elif mutation == "duplicate":
            series["data"].append(series["data"][0])
        elif mutation == "empty":
            series["data"] = []
        else:
            for row in series["data"]:
                row["value"] = "-"
        changes["body"] = canonical(raw)
    with pytest.raises(AtlasError):
        library.publish("cpi", simulated_capture(**changes), source_access="retained_capture")
    assert (
        library.get(ProductQuery(series="cpi"), max_age_seconds=60, allow_synthetic=True)
        == previous
    )


def test_missing_values_remain_null_and_annual_averages_stay_distinct(tmp_path):
    snap = simulated_capture()
    raw = json.loads(snap.body)
    records = raw["Results"]["series"][0]["data"]
    records[0]["value"] = "-"
    assert any(row["period"] == "M13" for row in records)
    data = ProductLibrary(tmp_path).publish(
        "cpi", simulated_capture(body=canonical(raw)), source_access="retained_capture"
    )
    assert any(
        row["value"] is None and row["status"] == "unavailable" for row in data.research.records
    )
    assert any(row["period_kind"] == "annual_average" for row in data.research.records)


def test_import_requires_matching_hash_and_never_prints_raw_data(tmp_path):
    snap = simulated_capture()
    body, meta = tmp_path / "body.json", tmp_path / "meta.json"
    body.write_bytes(snap.body)
    payload = {
        "series": "cpi",
        "url": snap.url,
        "retrieved_at": snap.retrieved_at,
        "sha256": "0" * 64,
    }
    meta.write_bytes(canonical(payload))
    library = ProductLibrary(tmp_path / "state")
    with pytest.raises(AtlasError):
        library.import_capture(meta, body)
    meta.write_bytes(canonical(payload | {"sha256": snap.sha256}))
    assert library.import_capture(meta, body).source_access == "retained_capture"


def test_stale_corrupt_and_fictional_captures_are_unsaleable(tmp_path):
    library = ProductLibrary(tmp_path)
    library.refresh("cpi", fixture=True)
    with pytest.raises(AtlasError) as exc:
        library.get(ProductQuery(series="cpi"), max_age_seconds=172800, allow_synthetic=False)
    assert exc.value.code == "POLICY_DENIED"
    library.publish(
        "cpi",
        simulated_capture(retrieved_at=stamp(utcnow() - timedelta(days=3))),
        source_access="retained_capture",
    )
    with pytest.raises(AtlasError, match="age limit"):
        library.get(ProductQuery(series="cpi"), max_age_seconds=172800, allow_synthetic=False)
    with library.db.connect(write=True) as conn:
        conn.execute("UPDATE products SET raw=?", (b"changed",))
    with pytest.raises(AtlasError) as exc:
        library.get(ProductQuery(series="cpi"), max_age_seconds=366 * 86400, allow_synthetic=False)
    assert exc.value.code == "LEDGER_UNAVAILABLE"


def test_product_recovery_survives_publication_change_restart_and_new_quotes(tmp_path):
    library = ProductLibrary(tmp_path)
    data = library.refresh("cpi", fixture=True)
    adapter = MockAdapter()
    service = SellerOrders(library, make_pricing("mock"), base_origin=ORIGIN, adapter=adapter)
    quote, token = service.prepare_product(ProductQuery(series="cpi"), intent_key="a" * 40)
    body, status, _ = service.result(quote.quote_id, proof=MockAdapter.sign(quote, ORIGIN))
    assert status == 200 and body.data == data and body.receipt.simulated
    library.publish("cpi", simulated_capture(), source_access="retained_capture")
    assert service.prepare_product(ProductQuery(series="cpi"), intent_key="a" * 40)[0] == quote
    assert service.result(quote.quote_id, bearer=token)[0] == body
    service.close()
    restarted = SellerOrders(
        library, make_pricing("mock", "50000"), base_origin=ORIGIN, adapter=MockAdapter()
    )
    assert restarted.result(quote.quote_id, bearer=token)[0] == body
    assert restarted.adapter.settlements == 0
    newer, _ = restarted.prepare_product(ProductQuery(series="cpi"))
    assert newer.result_sha256 != quote.result_sha256 and newer.pricing.amount_atomic == "50000"
    restarted.close()


@pytest.mark.parametrize(
    "path",
    [
        "/v1/research",
        "/v1/research/batch",
        "/v1/research/evidence/anything",
        "/v1/quotes",
        "/v1/filings",
        "/v1/risk",
        "/v1/seller/import",
        "/v1/seller/refresh",
    ],
)
def test_seller_app_does_not_publish_free_or_operator_routes(tmp_path, path):
    app = create_seller_app(directory=tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(path).status_code == 404
        assert client.post(path, json={}).status_code == 404


def test_http_discloses_price_but_not_values_or_recovery_secrets(tmp_path):
    ProductLibrary(tmp_path).refresh("cpi", fixture=True)
    with TestClient(
        create_seller_app(directory=tmp_path, payments="mock"), base_url=ORIGIN
    ) as client:
        response = client.post("/v1/seller/quotes", json={"series": "CUUR0000SA0"})
        assert response.status_code == 200
        quote = ProductQuote.model_validate(response.json())
        token = response.headers["Atlas-Recovery-Token"]
        assert quote.selection.unit == BLS_SERIES["cpi"][2]
        assert quote.selection.first_observation_date <= quote.selection.last_observation_date
        assert (
            quote.selection.reported_records + quote.selection.missing_records == quote.record_count
        )
        assert "annual_average" in quote.selection.period_kinds
        assert '"value"' not in response.text and token not in response.text
        assert quote.query.series == "cpi" and Decimal(quote.pricing.display_amount) == Decimal(
            "0.01"
        )
        assert (
            client.get(
                quote.resource_path, headers={"Authorization": "Bearer " + token}
            ).status_code
            == 402
        )
        result = client.get(
            quote.resource_path, headers={"Atlas-Mock-Payment": MockAdapter.sign(quote, ORIGIN)}
        )
        assert result.status_code == 200
        assert token not in result.text and result.headers["Cache-Control"] == "no-store"
        assert client.get(quote.resource_path).status_code == 401
        recovered = client.get(quote.resource_path, headers={"Authorization": "Bearer " + token})
        assert recovered.json() == result.json()
        assert digest(canonical(result.json()["data"])) == quote.result_sha256


def test_http_boundaries_reject_host_origin_extra_fields_and_oversize(tmp_path):
    with TestClient(create_seller_app(directory=tmp_path), base_url=ORIGIN) as client:
        assert client.get("/healthz", headers={"Host": "attacker.invalid"}).status_code == 403
        assert (
            client.get("/healthz", headers={"Origin": "https://attacker.invalid"}).status_code
            == 403
        )
        assert (
            client.post("/v1/seller/quotes", json={"series": "cpi", "amount": "1"}).status_code
            == 422
        )
        assert client.post("/v1/seller/quotes", content=b" " * 16385).status_code == 413
        assert client.get("/healthz", headers={"PAYMENT-SIGNATURE": "x" * 16385}).status_code == 413
        assert client.get("/v1/seller/quotes/invalid/status").status_code == 422
        for _ in range(120):
            response = client.get("/healthz")
        assert response.status_code == 429


@pytest.mark.parametrize("mode", ["off", "mock", "mainnet"])
def test_startup_modes_fail_closed_without_real_configuration(tmp_path, mode):
    kwargs = {"payments": mode, "directory": tmp_path}
    if mode != "mainnet":
        kwargs["base_origin"] = "https://seller.atlas.invalid"
    with pytest.raises(AtlasError):
        create_seller_app(**kwargs)
