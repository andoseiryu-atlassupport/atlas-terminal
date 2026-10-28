"""Local observations, repeated vintages, migration, exact diffs and atomic quotas."""

import json

import httpx
import pytest

import atlas_terminal.source_history as history_module
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.public_sources import DEBT_URL, PublicTransport
from atlas_terminal.source_history import SCHEMA, source_history
from atlas_terminal.util import Database, digest

URL = DEBT_URL
A = b'{"value":1.000000000000000001,"escaped/~":null}'
B = b'{"value":1.000000000000000002,"added":0}'
T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-01T00:00:01.000000Z"
T2 = "2026-01-01T00:00:02.000000Z"


def transport(directory):
    return PublicTransport(cache_path=directory / "public-cache.sqlite3")


def save(t, monkeypatch, body, when):
    monkeypatch.setattr(history_module, "stamp", lambda: when)
    t.save(URL, body, 0, {})


def get(directory, action="list", **params):
    return source_history({"action": action, "url": URL, **params}, directory)


def test_a_b_a_as_of_inclusive_boundaries_timezone_offsets_and_restart(tmp_path, monkeypatch):
    t = transport(tmp_path)
    for body, when in [(A, T0), (B, T1), (A, T2)]:
        save(t, monkeypatch, body, when)
    t.close()
    listing = get(tmp_path)
    assert len(listing["versions"]) == 2
    assert [r["sha256"] for r in listing["observations"]] == [digest(A), digest(B), digest(A)]
    assert not listing["point_in_time"]
    for when, expected in [
        (T0, A),
        ("2026-01-01T00:00:00.999999Z", A),
        (T1, B),
        ("2026-01-01T09:00:01+09:00", B),
        (T2, A),
    ]:
        result = get(tmp_path, "as_of", as_of=when)
        assert result["body_utf8"].encode() == expected
        assert result["version"]["last_seen"] == result["observed_at"]
    with pytest.raises(AtlasError) as caught:
        get(tmp_path, "as_of", as_of="2025-12-31T23:59:59.999999Z")
    assert caught.value.code == "INCOMPLETE_COVERAGE"


def test_repeated_observations_at_identical_microsecond_keep_order(tmp_path, monkeypatch):
    t = transport(tmp_path)
    for body in (A, B, A):
        save(t, monkeypatch, body, T0)
    assert len(get(tmp_path)["observations"]) == 3
    assert get(tmp_path, "as_of", as_of=T0)["body_utf8"].encode() == A
    t.close()


def test_exact_decimal_diff_missing_null_pointer_escaping_and_raw_byte_only(tmp_path, monkeypatch):
    t = transport(tmp_path)
    save(t, monkeypatch, A, T0)
    save(t, monkeypatch, B, T1)
    result = get(tmp_path, "diff", before_sha256=digest(A), sha256=digest(B))
    values = {r["json_pointer"]: r for r in result["json_changes"]}
    assert values["/value"]["before"] == "1.000000000000000001"
    assert values["/value"]["after"] == "1.000000000000000002"
    assert values["/escaped~1~0"]["before_present"]
    assert not values["/escaped~1~0"]["after_present"]
    assert values["/added"]["after"] == 0
    binary = b"\xff\x00\x01"
    save(t, monkeypatch, binary, T2)
    result = get(tmp_path, "diff", before_sha256=digest(A), sha256=digest(binary))
    assert result["json_changes"] is None and result["diff_basis"] == "raw_byte_hash_only"
    assert get(tmp_path, "get", sha256=digest(binary))["body_encoding"] == "binary_not_rendered"
    t.close()


@pytest.mark.parametrize(
    "action,params",
    [
        ("list", {}),
        ("get", {"sha256": digest(A)}),
        ("as_of", {"as_of": T1}),
        ("diff", {"before_sha256": digest(A), "sha256": digest(B)}),
    ],
)
def test_tampered_archived_bytes_are_never_served(tmp_path, monkeypatch, action, params):
    t = transport(tmp_path)
    save(t, monkeypatch, A, T0)
    save(t, monkeypatch, B, T1)
    with t.db.connect(write=True) as conn:
        conn.execute("update source_versions set body=? where sha256=?", (b"tampered", digest(A)))
    with pytest.raises(AtlasError, match="integrity"):
        get(tmp_path, action, **params)
    t.close()


