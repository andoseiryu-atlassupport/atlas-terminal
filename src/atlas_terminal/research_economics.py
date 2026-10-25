"""Current official economic observations with exact units and source locators."""

import re
import unicodedata
from typing import Any

from atlas_terminal.economic_catalog import JAPAN_SERIES, NATIONAL_ACCOUNTS
from atlas_terminal.economic_dates import month_end, window_metadata, within, years
from atlas_terminal.economic_sources import (
    bea_url,
    boj_url,
    estat_cycle,
    estat_data_url,
    estat_metadata_url,
)
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Transport
from atlas_terminal.public_sources import BLS_BASE, BLS_SERIES, SOURCE_CATALOG, bls_url, xml_root
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, observation_date, result_page, source_error
from atlas_terminal.research_models import ResearchQuery


def economic_page(
    q: ResearchQuery,
    records: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    source_id: str,
    title: str,
    window: str,
    limitations: list[str],
) -> dict[str, Any]:
    records.sort(key=lambda row: (row["date"], row.get("period", "")), reverse=True)
    result = result_page(
        records,
        sources,
        q.limit,
        window=window,
        limitations=limitations,
        series_title=title,
        source_id=source_id,
    )
    result["coverage"].update(window_metadata(records, q.start_date, q.end_date))
    return result


def bls_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    key, (series_id, title, unit, adjusted) = next(
        (key, meta) for key, meta in BLS_SERIES.items() if q.series in {key, meta[0]}
    )
    first, last = years(q.start_date, q.end_date)
    spans: list[tuple[int | None, int | None]] = (
        [(y, min(y + 9, last)) for y in range(first, last + 1, 10)]
        if q.start_date
        else [(None, None)]
    )
    records, sources, seen = [], [], set()
    for low, high in spans:
        url = (
            bls_url(series_id, low, high)
            if low is not None and high is not None
            else BLS_BASE + series_id
        )
        snapshot = transport.get(url, 21600)
        raw = snapshot.data
        if not isinstance(raw, dict) or raw.get("status") != "REQUEST_SUCCEEDED":
            raise source_error("BLS rejected or could not fulfill the request.")
        results = raw.get("Results")
        prefix = "/Results"
        if isinstance(results, list) and len(results) == 1:
            results, prefix = results[0], "/Results/0"
        series = results.get("series") if isinstance(results, dict) else None
        if (
            not isinstance(series, list)
            or len(series) != 1
            or not isinstance(series[0], dict)
            or series[0].get("seriesID") != series_id
        ):
            raise source_error("BLS response series does not match the request.")
        rows = series[0].get("data")
        if not isinstance(rows, list) or len(rows) > 130:
            raise source_error("BLS response exceeds a ten-year monthly window.")
        request = (
            {
                "request_method": "POST",
                "request_url": BLS_BASE,
                "request_body": {
                    "seriesid": [series_id],
                    "startyear": str(low),
                    "endyear": str(high),
                },
            }
            if low is not None
            else {}
        )
        sources.append({**reference(snapshot, ""), **request})
        for i, row in enumerate(rows):
            if (
                not isinstance(row, dict)
                or not re.fullmatch(r"M(?:0[1-9]|1[0-3])", str(row.get("period", "")))
                or not re.fullmatch(r"[0-9]{4}", str(row.get("year", "")))
                or not 1900 <= int(row["year"]) <= 2100
            ):
                raise source_error("Unsupported BLS observation period.")
            year, month = int(row["year"]), int(row["period"][1:])
            # Fictional fixtures intentionally contain only their documented sample years.
            if low is not None and high is not None and not low <= year <= high:
                if transport.synthetic:
                    continue
                raise source_error("BLS returned a year outside the requested chunk.")
            identity = (year, month)
            if identity in seen:
                raise source_error("Duplicate BLS observation period.")
            seen.add(identity)
            observed = month_end(year, min(month, 12))
            if not within(observed, q.start_date, q.end_date):
                continue
            value = None if row.get("value") in ("-", "", None) else numeric(row["value"])
            records.append(
                {
                    "series": key,
                    "series_id": series_id,
                    "year": row["year"],
                    "period": row["period"],
                    "period_kind": "annual_average" if month == 13 else "month",
                    "date": observed,
                    "date_basis": "observation_period_end",
                    "value": value,
                    "status": "unavailable" if value is None else "reported",
                    "unit": unit,
                    "seasonally_adjusted": adjusted,
                    "frequency": "annual_average" if month == 13 else "monthly",
                    "published_at": None,
                    "footnotes": row.get("footnotes", []),
                    "latest": row.get("latest"),
                    "source": {**reference(snapshot, f"{prefix}/series/0/data/{i}"), **request},
                }
            )
    return economic_page(
        q,
        records,
        sources,
        source_id="bls",
        title=title,
        window=f"BLS V1 selected years {first}–{last} in ten-year chunks"
        if q.start_date
        else "BLS V1 latest three years",
        limitations=[
            (
                "Current revised BLS observations, not release-time vintages. "
                "Dates are period ends, not release dates."
            ),
            (
                "M13 annual averages are separate from monthly observations; no "
                "implicit percent-change calculation."
            ),
            (
                "Dated requests use read-only POSTs of at most ten calendar years; "
                "shared local and upstream per-IP quotas apply."
            ),
        ],
    )


