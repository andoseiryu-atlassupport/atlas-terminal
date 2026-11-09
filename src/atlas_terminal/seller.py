"""BLS seller orders reuse the immutable snapshot and settlement engine."""

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Pricing, Receipt
from atlas_terminal.payments.common import PaymentAdapter, PaymentQuote
from atlas_terminal.payments.orders import OrderService
from atlas_terminal.payments.reconciliation import SettlementReconciler
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_models import (
    ProductData,
    ProductPurchase,
    ProductQuery,
    ProductQuote,
    product_selection,
)
from atlas_terminal.util import stamp


class SellerOrders(OrderService):
    def __init__(
        self,
        library: ProductLibrary,
        pricing: Pricing,
        *,
        base_origin: str,
        adapter: PaymentAdapter | None = None,
        reconciler: SettlementReconciler | None = None,
        max_source_age_seconds: int = 172800,
        **kwargs: Any,
    ) -> None:
        if not 60 <= max_source_age_seconds <= 366 * 86400:
            raise AtlasError(
                "CONFIG_REQUIRED", "Source age limit must be between 60 seconds and 366 days."
            )
        self.library, self.max_source_age_seconds = library, max_source_age_seconds
        self.allow_synthetic = pricing.mode in {"off", "mock"}
        super().__init__(
            Atlas("public", directory=library.directory),
            library.directory / "seller-orders.sqlite3",
            pricing,
            base_origin=base_origin,
            adapter=adapter,
            reconciler=reconciler,
            **kwargs,
        )

    def load_quote(self, metadata: str) -> ProductQuote:
        return ProductQuote.model_validate_json(metadata)

    def read_result(self, query: BaseModel) -> ProductData:
        if not isinstance(query, ProductQuery):
            raise AtlasError("INVALID_INPUT", "Expected a seller product query.")
        return self.library.get(
            query, max_age_seconds=self.max_source_age_seconds, allow_synthetic=self.allow_synthetic
        )

    def build_quote(
        self, query: BaseModel, result: BaseModel, quote_id: str, snapshot_id: str, now: datetime
    ) -> PaymentQuote:
        assert isinstance(query, ProductQuery) and isinstance(result, ProductData)
        return ProductQuote(
            quote_id=quote_id,
            query=query,
            title=result.title,
            snapshot_id=snapshot_id,
            result_sha256=snapshot_id,
            record_count=len(result.research.records),
            selection=product_selection(result),
            captured_at=result.captured_at,
            source_access=result.source_access,
            pricing=self.pricing,
            expires_at=stamp(now + timedelta(seconds=self.ttl)),
            resource_path=f"/v1/seller/quotes/{quote_id}/result",
            is_synthetic=result.is_synthetic,
            limitations=result.limitations,
        )

    def prepare_product(
        self, query: ProductQuery, *, intent_key: str | None = None
    ) -> tuple[ProductQuote, str]:
        quote, token = self.prepare_order(query, intent_key=intent_key)
        assert isinstance(quote, ProductQuote)
        return quote, token

    def purchased_data(self, body: bytes, receipt: Receipt) -> ProductPurchase:
        return ProductPurchase(data=ProductData.model_validate_json(body), receipt=receipt)

    def catalog(self) -> dict[str, Any]:
        return {
            "schema_version": "seller-1.0",
            "product_id": "bls-series-v1",
            "description": (
                "One prepared BLS series with units, periods, source evidence and explicit gaps."
            ),
            "pricing": self.pricing.model_dump(mode="json"),
            "price_basis": "one immutable result; authenticated recovery is included for 24 hours",
            "source_terms_url": "https://www.bls.gov/bls/linksite.htm",
            "attribution": "Source: U.S. Bureau of Labor Statistics. Normalized by Atlas Terminal.",
            "upstream_data_cost": "0",
            "max_source_age_seconds": self.max_source_age_seconds,
            "source_notice": (
                "BLS source data is freely available. Atlas charges for processing and delivery."
            ),
            "series": self.library.catalog(
                max_age_seconds=self.max_source_age_seconds, allow_synthetic=self.allow_synthetic
            ),
        }
