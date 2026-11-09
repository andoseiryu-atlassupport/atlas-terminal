"""Bounded, private BLS publication store. Buyer requests never fetch upstream data."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import FixtureTransport, SECProvider, Snapshot
from atlas_terminal.public_sources import (
    BLS_BASE,
    BLS_SERIES,
    PublicFixtures,
    PublicTransport,
    bls_url,
)
from atlas_terminal.research import ResearchService
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_views import ResearchResult, SourceEvidence
from atlas_terminal.seller_models import ProductData, ProductQuery, product_selection
from atlas_terminal.util import Database, canonical, digest, stamp, utcnow

LIBRARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
 series TEXT PRIMARY KEY, body BLOB NOT NULL, sha256 TEXT NOT NULL,
 raw BLOB NOT NULL, raw_sha256 TEXT NOT NULL, captured_at TEXT NOT NULL
);
"""


class CapturedTransport:
    def __init__(self, snapshot: Snapshot, expected_url: str, synthetic: bool) -> None:
        self.snapshot, self.expected_url, self.synthetic = snapshot, expected_url, synthetic

    def get(self, url: str, ttl: int) -> Snapshot:
        if url != self.expected_url:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "The retained capture does not cover this request."
            )
        return self.snapshot

    def close(self) -> None:
        pass


