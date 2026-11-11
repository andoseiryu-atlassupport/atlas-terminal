"""Bounded, source-addressable readers for dated BLS CPI HTML release tables."""

import calendar
import re
from datetime import datetime
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from atlas_terminal.cpi_catalog import ARCHIVE_URL, CATEGORIES, shift_month
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot

RELEASE_PATTERN = r"https://www\.bls\.gov/news\.release/archives/cpi_([0-9]{8})\.htm"
RI_PATTERN = r"https://www\.bls\.gov/cpi/tables/relative-importance/(20[0-9]{2})\.htm"
MONTH_NAMES = {
    name.lower(): i
    for i in range(1, 13)
    for name in (calendar.month_name[i], calendar.month_abbr[i])
}
MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(MONTH_NAMES, key=len, reverse=True)) + r")\.?\s+(20\d{2}|19\d{2})\b",
    re.I,
)


def source_error(reason: str) -> AtlasError:
    return AtlasError(
        "SOURCE_UNAVAILABLE",
        "Unsupported or incomplete BLS CPI document.",
        details={"reason": reason},
        status=502,
    )


def cpi_identity(url: str) -> str | None:
    if url == ARCHIVE_URL:
        return "archive"
    if match := re.fullmatch(RELEASE_PATTERN, url):
        try:
            datetime.strptime(match[1], "%m%d%Y")
        except ValueError:
            return None
        return "release"
    if re.fullmatch(RI_PATTERN, url):
        return "weights"
    return None


def text_months(text: str) -> list[str]:
    return [
        f"{int(m[2]):04d}-{MONTH_NAMES[m[1].lower()]:02d}" for m in MONTH_PATTERN.finditer(text)
    ]


def normalized(text: str) -> str:
    text = re.sub(r"\(\d+\)", "", text).replace("’", "'").replace("−", "-")
    return re.sub(r"u\.\s+s\.", "u.s.", " ".join(text.split()).strip().casefold())


def decode_html(body: bytes) -> str:
    """Preserve UTF-8 character offsets; tolerate isolated bad bytes outside tables."""
    text = body.decode("utf-8-sig", errors="surrogateescape")
    invalid = re.compile(r"[\udc80-\udcff]")
    if invalid.search(text):
        for table in re.finditer(r"<table\b.*?(?:</table\s*>|\Z)", text, re.I | re.S):
            if invalid.search(table[0]):
                raise source_error("html_encoding_in_table")
        text = invalid.sub("\ufffd", text)
    return text


