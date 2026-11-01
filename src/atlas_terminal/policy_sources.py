"""Fixed central-bank policy instruments, never overnight-market-rate proxies."""

import re
from datetime import date
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.fixed_income_sources import csv_error as source_error
from atlas_terminal.fixed_income_sources import csv_reference
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.regional_bond_sources import decimal_cell, source_csv

RBA_CASH_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"
ECB_POLICY_BASE = "https://data-api.ecb.europa.eu/service/data/FM/"
POLICY_SERIES = {
    "ecb_deposit_facility": {
        "code": "DFR",
        "source_id": "ecb",
        "country": "EA",
        "currency": "EUR",
        "title": "ECB deposit facility rate",
    },
    "ecb_main_refinancing": {
        "code": "MRR_FR",
        "source_id": "ecb",
        "country": "EA",
        "currency": "EUR",
        "title": "ECB main refinancing operations fixed rate",
    },
    "ecb_marginal_lending": {
        "code": "MLFR",
        "source_id": "ecb",
        "country": "EA",
        "currency": "EUR",
        "title": "ECB marginal lending facility rate",
    },
    "rba_cash_target": {
        "code": "FIRMMCRTD",
        "source_id": "rba",
        "country": "AU",
        "currency": "AUD",
        "title": "RBA cash rate target",
    },
}


def policy_url(series: str, start: str, end: str) -> str:
    if series not in POLICY_SERIES:
        raise AtlasError("INVALID_INPUT", "Choose a registered central-bank policy instrument.")
    for point in (start, end):
        if (
            not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", point)
            or not 1900 <= date.fromisoformat(point).year <= 2100
        ):
            raise AtlasError("INVALID_INPUT", "Invalid policy rate observation dates.")
    if start > end:
        raise AtlasError("INVALID_INPUT", "Policy observation dates must be ordered.")
    if series == "rba_cash_target":
        return RBA_CASH_URL
    code = POLICY_SERIES[series]["code"]
    return (
        f"{ECB_POLICY_BASE}D.U2.EUR.4F.KR.{code}.LEV"
        f"?startPeriod={start}&endPeriod={end}&format=csvdata"
    )


def policy_identity(url: str) -> tuple[str, str, str] | None:
    if url == RBA_CASH_URL:
        return "rba_cash_target", "1900-01-01", "2100-12-31"
    match = re.fullmatch(
        re.escape(ECB_POLICY_BASE) + r"D\.U2\.EUR\.4F\.KR\.([A-Z_]+)\.LEV\?startPeriod=([0-9-]{10})"
        r"&endPeriod=([0-9-]{10})&format=csvdata",
        url,
    )
    if match:
        for name, spec in POLICY_SERIES.items():
            if spec["source_id"] == "ecb" and spec["code"] == match[1]:
                try:
                    if policy_url(name, match[2], match[3]) == url:
                        return name, match[2], match[3]
                except (AtlasError, ValueError):
                    return None
    return None