class ProductLibrary:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.db = Database(self.directory / "seller-products.sqlite3", LIBRARY_SCHEMA)

    def publish(
        self,
        series: str,
        snapshot: Snapshot,
        *,
        source_access: Literal["source_response", "retained_capture", "fictional_fixture"],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> ProductData:
        query = ProductQuery(series=series)
        synthetic = source_access == "fictional_fixture"
        source_query = ResearchQuery(
            dataset="economics",
            series=query.series,
            limit=130,
            start_date=start_date,
            end_date=end_date,
        )
        if bool(start_date) != bool(end_date):
            raise AtlasError("INVALID_INPUT", "Capture dates must be supplied together.")
        spec = BLS_SERIES[query.series]
        expected = BLS_BASE + spec[0]
        if start_date and end_date:
            first, last = int(start_date[:4]), int(end_date[:4])
            if last - first > 9:
                raise AtlasError("INVALID_INPUT", "One product covers at most ten calendar years.")
            expected = bls_url(spec[0], first, last)
        try:
            captured = datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00"))
            if captured.tzinfo is None or captured > utcnow():
                raise ValueError("Invalid capture time")
        except ValueError:
            raise AtlasError(
                "INVALID_INPUT", "Capture needs a past, timezone-aware retrieval time."
            ) from None
        if len(snapshot.body) > 2 * 1024 * 1024 or (not synthetic and snapshot.url != expected):
            raise AtlasError("INVALID_INPUT", "Capture URL or size does not match the BLS product.")
        transport = CapturedTransport(snapshot, expected, synthetic)
        service = ResearchService(
            lambda: SECProvider(FixtureTransport()),
            synthetic=synthetic,
            public=transport,
            directory=self.directory,
        )
        try:
            research = ResearchResult.model_validate(service.get(source_query))
        finally:
            service.close()
        if not research.records or not any(
            row.get("value") is not None for row in research.records
        ):
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "An empty or entirely missing series is not saleable."
            )
        if research.coverage.get("truncated"):
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "A product must retain its complete selected capture."
            )
        data = ProductData(
            query=query,
            title=spec[1],
            captured_at=stamp(captured),
            prepared_at=stamp(),
            source_access=source_access,
            is_synthetic=synthetic,
            research=research,
            limitations=[
                "A retained BLS snapshot, not a live quote or release-time vintage.",
                (
                    "Upstream BLS information is freely available. "
                    "The price covers Atlas processing and delivery."
                ),
                "Capture time is not an observation date or a verified publication timestamp.",
                (
                    "Missing observations remain null; "
                    "annual averages and monthly observations remain distinct."
                ),
                *(
                    [
                        (
                            "Capture origin and retrieval time were supplied by the operator; "
                            "no new source request was made."
                        )
                    ]
                    if source_access == "retained_capture"
                    else []
                ),
            ],
        )
        body = canonical(data)
        if len(body) > 2 * 1024 * 1024:
            raise AtlasError("SOURCE_UNAVAILABLE", "Product exceeds the snapshot size limit.")
        with self.db.connect(write=True) as conn:
            previous = conn.execute(
                "SELECT captured_at FROM products WHERE series=?", (query.series,)
            ).fetchone()
            if previous and previous[0] > data.captured_at:
                raise AtlasError(
                    "INVALID_INPUT", "An older capture cannot replace the published series."
                )
            size = conn.execute(
                "SELECT coalesce(sum(length(body)+length(raw)),0) FROM products WHERE series!=?",
                (query.series,),
            ).fetchone()[0]
            if size + len(body) + len(snapshot.body) > 64 * 1024 * 1024:
                raise AtlasError("CACHE_FULL", "Seller publication storage is full.")
            conn.execute(
                "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?)",
                (
                    query.series,
                    body,
                    digest(body),
                    snapshot.body,
                    snapshot.sha256,
                    data.captured_at,
                ),
            )
        return data

    def get(
        self, query: ProductQuery, *, max_age_seconds: int, allow_synthetic: bool
    ) -> ProductData:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM products WHERE series=?", (query.series,)).fetchone()
        if not row:
            raise AtlasError("SOURCE_UNAVAILABLE", "This series has no prepared seller snapshot.")
        if digest(row["body"]) != row["sha256"] or digest(row["raw"]) != row["raw_sha256"]:
            raise AtlasError("LEDGER_UNAVAILABLE", "Seller snapshot integrity verification failed.")
        try:
            data = ProductData.model_validate_json(row["body"])
            captured = datetime.fromisoformat(data.captured_at.replace("Z", "+00:00"))
            age = (utcnow() - captured).total_seconds()
            if data.query != query or data.is_synthetic != data.research.is_synthetic:
                raise ValueError("Snapshot identity mismatch")
            if any(
                not isinstance(s, SourceEvidence) or s.raw_sha256 != row["raw_sha256"]
                for s in data.research.sources
            ):
                raise ValueError("Snapshot evidence mismatch")
        except (ValueError, TypeError, AttributeError):
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Seller snapshot metadata is inconsistent."
            ) from None
        if data.is_synthetic and not allow_synthetic:
            raise AtlasError("POLICY_DENIED", "Real-payment modes cannot sell fictional data.")
        if not data.is_synthetic and (age < 0 or age > max_age_seconds):
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Prepared source capture exceeds the configured age limit."
            )
        return data

    def catalog(self, *, max_age_seconds: int, allow_synthetic: bool) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            series = [row[0] for row in conn.execute("SELECT series FROM products ORDER BY series")]
        entries = []
        for key in series:
            try:
                data = self.get(
                    ProductQuery(series=key),
                    max_age_seconds=max_age_seconds,
                    allow_synthetic=allow_synthetic,
                )
            except AtlasError as exc:
                entries.append({"series": key, "available": False, "reason": exc.code})
            else:
                entries.append(
                    {
                        "series": key,
                        "available": True,
                        "title": data.title,
                        "captured_at": data.captured_at,
                        "is_synthetic": data.is_synthetic,
                        "source_access": data.source_access,
                        "record_count": len(data.research.records),
                        "selection": product_selection(data).model_dump(mode="json"),
                    }
                )
        return entries

    def refresh(
        self, series: str, *, allow_network: bool = False, fixture: bool = False
    ) -> ProductData:
        query = ProductQuery(series=series)
        if not fixture and not allow_network:
            raise AtlasError("CONFIG_REQUIRED", "Source refresh requires explicit --allow-network.")
        transport = (
            PublicFixtures()
            if fixture
            else PublicTransport(cache_path=self.directory / "public-cache.sqlite3")
        )
        try:
            snapshot = transport.get(BLS_BASE + BLS_SERIES[query.series][0], 21600)
            return self.publish(
                query.series,
                snapshot,
                source_access="fictional_fixture" if fixture else "source_response",
            )
        finally:
            transport.close()

    def import_capture(self, metadata_path: Path, body_path: Path) -> ProductData:
        try:
            if metadata_path.stat().st_size > 16384 or body_path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Oversized import")
            metadata = json.loads(metadata_path.read_bytes())
            body = body_path.read_bytes()
            if set(metadata) - {
                "series",
                "url",
                "retrieved_at",
                "sha256",
                "start_date",
                "end_date",
            }:
                raise ValueError("Unknown metadata")
            if digest(body) != metadata["sha256"]:
                raise ValueError("Hash mismatch")
            snapshot = Snapshot(body, metadata["url"], metadata["retrieved_at"])
            return self.publish(
                metadata["series"],
                snapshot,
                source_access="retained_capture",
                start_date=metadata.get("start_date"),
                end_date=metadata.get("end_date"),
            )
        except (OSError, ValueError, KeyError, TypeError):
            raise AtlasError(
                "INVALID_INPUT", "Capture files, metadata or source hash are invalid."
            ) from None
