"""Exact Eurostat JSON-stat and ONS observations through the shared research service."""

import re
from typing import Any

from atlas_terminal.economic_dates import month_end, within, years
from atlas_terminal.providers import Transport
from atlas_terminal.regional_economic_catalog import EUROPE_SERIES, UK_SERIES
from atlas_terminal.regional_economic_sources import (
    REGIONAL_ECONOMIC_SOURCES,
    eurostat_url,
    ons_url,
)
from atlas_terminal.research_economics import economic_page
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery

MONTHS = {
    name: n
    for n, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}


def period_end(period: str, frequency: str) -> str:
    try:
        if frequency == "M" and re.fullmatch(
            r"(?:19|20)[0-9]{2}-(?:0[1-9]|1[0-2])|2100-(?:0[1-9]|1[0-2])", period
        ):
            return month_end(int(period[:4]), int(period[-2:]))
        if frequency == "Q" and re.fullmatch(r"(?:19|20)[0-9]{2}-Q[1-4]|2100-Q[1-4]", period):
            return month_end(int(period[:4]), int(period[-1]) * 3)
    except ValueError:
        pass
    raise source_error("Unsupported or invalid source observation period.")


def category_codes(dimension: Any, size: int) -> list[str]:
    category = dimension.get("category") if isinstance(dimension, dict) else None
    index = category.get("index") if isinstance(category, dict) else None
    if isinstance(index, list):
        codes = index
    elif isinstance(index, dict):
        if any(type(position) is not int for position in index.values()) or sorted(
            index.values()
        ) != list(range(size)):
            raise source_error("Invalid JSON-stat category positions.")
        codes = sorted(index, key=lambda key: index[key])
    else:
        raise source_error("Missing JSON-stat category index.")
    if (
        len(codes) != size
        or any(not isinstance(code, str) or not code for code in codes)
        or len(set(codes)) != size
    ):
        raise source_error("Missing, duplicate or inconsistent JSON-stat categories.")
    return codes


def cells(value: Any, count: int, *, status: bool = False) -> dict[str, Any]:
    if isinstance(value, list) and len(value) == count:
        return {str(i): item for i, item in enumerate(value)}
    if isinstance(value, dict) and len(value) <= count:
        if any(
            not isinstance(k, str) or not re.fullmatch(r"0|[1-9][0-9]*", k) or int(k) >= count
            for k in value
        ):
            raise source_error("JSON-stat cell index is outside the selected series.")
        return value
    if value is None and status:
        return {}
    raise source_error("Expected bounded JSON-stat dense or sparse cells.")


