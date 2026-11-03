"""Fixed official headline feeds; article URLs are display-only references."""

from typing import Any, TypedDict

from atlas_terminal.errors import AtlasError


class FeedSpec(TypedDict):
    source_id: str
    publisher: str
    country: str
    language: str
    url: str
    link_domains: tuple[str, ...]
    timezone: str
    documentation: str
    terms: str
    attribution: str


FEEDS: dict[str, FeedSpec] = {
    "fed_press": {
        "source_id": "federal_reserve",
        "publisher": "Federal Reserve Board",
        "country": "US",
        "language": "en",
        "timezone": "America/New_York",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "link_domains": ("federalreserve.gov",),
        "documentation": "https://www.federalreserve.gov/feeds/feeds.htm",
        "terms": "https://www.federalreserve.gov/disclaimer.htm",
        "attribution": "Source: Federal Reserve Board. Official headline metadata; no endorsement.",
    },
    "ecb_press": {
        "source_id": "ecb",
        "publisher": "European Central Bank",
        "country": "EA",
        "language": "en",
        "timezone": "Europe/Berlin",
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "link_domains": ("ecb.europa.eu", "bankingsupervision.europa.eu"),
        "documentation": "https://www.ecb.europa.eu/home/html/rss.en.html",
        "terms": "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "attribution": (
            "Source: European Central Bank. Headlines reproduced with attribution; no endorsement."
        ),
    },
    "ecb_statistics": {
        "source_id": "ecb",
        "publisher": "European Central Bank",
        "country": "EA",
        "language": "en",
        "timezone": "Europe/Berlin",
        "url": "https://www.ecb.europa.eu/rss/statpress.html",
        "link_domains": ("ecb.europa.eu", "bankingsupervision.europa.eu"),
        "documentation": "https://www.ecb.europa.eu/home/html/rss.en.html",
        "terms": "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "attribution": (
            "Source: European Central Bank. Headlines reproduced with attribution; no endorsement."
        ),
    },
    "rba_media": {
        "source_id": "rba",
        "publisher": "Reserve Bank of Australia",
        "country": "AU",
        "language": "en",
        "timezone": "Australia/Sydney",
        "url": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
        "link_domains": ("rba.gov.au",),
        "documentation": "https://www.rba.gov.au/rss/",
        "terms": "https://www.rba.gov.au/copyright/",
        "attribution": (
            "Source: Reserve Bank of Australia. Preserve the source year and "
            "applicable copyright conditions; no endorsement."
        ),
    },
    "mof_news": {
        "source_id": "japan_mof",
        "publisher": "Ministry of Finance Japan",
        "country": "JP",
        "language": "ja",
        "timezone": "Asia/Tokyo",
        "url": "https://www.mof.go.jp/news.rss",
        "link_domains": ("mof.go.jp",),
        "documentation": "https://www.mof.go.jp/about_mof/rss/index.html",
        "terms": "https://www.mof.go.jp/about_mof/notice/index.html",
        "attribution": (
            "Source: Ministry of Finance Japan. Headline normalization and "
            "grouping by Atlas; no endorsement."
        ),
    },
}
NEWS_DATASETS = {"news_feed", "news_digest"}
NEWS_TTL = 900


def selected_feeds(feeds: list[str], country: str | None) -> list[str]:
    if len(set(feeds)) != len(feeds) or set(feeds) - FEEDS.keys():
        raise AtlasError(
            "INVALID_INPUT", "Choose unique registered official feed names.", fields=["feeds"]
        )
    if country is not None and country not in {spec["country"] for spec in FEEDS.values()}:
        raise AtlasError("INVALID_INPUT", "Choose a registered news region.", fields=["country"])
    result = [
        name for name in feeds or FEEDS if country is None or FEEDS[name]["country"] == country
    ]
    if not result:
        raise AtlasError(
            "INVALID_INPUT",
            "The selected feeds do not cover that region.",
            fields=["feeds", "country"],
        )
    return result


def feed_identity(url: str) -> str | None:
    return next((name for name, spec in FEEDS.items() if spec["url"] == url), None)


def news_catalog() -> dict[str, Any]:
    return {
        "feeds": {
            name: {**spec, "link_domains": list(spec["link_domains"])}
            for name, spec in FEEDS.items()
        },
        "default_limit": 20,
        "cache_ttl_seconds": NEWS_TTL,
        "default_allow_partial": True,
        "api_key_required": False,
        "summary_method": "original_headlines_grouped_by_publisher",
        "article_bodies_fetched": False,
        "llm_used": False,
        "watch_min_interval_seconds": NEWS_TTL,
        "watch_delivery": "best_effort_rolling_feed_polling; restart replays initial selections",
        "archive_scope": "news",
        "complete_news_coverage": False,
    }
