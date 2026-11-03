"""Bounded official RSS/Atom metadata, with no article fetches or credential injection."""

import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from atlas_terminal.errors import AtlasError
from atlas_terminal.news_catalog import FEEDS, FeedSpec, feed_identity
from atlas_terminal.providers import CachedHTTPTransport, Snapshot
from atlas_terminal.util import canonical, digest, stamp, state_dir

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_FEED_ITEMS = 2000


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object", "svg", "template"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object", "svg", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_title(value: str) -> str:
    parser = PlainText()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts)
    return " ".join(
        "".join(
            c if c.isspace() or unicodedata.category(c) not in {"Cc", "Cf", "Cs"} else ""
            for c in text
        ).split()
    )


def child_text(node: ET.Element, name: str) -> str | None:
    child = next((c for c in node if c.tag.rsplit("}", 1)[-1] == name), None)
    if child is None:
        return None
    if len(child):
        # Strip active XHTML subtrees before collecting text from Atom text constructs.
        def visible(element: ET.Element) -> str:
            if element.tag.rsplit("}", 1)[-1] in {
                "script",
                "style",
                "iframe",
                "object",
                "svg",
                "template",
            }:
                return ""
            return (element.text or "") + " ".join(visible(c) + (c.tail or "") for c in element)

        return visible(child).strip() or None
    return (child.text or "").strip() or None


def article_url(raw: str | None, spec: FeedSpec) -> str | None:
    if not raw or len(raw) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in raw) or "\\" in raw:
        return None
    if re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", raw, re.I):
        return None
    url = urljoin(spec["url"], raw)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            return None
        if not any(
            host == domain or host.endswith("." + domain) for domain in spec["link_domains"]
        ):
            return None
    except ValueError:
        return None
    return url


def publication(raw: str | None, timezone: str) -> tuple[str | None, str | None, str]:
    if not raw:
        return None, None, "unknown"
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
            if not 1900 <= int(raw[:4]) <= 2100:
                return None, None, "unknown"
            return None, datetime.strptime(raw, "%Y-%m-%d").date().isoformat(), "day"
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(raw)
        if not 1900 <= parsed.year <= 2100:
            return None, None, "unknown"
        if parsed.tzinfo is None:
            return None, parsed.date().isoformat(), "day"
        return (
            parsed.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            parsed.astimezone(ZoneInfo(timezone)).date().isoformat(),
            "second",
        )
    except (ValueError, TypeError, OverflowError):
        return None, None, "unknown"


