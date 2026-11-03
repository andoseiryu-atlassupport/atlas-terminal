"""Price-level snapshots from bitbank's public API or an operator-owned local book."""

import os
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.local_inputs import public_reference, read_regular
from atlas_terminal.models import Model, identifier
from atlas_terminal.providers import CachedHTTPTransport, Snapshot, Transport
from atlas_terminal.research_financials import reference
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.source_history import SCHEMA, retain_version
from atlas_terminal.util import Database, stamp, state_dir

BITBANK_BASE = "https://public.bitbank.cc/"
PAIRS = {"BTC_JPY": "BTC", "ETH_JPY": "ETH", "XRP_JPY": "XRP"}
LOCAL_BOOK_URL = "https://local.atlas.invalid/order-book"
MAX_BOOK_BYTES = 1024 * 1024
BOOK_TTL = 5


def book_url(symbol: str) -> str:
    if symbol not in PAIRS:
        raise AtlasError("INVALID_INPUT", "Choose BTC_JPY, ETH_JPY or XRP_JPY.")
    return BITBANK_BASE + symbol.lower() + "/depth"


def amount(value: Any, *, positive: bool = True) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,24}(?:\.[0-9]{1,18})?", value):
        raise ValueError("Expected a bounded nonnegative decimal string")
    result = Decimal(value)
    if positive and result <= 0:
        raise ValueError("Price and displayed quantity must be positive")
    return result


def levels(value: Any, side: str) -> list[list[str]]:
    if not isinstance(value, list) or len(value) > 400:
        raise ValueError("Expected at most 400 price levels per side")
    previous = None
    for row in value:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("Expected price/quantity pairs")
        price, _ = amount(row[0]), amount(row[1])
        if previous is not None and (price <= previous if side == "asks" else price >= previous):
            raise ValueError("Duplicate or unordered price levels")
        previous = price
    return value


def bitbank_data(snapshot: Snapshot) -> dict[str, Any]:
    try:
        payload = snapshot.data
        if type(payload.get("success")) is not int or payload["success"] != 1:
            raise ValueError("Unsuccessful response")
        data = payload["data"]
        for side in ("bids", "asks"):
            levels(data[side], side)
        ts = data["timestamp"]
        if type(ts) is not int or not 946684800000 <= ts <= 4133980800000:
            raise ValueError("Invalid millisecond time")
        sequence = data.get("sequenceId")
        if sequence is not None and (
            isinstance(sequence, bool) or not re.fullmatch(r"[0-9]{1,30}", str(sequence))
        ):
            raise ValueError("Invalid sequence")
        for key in (
            "asks_over",
            "bids_under",
            "asks_under",
            "bids_over",
            "ask_market",
            "bid_market",
        ):
            if key in data:
                amount(data[key], positive=False)
        return data
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        raise AtlasError("SOURCE_UNAVAILABLE", "Malformed bitbank order-book response.") from None


