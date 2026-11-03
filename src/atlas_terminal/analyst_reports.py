"""Public report-link discovery and private, explicitly imported report documents."""

import json
import re
import subprocess
import sys
from datetime import date, datetime
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError, field_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.local_inputs import public_reference, read_regular
from atlas_terminal.models import Model, identifier
from atlas_terminal.news_sources import plain_title
from atlas_terminal.providers import CachedHTTPTransport, Snapshot, Transport
from atlas_terminal.report_pdf import MAX_DOCUMENT_BYTES, MAX_TEXT_CHARS
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.util import Database, canonical, digest, stamp, state_dir

FISCO_URL = "https://www.fisco.co.jp/service/report/"
FISCO_TERMS = "https://www.fisco.co.jp/copyright/"
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_REPORTS = 64
MAX_LIBRARY_BYTES = 256 * 1024 * 1024
REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyst_documents (
 id TEXT PRIMARY KEY, body BLOB NOT NULL, raw_sha256 TEXT NOT NULL,
 metadata BLOB NOT NULL, metadata_sha256 TEXT NOT NULL,
 pages BLOB NOT NULL, pages_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
);
"""


class ReportLinks(HTMLParser):
    """Collect link labels and nearby publication labels, never article content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None
        self.context = ""
        self.hidden = 0
        self.anchors = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template", "iframe"}:
            self.hidden += 1
        if tag == "a":
            self.anchors += 1
            if self.anchors > 20000:
                raise ValueError("Too many catalogue links")
            self.pending = (
                None
                if self.hidden
                else {
                    "href": dict(attrs).get("href") or "",
                    "label": "",
                    "before": self.context[-300:],
                    "anchor": self.anchors,
                }
            )

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        self.context = (self.context + data)[-300:]
        if self.pending:
            self.pending["label"] += data
            if len(self.pending["label"]) > 2000:
                raise ValueError("Oversized link label")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template", "iframe"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a" and self.pending:
            self.links.append(self.pending)
            self.pending = None
            # A date must belong to this link, not an earlier report.
            self.context = ""


def report_links(snapshot: Snapshot) -> list[dict[str, Any]]:
    try:
        parser = ReportLinks()
        parser.feed(snapshot.body.decode("utf-8-sig"))
        parser.close()
        rows: dict[str, dict[str, Any]] = {}
        for link in parser.links:
            try:
                url = public_reference(urljoin(FISCO_URL, link["href"]))
            except ValueError:
                continue
            host = (
                "reports.fisco.invalid"
                if snapshot.url.startswith("https://fixtures.atlas.invalid/")
                else "www.fisco.co.jp"
            )
            if urlsplit(url).hostname != host or not urlsplit(url).path.lower().endswith(".pdf"):
                continue
            label_raw = link["label"]
            label = plain_title(label_raw)
            code = re.search(r"[<＜〈]([0-9]{4}|[0-9]{3}[A-Z])[>＞〉]", label)
            dates = re.findall(
                r"(20[0-9]{2})年\s*([0-9]{1,2})月\s*([0-9]{1,2})日", link["before"] + label
            )
            if not dates:
                continue
            year, month, day = map(int, dates[-1])
            published = date(year, month, day).isoformat()
            future = (
                published
                > datetime.fromisoformat(snapshot.retrieved_at)
                .astimezone(ZoneInfo("Asia/Tokyo"))
                .date()
                .isoformat()
            )
            row = {
                "report_id": "rp_" + digest(url.encode()),
                "identifier": code[1] if code else None,
                "title": label,
                "title_raw": label_raw,
                "url_raw": link["href"],
                "publisher": "FISCO",
                "analyst": None,
                "published_date": published,
                "date": published,
                "publication_time_precision": "day",
                "publisher_timezone": "Asia/Tokyo",
                "original_source_url": url,
                "source_kind": "public_report_directory",
                "research_type": "company_commissioned_research",
                "sponsored": True,
                "sponsorship_verified_for_document": False,
                "language": "en" if "英語版" in label else "ja",
                "language_verified_for_document": False,
                "body_available": False,
                "status": "ambiguous" if future else "reported",
                "publication_after_retrieval": future,
                "unit": "report_metadata",
                "source": {
                    "raw_source_url": snapshot.url,
                    "raw_sha256": snapshot.sha256,
                    "retrieved_at": snapshot.retrieved_at,
                    "xml_local_path": f"//a[{link['anchor']}]",
                    "locator_basis": "one-based HTML anchor document order",
                    "publication_label_context": link["before"],
                },
            }
            if url in rows and (rows[url]["title"], rows[url]["published_date"]) != (
                label,
                published,
            ):
                raise ValueError("Conflicting report-link metadata")
            rows[url] = row
        if not rows:
            raise ValueError(
                "No recognizable dated report links; directory format may have changed"
            )
        return list(rows.values())
    except (UnicodeError, ValueError, TypeError, OverflowError):
        raise AtlasError(
            "SOURCE_UNAVAILABLE", "Public report directory could not be parsed."
        ) from None


