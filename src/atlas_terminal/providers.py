"""SEC-only transport, synthetic provider and accession-indexed filing metadata."""

import json
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from importlib.resources import files
from math import ceil
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import httpx

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Company, identifier
from atlas_terminal.security import private_library_logs
from atlas_terminal.util import Database, digest, stamp, state_dir

TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/"
ARCHIVE_PATTERN = r"CIK[0-9]{10}-submissions-[0-9]{3,}\.json"
ACCESSION_PATTERN = r"[0-9]{10}-[0-9]{2}-[0-9]{6}"


def decode_json(body: bytes) -> Any:
    def reject(value: str) -> None:
        raise ValueError("Non-finite JSON")

    return json.loads(body, parse_float=Decimal, parse_constant=reject)


@dataclass(frozen=True)
class Snapshot:
    body: bytes
    url: str
    retrieved_at: str

    @property
    def data(self) -> Any:
        return decode_json(self.body)

    @property
    def sha256(self) -> str:
        return digest(self.body)


class Transport(Protocol):
    synthetic: bool

    def get(self, url: str, ttl: int) -> Snapshot: ...

    def close(self) -> None: ...


class FixtureTransport:
    synthetic = True

    def __init__(self, overrides: dict[str, bytes] | None = None) -> None:
        self.overrides = overrides or {}

    def get(self, url: str, ttl: int) -> Snapshot:
        name = url.rsplit("/", 1)[-1]
        if url.startswith(FACTS):
            name = "companyfacts.json"
        elif name == "CIK9999999999.json":
            name = "submissions.json"
        if name not in {
            "company_tickers.json",
            "companyfacts.json",
            "submissions.json",
            "CIK9999999999-submissions-001.json",
        }:
            raise AtlasError("COMPANY_NOT_FOUND", "No such synthetic company.", status=404)
        body = self.overrides.get(name)
        if body is None:
            body = files("atlas_terminal").joinpath("resources", "sec_like", name).read_bytes()
        return Snapshot(
            body=body,
            url=url.replace("https://data.sec.gov", "https://fixtures.atlas.invalid").replace(
                "https://www.sec.gov", "https://fixtures.atlas.invalid"
            ),
            retrieved_at="2026-10-20T00:00:00Z",
        )

    def close(self) -> None:
        pass


CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_cache (
 url TEXT PRIMARY KEY, body BLOB NOT NULL, retrieved TEXT NOT NULL,
 expires REAL NOT NULL, etag TEXT, modified TEXT, sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_state (id INTEGER PRIMARY KEY, next_at REAL NOT NULL);
INSERT OR IGNORE INTO rate_state VALUES (1, 0);
CREATE TABLE IF NOT EXISTS source_leases (owner TEXT PRIMARY KEY, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS source_daily_usage (
 bucket TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL,
 PRIMARY KEY (bucket, day)
);
"""


class CachedHTTPTransport:
    """Bounded, conditional HTTP cache. Subclasses define the approved endpoint set."""

    synthetic = False

    def __init__(
        self,
        user_agent: str,
        *,
        cache_path: Path | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        interval: float = 0.5,
        max_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        private_library_logs()
        if not user_agent or any(c in user_agent for c in "\r\n"):
            raise AtlasError("CONFIG_REQUIRED", "A valid identifying User-Agent is required.")
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self.db = Database(
            (cache_path or state_dir() / "sec-cache.sqlite3").resolve(), CACHE_SCHEMA
        )
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.clock, self.sleep = clock, sleep
        self.interval, self.max_bytes = max(0.5, interval), max_bytes

    @staticmethod
    def validate_url(url: str) -> None:
        raise AtlasError("INVALID_INPUT", "No endpoint is approved by this transport.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        if not body:
            raise ValueError("Empty response")

    def before_request(self, url: str) -> None:
        """Source-specific quotas, charged for network attempts rather than cache hits."""

    @contextmanager
    def request_permit(self, url: str) -> Iterator[None]:
        with self.permit():
            self.before_request(url)
            yield

    @contextmanager
    def permit(self) -> Iterator[None]:
        owner = uuid4().hex
        started = self.clock()
        while True:
            now = self.clock()
            with self.db.connect(write=True) as conn:
                conn.execute("DELETE FROM source_leases WHERE expires <= ?", (now,))
                active = conn.execute("SELECT count(*) FROM source_leases").fetchone()[0]
                next_at = conn.execute("SELECT next_at FROM rate_state WHERE id=1").fetchone()[0]
                acquired = active < 2 and next_at <= now
                if acquired:
                    conn.execute("INSERT INTO source_leases VALUES (?,?)", (owner, now + 60))
                    conn.execute(
                        "UPDATE rate_state SET next_at=? WHERE id=1", (now + self.interval,)
                    )
            if acquired:
                break
            if now - started > 30:
                raise AtlasError("SOURCE_RATE_LIMITED", "Source transport is busy.", retryable=True)
            self.sleep(min(0.5, max(0.01, next_at - now)))
        try:
            yield
        finally:
            with self.db.connect(write=True) as conn:
                conn.execute("DELETE FROM source_leases WHERE owner=?", (owner,))

    def get(self, url: str, ttl: int, *, force_refresh: bool = False) -> Snapshot:
        self.validate_url(url)
        with self.db.connect() as conn:
            cached = conn.execute("SELECT * FROM source_cache WHERE url=?", (url,)).fetchone()
        if cached and not force_refresh and cached["expires"] > self.clock():
            if digest(cached["body"]) != cached["sha256"]:
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "Cached source failed integrity verification."
                )
            return Snapshot(cached["body"], url, cached["retrieved"])
        headers = dict(self.headers)
        if cached:
            if cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
            if cached["modified"]:
                headers["If-Modified-Since"] = cached["modified"]
        for attempt in range(3):
            try:
                with (
                    self.request_permit(url),
                    self.client.stream("GET", url, headers=headers) as response,
                ):
                    response_started = self.clock()
                    if response.status_code in {429, 503}:
                        wait = self.retry_after(response.headers.get("retry-after"), attempt)
                        if attempt == 2 or wait > 30:
                            raise AtlasError(
                                "SOURCE_RATE_LIMITED",
                                "Source requested a later retry.",
                                retryable=True,
                                retry_after_seconds=ceil(min(wait, 86400)),
                                status=429,
                            )
                    elif response.status_code == 304 and cached:
                        body = cached["body"]
                        if digest(body) != cached["sha256"]:
                            raise AtlasError("SOURCE_UNAVAILABLE", "Cache integrity check failed.")
                        self.save(url, body, ttl, response.headers, cached)
                        return Snapshot(body, url, stamp())
                    elif response.status_code == 200:
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.max_bytes or self.clock() - response_started > 30:
                                raise AtlasError(
                                    "SOURCE_UNAVAILABLE",
                                    "Source response exceeds size or time limit.",
                                )
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        self.validate_body(url, body)
                        retrieved = self.save(url, body, ttl, response.headers)
                        return Snapshot(body, url, retrieved)
                    else:
                        raise AtlasError(
                            "SOURCE_UNAVAILABLE",
                            "Source retrieval failed; no provider fallback was attempted.",
                            details={"http_status": response.status_code},
                            status=502,
                        )
                self.sleep(wait)
            except (httpx.HTTPError, ValueError):
                raise AtlasError(
                    "SOURCE_UNAVAILABLE",
                    "Source response could not be retrieved or parsed.",
                    status=502,
                ) from None
        raise AtlasError("SOURCE_UNAVAILABLE", "Source retrieval exhausted retries.")

    def save(self, url: str, body: bytes, ttl: int, headers: Any, old: Any = None) -> str:
        retrieved = stamp()
        with self.db.connect(write=True) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO source_cache VALUES (?,?,?,?,?,?,?)",
                (
                    url,
                    body,
                    retrieved,
                    self.clock() + ttl,
                    headers.get("etag") or (old["etag"] if old else None),
                    headers.get("last-modified") or (old["modified"] if old else None),
                    digest(body),
                ),
            )
        return retrieved

    def retry_after(self, header: str | None, attempt: int) -> float:
        try:
            if header and header.isdigit():
                return float(header)
            if header:
                return max(0, parsedate_to_datetime(header).timestamp() - self.clock())
        except (ValueError, OverflowError):
            pass
        return float(2**attempt)

    def close(self) -> None:
        self.client.close()


DOCUMENT_PATTERN = r"(?:xsl[A-Za-z0-9_-]+/)?[A-Za-z0-9_][A-Za-z0-9_.-]*"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/"


class SecTransport(CachedHTTPTransport):
    def __init__(self, user_agent: str, **kwargs: Any) -> None:
        if (
            not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_agent)
            or ".invalid" in user_agent.lower()
            or any(c in user_agent for c in "\r\n")
        ):
            raise AtlasError(
                "CONFIG_REQUIRED",
                "SEC live requires your identifying SEC_USER_AGENT with contact email.",
            )
        super().__init__(user_agent, **kwargs)

    @staticmethod
    def validate_url(url: str) -> None:
        allowed = (
            url == TICKERS
            or re.fullmatch(re.escape(SUBMISSIONS) + r"CIK[0-9]{10}\.json", url)
            or re.fullmatch(re.escape(SUBMISSIONS) + ARCHIVE_PATTERN, url)
            or re.fullmatch(re.escape(FACTS) + r"CIK[0-9]{10}\.json", url)
            or re.fullmatch(
                re.escape(SEC_ARCHIVES) + r"[1-9][0-9]{0,9}/[0-9]{18}/" + DOCUMENT_PATTERN,
                url,
            )
        )
        if not allowed:
            raise AtlasError("INVALID_INPUT", "SEC URL is outside the approved endpoint set.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        if url.endswith(".json"):
            if not isinstance(decode_json(body), dict):
                raise ValueError("Expected JSON object")
        elif not body:
            raise ValueError("Empty filing document")


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filed_on: str
    report_date: str
    accepted_at: str | None
    primary_document: str | None
    source_url: str
    reference_kind: str


def archive_name(value: str, cik: str) -> str:
    if not re.fullmatch(ARCHIVE_PATTERN, value) or not value.startswith(f"CIK{cik}-"):
        raise AtlasError("INVALID_SOURCE_PATH", "Unsafe SEC submission archive filename.")
    return value


def filing_rows(data: dict[str, Any], cik: str, *, synthetic: bool) -> list[Filing]:
    accessions = data.get("accessionNumber", [])
    result = []
    origin = "https://fixtures.atlas.invalid" if synthetic else "https://www.sec.gov"
    for i, accession in enumerate(accessions):
        if not isinstance(accession, str) or not re.fullmatch(ACCESSION_PATTERN, accession):
            raise AtlasError("SOURCE_UNAVAILABLE", "Malformed accession metadata.")

        def at(key: str, position: int = i) -> Any:
            return data.get(key, [])[position] if position < len(data.get(key, [])) else None

        filed, report = at("filingDate"), at("reportDate")
        try:
            datetime.strptime(filed, "%Y-%m-%d")
            if report:
                datetime.strptime(report, "%Y-%m-%d")
        except (ValueError, TypeError):
            raise AtlasError("SOURCE_UNAVAILABLE", "Malformed filing dates.") from None
        document = at("primaryDocument")
        if document and not re.fullmatch(DOCUMENT_PATTERN, document):
            raise AtlasError("INVALID_SOURCE_PATH", "Unsafe primary document filename.")
        base = f"{origin}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
        source_url = f"{base}/{document or (accession + '-index.html')}"
        accepted = at("acceptanceDateTime") or None
        if accepted:
            try:
                parsed = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
                accepted = accepted if parsed.tzinfo else None
            except ValueError:
                accepted = None
        result.append(
            Filing(
                accession,
                at("form") or "",
                filed,
                report or "",
                accepted,
                document or None,
                source_url,
                "primary_document" if document else "filing_index",
            )
        )
    return result


class SECProvider:
    def __init__(self, transport: Transport, *, max_pages: int = 20) -> None:
        self.transport = transport
        self.max_pages = min(20, max(0, max_pages))

    def resolve(self, query: str) -> Company:
        query = identifier(query)
        raw = self.transport.get(TICKERS, 86400).data
        grouped: dict[str, Company] = {}
        for item in raw.values():
            cik = str(item["cik_str"]).zfill(10)
            if cik not in grouped:
                grouped[cik] = Company(cik=cik, name=item["title"], tickers=[])
            if item["ticker"] not in grouped[cik].tickers:
                grouped[cik].tickers.append(item["ticker"])
        candidates = [
            c
            for c in grouped.values()
            if query.upper() in c.tickers or (query.isdigit() and query.zfill(10) == c.cik)
        ]
        if not candidates and not query.isdigit():
            candidates = [c for c in grouped.values() if query.casefold() in c.name.casefold()]
        if not candidates and query.isdigit() and 0 < int(query) < 10**10:
            cik = query.zfill(10)
            data = self.transport.get(f"{SUBMISSIONS}CIK{cik}.json", 900).data
            return Company(cik=cik, name=data["name"], tickers=data.get("tickers", []))
        if not candidates:
            raise AtlasError("COMPANY_NOT_FOUND", "No matching current issuer.", status=404)
        if len(candidates) > 1:
            raise AtlasError(
                "AMBIGUOUS_COMPANY",
                "Choose a ticker or CIK from the candidates.",
                details={"candidates": [c.model_dump() for c in candidates[:20]]},
            )
        return candidates[0]

    def history(self, company: Company) -> tuple[Company, list[Filing], bool]:
        data = self.transport.get(f"{SUBMISSIONS}CIK{company.cik}.json", 900).data
        if str(data.get("cik", "")).zfill(10) != company.cik:
            raise AtlasError("SOURCE_UNAVAILABLE", "Submissions issuer does not match the request.")
        company = Company(
            cik=company.cik,
            name=data["name"],
            tickers=list(dict.fromkeys(company.tickers + data.get("tickers", []))),
        )
        filings = filing_rows(
            data["filings"]["recent"], company.cik, synthetic=self.transport.synthetic
        )
        pages = data["filings"].get("files", [])
        # Validate all names, including those past the fetch cap, before any request.
        names = [archive_name(p["name"], company.cik) for p in pages]
        for name in names[: self.max_pages]:
            page = self.transport.get(SUBMISSIONS + name, 900).data
            filings.extend(filing_rows(page, company.cik, synthetic=self.transport.synthetic))
        unique: dict[str, Filing] = {}
        for filing in filings:
            if filing.accession in unique and unique[filing.accession] != filing:
                raise AtlasError("SOURCE_UNAVAILABLE", "Conflicting metadata for one accession.")
            unique[filing.accession] = filing
        return (
            company,
            sorted(unique.values(), key=lambda f: f.filed_on, reverse=True),
            len(names) > self.max_pages,
        )

    def financial_inputs(self, query: str) -> tuple[Company, Snapshot, list[Filing], bool]:
        company, filings, truncated = self.history(self.resolve(query))
        raw = self.transport.get(f"{FACTS}CIK{company.cik}.json", 3600)
        if str(raw.data.get("cik", "")).zfill(10) != company.cik:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Companyfacts issuer does not match submissions."
            )
        return company, raw, filings, truncated

    def close(self) -> None:
        self.transport.close()
