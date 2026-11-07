"""Fixed JPX all-issue margin report directory and canonical dated PDFs."""

import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot

MARGIN_INDEX = "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html"
MARGIN_PDF_BASE = "https://www.jpx.co.jp/markets/statistics-equities/margin/tvdivq0000001rnl-att/"
DAILY_START = "2026-09-25"
MAX_PDF_BYTES = 8 * 1024 * 1024


def margin_report_url(day: str) -> str:
    try:
        parsed = date.fromisoformat(day)
        if parsed.isoformat() != day or not DAILY_START <= day <= "2100-12-31":
            raise ValueError("Outside the daily publication convention")
    except ValueError:
        raise AtlasError(
            "INVALID_INPUT", "Use an ISO application date on or after 2026-09-25."
        ) from None
    return MARGIN_PDF_BASE + parsed.strftime("%Y%m%d") + "_mtall.pdf"


def margin_identity(url: str) -> str | None:
    if url == MARGIN_INDEX:
        return "index"
    match = re.fullmatch(re.escape(MARGIN_PDF_BASE) + r"([0-9]{8})_mtall\.pdf", url)
    if match:
        digits = match[1]
        day = f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
        try:
            return day if url == margin_report_url(day) else None
        except AtlasError:
            pass
    return None


class MarginDirectory(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, list[str], int]] = []
        self.parts: list[str] | None = None
        self.links: list[str] = []
        self.line = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.parts, self.links, self.line = [], [], self.getpos()[0]
        if tag == "a" and self.parts is not None:
            href = dict(attrs).get("href")
            if href and "_mtall.pdf" in href:
                self.links.append(urljoin(MARGIN_INDEX, href))

    def handle_data(self, data: str) -> None:
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self.parts is not None:
            if self.links:
                self.rows.append((" ".join(self.parts), self.links, self.line))
            self.parts = None


def margin_reports(snapshot: Snapshot) -> list[dict[str, str | int]]:
    """Select exact, currently linked dates; never probe guessed historical URLs."""
    try:
        if len(snapshot.body) > 2 * 1024 * 1024:
            raise ValueError("Directory size bound exceeded")
        parser = MarginDirectory()
        parser.feed(snapshot.body.decode("utf-8", errors="strict"))
        reports: list[dict[str, str | int]] = []
        seen: set[str] = set()
        if not 1 <= len(parser.rows) <= 100:
            raise ValueError("Missing or oversized report directory")
        for text, links, line in parser.rows:
            matched = re.search(r"(20[0-9]{2})年\s*([0-9]{1,2})月\s*([0-9]{1,2})日申込分", text)
            if matched is None or len(links) != 1:
                raise ValueError("Ambiguous report date or link")
            day = date(*(int(value) for value in matched.groups())).isoformat()
            if day in seen or links[0] != margin_report_url(day):
                raise ValueError("Duplicate date or mismatched PDF identity")
            seen.add(day)
            reports.append({"date": day, "url": links[0], "text_line": line})
        return sorted(reports, key=lambda row: str(row["date"]), reverse=True)
    except (ValueError, UnicodeError, AtlasError):
        raise AtlasError(
            "SOURCE_UNAVAILABLE",
            "JPX margin directory differs from the supported contract.",
            status=502,
        ) from None
