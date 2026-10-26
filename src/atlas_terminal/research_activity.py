"""Official activity observations with explicit source scale, periods and evidence."""

import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from atlas_terminal.activity_catalog import ACTIVITY_SERIES
from atlas_terminal.activity_sources import (
    G17_BASE,
    STATCAN_BASE,
    census_url,
    fred_url,
    statcan_url,
    world_bank_url,
)
from atlas_terminal.economic_dates import month_end, within, years
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_economics import economic_page
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_sdmx import international_economics, structure


def selected_window(q: ResearchQuery) -> tuple[str, str]:
    first, last = years(q.start_date, q.end_date)
    return q.start_date or f"{first}-01-01", q.end_date or f"{last}-12-31"


def source_cell(value: Any) -> Any:
    """Retain JSON numeric decimals without introducing a binary float in output."""
    return numeric(value) if isinstance(value, Decimal) else value


def base_record(
    series: str, observed: str, value: str | None, source: dict[str, Any]
) -> dict[str, Any]:
    spec = ACTIVITY_SERIES[series]
    return {
        "series": series,
        "series_id": spec["code"],
        "series_title": spec["title"],
        "country": spec["country"],
        "date": observed,
        "date_basis": "observation_period_end",
        "frequency": spec["frequency"],
        "unit": spec["unit"],
        "seasonally_adjusted": spec["seasonally_adjusted"],
        "annualized": spec["annualized"],
        "value": value,
        "status": "reported" if value is not None else "unavailable",
        "published_at": None,
        "source": source,
    }