class CPIHTML(HTMLParser):
    """Keep original character offsets; expand spans only when reading table headers."""

    def __init__(self, body: bytes) -> None:
        super().__init__(convert_charrefs=True)
        if len(body) > 8 * 1024 * 1024 or b"\x00" in body:
            raise source_error("html_size_or_encoding")
        self.original = decode_html(body)
        self.starts = [0]
        self.starts.extend(m.end() for m in re.finditer("\n", self.original))
        self.tables: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.table: dict[str, Any] | None = None
        self.row: list[dict[str, Any]] | None = None
        self.cell: dict[str, Any] | None = None
        self.link: dict[str, Any] | None = None
        self.caption = False
        self.heading = False
        self.heading_text = ""
        self.last_heading = ""
        self.ignored: str | None = None
        self.tags = 0
        self.feed(self.original)
        self.close()
        if self.table is not None or self.cell is not None:
            raise source_error("unclosed_table")

    def source_offset(self) -> int:
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags += 1
        if self.tags > 250000:
            raise source_error("html_tag_limit")
        if self.ignored:
            return
        if tag in {"script", "style"}:
            self.ignored = tag
            return
        a = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4"}:
            self.heading = True
            self.heading_text = ""
        if tag == "table":
            if self.table is not None or len(self.tables) >= 100:
                raise source_error("nested_or_excessive_tables")
            self.table = {
                "caption": "",
                "heading": self.last_heading,
                "rows": [],
                "index": len(self.tables) + 1,
                "start": self.source_offset(),
            }
            self.tables.append(self.table)
        elif tag == "caption" and self.table is not None:
            self.caption = True
        elif tag == "tr" and self.table is not None:
            if self.row is not None:
                raise source_error("overlapping_table_rows")
            self.row = []
            self.table["rows"].append(self.row)
            if len(self.table["rows"]) > 3000:
                raise source_error("table_row_limit")
        elif tag in {"td", "th"} and self.row is not None and self.table is not None:
            if self.cell is not None or len(self.row) >= 32:
                raise source_error("overlapping_or_excessive_cells")
            spans = []
            for name in ("rowspan", "colspan"):
                value = a.get(name, "1") or "1"
                if not value.isdigit() or not 1 <= int(value) <= 32:
                    raise source_error("unsupported_cell_span")
                spans.append(int(value))
            self.cell = {
                "text": "",
                "start": self.source_offset(),
                "line": self.getpos()[0],
                "row": len(self.table["rows"]),
                "column": len(self.row) + 1,
                "rowspan": spans[0],
                "colspan": spans[1],
                "tag": tag,
            }
            self.row.append(self.cell)
        if tag == "a" and a.get("href"):
            self.link = {
                "href": a["href"],
                "text": "",
                "start": self.source_offset(),
                "line": self.getpos()[0],
            }
        if tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.ignored:
            if tag == self.ignored:
                self.ignored = None
            return
        if tag in {"p", "div", "li"}:
            self.handle_data(" ")
        if tag in {"h1", "h2", "h3", "h4"} and self.heading:
            self.last_heading = self.heading_text
            self.heading = False
        if tag == "a" and self.link is not None:
            self.link["end"] = self.original.index(">", self.source_offset()) + 1
            self.links.append(self.link)
            self.link = None
        if tag in {"td", "th"} and self.cell is not None:
            self.cell["end"] = self.original.index(">", self.source_offset()) + 1
            self.cell = None
        elif tag == "tr":
            self.row = None
        elif tag == "caption":
            self.caption = False
        elif tag == "table" and self.table is not None:
            self.table["end"] = self.original.index(">", self.source_offset()) + 1
            self.table = None

    def handle_data(self, data: str) -> None:
        if self.ignored:
            return
        if self.cell is not None:
            self.cell["text"] += data
        if self.link is not None:
            self.link["text"] += data
        if self.caption and self.table is not None:
            self.table["caption"] += data
        if self.heading:
            self.heading_text += data


def reference(
    snapshot: Snapshot, cell: dict[str, Any], *, table: str, published_at: str | None = None
) -> dict[str, Any]:
    return {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        "published_at": published_at,
        "text_line": cell.get("line", 1),
        "html_start_char": cell.get("start", 0),
        "html_end_char": cell["end"] if "end" in cell else len(decode_html(snapshot.body)),
        "html_table": table,
        "html_row": cell.get("row"),
        "html_column": cell.get("column"),
        "offset_encoding": (
            "utf-8-sig decoded characters; invalid non-table bytes replaced by U+FFFD"
        ),
    }


