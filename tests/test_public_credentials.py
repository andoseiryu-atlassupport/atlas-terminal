"""Private BEA credentials must not enter captures, history or redirected requests."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from atlas_terminal.economic_catalog import NATIONAL_ACCOUNTS
from atlas_terminal.economic_sources import bea_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.util import digest

FAKE_KEY = "Z" * 36


def url():
    spec = next(iter(NATIONAL_ACCOUNTS.values()))
    return bea_url(spec["table"], spec["frequency"], 2026, 2026)


def test_bea_key_is_sent_only_to_validated_origin_and_never_follows_redirect(tmp_path, monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", FAKE_KEY)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "apps.bea.gov"
        assert parse_qs(request.url.query.decode())["UserID"] == [FAKE_KEY]
        return httpx.Response(
            302, headers={"Location": "https://example.invalid/sink?UserID=" + FAKE_KEY}
        )

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    with pytest.raises(AtlasError) as error:
        transport.get(url(), 3600)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert len(requests) == 1
    with transport.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 0
    transport.close()


@pytest.mark.parametrize("encoding", ["raw", "escaped", "object_key"])
def test_bea_echoed_credentials_fail_before_raw_capture_or_history(
    tmp_path, monkeypatch, caplog, encoding
):
    monkeypatch.setenv("BEA_API_KEY", FAKE_KEY)
    payload = {
        "BEAAPI": {
            "Request": {"RequestParam": [{"ParameterName": "USERID", "ParameterValue": FAKE_KEY}]},
            "Results": {},
        }
    }
    if encoding == "object_key":
        payload = {FAKE_KEY: {"Results": {}}}
    body = json.dumps(payload).encode()
    if encoding == "escaped":
        body = body.replace(FAKE_KEY.encode(), b"\\u005a" * 36)
    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        ),
    )
    with pytest.raises(AtlasError) as error:
        transport.get(url(), 3600)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert FAKE_KEY not in str(error.value) and FAKE_KEY not in caplog.text
    with transport.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 0
    transport.close()


def test_bea_public_bytes_are_unchanged_and_secret_headers_are_not_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", FAKE_KEY)
    body = b'{"BEAAPI":{"Results":{"Data":[]}}}'
    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=body, headers={"etag": FAKE_KEY, "last-modified": FAKE_KEY}
                )
            )
        ),
    )
    result = transport.get(url(), 3600)
    assert result.body == body and result.url == url()
    with transport.db.connect() as conn:
        row = conn.execute("SELECT * FROM source_cache").fetchone()
        assert row["etag"] is None and row["modified"] is None
        assert row["body"] == body
        assert conn.execute("SELECT body FROM source_versions").fetchone()[0] == body
    transport.close()


def test_bea_cache_hit_requires_private_key_and_rejects_legacy_credential_echo(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BEA_API_KEY", FAKE_KEY)
    transport = PublicTransport(cache_path=tmp_path / "cache.sqlite3")
    body = json.dumps({"echo": FAKE_KEY}).encode()
    with transport.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO source_cache VALUES (?,?,?,?,?,?,?)",
            (url(), body, "2026-10-29T00:00:00Z", 10**12, None, None, digest(body)),
        )
    with pytest.raises(AtlasError) as error:
        transport.get(url(), 3600)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    monkeypatch.delenv("BEA_API_KEY")
    with pytest.raises(AtlasError) as error:
        transport.get(url(), 3600)
    assert error.value.code == "CONFIG_REQUIRED"
    transport.close()


def test_bea_304_cannot_copy_legacy_credential_echo_into_version_history(tmp_path, monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", FAKE_KEY)
    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(304))),
    )
    body = json.dumps({"echo": FAKE_KEY}).encode()
    with transport.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO source_cache VALUES (?,?,?,?,?,?,?)",
            (url(), body, "2026-10-29T00:00:00Z", 0, '"old"', None, digest(body)),
        )
    with pytest.raises(AtlasError) as error:
        transport.get(url(), 3600)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    with transport.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 0
        assert conn.execute("SELECT expires FROM source_cache").fetchone()[0] == 0
    transport.close()


def test_bea_origin_path_and_canonical_query_are_validated_before_reading_key(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("BEA_API_KEY", raising=False)
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("Invalid URL reached the network")

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    for invalid in (
        url().replace("apps.bea.gov", "apps.bea.gov.example.invalid"),
        url().replace("https://", "http://"),
        url() + "&UserID=" + FAKE_KEY,
        url() + "#fragment",
        url().replace("api/data?", "api/other?"),
    ):
        with pytest.raises(AtlasError) as error:
            transport.get(invalid, 3600)
        assert error.value.code == "INVALID_INPUT"
    assert requests == []
    transport.close()