def census_data(
    transport: Transport, q: ResearchQuery
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert q.series is not None
    start, end = selected_window(q)
    spec = ACTIVITY_SERIES[q.series]
    snapshot = transport.get(census_url(q.series, start, end), 21600)
    raw = snapshot.data
    if not isinstance(raw, list) or not raw or len(raw) > 1001 or not isinstance(raw[0], list):
        raise source_error("Expected a bounded Census table with a header row.")
    headers = raw[0]
    required = {
        "cell_value",
        "data_type_code",
        "category_code",
        "seasonally_adj",
        "error_data",
        "program_code",
        "time_slot_id",
        "time",
        "us",
    }
    if (
        not all(isinstance(h, str) for h in headers)
        or len(headers) != len(set(headers))
        or set(headers) != required
    ):
        raise source_error(
            "Census response lacks exact unique requested dimensions and national geography."
        )
    records, seen, slots, programs = [], set(), set(), set()
    for i, cells in enumerate(raw[1:], 1):
        if not isinstance(cells, list) or len(cells) != len(headers):
            raise source_error("Census row width differs from its headers.")
        row = dict(zip(headers, cells, strict=True))
        program, slot = row["program_code"], row["time_slot_id"]
        if (
            not isinstance(program, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", program)
            or (spec["table"] == "ftd" and program != "FTD")
            or not isinstance(slot, str)
            or not re.fullmatch(r"[0-9]{1,10}", slot)
            or slot in slots
        ):
            raise source_error("Census program or observation-slot identity is invalid.")
        programs.add(program)
        slots.add(slot)
        if len(programs) != 1:
            raise source_error("Census returned multiple programs for one selected table.")
        if any(
            row[k] != v
            for k, v in {
                "data_type_code": spec["data_type"],
                "category_code": spec["code"],
                "seasonally_adj": "yes",
                "error_data": "no",
                "us": "1",
            }.items()
        ):
            raise source_error(
                "Census returned a different measure, error estimate, adjustment or geography."
            )
        if (
            not isinstance(row["time"], str)
            or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", row["time"])
            or not 1900 <= int(row["time"][:4]) <= 2100
        ):
            raise source_error("Unsupported Census observation period.")
        observed = month_end(int(row["time"][:4]), int(row["time"][-2:]))
        if observed in seen:
            raise source_error("Duplicate Census observation period.")
        seen.add(observed)
        if not within(observed, start, end):
            continue
        value = row["cell_value"]
        # Suppressed/unavailable cells stay unavailable; numeric zero remains valid.
        parsed = (
            None
            if value in (None, "", "NA", "N", "D", "S", "Z", "(NA)", "(D)", "(S)", "(Z)")
            else numeric(value)
        )
        records.append(
            {
                **base_record(
                    q.series,
                    observed,
                    parsed,
                    reference(snapshot, f"/{i}/{headers.index('cell_value')}"),
                ),
                "source_period": row["time"],
                "source_value": source_cell(value),
                "source_dimensions": {k: v for k, v in row.items() if k != "cell_value"},
                "table": spec["table"],
                "unit_basis": "curated_publisher_definition",
            }
        )
    return records, [reference(snapshot, "")]


def fred_data(
    transport: Transport, q: ResearchQuery
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert q.series is not None
    start, end = selected_window(q)
    spec = ACTIVITY_SERIES[q.series]
    meta = transport.get(fred_url(q.series), 86400)
    raw = meta.data
    series = raw.get("seriess") if isinstance(raw, dict) else None
    if not isinstance(series, list) or len(series) != 1 or not isinstance(series[0], dict):
        raise source_error("Expected one FRED series metadata record.")
    detail = series[0]
    if (
        detail.get("id") != spec["code"]
        or detail.get("frequency_short") != "W"
        or detail.get("frequency") != "Weekly, Ending Saturday"
        or detail.get("units") != "Number"
        or detail.get("seasonal_adjustment_short") != "SA"
    ):
        raise source_error("FRED claims identity, weekly convention, unit or adjustment changed.")
    snapshot = transport.get(fred_url(q.series, start, end), 21600)
    raw = snapshot.data
    rows = raw.get("observations") if isinstance(raw, dict) else None
    if (
        not isinstance(rows, list)
        or len(rows) > 2000
        or raw.get("count") != len(rows)
        or raw.get("offset") != 0
        or raw.get("units") != "lin"
        or raw.get("output_type") != 1
    ):
        raise source_error("FRED observations are truncated, transformed or malformed.")
    records, seen = [], set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            raise source_error("FRED observation lacks its week-ending date.")
        try:
            observed = date.fromisoformat(row["date"])
        except ValueError:
            raise source_error("Malformed FRED observation date.") from None
        if (
            observed.weekday() != 5
            or observed.isoformat() in seen
            or not within(observed.isoformat(), start, end)
        ):
            raise source_error(
                "FRED returned a duplicate, out-of-window or non-Saturday claim observation."
            )
        seen.add(observed.isoformat())
        value = None if row.get("value") == "." else numeric(row.get("value"))
        if value is not None and (not re.fullmatch(r"[0-9]+(?:\.0+)?", value)):
            raise source_error("Claim counts must be nonnegative integer observations.")
        records.append(
            {
                **base_record(
                    q.series,
                    observed.isoformat(),
                    value,
                    reference(snapshot, f"/observations/{i}/value"),
                ),
                "reference_period_kind": "week_ending_saturday",
                "original_producer": "US Department of Labor",
                "disseminator": "Federal Reserve Bank of St. Louis (FRED)",
                "source_last_updated": detail.get("last_updated"),
                "metadata_source": reference(meta, "/seriess/0"),
            }
        )
    return records, [reference(meta, ""), reference(snapshot, "")]


def statcan_object(snapshot: Snapshot) -> tuple[dict[str, Any], str]:
    raw = snapshot.data
    prefix = ""
    if isinstance(raw, list) and len(raw) == 1:
        raw, prefix = raw[0], "/0"
    if (
        not isinstance(raw, dict)
        or raw.get("status") != "SUCCESS"
        or not isinstance(raw.get("object"), dict)
        or raw["object"].get("responseStatusCode") != 0
    ):
        raise source_error("Statistics Canada did not return one successful series response.")
    return raw["object"], prefix + "/object"


def statcan_data(
    transport: Transport, q: ResearchQuery
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert q.series is not None
    start, end = selected_window(q)
    spec = ACTIVITY_SERIES[q.series]
    meta = transport.get(statcan_url(q.series), 86400)
    detail, meta_path = statcan_object(meta)
    if (
        str(detail.get("vectorId")) != spec["code"]
        or str(detail.get("productId")) != spec["table"]
        or detail.get("terminated") != 0
        or not isinstance(detail.get("SeriesTitleEn"), str)
        or not detail["SeriesTitleEn"].startswith("Canada;")
        and ";Canada;" not in detail["SeriesTitleEn"]
    ):
        raise source_error(
            "Statistics Canada metadata differs from the curated active national vector/table."
        )
    code_snapshot = transport.get(STATCAN_BASE + "getCodeSets", 86400)
    codes = code_snapshot.data
    if (
        not isinstance(codes, dict)
        or codes.get("status") != "SUCCESS"
        or not isinstance(codes.get("object"), dict)
    ):
        raise source_error("Statistics Canada code sets are unavailable.")
    frequencies = codes["object"].get("frequency")
    if not isinstance(frequencies, list):
        raise source_error("Statistics Canada frequency code set is missing.")
    frequency_codes = [
        r.get("frequencyCode")
        for r in frequencies
        if isinstance(r, dict)
        and isinstance(r.get("frequencyDescEn"), str)
        and r["frequencyDescEn"].strip().casefold() == "monthly"
    ]
    if len(frequency_codes) != 1 or detail.get("frequencyCode") != frequency_codes[0]:
        raise source_error("Statistics Canada vector is not explicitly monthly.")
    units = [
        row
        for values in codes["object"].values()
        if isinstance(values, list)
        for row in values
        if isinstance(row, dict)
        and row.get("memberUomCode") == detail.get("memberUomCode")
        and isinstance(row.get("memberUomEn"), str)
    ]
    if len(units) != 1:
        raise source_error("Statistics Canada vector unit is not uniquely defined in its code set.")
    request = {
        "request_method": "POST",
        "request_url": STATCAN_BASE + "getSeriesInfoFromVector",
        "request_body": [{"vectorId": int(spec["code"])}],
    }
    metadata_ref = {**reference(meta, meta_path), **request}
    snapshot = transport.get(statcan_url(q.series, start, end), 21600)
    raw, path = statcan_object(snapshot)
    if (
        str(raw.get("vectorId")) != spec["code"]
        or str(raw.get("productId")) != spec["table"]
        or raw.get("coordinate") != detail.get("coordinate")
    ):
        raise source_error("Statistics Canada data identity/coordinate differs from its metadata.")
    rows = raw.get("vectorDataPoint")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise source_error("Expected bounded Statistics Canada datapoints.")
    records, seen = [], set()
    for i, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("refPer"), str)
            or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])-01", row["refPer"])
            or not 1900 <= int(row["refPer"][:4]) <= 2100
            or row.get("frequencyCode") != frequency_codes[0]
            or row.get("refPer2") not in (None, "")
        ):
            raise source_error("Unsupported Statistics Canada period or frequency.")
        observed = month_end(int(row["refPer"][:4]), int(row["refPer"][5:7]))
        if observed in seen:
            raise source_error("Duplicate Statistics Canada monthly period.")
        seen.add(observed)
        if not within(observed, start, end):
            continue
        multiplier = row.get("scalarFactorCode")
        if (
            type(multiplier) is not int
            or not 0 <= multiplier <= 9
            or multiplier != detail.get("scalarFactorCode")
            or type(row.get("securityLevelCode")) is not int
            or row["securityLevelCode"] not in {0, 1}
            or type(row.get("statusCode")) is not int
            or row["statusCode"] not in set(range(11))
            or type(row.get("symbolCode")) is not int
            or row["symbolCode"] not in {0, 1, 3}
        ):
            raise source_error("Statistics Canada scalar, status or security metadata changed.")
        # Suppressed/confidential/unavailable observations are not usable numeric facts.
        usable = row["securityLevelCode"] == 0 and row["statusCode"] in {0, 3, 4, 5, 6, 7}
        value = numeric(row.get("value")) if usable else None
        records.append(
            {
                **base_record(
                    q.series,
                    observed,
                    value,
                    reference(snapshot, f"{path}/vectorDataPoint/{i}/value"),
                ),
                "series_title": detail["SeriesTitleEn"],
                "table": spec["table"],
                "coordinate": raw["coordinate"],
                "unit": units[0]["memberUomEn"],
                "unit_code": detail.get("memberUomCode"),
                "unit_multiplier": multiplier,
                "source_period": row["refPer"],
                "source_period_raw": row.get("refPerRaw"),
                "source_value": source_cell(row.get("value")),
                "source_release_time": row.get("releaseTime"),
                "source_status": row["statusCode"],
                "source_symbol": row.get("symbolCode"),
                "source_security_level": row["securityLevelCode"],
                "metadata_source": metadata_ref,
                "code_metadata_source": reference(code_snapshot, "/object"),
            }
        )
    return records, [metadata_ref, reference(code_snapshot, "/object"), reference(snapshot, "")]


