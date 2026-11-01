"""Bounded visible filing text and reported HTML cells with original coordinates."""

import re
from html.parser import HTMLParser
from typing import Any

from atlas_terminal.providers import Filing, Snapshot
from atlas_terminal.research_macro import source_error
from atlas_terminal.research_models import ResearchQuery

BLOCKS = {"p", "div", "br", "hr", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
ITEM = re.compile(r"^\s*Item\s+([0-9]{1,2}(?:\.[0-9]{2}|[a-c])?)\s*[.\-:–—]?\s+(.{0,240})$", re.I)


class FilingHTML(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_text = text
        self.line_starts = [0, *(m.end() for m in re.finditer("\n", text))]
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[tuple[str, int, int, int, int]] = []
        self.tables: list[dict[str, Any]] = []
        self.active_tables: list[dict[str, Any]] = []
        self.cells: list[dict[str, Any]] = []
        self.cell_count = 0
        self.text_size = 0
        self.tag_count = 0

    def position(self) -> tuple[int, int]:
        line, column = self.getpos()
        return line, self.line_starts[line - 1] + column

    def visible(self) -> bool:
        return not self.stack or not self.stack[-1][1]

    def append(self, text: str, *, source_data: bool = False) -> None:
        if not self.visible():
            return
        line, offset = self.position()
        self.text_size += len(text)
        if self.text_size > 8_000_000 or len(self.parts) > 250_000:
            raise source_error("Filing HTML visible text exceeds supported bounds.")
        end = self.source_text.find("<", offset) if source_data else offset
        if end < 0:
            end = len(self.source_text)
        last_line = line + self.source_text[offset:end].count("\n")
        self.parts.append((text, line, offset, end, last_line))
        if (
            self.cells
            and self.active_tables
            and (self.cells[-1]["table_index"] == self.active_tables[-1]["table_index"])
        ):
            self.cells[-1]["parts"].append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag_count += 1
        if self.tag_count > 250_000 or len(self.stack) >= 1024:
            raise source_error("Filing HTML tag count or nesting exceeds bounds.")
        values = dict(attrs)
        style = re.sub(r"\s+", "", values.get("style") or "").lower()
        hidden = (
            not self.visible()
            or tag in {"script", "style", "ix:hidden", "head"}
            or "hidden" in values
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag in BLOCKS and not hidden:
            self.append("\n")
        if tag not in VOID:
            self.stack.append((tag, hidden))
        if hidden:
            return
        line, offset = self.position()
        if tag == "table":
            if len(self.tables) >= 500 or len(self.active_tables) >= 16:
                raise source_error("Filing HTML table nesting/count exceeds bounds.")
            table = {"table_index": len(self.tables) + 1, "row_index": 0, "cell_index": 0}
            self.tables.append(table)
            self.active_tables.append(table)
        elif tag == "tr" and self.active_tables:
            self.active_tables[-1]["row_index"] += 1
            self.active_tables[-1]["cell_index"] = 0
        elif tag in {"td", "th"} and self.active_tables:
            table = self.active_tables[-1]
            if self.cells and self.cells[-1]["table_index"] == table["table_index"]:
                raise source_error("Close a reported HTML table cell before the next cell.")
            table["cell_index"] += 1
            if table["row_index"] < 1:
                raise source_error("Reported HTML cell lacks a row.")
            spans = {}
            for key in ("rowspan", "colspan"):
                raw = values.get(key) or "1"
                if not re.fullmatch(r"[1-9][0-9]{0,2}", raw):
                    raise source_error("Unsupported HTML table span.")
                spans[key] = int(raw)
            self.cells.append(
                {
                    **{key: table[key] for key in ("table_index", "row_index", "cell_index")},
                    **spans,
                    "cell_kind": tag,
                    "parts": [],
                    "text_line": line,
                    "html_start_char": offset,
                }
            )
            self.cell_count += 1
            if self.cell_count > 50_000:
                raise source_error("Filing HTML cell count exceeds bounds.")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        visible = self.visible()
        line, offset = self.position()
        if visible and tag in {"td", "th"} and self.cells:
            cell = self.cells.pop()
            if cell["cell_kind"] != tag:
                raise source_error("Filing HTML table cell end tag is mismatched.")
            cell["text"] = " ".join("".join(cell.pop("parts")).split())
            if len(cell["text"]) > 100_000:
                raise source_error("Filing table cell text exceeds bounds.")
            cell.update(text_end_line=line, html_end_char=self.source_text.find(">", offset) + 1)
            self.tables[cell["table_index"] - 1].setdefault("cells", []).append(cell)
        if visible and tag == "table" and self.active_tables:
            self.active_tables.pop()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if visible and tag in BLOCKS:
            self.append("\n")

    def handle_data(self, data: str) -> None:
        self.append(data, source_data=True)


def html_records(snapshot: Snapshot, q: ResearchQuery, filing: Filing) -> list[dict[str, Any]]:
    if len(snapshot.body) > 16 * 1024 * 1024 or b"\x00" in snapshot.body:
        raise source_error("Unsupported filing HTML size or encoding.")
    try:
        text = snapshot.body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise source_error("Filing HTML must be UTF-8; no lossy decoding is applied.") from None
    parser = FilingHTML(text)
    parser.feed(text)
    parser.close()
    if parser.cells or parser.active_tables:
        raise source_error("Filing HTML contains unclosed tables/cells.")

    def evidence(start: int, end: int, line: int, last: int) -> dict[str, Any]:
        return {
            "raw_source_url": snapshot.url,
            "raw_sha256": snapshot.sha256,
            "retrieved_at": snapshot.retrieved_at,
            "text_line": line,
            "text_end_line": last,
            "html_start_char": start,
            "html_end_char": end,
            "offset_unit": "decoded_unicode_codepoints",
            "coordinate_semantics": "source_envelope_for_normalized_visible_text",
        }

    common = {
        "accession": filing.accession,
        "filed_on": filing.filed_on,
        "accepted_at": filing.accepted_at,
        "status": "reported",
    }
    if q.dataset == "filing_tables":
        rows = []
        for table in parser.tables:
            if q.table_index is not None and table["table_index"] != q.table_index:
                continue
            for cell in table.get("cells", []):
                source = evidence(
                    cell["html_start_char"],
                    cell["html_end_char"],
                    cell["text_line"],
                    cell["text_end_line"],
                )
                rows.append(
                    {
                        **common,
                        **cell,
                        "source": source,
                        "column_convention": "source_cell_order_before_span_expansion",
                        "nested_table_text_included": False,
                    }
                )
        return sorted(rows, key=lambda r: (r["table_index"], r["row_index"], r["cell_index"]))
    # Keep source locations for each visible character block; whitespace alone is normalized.
    lines: list[tuple[str, int, int, int, int]] = []
    pending, first_line, first_offset, last_offset, last_line = "", 1, 0, 0, 1
    for part, line, offset, end_offset, end_line in parser.parts:
        for piece in part.splitlines(keepends=True) or [part]:
            if not pending:
                first_line, first_offset = line, offset
            pending += piece
            last_offset, last_line = end_offset, end_line
            if piece.endswith(("\n", "\r")):
                cleaned = " ".join(pending.split())
                if cleaned:
                    lines.append((cleaned, first_line, first_offset, last_offset, last_line))
                pending = ""
    if pending.strip():
        lines.append((" ".join(pending.split()), first_line, first_offset, last_offset, last_line))
    headings = [(i, ITEM.fullmatch(row[0])) for i, row in enumerate(lines)]
    headings = [(i, match) for i, match in headings if match is not None]
    selections: list[tuple[int, int, str, str | None]] = []
    if q.section == "document":
        selections = [(0, len(lines), "document", None)]
    else:
        for n, (i, match) in enumerate(headings):
            assert match is not None
            key = "item_" + match[1].lower()
            if q.section in {"all", key}:
                stop = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
                selections.append((i, stop, key, lines[i][0]))
    occurrences: dict[str, int] = {}
    rows = []
    for start, stop, key, title in selections:
        occurrences[key] = occurrences.get(key, 0) + 1
        chunk: list[tuple[str, int, int, int, int]] = []
        chunks = []
        size = 0
        for entry in lines[start:stop]:
            if chunk and size + len(entry[0]) > 6000:
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append(entry)
            size += len(entry[0]) + 1
        if chunk:
            chunks.append(chunk)
        for index, entries in enumerate(chunks, 1):
            if any(len(entry[0]) > 100_000 for entry in entries):
                raise source_error("Filing text line exceeds bounds.")
            rows.append(
                {
                    **common,
                    "section": key,
                    "title": title,
                    "occurrence": occurrences[key],
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "text": "\n".join(e[0] for e in entries),
                    "source": evidence(
                        entries[0][2], entries[-1][3], entries[0][1], entries[-1][4]
                    ),
                }
            )
    return rows