def europe_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in EUROPE_SERIES
    spec = EUROPE_SERIES[q.series]
    first, last = years(q.start_date, q.end_date)
    start, end = q.start_date or f"{first}-01-01", q.end_date or f"{last}-12-31"
    snapshot = transport.get(eurostat_url(q.series, start, end), 21600)
    raw = snapshot.data
    if (
        not isinstance(raw, dict)
        or raw.get("class") != "dataset"
        or raw.get("version") != "2.0"
        or raw.get("source") != "ESTAT"
    ):
        raise source_error("Expected an official Eurostat JSON-stat 2.0 dataset.")
    extension = raw.get("extension")
    if (
        not isinstance(extension, dict)
        or str(extension.get("id", "")).lower() != spec["dataset"]
        or extension.get("agencyId") != "ESTAT"
    ):
        raise source_error("Eurostat dataset identity differs from the requested table.")
    ids, sizes, dimensions = raw.get("id"), raw.get("size"), raw.get("dimension")
    if (
        not isinstance(ids, list)
        or not isinstance(sizes, list)
        or not isinstance(dimensions, dict)
        or len(ids) != len(sizes)
        or any(not isinstance(i, str) for i in ids)
        or len(set(ids)) != len(ids)
        or set(ids) != {*spec["filters"], "time"}
        or set(dimensions) != set(ids)
        or any(type(n) is not int or not 0 <= n <= 360 for n in sizes)
    ):
        raise source_error("Eurostat dimensions are missing, duplicated or not single-series.")
    periods: list[str] = []
    labels: dict[str, str | None] = {}
    for dimension, size in zip(ids, sizes, strict=True):
        codes = category_codes(dimensions[dimension], size)
        if dimension == "time":
            periods = codes
        elif codes != [spec["filters"][dimension]]:
            raise source_error(
                "Eurostat returned a different country, measure, unit or adjustment."
            )
        else:
            label_map = dimensions[dimension]["category"].get("label", {})
            labels[dimension] = label_map.get(codes[0]) if isinstance(label_map, dict) else None
    # Every non-time dimension has size one, so the flat offset equals the time index,
    # regardless of whether time is the first, last or an intermediate dimension.
    values = cells(raw.get("value"), len(periods))
    flags = cells(raw.get("status"), len(periods), status=True)
    records, seen = [], set()
    for i, period in enumerate(periods):
        observed = period_end(period, spec["frequency"])
        if observed in seen or not first <= int(observed[:4]) <= last:
            raise source_error("Eurostat returned duplicate or out-of-window observation periods.")
        seen.add(observed)
        flag = flags.get(str(i))
        if flag is not None and (not isinstance(flag, str) or len(flag) > 100):
            raise source_error("Malformed Eurostat observation status.")
        value = numeric(values.get(str(i)))
        forecast = bool(flag and "f" in flag.lower())
        if not within(observed, start, end):
            continue
        records.append(
            {
                "series": q.series,
                "series_id": spec["dataset"],
                "country": spec["country"],
                "series_title": spec["title"],
                "date": observed,
                "date_basis": "observation_period_end",
                "source_period": period,
                "period": period,
                "year": period[:4],
                "period_kind": "quarter" if spec["frequency"] == "Q" else "month",
                "frequency": "quarterly" if spec["frequency"] == "Q" else "monthly",
                "value": None if forecast else value,
                "source_value": value,
                "unit": spec["filters"]["unit"],
                "unit_label": labels.get("unit"),
                "base_period": spec["base_period"],
                "annualized": False,
                "seasonally_adjusted": spec["seasonally_adjusted"],
                "seasonal_adjustment": spec["filters"].get("s_adj", "NSA"),
                "dimensions": spec["filters"],
                "dimension_labels": labels,
                "reference_area": spec["filters"]["geo"],
                "observation_status": flag,
                "source_updated_at": raw.get("updated"),
                "published_at": None,
                "status": "unavailable" if value is None or forecast else "reported",
                "reason": "Source flags this cell as a forecast."
                if forecast
                else "Source observation is missing."
                if value is None
                else None,
                "source_cell_index": i,
                "source": reference(snapshot, f"/value/{i}" if str(i) in values else "/value"),
                "metadata_source": reference(snapshot, "/dimension"),
            }
        )
    result = economic_page(
        q,
        records,
        [reference(snapshot, "")],
        source_id="eurostat",
        title=spec["title"],
        window=f"Eurostat {first}–{last}, exact single-series dimensions",
        limitations=[
            (
                "GDP levels and quarter-on-quarter growth are not annualized; HICP rates are "
                "percentages."
            ),
            (
                "HICP uses ECOICOP version 2 and the current prc_hicp_minr table; "
                "no archived-series splice."
            ),
            (
                "EA denotes the publisher's changing euro-area composition; original geo labels "
                "remain explicit."
            ),
            (
                "Source flags, units, index bases and adjustments remain explicit. "
                "Revised snapshots are not vintages."
            ),
        ],
    )
    result["attributions"] = [
        {
            "source_id": "eurostat",
            "notice": "Source: Eurostat (online data code: " + spec["dataset"] + ").",
            "terms_url": REGIONAL_ECONOMIC_SOURCES["eurostat"]["terms"],
            "modifications": (
                "Dates, field names and exact numeric strings normalized by Atlas Terminal. "
                "Eurostat is not responsible for Atlas Terminal's modifications or results."
            ),
        }
    ]
    return result