class BookTransport(CachedHTTPTransport):
    def __init__(self, *, cache_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(
            "AtlasTerminal/0.7 (public-order-book research)",
            cache_path=cache_path or state_dir() / "order-books-cache.sqlite3",
            max_bytes=MAX_BOOK_BYTES,
            **kwargs,
        )
        self.interval = max(1.0, self.interval)
        self.headers["Accept"] = "application/json"

    @staticmethod
    def validate_url(url: str) -> None:
        if url not in {book_url(symbol) for symbol in PAIRS}:
            raise AtlasError("INVALID_INPUT", "Use an exact registered bitbank depth URL.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        bitbank_data(Snapshot(body, url, stamp()))

    def request(self, url: str, headers: dict[str, str]) -> Any:
        self.validate_url(url)
        return self.client.stream("GET", url, headers=headers, follow_redirects=False)


class BookFixtures:
    synthetic = True

    def get(self, url: str, ttl: int) -> Snapshot:
        BookTransport.validate_url(url)
        return Snapshot(
            files("atlas_terminal").joinpath("resources/research/order_book.json").read_bytes(),
            "https://fixtures.atlas.invalid/order_book.json",
            "2026-10-20T00:00:01Z",
        )

    def close(self) -> None:
        pass


class LocalBook(Model):
    schema_version: Literal["0.1.0"]
    is_synthetic: bool = Field(strict=True)
    identifier: str
    venue: str = Field(min_length=1, max_length=100)
    asset_class: Literal["equity", "crypto", "future"]
    quote_currency: str = Field(pattern=r"^[A-Z]{3,8}$")
    quantity_unit: Literal["shares", "contracts", "base_asset"]
    base_asset: str | None = Field(default=None, pattern=r"^[A-Z0-9]{1,12}$")
    observed_at: str = Field(max_length=64)
    original_source_url: str = Field(max_length=2048)
    rights_basis: Literal["owned", "licensed", "permitted_use"]
    bids: list[list[str]] = Field(max_length=400)
    asks: list[list[str]] = Field(max_length=400)

    @model_validator(mode="after")
    def units(self) -> "LocalBook":
        expected = {"equity": "shares", "future": "contracts", "crypto": "base_asset"}
        if self.quantity_unit != expected[self.asset_class]:
            raise ValueError("Quantity unit must match the instrument class")
        if (self.asset_class == "crypto") != (self.base_asset is not None):
            raise ValueError("Only a crypto book requires the explicit base asset")
        return self

    @field_validator("identifier")
    @classmethod
    def symbol(cls, value: str) -> str:
        return identifier(value)

    @field_validator("original_source_url")
    @classmethod
    def source_url(cls, value: str) -> str:
        return public_reference(value)

    @field_validator("observed_at")
    @classmethod
    def time(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed > datetime.now(UTC):
            raise ValueError("Use a nonfuture timezone-aware observation time")
        return stamp(parsed.astimezone(UTC))


def local_book(directory: Path, *, synthetic: bool) -> tuple[Snapshot, LocalBook]:
    if synthetic:
        body = (
            files("atlas_terminal")
            .joinpath("resources/research/local_order_book.json")
            .read_bytes()
        )
    else:
        path = os.environ.get("ATLAS_ORDER_BOOK_FILE")
        if not path:
            raise AtlasError(
                "CONFIG_REQUIRED", "Configure an operator-owned ATLAS_ORDER_BOOK_FILE."
            )
        body = read_regular(Path(path), MAX_BOOK_BYTES)
    try:
        book = LocalBook.model_validate_json(body)
        levels(book.bids, "bids")
        levels(book.asks, "asks")
    except (ValidationError, ValueError, TypeError, AtlasError):
        raise AtlasError(
            "SOURCE_UNAVAILABLE", "Invalid local order-book metadata or levels."
        ) from None
    snapshot = Snapshot(
        body,
        "https://fixtures.atlas.invalid/local_order_book.json" if synthetic else LOCAL_BOOK_URL,
        stamp(),
    )
    if not synthetic:
        db = Database(directory / "order-books-cache.sqlite3", SCHEMA)
        with db.connect(write=True) as conn:
            retain_version(conn, snapshot.url, body, snapshot.retrieved_at)
    return snapshot, book


def order_book(
    q: ResearchQuery, transport: Transport | None, directory: Path, *, synthetic: bool
) -> dict[str, Any]:
    data: dict[str, Any]
    identity: dict[str, Any]
    if q.book_source == "local":
        snapshot, book = local_book(directory, synthetic=synthetic)
        if book.identifier != q.identifier:
            raise AtlasError(
                "INCOMPLETE_COVERAGE", "The local snapshot has a different identifier."
            )
        data = {"bids": book.bids, "asks": book.asks}
        when, prefix = book.observed_at, ""
        identity = {
            "venue": book.venue,
            "asset_class": book.asset_class,
            "quote_currency": book.quote_currency,
            "quantity_unit": book.quantity_unit,
            "base_asset": book.base_asset,
            "original_source_url": book.original_source_url,
            "rights_basis": book.rights_basis,
            "origin_verified": False,
        }
        synthetic = synthetic or book.is_synthetic
    else:
        assert transport is not None and q.identifier is not None
        snapshot = transport.get(book_url(q.identifier), BOOK_TTL)
        data = bitbank_data(snapshot)
        when = stamp(
            datetime.fromtimestamp(data["timestamp"] // 1000, UTC).replace(
                microsecond=data["timestamp"] % 1000 * 1000
            )
        )
        prefix = "/data"
        identity = {
            "venue": "bitbank",
            "asset_class": "crypto",
            "base_asset": PAIRS[q.identifier],
            "quote_currency": "JPY",
            "quantity_unit": "base_asset",
            "origin_verified": True,
        }
    future = datetime.fromisoformat(when) > datetime.fromisoformat(snapshot.retrieved_at)
    rows, totals = [], {}
    with localcontext() as ctx:
        ctx.prec = 100
        ctx.rounding = ROUND_HALF_EVEN
        for side in ("bids", "asks"):
            quantity, notional = Decimal(0), Decimal(0)
            for index, (price_raw, quantity_raw) in enumerate(data[side][: q.depth]):
                price, qty = amount(price_raw), amount(quantity_raw)
                quantity += qty
                notional += price * qty
                rows.append(
                    {
                        "identifier": q.identifier,
                        **identity,
                        "side": side,
                        "level": index + 1,
                        "price": price_raw,
                        "quantity": quantity_raw,
                        "observed_at": when,
                        "date": when[:10],
                        "future_observation": future,
                        "status": "ambiguous" if future else "reported",
                        "unit": identity["quote_currency"] + "_per_" + identity["quantity_unit"],
                        "cumulative_quantity": format(quantity, "f"),
                        "derived": True,
                        "cumulative_notional": format(notional, "f"),
                        "cumulative_formula": (
                            "quantity=sum(qty); notional=sum(price*qty), through this level"
                        ),
                        "source": reference(snapshot, f"{prefix}/{side}/{index}"),
                    }
                )
            totals[side] = quantity
        best_bid = Decimal(data["bids"][0][0]) if data["bids"] else None
        best_ask = Decimal(data["asks"][0][0]) if data["asks"] else None
        valid = best_bid is not None and best_ask is not None and best_bid < best_ask and not future
        total = totals["bids"] + totals["asks"]
        spread, midpoint, spread_bps = None, None, None
        if valid:
            assert best_ask is not None and best_bid is not None
            spread = best_ask - best_bid
            midpoint = (best_ask + best_bid) / 2
            spread_bps = (spread / midpoint * 10000).quantize(Decimal("1e-18"))
        imbalance = (totals["bids"] - totals["asks"]) / total if valid and total else None

        def decimal(value: Decimal | None) -> str | None:
            return format(value, "f") if value is not None else None

        analytics = {
            "status": "derived" if valid else "unavailable",
            "best_bid": decimal(best_bid),
            "best_ask": decimal(best_ask),
            "spread": decimal(spread),
            "midpoint": decimal(midpoint),
            "spread_bps": decimal(spread_bps),
            "quantity_imbalance": decimal(imbalance.quantize(Decimal("1e-18")))
            if imbalance is not None
            else None,
            "bid_quantity": decimal(totals["bids"]),
            "ask_quantity": decimal(totals["asks"]),
            "depth_per_side": q.depth,
            "quantity_unit": identity["quantity_unit"],
            "quote_currency": identity["quote_currency"],
            "formula": (
                "spread=best_ask-best_bid; midpoint=(best_ask+best_bid)/2; "
                "spread_bps=spread/midpoint*10000; "
                "quantity_imbalance=(bid_quantity-ask_quantity)/(bid_quantity+ask_quantity)"
            ),
            "rounding": "Ratios: 18 decimal places, half-even; quantities and notionals exact.",
            "source": reference(snapshot, prefix),
        }
    return {
        "records": rows,
        "sources": [reference(snapshot, "")],
        "source_id": "bitbank" if q.book_source == "bitbank" else "multiple",
        "is_synthetic": synthetic,
        "book_analytics": analytics,
        "snapshot": {
            "observed_at": when,
            "sequence_id": str(data["sequenceId"]) if data.get("sequenceId") is not None else None,
            "future_observation": future,
            "market_phase": "unknown",
            "overflow_quantities": {
                k: data.get(k)
                for k in (
                    "asks_over",
                    "bids_under",
                    "asks_under",
                    "bids_over",
                    "ask_market",
                    "bid_market",
                )
            },
        },
        "coverage": {
            "returned_records": len(rows),
            "source_levels": {s: len(data[s]) for s in ("bids", "asks")},
            "truncated": any(len(data[s]) > q.depth for s in ("bids", "asks")),
            "full_market_book": False,
            "point_in_time": False,
        },
        "limitations": [
            "One venue's snapshot, not consolidated orders, a stream or a fill guarantee.",
            "Overflow quantities are not priced levels. Market phase is not inferred.",
            "Crossed/locked, one-sided and future books suppress spread/midpoint/imbalance.",
            "Local metadata/times are assertions; no broker login or Excel bridge is supplied.",
            "The cache/archive does not certify currentness or historical exchange availability.",
        ],
    }
