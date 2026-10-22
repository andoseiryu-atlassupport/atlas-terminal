"""Inspect public documentation crawl signals; this does not measure search inclusion."""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from atlas_terminal.documentation import SITE_ORIGIN
from atlas_terminal.errors import AtlasError
from atlas_terminal.remote_mcp import validated_origin

AGENTS = ("AtlasDocsAudit", "OAI-SearchBot", "ChatGPT-User", "Googlebot")
MAX_BYTES = 1024 * 1024
MAX_PAGES = 64


class AuditFailure(Exception):
    """A safe failure code, never a raw network response or exception."""


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals = []
        self.directives = []
        self.has_article = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and "canonical" in (values.get("rel") or "").lower().split():
            self.canonicals.append(values.get("href"))
        if tag == "meta":
            name = (values.get("name") or "").lower()
            if name in {"robots", *(agent.lower() for agent in AGENTS)}:
                self.directives.append((name, values.get("content") or ""))
        if tag == "article":
            self.has_article = True


def blocks_indexing(value):
    return bool({"noindex", "none"} & set(re.split(r"[\s,]+", value.lower())))


def header_blocks_indexing(values, agent):
    for value in values:
        scope = None
        for part in value.split(","):
            if ":" in part:
                scope, part = part.split(":", 1)
                scope = scope.strip().lower()
            if scope in {None, agent.lower()} and blocks_indexing(part):
                return True
    return False


def robots_allows(text, agent, path):
    """Apply longest matching Allow/Disallow, including * and terminal $ patterns."""
    groups, agents, rules = [], [], []
    has_directive = False
    for line in [*text.splitlines(), "User-agent: __end__"]:
        key, separator, value = line.split("#", 1)[0].partition(":")
        if not separator:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if has_directive:
                groups.append((agents, rules))
                agents, rules = [], []
                has_directive = False
            agents.append(value.lower())
        elif agents:
            has_directive = True
            if key in {"allow", "disallow"} and value:
                rules.append((key, value))
    matches = []
    for names, entries in groups:
        specificity = max(
            (
                len(name) if name != "*" else 0
                for name in names
                if name == "*" or name in agent.lower()
            ),
            default=-1,
        )
        if specificity >= 0:
            matches.append((specificity, entries))
    if not matches:
        return True
    selected = max(score for score, _ in matches)
    decisions = []
    for score, entries in matches:
        if score != selected:
            continue
        for key, pattern in entries:
            anchored = pattern.endswith("$")
            body = pattern[:-1] if anchored else pattern
            expression = "^" + re.escape(body).replace(r"\*", ".*") + ("$" if anchored else "")
            if re.search(expression, path):
                decisions.append((len(body.replace("*", "").encode()), key == "allow"))
    return max(decisions, default=(0, True))[1]