def parse_feed(snapshot: Snapshot, feed: str, *, synthetic: bool = False) -> dict[str, Any]:
    from atlas_terminal.public_sources import xml_root

    if len(snapshot.body) > MAX_FEED_BYTES:
        raise AtlasError("SOURCE_UNAVAILABLE", "Official feed exceeds the 2 MiB bound.", status=502)
    root = xml_root(snapshot.body)
    pending, count = [(root, 0)], 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if depth > 64 or count > 50000:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Official feed XML structure bound exceeded.", status=502
            )
        pending.extend((child, depth + 1) for child in element)
    spec = FEEDS[feed]
    if synthetic:
        spec = {**spec, "url": snapshot.url, "link_domains": ("fixtures.atlas.invalid",)}
    atom = root.tag == "{http://www.w3.org/2005/Atom}feed"
    rdf = root.tag == "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"
    if atom:
        parent, prefix, entry_tag = root, "/feed", "entry"
    elif rdf:
        parent, prefix, entry_tag = root, "/RDF", "item"
    elif root.tag == "rss" and len(root.findall("channel")) == 1:
        channel = root.find("channel")
        assert channel is not None
        parent, prefix, entry_tag = channel, "/rss/channel", "item"
    else:
        raise AtlasError(
            "SOURCE_UNAVAILABLE", "Expected an official RSS 1.0/2.0 or Atom feed.", status=502
        )
    nodes = [c for c in parent if c.tag.rsplit("}", 1)[-1] == entry_tag]
    if len(nodes) > MAX_FEED_ITEMS:
        raise AtlasError("SOURCE_UNAVAILABLE", "Official feed item bound exceeded.", status=502)
    document = {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        "xml_local_path": prefix,
    }
    rows, rejected = [], {"missing_title": 0, "unsafe_or_foreign_link": 0}
    retrieved = datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00"))
    for index, node in enumerate(nodes, 1):
        title_raw = child_text(node, "title") or ""
        if len(title_raw) > 8192:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Official headline exceeds its size bound.", status=502
            )
        title = plain_title(title_raw)
        if not title:
            rejected["missing_title"] += 1
            continue
        raw_link = child_text(node, "link")
        link_path = "/link"
        if atom:
            links = [c for c in node if c.tag == "{http://www.w3.org/2005/Atom}link"]
            selected_link = next(
                (
                    (i, c.get("href"))
                    for i, c in enumerate(links, 1)
                    if c.get("rel", "alternate") == "alternate"
                ),
                None,
            )
            raw_link = selected_link[1] if selected_link else None
            link_path = f"/link[{selected_link[0]}]/@href" if selected_link else "/link/@href"
        link = article_url(raw_link, spec)
        if link is None:
            rejected["unsafe_or_foreign_link"] += 1
            continue
        date_field = "published" if atom else "date" if rdf else "pubDate"
        raw_date = child_text(node, date_field)
        published, published_date, precision = publication(raw_date, spec["timezone"])
        raw_update = child_text(node, "updated") if atom else None
        updated, _, _ = publication(raw_update, spec["timezone"])
        source = {**document, "xml_local_path": f"{prefix}/{entry_tag}[{index}]"}
        field_sources = {
            "title": {**source, "xml_local_path": source["xml_local_path"] + "/title"},
            "url": {
                **source,
                "xml_local_path": source["xml_local_path"] + link_path,
            },
        }
        if raw_date is not None:
            field_sources["published_at"] = {
                **source,
                "xml_local_path": source["xml_local_path"] + "/" + date_field,
            }
        parsed_link = urlsplit(link)
        canonical_link = urlunsplit(
            (
                parsed_link.scheme.lower(),
                parsed_link.netloc.lower(),
                parsed_link.path,
                parsed_link.query,
                "",
            )
        )
        record = {
            "id": digest(canonical({"publisher": spec["source_id"], "url": canonical_link})),
            "title": title,
            "title_raw": title_raw,
            "url": link,
            "url_raw": raw_link,
            "source_guid": child_text(node, "id" if atom else "guid")
            or node.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"),
            "source_id": spec["source_id"],
            "publisher": spec["publisher"],
            "country": spec["country"],
            "language": spec["language"],
            "published_at": published,
            "published_at_raw": raw_date,
            "published_date": published_date,
            "publication_time_precision": precision,
            "publication_date_basis": "publisher_local_calendar_date",
            "publisher_timezone": spec["timezone"],
            "updated_at": updated,
            "updated_at_raw": raw_update,
            "publication_after_retrieval": bool(
                published
                and datetime.fromisoformat(published.replace("Z", "+00:00")) > retrieved
                or not published
                and published_date
                and published_date
                > retrieved.astimezone(ZoneInfo(spec["timezone"])).date().isoformat()
            ),
            "feed_ids": [feed],
            "status": "reported",
            "content_is_untrusted": True,
            "article_body_fetched": False,
            "source_terms_url": spec["terms"],
            "attribution": spec["attribution"],
            "source": source,
            "field_sources": field_sources,
        }
        if spec["source_id"] == "rba":
            year = published_date[:4] if published_date else "year not supplied"
            record["attribution"] = (
                f"Source: Reserve Bank of Australia {year}. Headline metadata; no endorsement."
            )
        record["revision_id"] = digest(
            canonical(
                {key: record[key] for key in ("title", "url", "published_at_raw", "updated_at_raw")}
            )
        )
        rows.append(record)
    return {
        "records": rows,
        "source": document,
        "raw_items": len(nodes),
        "rejected_items": rejected,
    }


class NewsTransport(CachedHTTPTransport):
    def __init__(self, *, cache_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(
            "AtlasTerminal/0.7 (official-news client)",
            cache_path=cache_path or state_dir() / "news-cache.sqlite3",
            max_bytes=MAX_FEED_BYTES,
            **kwargs,
        )
        self.interval = max(1.0, self.interval)
        self.headers["Accept"] = (
            "application/rss+xml, application/atom+xml, application/xml, text/xml"
        )

    @staticmethod
    def validate_url(url: str) -> None:
        if feed_identity(url) is None:
            raise AtlasError("INVALID_INPUT", "Use an exact registered official feed URL.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        feed = feed_identity(url)
        if feed is None:
            raise AtlasError("INVALID_INPUT", "Unknown official feed.")
        parse_feed(Snapshot(body, url, stamp()), feed)

    def request(self, url: str, headers: dict[str, str]) -> Any:
        self.validate_url(url)
        return self.client.stream("GET", url, headers=headers, follow_redirects=False)


class NewsFixtures:
    synthetic = True

    def get(self, url: str, ttl: int) -> Snapshot:
        feed = feed_identity(url)
        if feed is None:
            raise AtlasError("INVALID_INPUT", "Unknown fictional official feed.")
        body = (
            files("atlas_terminal")
            .joinpath("resources", "research", "news_" + feed + ".xml")
            .read_bytes()
        )
        return Snapshot(
            body, "https://fixtures.atlas.invalid/news/" + feed + ".xml", "2026-10-20T00:00:00Z"
        )

    def close(self) -> None:
        pass