class ReportTransport(CachedHTTPTransport):
    def __init__(self, *, cache_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(
            "AtlasTerminal/0.7 (report-link research)",
            cache_path=cache_path or state_dir() / "reports-cache.sqlite3",
            max_bytes=MAX_CATALOG_BYTES,
            **kwargs,
        )
        self.interval = max(1.0, self.interval)
        self.headers["Accept"] = "text/html"

    @staticmethod
    def validate_url(url: str) -> None:
        if url != FISCO_URL:
            raise AtlasError("INVALID_INPUT", "Only the registered report directory is fetched.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        report_links(Snapshot(body, url, stamp()))

    def request(self, url: str, headers: dict[str, str]) -> Any:
        self.validate_url(url)
        return self.client.stream("GET", url, headers=headers, follow_redirects=False)


class ReportFixtures:
    synthetic = True

    def get(self, url: str, ttl: int) -> Snapshot:
        ReportTransport.validate_url(url)
        return Snapshot(
            files("atlas_terminal")
            .joinpath("resources/research/analyst_reports.html")
            .read_bytes(),
            "https://fixtures.atlas.invalid/analyst_reports.html",
            "2026-10-20T00:00:00Z",
        )

    def close(self) -> None:
        pass


class ReportMetadata(Model):
    schema_version: Literal["0.1.0"]
    is_synthetic: bool = Field(strict=True)
    identifier: str
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=200)
    analyst: str | None = Field(default=None, max_length=200)
    published_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    language: str = Field(pattern=r"^[a-z]{2,3}$")
    sponsored: bool | None = Field(default=None, strict=True)
    original_source_url: str = Field(max_length=2048)
    rights_basis: Literal["owned", "licensed", "permitted_use"]

    @field_validator("identifier")
    @classmethod
    def symbol(cls, value: str) -> str:
        return identifier(value)

    @field_validator("published_date")
    @classmethod
    def published(cls, value: str) -> str:
        when = date.fromisoformat(value)
        if not 1900 <= when.year <= 2100 or when > date.today():
            raise ValueError("Use a nonfuture publication date")
        return value

    @field_validator("original_source_url")
    @classmethod
    def source_url(cls, value: str) -> str:
        return public_reference(value)


def extract_document(body: bytes, file_type: str) -> list[dict[str, Any]]:
    if file_type == "text":
        try:
            text = body.decode("utf-8-sig")
            if not text.strip() or len(text) > MAX_TEXT_CHARS or "\x00" in text:
                raise ValueError("Empty, binary or oversized text")
            return [{"page": 1, "text": text}]
        except (UnicodeError, ValueError):
            raise AtlasError("INVALID_INPUT", "Use bounded nonempty UTF-8 text.") from None
    if file_type != "pdf":
        raise AtlasError("INVALID_INPUT", "Choose pdf or text document format.")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "atlas_terminal.report_pdf"],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
        if len(result.stdout) > 3 * 1024 * 1024:
            raise ValueError("Oversized extracted text")
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        raise AtlasError(
            "INVALID_INPUT",
            "PDF extraction failed or exceeded limits; encrypted, image-only "
            "and oversized documents are unsupported.",
        ) from None