def parse_policy(snapshot: Snapshot, series: str) -> list[dict[str, Any]]:
    spec = POLICY_SERIES[series]
    rows = source_csv(snapshot)
    output: list[dict[str, Any]] = []
    seen = set()

    def add(day: str, raw: str, row: int, column: int, header: str, **extra: Any) -> None:
        try:
            if (
                not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day)
                or not 1900 <= date.fromisoformat(day).year <= 2100
            ):
                raise ValueError
        except ValueError:
            raise source_error("Invalid central-bank policy observation date.") from None
        if day in seen:
            raise source_error("Duplicate policy observation date.")
        seen.add(day)
        value = decimal_cell(raw)
        output.append(
            {
                "series": series,
                "date": day,
                "value": value,
                "source_value": raw,
                "unit": "percent_per_annum",
                "frequency": "daily",
                "country": spec["country"],
                "currency": spec["currency"],
                "rate_type": "central_bank_policy_rate",
                "policy_instrument": spec["title"],
                "source_series": spec["code"],
                "status": "reported" if value is not None else "unavailable",
                "published_at": None,
                "derived": False,
                "source": csv_reference(snapshot, row, column, header),
                **extra,
            }
        )

    if spec["source_id"] == "ecb":
        if not rows or len(set(rows[0])) != len(rows[0]):
            raise source_error("ECB policy CSV needs unique headers.")
        header = rows[0]
        expected = {
            "KEY": f"FM.D.U2.EUR.4F.KR.{spec['code']}.LEV",
            "FREQ": "D",
            "REF_AREA": "U2",
            "CURRENCY": "EUR",
            "PROVIDER_FM": "4F",
            "INSTRUMENT_FM": "KR",
            "PROVIDER_FM_ID": spec["code"],
            "DATA_TYPE_FM": "LEV",
            "UNIT": "PCPA",
            "UNIT_MULT": "0",
        }
        if not set(expected) | {"TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"} <= set(header):
            raise source_error("ECB policy CSV headers changed.")
        for index, row in enumerate(rows[1:], 2):
            if not row:
                continue
            if len(row) != len(header):
                raise source_error("ECB policy CSV row width changed.")
            data = dict(zip(header, row, strict=True))
            if any(data[k] != value for k, value in expected.items()):
                raise source_error("ECB policy instrument, frequency or unit identity changed.")
            if data["OBS_STATUS"] not in {"A", "P", "E", "M", "L"}:
                raise source_error("Unsupported ECB policy observation status.")
            if data["OBS_STATUS"] in {"M", "L"} and data["OBS_VALUE"] not in {"", "."}:
                raise source_error("A missing ECB policy observation contains a value.")
            add(
                data["TIME_PERIOD"],
                data["OBS_VALUE"],
                index,
                header.index("OBS_VALUE") + 1,
                "OBS_VALUE",
                observation_status=data["OBS_STATUS"],
                source_title=data.get("TITLE"),
            )
    else:
        ids = [i for i, row in enumerate(rows) if row and row[0] == "Series ID"]
        if len(ids) != 1 or rows[ids[0]].count(spec["code"]) != 1:
            raise source_error("RBA cash-target series identity changed.")
        pos = ids[0]
        column = rows[pos].index(spec["code"])
        metadata = {row[0]: row[column] for row in rows[:pos] if len(row) > column and row[0]}
        if (
            metadata.get("Title") != "Cash Rate Target"
            or metadata.get("Units") != "Per cent"
            or metadata.get("Frequency") != "Daily"
            or metadata.get("Source") != "RBA"
            or metadata.get("Type") != "Original"
        ):
            raise source_error("RBA cash-target metadata changed.")
        import datetime

        for index, row in enumerate(rows[pos + 1 :], pos + 2):
            if not row:
                continue
            if not column < len(row) <= len(rows[pos]):
                raise source_error("RBA cash-target column is absent or the row is too wide.")
            try:
                day = datetime.datetime.strptime(row[0], "%d-%b-%Y").date().isoformat()
            except ValueError:
                raise source_error("Invalid RBA policy source date.") from None
            add(
                day,
                row[column],
                index,
                column + 1,
                spec["code"],
                source_publication_date=metadata.get("Publication date"),
            )
    return sorted(output, key=lambda r: r["date"])


def policy_rates(transport: Transport, q: Any) -> dict[str, Any]:
    spec = POLICY_SERIES[q.series]
    today = date.today()
    start, end = q.start_date or f"{today.year - 3}-01-01", q.end_date or today.isoformat()
    snapshot = transport.get(policy_url(q.series, start, end), 21600)
    all_rows = parse_policy(snapshot, q.series)
    matching = [row for row in all_rows if start <= row["date"] <= end]
    rows = list(reversed(matching))[: q.limit]
    return {
        "source_id": spec["source_id"],
        "records": rows,
        "sources": [csv_reference(snapshot, 1, 1, "source_header")],
        "coverage": {
            "matching_records": len(matching),
            "returned_records": len(rows),
            "truncated": len(matching) > q.limit,
            "point_in_time": False,
            "source_first_date": all_rows[0]["date"] if all_rows else None,
            "source_last_date": all_rows[-1]["date"] if all_rows else None,
        },
        "limitations": [
            "Official selected instruments only; deposit, refinancing, lending and "
            "cash-target rates are different instruments.",
            "No synthetic daily forward fill, event date, announcement timestamp or "
            "overnight-market proxy is inferred.",
            "RBA returns a rolling published table; its first source row bounds available history.",
            "Current revised source observations do not establish historical "
            "release-time availability.",
        ],
    }
