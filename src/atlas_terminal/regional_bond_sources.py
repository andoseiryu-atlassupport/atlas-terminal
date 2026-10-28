"""Fixed ECB/RBA government-rate CSV contracts with original-cell evidence."""

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from atlas_terminal.fixed_income_sources import csv_error, csv_reference
from atlas_terminal.providers import Snapshot

ECB_CURVE_BASE = "https://data-api.ecb.europa.eu/service/data/YC/"
ECB_MATURITIES = {
    "SR_3M": "3MONTH",
    "SR_6M": "6MONTH",
    "SR_1Y": "1YEAR",
    "SR_2Y": "2YEAR",
    "SR_3Y": "3YEAR",
    "SR_5Y": "5YEAR",
    "SR_7Y": "7YEAR",
    "SR_10Y": "10YEAR",
    "SR_15Y": "15YEAR",
    "SR_20Y": "20YEAR",
    "SR_30Y": "30YEAR",
}
ECB_UNIVERSES = {"ecb_aaa": "G_N_A", "ecb_all": "G_N_C"}
EURO_SERIES = {"aaa_spot": "ecb_aaa", "all_spot": "ecb_all"}
RBA_URL = "https://www.rba.gov.au/statistics/tables/csv/f2-data.csv"
RBA_SERIES = {
    "FCMYGBAG2D": "2YEAR",
    "FCMYGBAG3D": "3YEAR",
    "FCMYGBAG5D": "5YEAR",
    "FCMYGBAG10D": "10YEAR",
}
REGIONAL_CONTEXT: dict[str, dict[str, Any]] = {
    "ecb_aaa": {
        "country": "EA",
        "currency": "EUR",
        "rate_type": "zero_coupon_spot",
        "compounding": "continuous",
        "curve_universe": "euro_area_aaa_central_governments",
        "source_estimated": True,
    },
    "ecb_all": {
        "country": "EA",
        "currency": "EUR",
        "rate_type": "zero_coupon_spot",
        "compounding": "continuous",
        "curve_universe": "euro_area_all_ratings_central_governments",
        "source_estimated": True,
    },
    "rba": {
        "country": "AU",
        "currency": "AUD",
        "rate_type": "nominal_interpolated",
        "compounding": "source_published_convention",
        "curve_universe": "australian_government_bonds",
        "source_estimated": True,
    },
}


def ecb_curve_url(curve: str, year: int) -> str:
    return (
        ECB_CURVE_BASE
        + f"B.U2.EUR.4F.{ECB_UNIVERSES[curve]}.SV_C_YM."
        + "+".join(ECB_MATURITIES)
        + f"?format=csvdata&detail=full&startPeriod={year}-01-01&endPeriod={year}-12-31"
    )


def ecb_curve_identity(url: str) -> tuple[str, int] | None:
    match = re.search(r"&startPeriod=([0-9]{4})-01-01&endPeriod=\1-12-31$", url)
    if match and 2004 <= int(match[1]) <= 2100:
        for curve in ECB_UNIVERSES:
            if url == ecb_curve_url(curve, int(match[1])):
                return curve, int(match[1])
    return None


def source_csv(snapshot: Snapshot, *, max_rows: int = 30000) -> list[list[str]]:
    if len(snapshot.body) > 16 * 1024 * 1024 or b"\x00" in snapshot.body:
        raise csv_error("Government-rate CSV size or encoding bounds exceeded.")
    try:
        text = snapshot.body.decode("utf-8-sig")
        rows = []
        for index, row in enumerate(csv.reader(io.StringIO(text, newline=""), strict=True), 1):
            if index > max_rows or len(row) > 64 or any(len(cell) > 4096 for cell in row):
                raise csv_error("Government-rate CSV row, column or cell bounds exceeded.")
            rows.append([cell.strip() for cell in row])
        return rows
    except (UnicodeDecodeError, csv.Error):
        raise csv_error("Government-rate CSV needs valid UTF-8 and CSV quoting.") from None