def bea_period(raw: Any, frequency: str) -> tuple[str, str, str]:
    if not isinstance(raw, str):
        raise source_error("Missing BEA observation period.")
    match = re.fullmatch(r"([0-9]{4})([QM])([0-9]{1,2})", raw)
    if not match or match[2] != frequency:
        raise source_error("BEA observation frequency differs from the requested table.")
    year, part = int(match[1]), int(match[3])
    if not 1900 <= year <= 2100 or not 1 <= part <= (4 if frequency == "Q" else 12):
        raise source_error("Invalid BEA observation period.")
    return (
        month_end(year, part * 3 if frequency == "Q" else part),
        match[1],
        (f"Q{part}" if frequency == "Q" else f"M{part:02d}"),
    )


def national_accounts(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in NATIONAL_ACCOUNTS
    spec = NATIONAL_ACCOUNTS[q.series]
    first, last = years(q.start_date, q.end_date)
    records, sources, seen, identities = [], [], set(), set()
    for low in range(first, last + 1, 5):
        high = min(low + 4, last)
        snapshot = transport.get(bea_url(spec["table"], spec["frequency"], low, high), 21600)
        raw = snapshot.data
        api = raw.get("BEAAPI") if isinstance(raw, dict) else None
        result = api.get("Results") if isinstance(api, dict) else None
        if (
            not isinstance(api, dict)
            or not isinstance(result, dict)
            or "Error" in result
            or "Error" in api
        ):
            raise source_error(
                "BEA could not fulfill the NIPA request; verify the private key "
                "and requested years."
            )
        rows = result.get("Data")
        if not isinstance(rows, list) or len(rows) > 100000:
            raise source_error("Expected a bounded BEA NIPA table.")
        sources.append(reference(snapshot, "/BEAAPI/Results"))
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("TableName") != spec["table"]:
                raise source_error("BEA table identity differs from the request.")
            description = row.get("LineDescription")
            if not isinstance(description, str):
                raise source_error("Missing BEA line description.")
            # BEA appends backslash-delimited footnote references to some descriptions.
            label = re.sub(r"\s*\\[0-9, ]+\\\s*", "", description).strip()
            if not re.fullmatch(spec["description_pattern"], label, re.I):
                continue
            observed, year, period = bea_period(row.get("TimePeriod"), spec["frequency"])
            if not low <= int(year) <= high:
                if transport.synthetic:
                    continue
                raise source_error("BEA returned a year outside the requested table window.")
            identity = (row.get("SeriesCode"), row.get("LineNumber"))
            if not all(isinstance(v, str) and v for v in identity):
                raise source_error("Missing BEA line identity.")
            identities.add(identity)
            if len(identities) != 1 or observed in seen:
                raise source_error("Ambiguous BEA series or duplicate observation period.")
            seen.add(observed)
            if not within(observed, q.start_date, q.end_date):
                continue
            unit, multiplier, measurement = (
                row.get("CL_UNIT"),
                row.get("UNIT_MULT"),
                row.get("Metric_Name"),
            )
            if (
                not isinstance(unit, str)
                or not unit
                or not isinstance(measurement, str)
                or not measurement
                or not re.fullmatch(r"-?[0-9]{1,2}", str(multiplier))
                or not -12 <= int(str(multiplier)) <= 12
            ):
                raise source_error("BEA observation lacks explicit measurement and scale.")
            raw_value = row.get("DataValue")
            if raw_value in (None, "", "--", "---", "(NA)", "(D)"):
                value = None
            elif isinstance(raw_value, str) and re.fullmatch(
                r"-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", raw_value
            ):
                value = numeric(raw_value.replace(",", ""))
            else:
                raise source_error("Malformed BEA data value.")
            records.append(
                {
                    "series": q.series,
                    "series_id": row["SeriesCode"],
                    "table": spec["table"],
                    "line_number": row["LineNumber"],
                    "line_description": description,
                    "date": observed,
                    "date_basis": "observation_period_end",
                    "year": year,
                    "period": period,
                    "period_kind": "quarter" if spec["frequency"] == "Q" else "month",
                    "frequency": "quarterly" if spec["frequency"] == "Q" else "monthly",
                    "value": value,
                    "unit": unit,
                    "unit_multiplier": int(str(multiplier)),
                    "metric_name": measurement,
                    "scale_formula": "reported value * 10 ** unit_multiplier",
                    "seasonally_adjusted": True,
                    "annualized": spec["annualized"],
                    "status": "reported" if value is not None else "unavailable",
                    "published_at": None,
                    "source": reference(snapshot, f"/BEAAPI/Results/Data/{index}"),
                }
            )
    result = economic_page(
        q,
        records,
        sources,
        source_id="bea",
        title=spec["title"],
        window=f"BEA NIPA {first}–{last}, five-year table requests",
        limitations=[
            (
                "Reported values retain BEA CL_UNIT, UNIT_MULT and Metric_Name; no "
                "inferred base-year rebasing or currency conversion."
            ),
            (
                "GDP levels and income/consumption flows are seasonally adjusted "
                "annual rates; GDP growth is the published annualized quarterly "
                "percentage change. PCE price indexes are index levels."
            ),
            (
                "Current revised tables, not original release vintages. Missing "
                "source years and values are not filled."
            ),
        ],
    )
    result["attributions"] = [
        {
            "source_id": "bea",
            "notice": "Source: U.S. Bureau of Economic Analysis.",
            "terms_url": SOURCE_CATALOG["bea"]["terms"],
        }
    ]
    return result


def boj_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in JAPAN_SERIES
    spec = JAPAN_SERIES[q.series]
    first, last = years(q.start_date, q.end_date)
    start, end = q.start_date or f"{first}-01-01", q.end_date or f"{last}-12-31"
    snapshot = transport.get(boj_url(q.series, start, end), 21600)
    raw = snapshot.data
    if (
        not isinstance(raw, dict)
        or raw.get("STATUS") != 200
        or raw.get("NEXTPOSITION") is not None
        or not isinstance(raw.get("PARAMETER"), dict)
        or raw["PARAMETER"].get("DB") != spec["database"]
    ):
        raise source_error("BOJ returned an error, incomplete page or different database.")
    data = raw.get("DATA")
    if not isinstance(data, list) or len(data) != 1:
        raise source_error("Expected one requested BOJ series.")
    item = data[0]
    if (
        not isinstance(item, dict)
        or item.get("SERIES_CODE") != spec["code"]
        or item.get("FREQUENCY") != spec["frequency"]
    ):
        raise source_error("BOJ series or frequency does not match the request.")
    unit, values = item.get("UNIT"), item.get("VALUES")
    if not isinstance(unit, str) or not unit or not isinstance(values, dict):
        raise source_error("BOJ response lacks units or values.")
    dates, amounts = values.get("SURVEY_DATES"), values.get("VALUES")
    if (
        not isinstance(dates, list)
        or not isinstance(amounts, list)
        or len(dates) != len(amounts)
        or len(dates) > 12000
    ):
        raise source_error("BOJ dates and values do not align within bounds.")
    update = item.get("LAST_UPDATE")
    if update is not None and not re.fullmatch(r"[0-9]{8}", str(update)):
        raise source_error("Invalid BOJ last-update date.")
    updated = (
        observation_date(f"{str(update)[:4]}-{str(update)[4:6]}-{str(update)[6:]}")
        if update
        else None
    )
    records, seen = [], set()
    for i, (period, amount) in enumerate(zip(dates, amounts, strict=True)):
        if not isinstance(period, str) or not re.fullmatch(r"[0-9]{6}(?:[0-9]{2})?", period):
            raise source_error("Invalid BOJ period.")
        try:
            if spec["frequency"] == "DAILY" and len(period) == 8:
                observed = observation_date(f"{period[:4]}-{period[4:6]}-{period[6:]}")
            elif spec["frequency"] in {"MONTHLY", "QUARTERLY"} and len(period) == 6:
                part = int(period[4:])
                observed = month_end(
                    int(period[:4]), part * 3 if spec["frequency"] == "QUARTERLY" else part
                )
            else:
                raise ValueError("Period does not match frequency")
        except ValueError:
            raise source_error("Invalid BOJ period for the requested frequency.") from None
        if observed in seen:
            raise source_error("Duplicate BOJ observation period.")
        seen.add(observed)
        if not within(observed, start, end):
            continue
        value = numeric(amount)
        records.append(
            {
                "series": q.series,
                "series_id": spec["code"],
                "database": spec["database"],
                "series_title": item.get("NAME_OF_TIME_SERIES"),
                "country": "JP",
                "date": observed,
                "date_basis": "effective_date"
                if spec["frequency"] == "DAILY"
                else "observation_period_end",
                "source_period": period,
                "value": value,
                "unit": unit,
                "frequency": spec["frequency"].lower(),
                "seasonally_adjusted": spec["seasonally_adjusted"],
                "source_updated_on": updated,
                "published_at": None,
                "status": "reported" if value is not None else "unavailable",
                "source": reference(snapshot, f"/DATA/0/VALUES/VALUES/{i}"),
                "period_source": reference(snapshot, f"/DATA/0/VALUES/SURVEY_DATES/{i}"),
            }
        )
    result = economic_page(
        q,
        records,
        [reference(snapshot, "/DATA/0")],
        source_id="boj",
        title=spec["title"],
        window=f"BOJ {start} through {end}",
        limitations=[
            (
                "LAST_UPDATE is a source data/metadata update date, not the "
                "statistic's publication timestamp."
            ),
            (
                "Reported units and periods are preserved. No interpolation, "
                "rebasing or release-vintage reconstruction."
            ),
            (
                "Tokyo 17:00 USD/JPY is distinct from the ECB daily reference "
                "rate; no automatic merging or source substitution."
            ),
        ],
    )
    result["attributions"] = [
        {
            "source_id": "boj",
            "terms_url": SOURCE_CATALOG["boj"]["terms"],
            "notice": (
                'This service uses the API provided by the "Bank of Japan '
                'Time-Series Data Search." The Bank of Japan does not guarantee '
                "the content of the service."
            ),
            "modifications": (
                "Field names, date representation and numeric strings normalized by Atlas Terminal."
            ),
        }
    ]
    return result


def estat_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in JAPAN_SERIES
    spec = JAPAN_SERIES[q.series]
    seasonal = "2" if spec["seasonally_adjusted"] else "1"
    cycle = estat_cycle(q.series)
    meta = transport.get(estat_metadata_url(q.series), 86400)
    root = xml_root(meta.body)
    if root.tag != "GET_META_INDICATOR_INF" or root.findtext("RESULT/STATUS") != "0":
        raise source_error("Statistical Dashboard indicator discovery failed.")
    candidates = []

    def normalized_label(value: str) -> str:
        label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
        label = re.sub(r"^\(季節調整値\)", "", label)
        return re.sub(r"[0-9]{4}年基準$", "", label)

    expected = normalized_label(spec["code"])
    for i, node in enumerate(root.findall("METADATA_INF/CLASS_INF/CLASS_OBJ"), 1):
        label = normalized_label(node.get("name", ""))
        if label != expected:
            continue
        elements = [
            child
            for child in node.findall("CLASS")
            if all(
                (n := child.find(tag)) is not None and n.get("code") == expected_code
                for tag, expected_code in (
                    ("Cycle", cycle),
                    ("RegionalRank", "2"),
                    ("IsSeasonal", seasonal),
                )
            )
            and child.get("toDate", "").startswith("9999")
        ]
        if len(elements) == 1:
            candidates.append((node, i))
    if len(candidates) != 1:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            (
                "No unique active national indicator matches the requested survey, "
                "frequency and adjustment."
            ),
        )
    indicator, position = candidates[0]
    code = indicator.get("code", "")
    if not re.fullmatch(r"[0-9]{19}", code):
        raise source_error("Invalid Statistical Dashboard indicator identifier.")
    first, last = years(q.start_date, q.end_date)
    start, end = q.start_date or f"{first}-01-01", q.end_date or f"{last}-12-31"
    snapshot = transport.get(estat_data_url(q.series, code, start, end), 21600)
    root = xml_root(snapshot.body)
    if root.tag != "GET_STATS" or root.findtext("RESULT/STATUS") != "0":
        raise source_error("Statistical Dashboard data request failed.")
    base_path = "/GET_STATS/STATISTICAL_DATA"
    classes: dict[str, dict[str, str]] = {}
    for group in root.findall("STATISTICAL_DATA/CLASS_INF/CLASS_OBJ"):
        name = group.get("id", "")
        if name in classes:
            raise source_error("Duplicate Statistical Dashboard dimension metadata.")
        labels: dict[str, str] = {}
        for child in group.findall("CLASS"):
            key = child.get("id", child.get("code", ""))
            if not key or key in labels or not child.get("name"):
                raise source_error("Ambiguous Statistical Dashboard dimension labels.")
            labels[key] = child.get("name", "")
        classes[name] = labels
    nodes = root.findall("STATISTICAL_DATA/DATA_INF/DATA_OBJ")
    try:
        count = int(root.findtext("STATISTICAL_DATA/RESULT_INF/TOTAL_NUMBER", ""))
        returned = int(root.findtext("STATISTICAL_DATA/RESULT_INF/TO_NUMBER", ""))
    except ValueError:
        raise source_error("Missing Statistical Dashboard result counts.") from None
    if count != len(nodes) or returned != count or count > 1000:
        raise source_error("Statistical Dashboard response is incomplete or exceeds bounds.")
    records, seen = [], set()
    for i, node in enumerate(nodes, 1):
        values = node.findall("VALUE")
        if len(values) != 1:
            raise source_error("Expected one value per Statistical Dashboard observation.")
        value_node = values[0]
        p = value_node.attrib
        if any(
            p.get(k) != v
            for k, v in {
                "indicator": code,
                "stat": spec["database"],
                "regionCode": "00000",
                "cycle": cycle,
                "regionRank": "2",
                "isSeasonal": seasonal,
            }.items()
        ):
            raise source_error(
                "Statistical Dashboard dimensions do not match the requested government series."
            )
        period = p.get("time", "")
        try:
            if cycle == "2" and re.fullmatch(r"[0-9]{4}[1-4]Q00", period):
                observed = month_end(int(period[:4]), int(period[4]) * 3)
            elif cycle == "1" and re.fullmatch(r"[0-9]{6}00", period):
                observed = month_end(int(period[:4]), int(period[4:6]))
            else:
                raise ValueError("Frequency mismatch")
        except ValueError:
            raise source_error("Invalid Statistical Dashboard observation period.") from None
        if observed in seen:
            raise source_error("Duplicate Statistical Dashboard observation period.")
        seen.add(observed)
        unit = classes.get("unit", {}).get(p.get("unit", ""))
        survey = classes.get("stat", {}).get(spec["database"])
        title = classes.get("indicator", {}).get(code)
        if not unit or not survey or not title or p.get("isProvisional") not in {"0", "1"}:
            raise source_error(
                "Statistical Dashboard observation lacks unit, survey or provisional metadata."
            )
        if normalized_label(title) != expected:
            raise source_error("Statistical Dashboard value metadata differs from discovery.")
        if not within(observed, start, end):
            continue
        text = (value_node.text or "").strip()
        value = None if text in {"", "-", "...", "…", "***", "X", "x"} else numeric(text)
        records.append(
            {
                "series": q.series,
                "series_id": code,
                "series_title": title,
                "country": "JP",
                "region_code": "00000",
                "date": observed,
                "date_basis": "observation_period_end",
                "year": period[:4],
                "period": "Q" + period[4] if cycle == "2" else "M" + period[4:6],
                "period_kind": "quarter" if cycle == "2" else "month",
                "source_period": period,
                "frequency": spec["frequency"].lower(),
                "value": value,
                "unit": unit,
                "seasonally_adjusted": spec["seasonally_adjusted"],
                "annualized": spec.get("annualized", False),
                "base_period": (
                    match[0][:4] if (match := re.search(r"[0-9]{4}年基準", title)) else None
                ),
                "is_provisional": p["isProvisional"] == "1",
                "survey_code": spec["database"],
                "survey_name": survey,
                "published_at": None,
                "status": "reported" if value is not None else "unavailable",
                "annotations": [n.text for n in node.findall("CELL_ANNOTATIONS/CELL_ANNOTATION")],
                "source": reference(
                    snapshot, f"{base_path}/DATA_INF/DATA_OBJ[{i}]/VALUE", xml=True
                ),
                "metadata_source": reference(
                    meta,
                    f"/GET_META_INDICATOR_INF/METADATA_INF/CLASS_INF/CLASS_OBJ[{position}]",
                    xml=True,
                ),
            }
        )
    result = economic_page(
        q,
        records,
        [reference(meta, "/", xml=True), reference(snapshot, "/", xml=True)],
        source_id="estat",
        title=spec["title"],
        window=f"Japan Statistical Dashboard {start} through {end}",
        limitations=[
            (
                "Only curated government survey observations with explicit national geography, "
                "matching frequency, indicator identity and seasonal adjustment are accepted."
            ),
            (
                "The unique active indicator is resolved from official metadata; "
                "revised/rebased history is not an original-release vintage."
            ),
            (
                "Source units, provisional flags and annotations are preserved; "
                "the API execution timestamp is not the publication time."
            ),
        ],
    )
    result["attributions"] = [
        {
            "source_id": "estat",
            "terms_url": SOURCE_CATALOG["estat"]["terms"],
            "notice": (
                "This service uses the Statistical Dashboard API. The content of "
                "this service is not guaranteed by the Government of Japan."
            ),
            "original_sources": sorted({row["survey_name"] for row in records}),
            "modifications": (
                "Dates and numeric strings normalized; published observation "
                "values are not rebased or seasonally adjusted by Atlas."
            ),
        }
    ]
    return result