def world_bank_data(
    transport: Transport, q: ResearchQuery
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert q.series is not None
    spec = ACTIVITY_SERIES[q.series]
    start, end = selected_window(q)
    meta = transport.get(world_bank_url(q.series), 86400)
    raw = meta.data
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or not isinstance(raw[1], list)
        or len(raw[1]) != 1
        or not isinstance(raw[1][0], dict)
        or raw[1][0].get("id") != spec["code"]
        or not isinstance(raw[1][0].get("source"), dict)
        or raw[1][0]["source"].get("id") != "2"
    ):
        raise source_error("World Bank indicator metadata is not the requested WDI series.")
    detail = raw[1][0]
    snapshot = transport.get(world_bank_url(q.series, start, end), 21600)
    raw = snapshot.data
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or not isinstance(raw[0], dict)
        or not isinstance(raw[1], list)
        or raw[0].get("pages") != 1
        or raw[0].get("page") != 1
        or raw[0].get("total") != len(raw[1])
        or raw[0].get("sourceid") != "2"
        or len(raw[1]) > 30
    ):
        raise source_error("World Bank response is incomplete or not a single WDI page.")
    records, seen = [], set()
    for i, row in enumerate(raw[1]):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("indicator"), dict)
            or row["indicator"].get("id") != spec["code"]
            or row.get("countryiso3code") != "CHN"
            or not isinstance(row.get("date"), str)
            or not re.fullmatch(r"[0-9]{4}", row["date"])
            or not 1900 <= int(row["date"]) <= 2100
        ):
            raise source_error("World Bank returned another indicator, country or frequency.")
        observed = row["date"] + "-12-31"
        if observed in seen:
            raise source_error("Duplicate World Bank annual period.")
        seen.add(observed)
        if not within(observed, start, end):
            continue
        flag = row.get("obs_status")
        if not isinstance(flag, str):
            raise source_error("World Bank observation lacks an explicit status flag.")
        records.append(
            {
                **base_record(
                    q.series,
                    observed,
                    numeric(row.get("value")) if flag == "" else None,
                    reference(snapshot, f"/1/{i}/value"),
                ),
                "source_period": row["date"],
                "source_value": source_cell(row.get("value")),
                "observation_status": flag,
                "source_indicator_title": detail.get("name"),
                "source_note": detail.get("sourceNote"),
                "original_producers": detail.get("sourceOrganization"),
                "source_unit": detail.get("unit"),
                "source_last_updated": raw[0].get("lastupdated"),
                "metadata_source": reference(meta, "/1/0"),
            }
        )
    return records, [reference(meta, ""), reference(snapshot, "")]