def decimal_cell(value: str) -> str | None:
    if value in {"", "."}:
        return None
    if len(value) > 100 or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value):
        raise csv_error("Malformed published government yield.")
    try:
        parsed = Decimal(value)
        parts = parsed.as_tuple()
        if not parsed.is_finite() or len(parts.digits) > 80 or not -40 <= int(parts.exponent) <= 40:
            raise ValueError("Numeric bounds exceeded")
        return format(parsed, "f")
    except (InvalidOperation, ValueError):
        raise csv_error("Published government yield exceeds decimal bounds.") from None


def reported_record(
    snapshot: Snapshot,
    row: int,
    column: int,
    header: str,
    curve: str,
    observed: str,
    maturity: str,
    raw: str,
    **extra: Any,
) -> dict[str, Any]:
    value = decimal_cell(raw)
    return {
        **REGIONAL_CONTEXT[curve],
        "date": observed,
        "maturity": maturity,
        "value": value,
        "source_value": raw,
        "unit": "percent_per_annum",
        "frequency": "business_daily",
        "published_at": None,
        "status": "reported" if value is not None else "unavailable",
        "reason": "No published yield in this source cell." if value is None else None,
        "source": csv_reference(snapshot, row, column, header),
        **extra,
    }


def parse_ecb_curve(
    snapshot: Snapshot,
    curve: str,
    year: int,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    rows = source_csv(snapshot, max_rows=10000)
    if not rows or len(set(rows[0])) != len(rows[0]):
        raise csv_error("ECB curve CSV needs unique column names.")
    headers = rows[0]
    required = {"KEY", "TIME_PERIOD", "OBS_VALUE", "UNIT", "UNIT_MULT", "OBS_STATUS"}
    if not required <= set(headers):
        raise csv_error("ECB curve CSV lacks identity, dates, status or unit metadata.")
    results: dict[str, dict[str, dict[str, Any]]] = {}
    prefix = f"YC.B.U2.EUR.4F.{ECB_UNIVERSES[curve]}.SV_C_YM."
    for index, cells in enumerate(rows[1:], 2):
        if not cells or not any(cells):
            continue
        if len(cells) != len(headers):
            raise csv_error("ECB curve CSV columns are inconsistent.")
        item = dict(zip(headers, cells, strict=True))
        key = item["KEY"]
        code = key.removeprefix(prefix)
        if not key.startswith(prefix) or code not in ECB_MATURITIES:
            raise csv_error("ECB yield identity or rating universe differs from the request.")
        dimensions = {
            "FREQ": "B",
            "REF_AREA": "U2",
            "CURRENCY": "EUR",
            "PROVIDER_FM": "4F",
            "INSTRUMENT_FM": ECB_UNIVERSES[curve],
            "PROVIDER_FM_ID": "SV_C_YM",
            "DATA_TYPE_FM": code,
        }
        if any(name in item and item[name] != expected for name, expected in dimensions.items()):
            raise csv_error("ECB curve dimensions conflict with its series key.")
        if item["UNIT"] != "PCPA" or item["UNIT_MULT"] != "0":
            raise csv_error("ECB curve needs unscaled percent-per-annum observations.")
        observed = item["TIME_PERIOD"]
        try:
            if (
                not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", observed)
                or date.fromisoformat(observed).year != year
            ):
                raise ValueError("Year mismatch")
        except ValueError:
            raise csv_error(
                "ECB curve observation date differs from the requested calendar year."
            ) from None
        maturity = ECB_MATURITIES[code]
        if maturity in results.setdefault(observed, {}):
            raise csv_error("Duplicate ECB maturity/date; observation is ambiguous.")
        record = reported_record(
            snapshot,
            index,
            headers.index("OBS_VALUE") + 1,
            "OBS_VALUE",
            curve,
            observed,
            maturity,
            item["OBS_VALUE"],
            source_series=key,
            observation_status=item["OBS_STATUS"],
            reference_area="U2",
            source_unit=item["UNIT"],
            unit_multiplier=item["UNIT_MULT"],
        )
        if item["OBS_STATUS"] not in {"A", "P", "E"}:
            record.update(
                value=None,
                status="unavailable",
                reason="Missing or unsupported ECB observation status.",
            )
        results[observed][maturity] = record
    return results, csv_reference(snapshot, 1, headers.index("KEY") + 1, "KEY")


def parse_rba_curve(
    snapshot: Snapshot,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    rows = source_csv(snapshot, max_rows=100000)
    metadata: dict[str, list[str]] = {}
    headers = None
    results: dict[str, dict[str, dict[str, Any]]] = {}
    months = {
        name: index
        for index, name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
        )
    }
    document: dict[str, Any] = {}
    footer = False
    for index, cells in enumerate(rows, 1):
        if not cells or not any(cells):
            continue
        if headers is None:
            if cells[0] == "Series ID":
                headers = cells
                if len(set(headers)) != len(headers) or not set(RBA_SERIES) <= set(headers):
                    raise csv_error(
                        "RBA F2 needs each registered nominal daily series exactly once."
                    )
                for code in RBA_SERIES:
                    position = headers.index(code)
                    if (
                        len(metadata.get("units", [])) <= position
                        or metadata["units"][position].casefold()
                        not in {"per cent", "per cent per annum"}
                        or len(metadata.get("frequency", [])) <= position
                        or metadata["frequency"][position].casefold() != "daily"
                    ):
                        raise csv_error(
                            "RBA selected series need daily frequency and percent units."
                        )
                document = csv_reference(snapshot, index, 1, "Series ID")
            elif index <= 24:
                label = cells[0].casefold()
                if label in {"units", "frequency", "source"}:
                    if label in metadata:
                        raise csv_error("RBA CSV has duplicate unit/frequency metadata.")
                    metadata[label] = cells
            else:
                raise csv_error("RBA CSV has no recognized series-ID row.")
            continue
        matched = re.fullmatch(r"([0-9]{1,2})-([A-Za-z]{3})-([0-9]{4})", cells[0])
        if not matched:
            if any(cells[1:]) or cells[0][0].isdigit():
                raise csv_error("Malformed RBA observation date or trailer.")
            footer = True
            continue
        # Older F2 rows omit the trailing indexed-bond cell. All selected nominal
        # columns must still be physically present; no selected cell is invented.
        required_width = max(headers.index(code) for code in RBA_SERIES) + 1
        if footer or not (required_width <= len(cells) <= len(headers) or len(cells) == 1):
            raise csv_error("RBA CSV observations follow a trailer or have inconsistent columns.")
        try:
            observed = date(
                int(matched[3]), months[matched[2].title()], int(matched[1])
            ).isoformat()
        except (ValueError, KeyError):
            raise csv_error("RBA CSV contains an invalid calendar date.") from None
        if observed in results or not "2013-01-01" <= observed <= "2100-12-31":
            raise csv_error("RBA CSV contains a duplicate or out-of-range observation date.")
        results[observed] = {}
        for code, maturity in RBA_SERIES.items():
            position = headers.index(code)
            row = reported_record(
                snapshot,
                index,
                headers.index(code) + 1,
                code,
                "rba",
                observed,
                maturity,
                cells[position] if position < len(cells) else "",
                source_series=code,
                source_provider=(
                    metadata["source"][headers.index(code)]
                    if len(metadata.get("source", [])) > headers.index(code)
                    else None
                ),
            )
            if position >= len(cells):
                row.update(
                    source=csv_reference(snapshot, index, 1, "Series ID"),
                    source_value=None,
                    value_cell_present=False,
                    reason="Source date row has no yield cells.",
                )
            results[observed][maturity] = row
    if headers is None:
        raise csv_error("RBA CSV has no recognized series-ID row.")
    return results, document