def import_report(
    document: Path, metadata_path: Path, directory: Path, *, file_type: str = "pdf"
) -> dict[str, Any]:
    body = read_regular(document, MAX_DOCUMENT_BYTES)
    try:
        metadata = ReportMetadata.model_validate_json(read_regular(metadata_path, 16384))
    except (ValidationError, ValueError, TypeError):
        raise AtlasError(
            "INVALID_INPUT",
            "Invalid report metadata; include source, date, identifier and rights basis.",
        ) from None
    meta = canonical({**metadata.model_dump(), "file_type": file_type})
    raw_sha, meta_sha = digest(body), digest(meta)
    report_id = "rp_" + digest(canonical({"raw_sha256": raw_sha, "metadata_sha256": meta_sha}))
    db = Database(directory / "analyst-reports.sqlite3", REPORT_SCHEMA)
    with db.connect() as conn:
        old = conn.execute("SELECT id FROM analyst_documents WHERE id=?", (report_id,)).fetchone()
    if old:
        stored = read_report(directory, report_id)
        return {
            "report_id": report_id,
            "already_imported": True,
            "raw_sha256": raw_sha,
            "imported_at": stored["imported_at"],
            "pages": len(stored["pages"]),
        }
    pages = canonical(extract_document(body, file_type))
    when = stamp()
    with db.connect(write=True) as conn:
        old = conn.execute("SELECT id FROM analyst_documents WHERE id=?", (report_id,)).fetchone()
        if not old:
            count, used = conn.execute(
                "SELECT count(*),coalesce(sum(length(body)+length(metadata)+length(pages)),0) "
                "FROM analyst_documents"
            ).fetchone()
            if (
                count >= MAX_REPORTS
                or used + len(body) + len(meta) + len(pages) > MAX_LIBRARY_BYTES
            ):
                raise AtlasError(
                    "CACHE_FULL", "Private report library quota reached; no documents were evicted."
                )
            conn.execute(
                "INSERT INTO analyst_documents VALUES (?,?,?,?,?,?,?,?)",
                (report_id, body, raw_sha, meta, meta_sha, pages, digest(pages), when),
            )
    stored = read_report(directory, report_id)
    return {
        "report_id": report_id,
        "raw_sha256": raw_sha,
        "imported_at": stored["imported_at"],
        "pages": len(stored["pages"]),
        "already_imported": bool(old),
    }


def read_report(directory: Path, report_id: str) -> dict[str, Any]:
    db = Database(directory / "analyst-reports.sqlite3", REPORT_SCHEMA)
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM analyst_documents WHERE id=?", (report_id,)).fetchone()
    if row is None:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "No privately imported document has this report ID."
        )
    if any(
        digest(row[field]) != row[sha]
        for field, sha in (
            ("body", "raw_sha256"),
            ("metadata", "metadata_sha256"),
            ("pages", "pages_sha256"),
        )
    ):
        raise AtlasError("SOURCE_UNAVAILABLE", "Private report integrity verification failed.")
    if report_id != "rp_" + digest(
        canonical({"raw_sha256": row["raw_sha256"], "metadata_sha256": row["metadata_sha256"]})
    ):
        raise AtlasError("SOURCE_UNAVAILABLE", "Private report identity verification failed.")
    return {
        "report_id": report_id,
        "metadata": json.loads(row["metadata"]),
        "pages": json.loads(row["pages"]),
        "raw_sha256": row["raw_sha256"],
        "metadata_sha256": row["metadata_sha256"],
        "imported_at": row["imported_at"],
    }


def local_reports(directory: Path, *, synthetic: bool) -> list[dict[str, Any]]:
    if synthetic:
        raw = files("atlas_terminal").joinpath("resources/research/analyst_report.txt").read_bytes()
        meta = json.loads(
            files("atlas_terminal")
            .joinpath("resources/research/analyst_report_metadata.json")
            .read_bytes()
        )
        return [
            {
                "report_id": "rp_" + "0" * 64,
                "raw_source_url": "https://fixtures.atlas.invalid/analyst_report.txt",
                "metadata": {**meta, "file_type": "text"},
                "pages": [{"page": 1, "text": raw.decode()}],
                "raw_sha256": digest(raw),
                "metadata_sha256": digest(canonical(meta)),
                "imported_at": "2026-10-20T00:00:00Z",
            }
        ]
    db = Database(directory / "analyst-reports.sqlite3", REPORT_SCHEMA)
    with db.connect() as conn:
        ids = [
            r[0]
            for r in conn.execute("SELECT id FROM analyst_documents ORDER BY imported_at DESC,id")
        ]
    return [read_report(directory, key) for key in ids]