def read_response(client, url, agent):
    # Never follow redirects to a different origin, send credentials, or expose response bodies.
    try:
        with client.stream("GET", url, headers={"User-Agent": agent + "/1.0"}) as response:
            if response.status_code != 200:
                raise AuditFailure(f"HTTP_{response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise AuditFailure("RESPONSE_TOO_LARGE")
            return response.headers, bytes(body)
    except httpx.HTTPError:
        raise AuditFailure("NETWORK_ERROR") from None


def inspect_page(client, url, agent):
    headers, body = read_response(client, url, agent)
    if "text/html" not in headers.get("content-type", "").lower():
        raise AuditFailure("HTML_CONTENT_TYPE_REQUIRED")
    parser = PageMetadata()
    parser.feed(body.decode("utf-8", errors="replace"))
    if parser.canonicals != [url]:
        raise AuditFailure("CANONICAL_MISMATCH")
    if header_blocks_indexing(headers.get_list("x-robots-tag"), agent) or any(
        name in {"robots", agent.lower()} and blocks_indexing(value)
        for name, value in parser.directives
    ):
        raise AuditFailure("NOINDEX")
    if not parser.has_article or b"Atlas Terminal" not in body:
        raise AuditFailure("DOCUMENTATION_CONTENT_MISSING")
    return {"url": url, "agent": agent, "status": "PASS"}


def audit(client, origin):
    origin = validated_origin(origin)
    if not origin.startswith("https://"):
        raise ValueError("Public checks require an HTTPS origin.")
    checks, pages = [], []
    robots_by_agent = {}
    for agent in AGENTS:
        try:
            _, raw = read_response(client, origin + "/robots.txt", agent)
            text = raw.decode("utf-8", errors="strict")
            if "<html" in text.lower() or not re.search(r"(?im)^user-agent\s*:", text):
                raise AuditFailure("ROBOTS_CONTENT_INVALID")
            robots_by_agent[agent] = text
            checks.append({"url": origin + "/robots.txt", "agent": agent, "status": "PASS"})
        except (AuditFailure, UnicodeError) as exc:
            code = str(exc) if isinstance(exc, AuditFailure) else "INVALID_UTF8"
            checks.append(
                {"url": origin + "/robots.txt", "agent": agent, "status": "FAIL", "code": code}
            )
    try:
        _, raw = read_response(client, origin + "/sitemap.xml", AGENTS[0])
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise AuditFailure("SITEMAP_CONTENT_INVALID")
        tree = ElementTree.fromstring(raw)
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        if tree.tag != ns + "urlset":
            raise AuditFailure("SITEMAP_CONTENT_INVALID")
        pages = [node.text or "" for node in tree.findall(ns + "url/" + ns + "loc")]
        if not pages or len(pages) > MAX_PAGES or len(set(pages)) != len(pages):
            raise AuditFailure("SITEMAP_PAGE_COUNT_INVALID")
        if origin + "/" not in pages or any(
            urlsplit(url).scheme != "https"
            or urlsplit(url).netloc != urlsplit(origin).netloc
            or urlsplit(url).query
            or urlsplit(url).fragment
            or "\\" in url
            for url in pages
        ):
            raise AuditFailure("SITEMAP_ORIGIN_MISMATCH")
        checks.append({"url": origin + "/sitemap.xml", "agent": AGENTS[0], "status": "PASS"})
    except (AuditFailure, ElementTree.ParseError, ValueError) as exc:
        code = str(exc) if isinstance(exc, AuditFailure) else "SITEMAP_CONTENT_INVALID"
        pages = []
        checks.append(
            {"url": origin + "/sitemap.xml", "agent": AGENTS[0], "status": "FAIL", "code": code}
        )
    for agent in AGENTS:
        # All sitemap pages are checked as a search agent; other UAs check the home page.
        urls = pages if agent == "OAI-SearchBot" else [origin + "/"]
        for url in urls:
            try:
                if agent not in robots_by_agent:
                    raise AuditFailure("ROBOTS_UNVERIFIED")
                if not robots_allows(robots_by_agent[agent], agent, urlsplit(url).path):
                    raise AuditFailure("ROBOTS_DISALLOWS")
                checks.append(inspect_page(client, url, agent))
            except AuditFailure as exc:
                checks.append({"url": url, "agent": agent, "status": "FAIL", "code": str(exc)})
    return {
        "schema_version": "1.0.0",
        "checked_at": datetime.now(UTC).isoformat(),
        "origin": origin,
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "sitemap_pages": len(pages),
        "user_agent_simulation": True,
        "official_crawler_ip_verified": False,
        "search_indexing_verified": False,
        "model_citation_measured": False,
        "checks": checks,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--origin", default=SITE_ORIGIN)
    cli.add_argument("--allow-network", action="store_true")
    args = cli.parse_args()
    if not args.allow_network:
        cli.error("Public HTTP checks require --allow-network.")
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            result = audit(client, args.origin)
    except (AtlasError, ValueError):
        cli.error(
            "Use one exact public HTTPS origin without credentials, paths or query parameters."
        )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
