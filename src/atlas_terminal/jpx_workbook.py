"""Read the single published JPX reference worksheet with exact cell coordinates."""

import re
from typing import Any

import xlrd

from atlas_terminal.public_sources import xml_root
from atlas_terminal.source_archives import bounded_zip, member_bytes

SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def xlsx_table(body: bytes) -> tuple[str, list[list[str]]]:
    with bounded_zip(body) as archive:
        workbook = xml_root(member_bytes(archive, "xl/workbook.xml"))
        sheets = workbook.findall(SS + "sheets/" + SS + "sheet")
        if len(sheets) != 1:
            raise ValueError("Expected one JPX worksheet")
        relationships = xml_root(member_bytes(archive, "xl/_rels/workbook.xml.rels"))
        targets = [
            node.get("Target")
            for node in relationships.findall(PACKAGE + "Relationship")
            if node.get("Id") == sheets[0].get(REL + "id")
            and node.get("Type") == REL[1:-1] + "/worksheet"
            and node.get("TargetMode", "Internal") == "Internal"
        ]
        if len(targets) != 1 or targets[0] != "worksheets/sheet1.xml":
            raise ValueError("Unsupported worksheet relationship")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = xml_root(member_bytes(archive, "xl/sharedStrings.xml"))
            strings = ["".join(n.itertext()) for n in shared.findall(SS + "si")]
        sheet = xml_root(member_bytes(archive, "xl/worksheets/sheet1.xml"))
        rows = []
        for index, row in enumerate(sheet.findall(SS + "sheetData/" + SS + "row"), 1):
            if row.get("r") != str(index) or index > 10000:
                raise ValueError("Missing, duplicated or oversized worksheet row")
            cells, seen = [""] * 32, set()
            for cell in row.findall(SS + "c"):
                coordinate = re.fullmatch(r"([A-Z]{1,2})([1-9][0-9]*)", cell.get("r", ""))
                if not coordinate or coordinate[2] != str(index):
                    raise ValueError("Invalid worksheet coordinate")
                column = 0
                for char in coordinate[1]:
                    column = column * 26 + ord(char) - ord("A") + 1
                if column > 32 or column in seen or cell.find(SS + "f") is not None:
                    raise ValueError("Duplicate, formula or unsupported worksheet cell")
                seen.add(column)
                kind = cell.get("t", "n")
                value = cell.findtext(SS + "v", "")
                if kind == "s":
                    if not value.isdigit() or int(value) >= len(strings):
                        raise ValueError("Unknown shared string")
                    value = strings[int(value)]
                elif kind == "inlineStr":
                    value = "".join(cell.find(SS + "is").itertext())  # type: ignore[union-attr]
                elif kind != "n":
                    raise ValueError("Unsupported worksheet cell type")
                if len(value) > 4096:
                    raise ValueError("Oversized worksheet cell")
                cells[column - 1] = value.strip()
            rows.append(cells)
        if not rows:
            raise ValueError("Empty worksheet")
        # Empty padding has no reported heading and is never a reported cell.
        width = max(i for i, value in enumerate(rows[0], 1) if value)
        if any(any(row[width:]) for row in rows):
            raise ValueError("Values without worksheet headings")
        return sheets[0].get("name", ""), [row[:width] for row in rows]


def workbook_table(body: bytes) -> tuple[str, str, list[list[str]]]:
    if body.startswith(b"PK"):
        name, rows = xlsx_table(body)
        return name, "xlsx_worksheet_cell", rows
    book = xlrd.open_workbook(file_contents=body, on_demand=True)
    try:
        if book.nsheets != 1:
            raise ValueError("Expected one JPX sheet")
        sheet = book.sheet_by_index(0)
        if sheet.nrows > 10000 or sheet.ncols > 32:
            raise ValueError("Oversized JPX sheet")

        def text(value: Any) -> str:
            return (
                str(int(value))
                if isinstance(value, float) and value.is_integer()
                else str(value).strip()
            )

        return (
            sheet.name,
            "xls_worksheet_cell",
            [[text(v) for v in sheet.row_values(i)] for i in range(sheet.nrows)],
        )
    finally:
        book.release_resources()
