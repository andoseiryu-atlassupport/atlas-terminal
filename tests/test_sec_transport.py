import json

import httpx
import pytest
from conftest import FIXTURES

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import (
    FACTS,
    SUBMISSIONS,
    TICKERS,
    FixtureTransport,
    SECProvider,
    SecTransport,
)


@pytest.fixture
def virtual_clock():
    now = [1000.0]

    def sleep(seconds):
        now[0] += seconds

    return now, lambda: now[0], sleep


def transport(tmp_path, handler, virtual_clock, **kwargs):
    now, clock, sleep = virtual_clock
    return SecTransport(
        "Atlas tests operator@example.test",
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock,
        sleep=sleep,
        **kwargs,
    )


def test_shared_rate_and_cache_across_transport_instances(tmp_path, virtual_clock):
    times = []

    def handler(request):
        times.append(virtual_clock[0][0])
        return httpx.Response(200, json={"ok": 1})

    first = transport(tmp_path, handler, virtual_clock)
    second = transport(tmp_path, handler, virtual_clock)
    first.get(TICKERS, 100)
    second.get(TICKERS, 100)
    second.get(SUBMISSIONS + "CIK0000000001.json", 100)
    first.get(FACTS + "CIK0000000001.json", 100)
    assert len(times) == 3
    assert times[1] - times[0] >= 0.5 and times[2] - times[1] >= 0.5


def test_conditional_refresh_and_expired_cache_failure(tmp_path, virtual_clock):
    responses = [200, 304, 503, 503, 503]
    requests = []

    def handler(request):
        requests.append(request)
        status = responses.pop(0)
        return httpx.Response(
            status,
            content=b'{"exact":9007199254740993}' if status == 200 else b"",
            headers={
                "ETag": '"v1"',
                "Last-Modified": "Tue, 20 Oct 2026 00:00:00 GMT",
                "Retry-After": "1",
            },
        )

    t = transport(tmp_path, handler, virtual_clock)
    first = t.get(TICKERS, 1)
    virtual_clock[0][0] += 2
    second = t.get(TICKERS, 1)
    assert first.body == second.body
    assert requests[1].headers["If-None-Match"] == '"v1"'
    virtual_clock[0][0] += 2
    with pytest.raises(AtlasError) as e:
        t.get(TICKERS, 1)
    assert e.value.code == "SOURCE_RATE_LIMITED"
    assert len(requests) == 5


@pytest.mark.parametrize(
    "status,attempts,code",
    [
        (403, 1, "SOURCE_UNAVAILABLE"),
        (429, 3, "SOURCE_RATE_LIMITED"),
        (503, 3, "SOURCE_RATE_LIMITED"),
        (302, 1, "SOURCE_UNAVAILABLE"),
    ],
)
def test_bounded_errors_no_proxy_or_ua_evasion(tmp_path, virtual_clock, status, attempts, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Retry-After": "2", "Location": "https://other.invalid/"}
        )

    t = transport(tmp_path, handler, virtual_clock)
    with pytest.raises(AtlasError) as e:
        t.get(TICKERS, 1)
    assert e.value.code == code and len(calls) == attempts
    assert all(c.headers["User-Agent"] == "Atlas tests operator@example.test" for c in calls)


def test_long_retry_after_stops_without_early_retry(tmp_path, virtual_clock):
    t = transport(
        tmp_path, lambda req: httpx.Response(429, headers={"Retry-After": "120"}), virtual_clock
    )
    with pytest.raises(AtlasError):
        t.get(TICKERS, 1)
    assert virtual_clock[0][0] == 1000


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/companyfacts",
        SUBMISSIONS + "../private.env",
        SUBMISSIONS + "CIK0000000001-submissions-001.json?other=1",
        TICKERS + "#fragment",
    ],
)
def test_unapproved_endpoints_never_reach_http(tmp_path, virtual_clock, url):
    def forbidden(request):
        raise AssertionError("No HTTP request should happen")

    t = transport(tmp_path, forbidden, virtual_clock)
    with pytest.raises(AtlasError):
        t.get(url, 1)


def test_large_or_nonfinite_body_is_rejected(tmp_path, virtual_clock):
    t = transport(
        tmp_path, lambda req: httpx.Response(200, content=b"x" * 101), virtual_clock, max_bytes=100
    )
    with pytest.raises(AtlasError):
        t.get(TICKERS, 1)
    t = transport(tmp_path, lambda req: httpx.Response(200, content=b'{"val":NaN}'), virtual_clock)
    with pytest.raises(AtlasError):
        t.get(TICKERS, 1)


def test_archives_cap_is_explicit_and_unsafe_names_rejected_before_http():
    source = SECProvider(FixtureTransport(), max_pages=0)
    company, history, truncated = source.history(source.resolve("ATLASDEMO"))
    assert truncated and len(history) == 4
    data = json.loads((FIXTURES / "sec_like/submissions.json").read_bytes())
    data["filings"]["files"][0]["name"] = "https://evil.invalid/secret"
    source = SECProvider(FixtureTransport({"submissions.json": json.dumps(data).encode()}))
    with pytest.raises(AtlasError) as e:
        source.history(source.resolve("ATLASDEMO"))
    assert e.value.code == "INVALID_SOURCE_PATH"


def test_missing_sec_config_is_explanatory_and_private(tmp_path):
    for contact in ["", "anonymous", "fake@example.invalid", "name@example.test\r\nx: y"]:
        with pytest.raises(AtlasError) as e:
            SecTransport(contact, cache_path=tmp_path / "cache.sqlite3")
        assert e.value.code == "CONFIG_REQUIRED"
        assert contact not in str(e.value) or not contact
