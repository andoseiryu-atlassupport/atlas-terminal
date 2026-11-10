"""Cleveland Fed's published monthly median and 16% trimmed-mean CPI CSVs."""

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from atlas_terminal.economic_dates import month_end
from atlas_terminal.errors import AtlasError
from atlas_terminal.fixed_income_sources import csv_reference
from atlas_terminal.providers import Snapshot

INFLATION_DOCUMENTATION = "https://www.clevelandfed.org/indicators-and-data/median-cpi"
INFLATION_URLS = {
    kind: "https://www.clevelandfed.org/-/media/files/webcharts/mediancpi/"
    + filename
    + "_revised.csv?sc_lang=en"
    for kind, filename in (("trimmed_mean_cpi", "trim"), ("median_cpi", "mcpi"))
}
INFLATION_SERIES = {
    kind + suffix: {
        "title": ("16% trimmed-mean CPI" if kind == "trimmed_mean_cpi" else "Median CPI")
        + (", annualized monthly change" if annualized else ", monthly change"),
        "kind": kind,
        "column": 3 if annualized else 2,
        "unit": "percent" if annualized else "ratio",
        "annualized": annualized,
        "category_path": ["cpi", "trimmed" if kind == "trimmed_mean_cpi" else "median"],
    }
    for kind in INFLATION_URLS
    for suffix, annualized in (("_mom", False), ("_annualized", True))
}
INFLATION_SOURCE = {
    "name": "Federal Reserve Bank of Cleveland inflation measures",
    "upstream_price": "0",
    "optional": True,
    "api_key_required": False,
    "contact_user_agent_required": False,
    "documentation": INFLATION_DOCUMENTATION,
    "terms": "https://www.clevelandfed.org/privacy",
    "use": (
        "The published reuse permission is for attributed noncommercial, "
        "personal or educational use. "
        "Commercial redistribution rights are not established; "
        "excluded from the BLS seller catalog."
    ),
    "cadence": (
        "Monthly, seasonally adjusted median and 16% trimmed-mean CPI; current revised history."
    ),
}


def parse_inflation_csv(snapshot: Snapshot, kind: str) -> list[dict[str, Any]]:
    """Fail closed on changed layout/duplicate months; preserve missing cells and raw spellings."""

    def invalid() -> AtlasError:
        return AtlasError(
            "SOURCE_UNAVAILABLE", "Unsupported Cleveland Fed inflation CSV.", status=502
        )

    if len(snapshot.body) > 2 * 1024 * 1024 or b"\x00" in snapshot.body:
        raise invalid()
    prefix = "trim" if kind == "trimmed_mean_cpi" else "mcpi"
    expected = ["date", prefix + "_monthly_chg", prefix + "_ann_monthly_chg"]
    try:
        reader = csv.reader(io.StringIO(snapshot.body.decode("utf-8-sig")), strict=True)
        headers = next(reader)
        if reader.line_num != 1 or [s.strip() for s in headers] != expected:
            raise invalid()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in reader:
            line = reader.line_num
            if line > 3001 or len(row) != 3 or any("\n" in s or "\r" in s for s in row):
                raise invalid()
            label = row[0].strip()
            if not re.fullmatch(r"[0-9]{1,2}/1/[0-9]{4}", label):
                raise invalid()
            month, day, year = (int(s) for s in label.split("/"))
            date(year, month, day)
            if not 1983 <= year <= 2100:
                raise invalid()
            observed = month_end(year, month)
            if observed in seen:
                raise invalid()
            seen.add(observed)
            for column in (2, 3):
                original = row[column - 1]
                raw = original.strip()
                value = None
                if raw not in {"", "NA", "N/A", "."}:
                    parsed = Decimal(raw)
                    if not parsed.is_finite() or len(raw) > 80:
                        raise invalid()
                    value = str(parsed)
                rows.append(
                    {
                        "date": observed,
                        "source_period": label,
                        "column": column,
                        "value": value,
                        "raw_value": original,
                        "source": csv_reference(snapshot, line, column, headers[column - 1]),
                    }
                )
        if not rows:
            raise invalid()
        return rows
    except (UnicodeError, csv.Error, StopIteration, ValueError, InvalidOperation):
        raise invalid() from None
