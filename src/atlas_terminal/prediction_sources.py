"""Canonical read-only Polymarket Gamma discovery and event endpoints."""

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atlas_terminal.errors import AtlasError

GAMMA = "https://gamma-api.polymarket.com"
SLUG_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SEARCH_ALIASES = {"アンソロピック": "Anthropic", "アンスロピック": "Anthropic"}
PREDICTION_SOURCE: dict[str, Any] = {
    "name": "Polymarket public Gamma event and market data",
    "source_kind": "primary_market",
    "upstream_price": "0",
    "api_key_required": False,
    "contact_user_agent_required": False,
    "optional": True,
    "documentation": "https://docs.polymarket.com/market-data/discover-markets",
    "terms": "https://polymarket.com/tos",
    "use": (
        "Read-only informational API access under Polymarket terms. Capital Market Clients "
        "and market-data distributors may require a written agreement; public access is not "
        "a redistribution license. No trading, wallet or geographic-restriction bypass."
    ),
    "cadence": "Current Gamma snapshots cached for 60 seconds; price observation time unverified.",
}


def search_term(value: str) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f]", value):
        raise AtlasError("INVALID_INPUT", "Use a bounded plain-text prediction search.")
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(value) <= 100:
        raise AtlasError("INVALID_INPUT", "Prediction search accepts one to 100 characters.")
    return SEARCH_ALIASES.get(value, value)


def event_slug(value: str) -> str:
    if not isinstance(value, str) or len(value) > 200 or not re.fullmatch(SLUG_PATTERN, value):
        raise AtlasError(
            "INVALID_INPUT", "Use an exact lowercase Polymarket event slug, not a URL."
        )
    return value


def search_url(query: str, limit: int, include_closed: bool = False) -> str:
    if type(limit) is not int or not 1 <= limit <= 5 or type(include_closed) is not bool:
        raise AtlasError("INVALID_INPUT", "Choose one to five prediction events.")
    values = [
        ("q", search_term(query)),
        ("limit_per_type", str(limit)),
        ("page", "1"),
        ("search_tags", "false"),
        ("search_profiles", "false"),
        ("keep_closed_markets", "1" if include_closed else "0"),
    ]
    if not include_closed:
        values.append(("events_status", "active"))
    return GAMMA + "/public-search?" + urlencode(values)


def event_url(slug: str) -> str:
    return GAMMA + "/events/slug/" + event_slug(slug)


def prediction_identity(url: str) -> tuple[str, str] | None:
    if url.startswith(GAMMA + "/events/slug/"):
        slug = url.removeprefix(GAMMA + "/events/slug/")
        try:
            return ("event", slug) if url == event_url(slug) else None
        except AtlasError:
            return None
    if url.startswith(GAMMA + "/public-search?"):
        try:
            values = parse_qs(urlsplit(url).query, strict_parsing=True)
            query = values["q"][0]
            limit = int(values["limit_per_type"][0])
            closed = values["keep_closed_markets"][0] == "1"
            return ("search", query) if url == search_url(query, limit, closed) else None
        except (AtlasError, KeyError, ValueError):
            return None
    return None