def archive_releases(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    html = CPIHTML(snapshot.body)
    result: dict[str, dict[str, Any]] = {}
    for anchor in html.links:
        url = urljoin(ARCHIVE_URL, anchor["href"])
        if cpi_identity(url) != "release":
            continue
        periods = text_months(anchor["text"])
        # The exact CPI archive path establishes the dataset. Archive editions
        # may put "Consumer Price Index" outside the month-labelled anchor.
        if len(periods) != 1:
            continue
        match = re.fullmatch(RELEASE_PATTERN, url)
        assert match is not None
        published = datetime.strptime(match[1], "%m%d%Y").date().isoformat()
        month = periods[0]
        if published[:7] <= month:
            raise source_error("archive_publication_period_mismatch")
        if month in result and result[month]["url"] != url:
            raise source_error("ambiguous_archive_editions")
        result[month] = {
            "url": url,
            "published_at": published,
            "source": reference(snapshot, anchor, table="release archive"),
        }
    if not result or len(result) > 1400:
        raise source_error("dated_html_releases_not_found")
    return result


def grid(table: dict[str, Any]) -> list[list[dict[str, Any] | None]]:
    occupied: dict[tuple[int, int], dict[str, Any]] = {}
    for r, row in enumerate(table["rows"]):
        column = 0
        for cell in row:
            while (r, column) in occupied:
                column += 1
            for y in range(r, r + cell["rowspan"]):
                for x in range(column, column + cell["colspan"]):
                    if x >= 32 or y >= 3000 or (y, x) in occupied:
                        raise source_error("ambiguous_table_grid")
                    occupied[y, x] = cell
            column += cell["colspan"]
    width = max((x + 1 for _, x in occupied), default=0)
    return [[occupied.get((r, x)) for x in range(width)] for r in range(len(table["rows"]))]


def number_cell(
    snapshot: Snapshot, cell: dict[str, Any], table: str, published_at: str | None
) -> dict[str, Any]:
    raw = cell["text"]
    clean = normalized(raw).replace(",", "")
    value = None
    if clean not in {"", "-", "—", "–", "n/a", "na", "(n)", "(r)"}:
        if not re.fullmatch(r"[+-]?[0-9]{1,16}(?:\.[0-9]{1,9})?", clean):
            raise source_error("unrecognized_numeric_cell")
        value = clean
    return {
        "value": value,
        "raw_value": raw,
        "reason": None if value is not None else "source_value_missing",
        "source": reference(snapshot, cell, table=table, published_at=published_at),
    }


def _table_rows(table: dict[str, Any]) -> tuple[list[str], list[list[dict[str, Any] | None]]]:
    rows = grid(table)
    labels = {normalized(spec["label"]) for spec in CATEGORIES.values()}
    first = next(
        (
            i
            for i, row in enumerate(rows)
            if row and row[0] and normalized(row[0]["text"]) in labels
        ),
        None,
    )
    if first is None or first == 0:
        raise source_error("expenditure_header_or_rows_missing")
    headers = []
    for column in range(len(rows[0])):
        cells: list[dict[str, Any]] = []
        for row in rows[:first]:
            cell = row[column]
            if cell is not None and all(cell is not seen for seen in cells):
                cells.append(cell)
        headers.append(" ".join(c["text"] for c in cells))
    return headers, rows[first:]


def _one(columns: list[int], reason: str) -> int:
    if len(columns) != 1:
        raise source_error(reason)
    return columns[0]


def release_tables(snapshot: Snapshot, month: str, published_at: str) -> dict[str, Any]:
    html = CPIHTML(snapshot.body)
    output: dict[str, Any] = {"month": month, "tables": {}, "indexes": {}, "issues": []}
    labels = {normalized(spec["label"]): key for key, spec in CATEGORIES.items()}
    for table in html.tables:
        title = table["caption"] or table["heading"]
        name = normalized(title)
        # A caption's explicit number/scope wins over a stale preceding heading.
        # In particular, a CPI-W or regional table must never inherit CPI-U scope.
        match = re.search(r"\btable\s+([0-9]+)\.", name)
        if match is None:
            match = re.search(r"\btable\s+([0-9]+)\.", normalized(table["heading"]))
        if (
            not match
            or match[1] not in {"1", "6", "7"}
            or "(cpi-u)" not in name
            or "u.s. city average" not in name
        ):
            continue
        number = int(match[1])
        if text_months(title) != [month]:
            raise source_error("release_table_period_mismatch")
        if number in output["tables"]:
            raise source_error("duplicate_analysis_table")
        headers, rows = _table_rows(table)
        weight_col = _one(
            [i for i, h in enumerate(headers) if "relative importance" in normalized(h)],
            "weight_column_missing",
        )
        weight_periods = text_months(headers[weight_col])
        if weight_periods != [shift_month(month, -1)]:
            raise source_error("weight_base_month_mismatch")
        parsed: dict[str, Any] = {"weight_base_month": weight_periods[0], "rows": {}}
        columns: dict[str, int] = {"relative_importance_pct": weight_col}
        if number in {6, 7}:
            expected_adjustment = "seasonally adjusted" if number == 6 else "unadjusted"
            # Table 7's caption/header can say "unadjusted" or "not seasonally adjusted".
            prefix = "seasonally adjusted" if number == 6 else "unadjusted"
            base = shift_month(month, -1 if number == 6 else -12)
            for field_key, phrase in (
                ("rate_pct", "percent change"),
                ("effect_pt", "effect on all items"),
            ):
                candidates = []
                for i, h in enumerate(headers):
                    clean = normalized(h).replace("not seasonally adjusted", "unadjusted")
                    if phrase in clean and prefix in clean and text_months(h) == [base, month]:
                        candidates.append(i)
                columns[field_key] = _one(
                    candidates, f"{expected_adjustment}_{field_key}_column_missing"
                )
        else:
            for i, h in enumerate(headers):
                periods = text_months(h)
                if "unadjusted indexes" in normalized(h) and len(periods) == 1:
                    columns["index:" + periods[0]] = i
            if "index:" + month not in columns:
                raise source_error("unadjusted_indexes_missing")
            for offset, key in ((-1, "mom_rate"), (-12, "yoy_rate")):
                matches = [
                    i
                    for i, h in enumerate(headers)
                    if "unadjusted percent change" in normalized(h)
                    and text_months(h) == [shift_month(month, offset), month]
                ]
                if matches:
                    columns[key] = _one(matches, "duplicate_unadjusted_rate_column")
        # BLS footnote numbers are table-local. Keep the local table text and the
        # short following text (up to the next table), never another table's notes.
        next_start = min(
            (t["start"] for t in html.tables if t["start"] > table["start"]),
            default=len(html.original),
        )
        local_text = unescape(re.sub(r"<[^>]*>", " ", html.original[table["start"] : next_start]))
        notes = {
            m[1]
            for m in re.finditer(r"\((\d+)\)\s*Not\s+seasonally\s+adjusted\b", local_text, re.I)
        }
        for row in rows:
            if not row or row[0] is None:
                continue
            category = labels.get(normalized(row[0]["text"]))
            if category is None:
                continue
            if category in parsed["rows"]:
                raise source_error("duplicate_expenditure_row")
            data: dict[str, Any] = {}
            for field, column in columns.items():
                cell = row[column]
                if cell is None or cell["colspan"] != 1 or cell["rowspan"] != 1:
                    raise source_error("ambiguous_numeric_cell_span")
                data[field] = number_cell(snapshot, cell, title, published_at)
                if field == "relative_importance_pct" and data[field]["value"] is not None:
                    if not 0 <= Decimal(data[field]["value"]) <= 100:
                        raise source_error("relative_importance_outside_percent_bounds")
                data[field]["source"]["header"] = headers[column]
            data["rate_adjustment"] = (
                "nsa"
                if number != 6 or notes.intersection(re.findall(r"\((\d+)\)", row[0]["text"]))
                else "sa"
            )
            parsed["rows"][category] = data
        if "cpi" not in parsed["rows"]:
            raise source_error("all_items_row_missing")
        output["tables"][number] = parsed
    if not output["tables"]:
        raise source_error("supported_cpi_u_tables_not_found")
    return output


def annual_weights(snapshot: Snapshot, pivot_year: int) -> dict[str, Any]:
    """Select the new annual expenditure vintage explicitly, not the first table."""
    html = CPIHTML(snapshot.body)
    candidates = []
    for table in html.tables:
        title = table["caption"] or table["heading"]
        clean = normalized(title)
        number = re.search(r"\btable\s+([0-9]+)\b", clean)
        if number is None:
            number = re.search(r"\btable\s+([0-9]+)\b", normalized(table["heading"]))
        vintages = re.findall(r"\(([0-9]{4}) weights\)", clean)
        if not vintages:
            vintages = re.findall(r"\(([0-9]{4}) weights\)", normalized(table["heading"]))
        if (
            vintages == [str(pivot_year - 1)]
            and number is not None
            and number[1] == "1"
            and "u.s. city average" in clean
            and text_months(title) == [f"{pivot_year}-12"]
        ):
            candidates.append(table)
    if len(candidates) != 1:
        raise source_error("new_annual_weight_vintage_not_identified")
    table = candidates[0]
    headers, rows = _table_rows(table)
    column = _one(
        [
            i
            for i, h in enumerate(headers)
            if "cpi-u" in normalized(h) and "u.s. city average" in normalized(h)
        ],
        "annual_cpi_u_column_missing",
    )
    labels = {normalized(v["label"]): k for k, v in CATEGORIES.items()}
    result: dict[str, Any] = {}
    for row in rows:
        cell = row[column]
        if row[0] is None or cell is None:
            continue
        key = labels.get(normalized(row[0]["text"]))
        if key is not None:
            if key in result:
                raise source_error("duplicate_annual_weight_row")
            result[key] = number_cell(snapshot, cell, table["caption"] or table["heading"], None)
            result[key]["expenditure_weight_year"] = pivot_year - 1
    return result