def g17_data(
    transport: Transport, q: ResearchQuery
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert q.series is not None
    spec = ACTIVITY_SERIES[q.series]
    snapshot = transport.get(G17_BASE + spec["table"], 21600)
    if len(snapshot.body) > 16 * 1024 * 1024 or b"\x00" in snapshot.body:
        raise source_error("G.17 text exceeds bounds or uses an unsupported encoding.")
    try:
        lines = snapshot.body.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        raise source_error("G.17 is not UTF-8 compatible text.") from None
    start, end = selected_window(q)
    records, seen, description = [], set(), None
    for line_number, line in enumerate(lines, 1):
        if line_number > 50000 or len(line) > 500:
            raise source_error("G.17 line bounds exceeded.")
        if re.fullmatch(r'"' + spec["code"] + r': [^"\r\n]+"', line.strip()):
            if description is not None:
                raise source_error("Duplicate G.17 series description.")
            description = line.strip()[1:-1]
            continue
        if line[:16].strip() != '"' + spec["code"] + '"':
            continue
        if (
            description is None
            or not re.fullmatch(r"[0-9]{4}", line[16:20])
            or line[20:25].strip()
            or len(line[25:].rstrip()) > 120
        ):
            raise source_error("G.17 selected row differs from its documented fixed-width format.")
        year = int(line[16:20])
        if not 1900 <= year <= 2100 or year in seen:
            raise source_error("Duplicate or out-of-range G.17 year.")
        seen.add(year)
        cells = [line[25 + 10 * m : 35 + 10 * m].strip() for m in range(12)]
        last = max((i for i, cell in enumerate(cells) if cell), default=-1)
        for month, cell in enumerate(cells[: last + 1], 1):
            observed = month_end(year, month)
            if not within(observed, start, end):
                continue
            if cell not in {"", "ND", "NA"} and not re.fullmatch(r"[0-9]{1,4}\.[0-9]{4}", cell):
                raise source_error("Malformed G.17 monthly numeric cell.")
            source = {
                "raw_source_url": snapshot.url,
                "raw_sha256": snapshot.sha256,
                "retrieved_at": snapshot.retrieved_at,
                "text_line": line_number,
                "text_column_start": 26 + 10 * (month - 1),
                "text_column_end": 35 + 10 * (month - 1),
                "text_encoding": "utf-8",
            }
            records.append(
                {
                    **base_record(
                        q.series,
                        observed,
                        None if cell in {"", "ND", "NA"} else numeric(cell),
                        source,
                    ),
                    "source_period": f"{year:04d}-{month:02d}",
                    "source_description": description,
                    "unit_basis": "curated_G17_release_definition",
                }
            )
    if description is None or not seen:
        raise source_error("G.17 file does not contain the curated series and dated rows.")
    document = {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        "text_line": 1,
    }
    return records, [document]


def sdmx_activity(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series is not None
    spec = ACTIVITY_SERIES[q.series]
    source = spec["source_id"]
    meta = structure(transport, source, spec["code"])
    dimensions = spec.get("dimensions", {})
    if set(dimensions) != {d["id"] for d in meta.dimensions} or any(
        not d["codes"] or dimensions[d["id"]] not in d["codes"] for d in meta.dimensions
    ):
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "The curated activity dimensions are absent from current source metadata.",
        )
    start, end = selected_window(q)
    query = ResearchQuery(
        dataset="international_economics",
        source_id=cast(Literal["abs", "oecd"], source),
        dataflow=meta.flow,
        dimensions=dimensions,
        start_date=start,
        end_date=end,
        limit=q.limit,
    )
    result = international_economics(transport, query)
    for row in result["records"]:
        if row.get("frequency") != spec["frequency"] or row.get("unit") is None:
            raise source_error("Curated SDMX frequency or explicit unit is absent.")
        expected_country = "CHN" if spec["country"] == "CN" else "AUS"
        area = row["dimensions"].get("REF_AREA") or row["dimensions"].get("REGION")
        if area != (dimensions.get("REGION") or expected_country):
            raise source_error("Curated SDMX geographic definition changed.")
        row.update(
            series=q.series,
            series_id=spec["code"],
            series_title=spec["title"],
            country=spec["country"],
            seasonally_adjusted=spec["seasonally_adjusted"],
            annualized=spec["annualized"],
        )
        # These endpoints can mark forecasts. Keep the source cell but make it unusable.
        if row.get("observation_status") in {"F", "PRED"}:
            row.update(
                source_value=row["value"],
                value=None,
                status="unavailable",
                reason="Source flags this observation as a forecast.",
            )
    result["series_title"] = spec["title"]
    result["limitations"].append(
        "Curated country series retain exact source frequency, geographic scope, "
        "dimensions and index bases; no implicit frequency conversion or "
        "definition harmonization."
    )
    return result


def activity_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in ACTIVITY_SERIES
    spec = ACTIVITY_SERIES[q.series]
    if spec["source_id"] in {"oecd", "abs"}:
        result = sdmx_activity(transport, q)
    else:
        result = ordinary_activity(transport, q)
    from atlas_terminal.public_sources import SOURCE_CATALOG

    terms = SOURCE_CATALOG[spec["source_id"]]
    result["attributions"] = [
        {
            "source_id": spec["source_id"],
            "notice": terms["use"],
            "terms_url": terms["terms"],
            "modifications": (
                "Dates and field names normalized by Atlas Terminal; source scale, "
                "units, source notes and flags retained."
            ),
        }
    ]
    return result


def ordinary_activity(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in ACTIVITY_SERIES
    spec = ACTIVITY_SERIES[q.series]
    records, sources = {
        "census": census_data,
        "fred": fred_data,
        "statcan": statcan_data,
        "world_bank": world_bank_data,
        "federal_reserve": g17_data,
    }[spec["source_id"]](transport, q)
    start, end = selected_window(q)
    return economic_page(
        q,
        records,
        sources,
        source_id=spec["source_id"],
        title=spec["title"],
        window=f"Current/revised observations, {start} through {end}",
        limitations=[
            (
                "Values retain source units and powers of ten; annual-rate flows, annual "
                "observations and growth rates are distinct."
            ),
            (
                "Observation-period ends are not publication or retrieval times. Unknown "
                "publication timestamps remain null; snapshots are not point-in-time "
                "vintages."
            ),
            (
                "Claims are US Department of Labor observations distributed by FRED; "
                "annual China GDP is WDI, not a domestic quarterly feed."
            ),
            (
                "No source fallback, calendar filling, forecast substitution or implicit "
                "cross-country harmonization."
            ),
        ],
    )