def document_source(document: dict[str, Any], page: int = 1) -> dict[str, Any]:
    return {
        "raw_source_url": document.get("raw_source_url")
        or "https://local.atlas.invalid/reports/" + document["report_id"],
        "raw_sha256": document["raw_sha256"],
        "retrieved_at": document["imported_at"],
        **({"pdf_page": page} if document["metadata"]["file_type"] == "pdf" else {"text_line": 1}),
        "metadata_sha256": document["metadata_sha256"],
        "metadata_origin": "operator_supplied",
    }


def reports_result(
    q: ResearchQuery, transport: Transport | None, directory: Path, *, synthetic: bool
) -> dict[str, Any]:
    if q.dataset == "analyst_reports" and q.report_source == "fisco":
        assert transport is not None
        snapshot = transport.get(FISCO_URL, 3600)
        rows = report_links(snapshot)
        sources = [
            {
                "raw_source_url": snapshot.url,
                "raw_sha256": snapshot.sha256,
                "retrieved_at": snapshot.retrieved_at,
                "xml_local_path": "/",
            }
        ]
        source_id = "fisco"
    else:
        docs = (
            [read_report(directory, q.report_id)]
            if q.dataset == "analyst_report_text" and q.report_id and not synthetic
            else local_reports(directory, synthetic=synthetic)
        )
        if q.dataset == "analyst_report_text":
            docs = [d for d in docs if d["report_id"] == q.report_id]
            if not docs:
                raise AtlasError(
                    "INCOMPLETE_COVERAGE", "Import a permitted document before reading its text."
                )
        rows, sources = [], []
        for doc in docs:
            meta = doc["metadata"]
            synthetic = synthetic or meta["is_synthetic"]
            source = document_source(doc)
            sources.append(source)
            base = {
                **meta,
                "report_id": doc["report_id"],
                "source_kind": "operator_imported",
                "date": meta["published_date"],
                "status": "reported",
                "body_available": True,
                "origin_verified": False,
                "metadata_origin": "operator_supplied",
                "source": source,
            }
            if q.dataset == "analyst_reports":
                rows.append({**base, "unit": "report_metadata", "page_count": len(doc["pages"])})
            else:
                rows.extend(
                    {
                        **base,
                        "unit": "document_text",
                        "page": p["page"],
                        "text": p["text"],
                        "source": document_source(doc, p["page"]),
                    }
                    for p in doc["pages"]
                )
        source_id = "multiple"
    terms = (q.search or "").casefold().split()
    selected = [
        r
        for r in rows
        if (not q.identifier or q.identifier == r["identifier"])
        and (not q.start_date or q.start_date <= r["published_date"] <= (q.end_date or ""))
        and all(
            t
            in (r.get("text", "") if q.dataset == "analyst_report_text" else r["title"]).casefold()
            for t in terms
        )
    ]
    if q.dataset == "analyst_reports":
        selected.sort(key=lambda r: (r["published_date"], r["report_id"]), reverse=True)
    return {
        "records": selected[: q.limit],
        "sources": sources,
        "source_id": source_id,
        "is_synthetic": synthetic,
        "coverage": {
            "matching_records": len(selected),
            "returned_records": min(len(selected), q.limit),
            "truncated": len(selected) > q.limit,
            "point_in_time": False,
        },
        "llm_used": False,
        "limitations": [
            "FISCO discovery reads metadata/links only; no remote PDF, login or paid feed.",
            "Company-commissioned research; individual sponsors/disclosures remain unverified.",
            "Imported files and metadata are operator-supplied, rights-asserted and unverified.",
            "Page text does not verify layout, tables, recommendations or targets. No OCR/summary.",
            "Imported metadata/text is visible to this service's clients; respect content rights.",
        ],
    }