@pytest.mark.parametrize(
    "quota,value", [("MAX_VERSIONS", 1), ("MAX_ARCHIVE_BYTES", len(A)), ("MAX_OBSERVATIONS", 1)]
)
def test_capacity_failure_preserves_cache_versions_and_events_atomically(
    tmp_path, monkeypatch, quota, value
):
    t = transport(tmp_path)
    save(t, monkeypatch, A, T0)
    monkeypatch.setattr(history_module, quota, value)
    with pytest.raises(AtlasError) as caught:
        save(t, monkeypatch, B, T1)
    assert caught.value.code == "CACHE_FULL"
    with t.db.connect() as conn:
        assert conn.execute("select body from source_cache").fetchone()[0] == A
        assert conn.execute("select count(*) from source_versions").fetchone()[0] == 1
        assert conn.execute("select count(*) from source_capture_events").fetchone()[0] == 1
    t.close()


def test_old_observation_archive_migrates_once_without_losing_prior_vintages(tmp_path):
    path = tmp_path / "public-cache.sqlite3"
    db = Database(
        path,
        "CREATE TABLE source_versions(url TEXT,sha256 TEXT,body BLOB,first_seen TEXT,"
        "last_seen TEXT,retrieved_at TEXT,PRIMARY KEY(url,sha256)); "
        "CREATE TABLE source_observations(url TEXT,sha256 TEXT,observed_at TEXT,"
        "PRIMARY KEY(url,observed_at));",
    )
    with db.connect(write=True) as conn:
        conn.execute(
            "insert into source_versions values(?,?,?,?,?,?)", (URL, digest(A), A, T0, T0, T0)
        )
        conn.execute("insert into source_observations values(?,?,?)", (URL, digest(A), T0))
    Database(path, SCHEMA)
    Database(path, SCHEMA)
    assert len(get(tmp_path)["observations"]) == 1
    assert get(tmp_path, "as_of", as_of=T0)["body_utf8"].encode() == A


def test_cache_hits_do_not_invent_observations_but_304_revalidations_do(tmp_path, monkeypatch):
    replies = iter(
        [
            httpx.Response(200, content=b'{"data":[]}', headers={"etag": "fictional"}),
            httpx.Response(304),
        ]
    )
    t = PublicTransport(
        cache_path=tmp_path / "public-cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))),
        sleep=lambda _: None,
    )
    monkeypatch.setattr(history_module, "stamp", lambda: T0)
    first = t.get(URL, 900)
    t.get(URL, 900)
    assert len(get(tmp_path)["observations"]) == 1
    monkeypatch.setattr(history_module, "stamp", lambda: T1)
    later = t.get(URL, 900, force_refresh=True)
    assert first.sha256 == later.sha256
    assert len(get(tmp_path)["observations"]) == 2
    t.close()


def test_bounded_differences_and_shared_research_history(tmp_path, monkeypatch):
    t = transport(tmp_path)
    left = json.dumps(list(range(600))).encode()
    right = json.dumps(list(range(1, 601))).encode()
    save(t, monkeypatch, left, T0)
    save(t, monkeypatch, right, T1)
    diff = get(tmp_path, "diff", before_sha256=digest(left), sha256=digest(right))
    assert len(diff["json_changes"]) == 500 and diff["truncated"]
    with Atlas("public", directory=tmp_path) as atlas:
        result = atlas.get_research(
            {"dataset": "source_history", "source_url": URL, "history_action": "list", "limit": 1}
        )
        assert len(result["records"]) == 1
        assert result["coverage"]["truncated"]
        assert result["history"]["observations_truncated"]
        replay = atlas.get_research(
            {"dataset": "source_history", "source_url": URL, "history_action": "as_of", "as_of": T0}
        )
        assert replay["history"]["version"]["sha256"] == digest(left)
    t.close()


def test_shared_history_body_is_not_duplicated_and_final_response_is_bounded(tmp_path, monkeypatch):
    t = transport(tmp_path)
    body = b"a" * (3 * 1024 * 1024)
    save(t, monkeypatch, body, T0)
    with Atlas("fixture", directory=tmp_path) as atlas:
        query = {
            "dataset": "source_history",
            "source_url": URL,
            "history_action": "get",
            "sha256": digest(body),
        }
        result = atlas.get_research(query)
        assert len(result["records"][0]["body_utf8"]) == len(body)
        assert "body_utf8" not in result["history"]
        from atlas_terminal.util import canonical

        assert len(canonical(result)) < 4 * 1024 * 1024
    t.close()


@pytest.mark.parametrize(
    "params",
    [
        {"action": "get"},
        {"action": "as_of", "as_of": "2026-01-01"},
        {"action": "list", "sha256": digest(A)},
        {"action": "diff", "sha256": digest(A)},
    ],
)
def test_bad_history_arguments_are_rejected(tmp_path, params):
    with pytest.raises(AtlasError) as caught:
        source_history({"url": URL, **params}, tmp_path)
    assert caught.value.code == "INVALID_INPUT"
