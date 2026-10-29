"""Metadata-led FRED observations, retaining native date labels and source restrictions."""

from typing import Any

from atlas_terminal.economic_dates import within
from atlas_terminal.errors import AtlasError
from atlas_terminal.fred_sources import (
    FRED_MAX_OBSERVATIONS,
    fred_series_id,
    fred_series_url,
    fred_window,
)
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, observation_date, result_page, source_error
from atlas_terminal.research_models import ResearchQuery


def _text(raw: dict[str, Any], name: str, *, maximum: int = 1000) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise source_error(f"FRED metadata lacks a bounded {name} field.")
    return value


def _real_time(raw: dict[str, Any]) -> dict[str, str]:
    start = observation_date(raw.get("realtime_start"))
    end = observation_date(raw.get("realtime_end"))
    if start > end:
        raise source_error("FRED realtime bounds are reversed.")
    return {"realtime_start": start, "realtime_end": end}


def fred_metadata(snapshot: Snapshot, series: str) -> dict[str, Any]:
    raw = snapshot.data
    items = raw.get("seriess") if isinstance(raw, dict) else None
    if (
        not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or items[0].get("id") != series
    ):
        raise source_error("FRED metadata does not identify the single requested series.")
    item = items[0]
    result: dict[str, Any] = {"series_id": series, **_real_time(item)}
    for name in (
        "title",
        "frequency",
        "frequency_short",
        "units",
        "units_short",
        "seasonal_adjustment",
        "seasonal_adjustment_short",
        "last_updated",
    ):
        result[name] = _text(item, name)
    if result["frequency_short"] not in {"D", "W", "BW", "M", "Q", "SA", "A"}:
        raise source_error("FRED frequency is outside the supported daily-to-annual observations.")
    first = observation_date(item.get("observation_start"))
    last = observation_date(item.get("observation_end"))
    if first > last:
        raise source_error("FRED series observation bounds are reversed.")
    result.update(observation_start=first, observation_end=last)
    notes = item.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 100000):
        raise source_error("FRED series notes exceed bounds or are malformed.")
    result["notes"] = notes
    result["source"] = reference(snapshot, "/seriess/0")
    return result


def fred_series(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    series = fred_series_id(q.series or "")
    start, end = fred_window(q.start_date, q.end_date)
    meta = transport.get(fred_series_url(series), 86400)
    detail = fred_metadata(meta, series)
    snapshot = transport.get(fred_series_url(series, start, end), 21600)
    raw = snapshot.data
    rows = raw.get("observations") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(rows, list):
        raise source_error("FRED did not return an observation array.")
    if (
        type(raw.get("count")) is not int
        or raw["count"] < 0
        or len(rows) > FRED_MAX_OBSERVATIONS
        or type(raw.get("offset")) is not int
        or raw["offset"] != 0
        or raw.get("units") != "lin"
        or type(raw.get("output_type")) is not int
        or raw["output_type"] != 1
        or raw.get("sort_order") != "asc"
        or raw.get("observation_start") != start
        or raw.get("observation_end") != end
    ):
        raise source_error("FRED observations are transformed, out of window or malformed.")
    if raw["count"] != len(rows):
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "FRED returned an incomplete source page; narrow the requested observation dates.",
        )
    envelope_time = _real_time(raw)
    records: list[dict[str, Any]] = []
    previous = ""
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise source_error("FRED observation is not an object.")
        observed = observation_date(row.get("date"))
        if not within(observed, start, end) or observed <= previous:
            raise source_error(
                "FRED observations contain duplicate, unordered or out-of-window dates."
            )
        previous = observed
        real_time = _real_time(row)
        source_value = row.get("value")
        if not isinstance(source_value, str) or not source_value:
            raise source_error(
                "FRED observation must contain a numeric string or the missing marker."
            )
        value = None if source_value == "." else numeric(source_value)
        records.append(
            {
                "series": series,
                "series_id": series,
                "series_title": detail["title"],
                "date": observed,
                "date_basis": "fred_native_observation_label",
                "frequency": detail["frequency"],
                "frequency_code": detail["frequency_short"],
                "unit": detail["units"],
                "unit_code": detail["units_short"],
                "seasonal_adjustment": detail["seasonal_adjustment"],
                "seasonal_adjustment_code": detail["seasonal_adjustment_short"],
                "source_last_updated": detail["last_updated"],
                "value": value,
                "source_value": source_value,
                "status": "unavailable" if value is None else "reported",
                "published_at": None,
                **real_time,
                "source": reference(snapshot, f"/observations/{i}/value"),
                "observation_source": reference(snapshot, f"/observations/{i}"),
                "metadata_source": detail["source"],
            }
        )
    records.reverse()
    result = result_page(
        records,
        [reference(meta, ""), reference(snapshot, "")],
        q.limit,
        source_id="fred",
        series_title=detail["title"],
        series_metadata=detail,
        source_realtime=envelope_time,
        window=f"FRED current/revised observations, {start} through {end}",
        limitations=[
            "FRED aggregates government and third-party series. Series notes and original-producer "
            "conditions may restrict use or redistribution; API access grants no blanket license.",
            "Native source date labels are retained, including period starts. They are not "
            "inferred "
            "period ends, publication timestamps or guaranteed information-availability dates.",
            "Current/revised observations only. Source realtime fields are retained as evidence; "
            "this query does not request ALFRED vintages or guarantee point-in-time availability.",
            "Values use source units without transformation, aggregation, seasonal adjustment, "
            "gap filling or automatic classification of forecasts versus measurements.",
        ],
    )
    result["coverage"].update(
        requested_start_date=q.start_date,
        requested_end_date=q.end_date,
        effective_start_date=start,
        effective_end_date=end,
        first_observation_date=min(row["date"] for row in records),
        last_observation_date=max(row["date"] for row in records),
        date_selection="inclusive_fred_native_observation_label",
        point_in_time=False,
        upstream_complete=True,
    )
    result["attributions"] = [
        {
            "source_id": "fred",
            "notice": (
                "Source: Federal Reserve Bank of St. Louis (FRED) and original series producers. "
                "Retain series-specific notes and conditions; see series_metadata.notes."
            ),
            "terms_url": "https://fred.stlouisfed.org/legal/",
            "modifications": (
                "Field names normalized; native date labels, units and values retained."
            ),
        }
    ]
    return result