def uk_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in UK_SERIES
    spec = UK_SERIES[q.series]
    snapshot = transport.get(ons_url(q.series), 21600)
    raw = snapshot.data
    description = raw.get("description") if isinstance(raw, dict) else None
    if (
        not isinstance(description, dict)
        or str(description.get("cdid", "")).upper() != spec["cdid"]
        or str(description.get("datasetId", "")).upper() != spec["dataset"]
        or not isinstance(description.get("unit"), str)
        or not description["unit"].strip()
        or not isinstance(description.get("title"), str)
        or not description["title"].strip()
    ):
        raise source_error("ONS response lacks matching series/dataset identity, title or units.")
    frequency = "months" if spec["frequency"] == "M" else "quarters"
    rows = raw.get(frequency)
    if not isinstance(rows, list) or len(rows) > 12000:
        raise source_error("Expected a bounded ONS observation array at the requested frequency.")
    first, last = years(q.start_date, q.end_date)
    start, end = q.start_date or f"{first}-01-01", q.end_date or f"{last}-12-31"
    records, seen = [], set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            raise source_error("ONS observation lacks an explicit source period.")
        match = re.fullmatch(r"([0-9]{4}) (Q[1-4]|[A-Z]{3})", row["date"])
        if not match:
            raise source_error("Unsupported ONS source period.")
        year, part = int(match[1]), match[2]
        if spec["frequency"] == "M" and part in MONTHS:
            month = MONTHS[part]
            observed = period_end(f"{year:04d}-{month:02d}", "M")
        elif spec["frequency"] == "Q" and re.fullmatch(r"Q[1-4]", part):
            month = int(part[-1]) * 3
            observed = period_end(f"{year:04d}-{part}", "Q")
        else:
            raise source_error("ONS period differs from the requested frequency.")
        if observed in seen:
            raise source_error("Duplicate ONS observation period.")
        seen.add(observed)
        # MGSX labels each three-month Labour Force Survey estimate by its middle month.
        label_end = observed
        if spec["rolling_months"] == 3:
            end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
            start_year, start_month = (year - 1, 12) if month == 1 else (year, month - 1)
            observed = month_end(end_year, end_month)
            period_start = f"{start_year:04d}-{start_month:02d}-01"
        else:
            period_start = f"{year:04d}-{month - 2 if spec['frequency'] == 'Q' else month:02d}-01"
        if not within(observed, start, end):
            continue
        raw_value = row.get("value")
        value = None if raw_value in (None, "", ":", "..", "...") else numeric(raw_value)
        records.append(
            {
                "series": q.series,
                "series_id": spec["cdid"],
                "table": spec["dataset"],
                "country": "GB",
                "series_title": description["title"],
                "date": observed,
                "date_basis": "observation_period_end",
                "source_period": row["date"],
                "source_label_date": label_end,
                "period_start": period_start,
                "period_end": observed,
                "reference_period_months": spec["rolling_months"] if frequency == "months" else 3,
                "reference_period_kind": "three_month_rolling"
                if spec["rolling_months"] == 3
                else "quarter"
                if frequency == "quarters"
                else "month",
                "frequency": "monthly" if frequency == "months" else "quarterly",
                "value": value,
                "unit": description["unit"],
                "annualized": False,
                "seasonally_adjusted": spec["seasonally_adjusted"],
                "base_period": None,
                "source_release_date": description.get("releaseDate"),
                "published_at": None,
                "status": "reported" if value is not None else "unavailable",
                "source": reference(snapshot, f"/{frequency}/{i}/value"),
                "metadata_source": reference(snapshot, "/description"),
            }
        )
    result = economic_page(
        q,
        records,
        [reference(snapshot, "")],
        source_id="ons",
        title=spec["title"],
        window=f"ONS current time series, {start} through {end}",
        limitations=[
            "One exact CDID/dataset pair; monthly, quarterly and annual arrays are never mixed.",
            "GDP quarter-on-quarter growth is not annualized; monetary values retain source scale.",
            (
                "MGSX is a three-month rolling estimate labelled by its middle month; "
                "Atlas dates it at the actual window end."
            ),
            (
                "Current/revised observations; release dates and source labels are retained "
                "without inventing publication times or vintages."
            ),
        ],
    )
    result["attributions"] = [
        {
            "source_id": "ons",
            "notice": REGIONAL_ECONOMIC_SOURCES["ons"]["use"],
            "terms_url": REGIONAL_ECONOMIC_SOURCES["ons"]["terms"],
            "modifications": (
                "Dates and fields normalized by Atlas; rolling windows explicitly dated at their "
                "end."
            ),
        }
    ]
    return result
