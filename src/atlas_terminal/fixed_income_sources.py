"""Bounded Ministry of Finance CSV contracts and original-cell evidence."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot

JGB_BASE = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
JGB_URLS = (JGB_BASE + "historical/jgbcme_all.csv", JGB_BASE + "jgbcme.csv")
JGB_MATURITIES = tuple(f"{year}YEAR" for year in (*range(1, 11), 15, 20, 25, 30, 40))
JGB_HEADERS = {maturity: maturity.replace("YEAR", "Y") for maturity in JGB_MATURITIES}


def csv_error(message: str) -> AtlasError:
    return AtlasError("SOURCE_UNAVAILABLE", message, status=502)


def csv_reference(snapshot: Snapshot, row: int, column: int, header: str) -> dict[str, Any]:
    return {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        "csv_row": row,
        "csv_column": column,
        "csv_header": header,
    }


@dataclass(frozen=True)
class JgbObservation:
    observed: str
    values: dict[str, str | None]
    snapshot: Snapshot
    row: int
    headers: tuple[str, ...]

    def record(self, maturity: str) -> dict[str, Any]:
        header = JGB_HEADERS[maturity]
        present = header in self.headers
        value = self.values.get(maturity)
        return {
            "date": self.observed,
            "maturity": maturity,
            "value": value,
            "unit": "percent_per_annum",
            "currency": "JPY",
            "country": "JP",
            "rate_type": "nominal_constant_maturity",
            "frequency": "business_daily",
            "published_at": None,
            "status": "reported" if value is not None else "unavailable",
            "reason": "Maturity absent or marked unavailable in this MOF observation."
            if value is None
            else None,
            "source": csv_reference(
                self.snapshot,
                self.row,
                self.headers.index(header) + 1 if present else 1,
                header if present else "Date",
            ),
        }


def parse_jgb_csv(snapshot: Snapshot) -> tuple[dict[str, JgbObservation], dict[str, Any]]:
    if len(snapshot.body) > 16 * 1024 * 1024 or b"\x00" in snapshot.body:
        raise csv_error("MOF CSV exceeds bounds or contains unsupported bytes.")
    try:
        text = snapshot.body.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = snapshot.body.decode("cp932")
        except UnicodeDecodeError:
            raise csv_error("MOF CSV has an unsupported text encoding.") from None
    headers: tuple[str, ...] | None = None
    preamble: list[str] = []
    rows: dict[str, JgbObservation] = {}
    document: dict[str, Any] = {}
    footer = False
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        for index, cells in enumerate(reader, 1):
            if index > 30000 or len(cells) > 16 or any(len(cell) > 4096 for cell in cells):
                raise csv_error("MOF CSV row or column bounds exceeded.")
            cells = [cell.strip() for cell in cells]
            if headers is None:
                if cells and cells[0] == "Date":
                    headers = tuple(cells)
                    if (
                        len(set(headers)) != len(headers)
                        or "10Y" not in headers
                        or set(headers[1:]) - set(JGB_HEADERS.values())
                        or not re.search(r"\bunit\s*:\s*%", " ".join(preamble), re.I)
                    ):
                        raise csv_error("MOF CSV needs unique dated year tenors and percent units.")
                    document = csv_reference(snapshot, index, 1, "Date")
                elif index <= 10:
                    preamble.extend(cells)
                else:
                    raise csv_error("MOF CSV has no recognized header.")
                continue
            if not cells or not any(cells):
                continue
            if not re.fullmatch(r"[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}", cells[0]):
                if any(cells[1:]) or cells[0][0].isdigit():
                    raise csv_error("Malformed MOF CSV observation date or trailer.")
                footer = True
                continue
            if footer or len(cells) != len(headers):
                raise csv_error("MOF observations follow a trailer or have inconsistent columns.")
            try:
                observed = date(*(int(part) for part in cells[0].split("/"))).isoformat()
            except ValueError:
                raise csv_error("MOF CSV contains an invalid calendar date.") from None
            if not "1974-09-24" <= observed <= "2100-12-31" or observed in rows:
                raise csv_error("MOF CSV contains an out-of-range or duplicate observation date.")
            values = {}
            for header, value in zip(headers[1:], cells[1:], strict=True):
                if value not in {"", "-"} and not re.fullmatch(
                    r"[+-]?[0-9]{1,3}(?:\.[0-9]{1,20})?", value
                ):
                    raise csv_error("Malformed MOF yield; only '-' or blank denotes missing data.")
                values[header.replace("Y", "YEAR")] = None if value in {"", "-"} else value
            rows[observed] = JgbObservation(observed, values, snapshot, index, headers)
    except csv.Error:
        raise csv_error("Malformed MOF CSV quoting.") from None
    if headers is None:
        raise csv_error("MOF CSV has no recognized header.")
    return rows, document
