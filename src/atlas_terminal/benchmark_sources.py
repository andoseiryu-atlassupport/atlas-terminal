"""Bounded JPX TOPIX historical slots, with bracketed date/price evidence."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport

TOPIX_BASE = "https://www.jpx.co.jp/market/indices/"
TOPIX_SLOTS = range(1, 6)


def topix_url(slot: int, *, time: bool = False) -> str:
    if slot not in TOPIX_SLOTS:
        raise ValueError("Only the five published historical slots are supported")
    return TOPIX_BASE + f"indices_stock_price3.{slot}" + (".time.txt" if time else ".txt")


def topix_identity(url: str) -> tuple[int, bool] | None:
    for slot in TOPIX_SLOTS:
        for time in (False, True):
            if url == topix_url(slot, time=time):
                return slot, time
    return None


def topix_time(snapshot: Snapshot) -> datetime:
    text = snapshot.body.decode("ascii").strip()
    if not re.fullmatch(r"[0-9]{12}", text):
        raise ValueError("JPX historical marker must contain one YYYYMMDDHHMM value")
    updated = datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    # These slots contain historical sessions, not the mutable current-price feed.
    if updated.hour < 16 or not 1990 <= updated.year <= 2100:
        raise ValueError("JPX historical marker does not identify an after-close edition")
    return updated


def topix_cell(snapshot: Snapshot) -> tuple[str | None, str | None]:
    document = snapshot.data
    rows = [document[group]["Topix"] for group in ("MainStockIndex", "Topix")]
    values: list[tuple[str | None, str | None]] = []
    for row in rows:
        if row.get("marketName") != "TOPIX (東証株価指数)":
            raise ValueError("JPX TOPIX name differs; no other index is substituted")
        raw = row.get("currentPrice")
        if raw in {"--", None}:
            values.append((raw, None))
        elif isinstance(raw, str) and re.fullmatch(
            r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\.[0-9]{2}", raw
        ):
            normalized = raw.replace(",", "")
            if not Decimal("0") < Decimal(normalized) < Decimal("1e12"):
                raise ValueError("JPX TOPIX level is outside bounds")
            values.append((raw, normalized))
        else:
            raise ValueError("Malformed JPX TOPIX level")
    if values[0] != values[1]:
        raise ValueError("Repeated JPX TOPIX entries conflict")
    return values[0]


def topix_prices(transport: Transport, start: str, end: str) -> dict[str, Any]:
    from atlas_terminal.research_financials import reference

    records, sources = [], []
    dates: set[str] = set()
    for slot in TOPIX_SLOTS:
        # Expired-at-write snapshots force revalidation for the rotating slots. Marker
        # reads bracket the value read; each original body remains in source history.
        before = transport.get(topix_url(slot, time=True), 0)
        snapshot = transport.get(topix_url(slot), 0)
        after = transport.get(topix_url(slot, time=True), 0)
        try:
            updated = topix_time(before)
            if before.body != after.body:
                raise ValueError("JPX historical slot rotated during acquisition")
            retrieved = datetime.fromisoformat(after.retrieved_at.replace("Z", "+00:00"))
            if updated > retrieved:
                raise ValueError("JPX historical update is later than retrieval")
            raw, value = topix_cell(snapshot)
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "JPX TOPIX historical identity, date or level differs."
            ) from None
        day = updated.date().isoformat()
        if day in dates:
            raise AtlasError("SOURCE_UNAVAILABLE", "JPX historical slots repeat a session date.")
        dates.add(day)
        field_sources = [
            reference(snapshot, f"/{group}/Topix/currentPrice")
            for group in ("MainStockIndex", "Topix")
        ]
        date_sources = [
            {
                "raw_source_url": item.url,
                "raw_sha256": item.sha256,
                "retrieved_at": item.retrieved_at,
                "text_line": 1,
            }
            for item in (before, after)
        ]
        sources.extend([reference(snapshot, ""), *date_sources])
        if start <= day <= end:
            records.append(
                {
                    "date": day,
                    "value": value,
                    "raw_value": raw,
                    "source": field_sources[0],
                    "fact_sources": field_sources,
                    "date_sources": date_sources,
                    "observation_at": None,
                    "source_updated_at": updated.isoformat(),
                    "source_kind": "primary",
                    "symbol": "Topix",
                    "normalization_formula": "remove_grouping_commas(currentPrice)",
                    "published_at": None,
                }
            )
    return {
        "records": records,
        "sources": sources,
        "available_start": min(dates),
        "available_end": max(dates),
        "history_scope": "latest_five_published_sessions",
        "date_binding": "equal_JPX_marker_reads_bracket_price_read_not_atomic_publisher_snapshot",
    }
