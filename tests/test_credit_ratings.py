"""Operator-owned fictional credit observations, local import and version integrity."""

import json
import os
from copy import deepcopy
from importlib.resources import files

import pytest
from pydantic import ValidationError

import atlas_terminal.research_credit as credit_module
import atlas_terminal.source_history as history_module
from atlas_terminal.client import Atlas
from atlas_terminal.credit_catalog import CREDIT_RATINGS_URL, CREDIT_SCALES
from atlas_terminal.errors import AtlasError
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_credit import CreditBook, CreditObservation, credit_snapshot
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.source_history import SCHEMA, source_history
from atlas_terminal.util import Database, digest

BOOK = json.loads(
    files("atlas_terminal").joinpath("resources/research/credit_ratings.json").read_bytes()
)
OBS = BOOK["ratings"][0]
QUERY = {"dataset": "credit_ratings", "identifier": "ATLASDEMO"}


def configured(tmp_path, monkeypatch, book=None):
    path = tmp_path / "operator-private-ratings.json"
    path.write_text(json.dumps(book or BOOK))
    monkeypatch.setenv("ATLAS_CREDIT_RATINGS_FILE", str(path))
    return path


def test_credit_observations_preserve_subject_agency_outlook_and_actual_raw_file(
    tmp_path, monkeypatch
):
    path = configured(tmp_path, monkeypatch)
    with Atlas("public") as atlas:
        result = atlas.get_research(QUERY)
        assert atlas._research.public is None  # No agency or referenced source URL is fetched.
    assert len(result["records"]) == 3 and result["is_synthetic"]
    assert not result["coverage"]["origin_verified"]
    assert result["source_text_is_untrusted"]
    assert {r["rating_scope"] for r in result["records"]} == {"issuer", "issue"}
    for row in result["records"]:
        assert row["source_kind"] == "operator_supplied"
        assert not row["origin_verified"] and not row["currentness_verified"]
        assert row["source"]["raw_source_url"] == CREDIT_RATINGS_URL
        assert row["source"]["raw_sha256"] == digest(path.read_bytes())
        assert row["source"]["json_pointer"].startswith("/ratings/")
        assert row["outlook"] == "stable" and row["watch"] == "none"


@pytest.mark.parametrize("agency", CREDIT_SCALES)
def test_grade_order_and_investment_grade_cutoff_remain_within_the_stated_scale(
    tmp_path, monkeypatch, agency
):
    grades = CREDIT_SCALES[agency]["grades"]
    book = deepcopy(BOOK)
    book["ratings"] = [OBS | {"agency": agency, "rating": grade} for grade in grades]
    configured(tmp_path, monkeypatch, book)
    with Atlas("public") as atlas:
        result = atlas.get_research(QUERY | {"agency": agency})
    assert [r["agency_grade_order"] for r in result["records"]] == list(range(1, len(grades) + 1))
    assert [r["investment_grade"] for r in result["records"]] == [True] * 10 + [False] * (
        len(grades) - 10
    )
    assert all(r["rating_scale"] == "global_long_term" for r in result["records"])


@pytest.mark.parametrize("status", ["withdrawn", "not_rated"])
def test_withdrawal_or_not_rated_is_distinct_from_a_grade_and_absence(
    tmp_path, monkeypatch, status
):
    book = deepcopy(BOOK)
    book["ratings"] = [OBS | {"rating_status": status, "rating": None}]
    configured(tmp_path, monkeypatch, book)
    with Atlas("public") as atlas:
        row = atlas.get_research(QUERY)["records"][0]
        assert row["rating_status"] == status
        assert row["rating"] is row["investment_grade"] is row["agency_grade_order"] is None
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(QUERY | {"agency": "moodys"})
        assert exc.value.code == "INCOMPLETE_COVERAGE"


@pytest.mark.parametrize(
    "patch",
    [
        {"rating": "A-1"},
        {"rating": "AA(jpn)"},
        {"rating": "aaa"},
        {"agency": "moodys", "rating": "BBB+"},
        {"rating_scale": "national_long_term"},
        {"rating_status": "withdrawn"},
        {"rating": None},
        {"rating_scope": "issue"},
        {"issue_identifier": "ISSUE1"},
        {"rating_scope": "issue", "issue_identifier": "ISSUE1", "rating": "SD"},
        {"agency": "fitch", "rating_scope": "issue", "issue_identifier": "ISSUE1", "rating": "RD"},
        {"rating_date": "2026-02-30"},
        {"rating_date": "2100-01-01"},
        {"published_at": "2026-10-20T00:00:00"},
        {"published_at": "2100-01-01T00:00:00Z"},
        {"original_source_url": "http://ratings.example/observation"},
        {"original_source_url": "https://ratings.example/observation?api_key=PRIVATE"},
        {"original_source_url": "https://user:PRIVATE@ratings.example/observation"},
        {"original_source_url": "https://ratings.example/observation#PRIVATE"},
    ],
)
def test_unsupported_scale_subject_dates_or_credential_urls_are_rejected(patch):
    with pytest.raises((ValidationError, AtlasError)):
        CreditObservation.model_validate(OBS | patch)


def test_grade_is_not_a_default_probability_or_analyst_recommendation():
    with pytest.raises(ValidationError):
        CreditObservation.model_validate(OBS | {"default_probability": "0.01"})
    with pytest.raises(ValidationError):
        CreditObservation.model_validate(OBS | {"rating": "Buy"})
    with pytest.raises(ValidationError):
        CreditBook.model_validate(BOOK | {"is_synthetic": "false"})
    with pytest.raises(ValidationError):
        ResearchQuery.model_validate(QUERY | {"file_path": "/private/file.json"})


def test_missing_or_unreadable_operator_file_does_not_leak_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_CREDIT_RATINGS_FILE", raising=False)
    with Atlas("public") as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(QUERY)
    assert exc.value.code == "CONFIG_REQUIRED"
    path = tmp_path / "PRIVATE-CONTACT-NAME.json"
    monkeypatch.setenv("ATLAS_CREDIT_RATINGS_FILE", str(path))
    with pytest.raises(AtlasError) as exc:
        credit_snapshot(False, tmp_path)
    assert "PRIVATE-CONTACT" not in str(exc.value) and str(tmp_path) not in str(exc.value)


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo", "oversize", "invalid"])
def test_import_is_bounded_regular_valid_input_and_does_not_archive_failures(
    tmp_path, monkeypatch, kind
):
    path = configured(tmp_path, monkeypatch)
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text(json.dumps(BOOK))
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "oversize":
        monkeypatch.setattr(credit_module, "MAX_CREDIT_BOOK_BYTES", 10)
        path.write_text(json.dumps(BOOK))
    else:
        path.write_text('{"PRIVATE-SECRET": "do not echo invalid content"}')
    with pytest.raises(AtlasError) as exc:
        credit_snapshot(False, tmp_path)
    assert exc.value.code in {"CONFIG_REQUIRED", "SOURCE_UNAVAILABLE"}
    assert "PRIVATE-SECRET" not in str(exc.value)
    assert not (tmp_path / "public-cache.sqlite3").exists()


def test_local_book_url_never_becomes_an_http_allowlist_destination():
    with pytest.raises(AtlasError):
        PublicTransport.validate_url(CREDIT_RATINGS_URL)


def test_rating_book_a_b_a_as_of_diff_tamper_and_configuration_gate(tmp_path, monkeypatch):
    path = configured(tmp_path, monkeypatch)
    original = path.read_bytes()
    changed_book = deepcopy(BOOK)
    changed_book["ratings"][0]["rating"] = "BB+"
    changed = json.dumps(changed_book).encode()
    times = ["2026-10-27T03:00:00Z", "2026-10-27T03:00:01Z", "2026-10-27T03:00:02Z"]
    for when, body in zip(times, [original, changed, original], strict=True):
        monkeypatch.setattr(history_module, "stamp", lambda when=when: when)
        monkeypatch.setattr(credit_module, "stamp", lambda when=when: when)
        path.write_bytes(body)
        credit_snapshot(False, tmp_path)
    base = {"url": CREDIT_RATINGS_URL}
    listing = source_history(base, tmp_path)
    assert len(listing["versions"]) == 2
    assert [r["sha256"] for r in listing["observations"]] == [
        digest(original),
        digest(changed),
        digest(original),
    ]
    for when, expected in zip(times, [original, changed, original], strict=True):
        result = source_history(base | {"action": "as_of", "as_of": when}, tmp_path)
        assert result["body_utf8"].encode() == expected and not result["point_in_time"]
    diff = source_history(
        base | {"action": "diff", "before_sha256": digest(original), "sha256": digest(changed)},
        tmp_path,
    )
    assert diff["json_changes"] == [
        {"json_pointer": "/ratings/0/rating", "before": "BBB+", "after": "BB+"}
    ]
    monkeypatch.delenv("ATLAS_CREDIT_RATINGS_FILE")
    with pytest.raises(AtlasError) as exc:
        source_history(base, tmp_path)
    assert exc.value.code == "CONFIG_REQUIRED"
    monkeypatch.setenv("ATLAS_CREDIT_RATINGS_FILE", str(path))
    store = Database(tmp_path / "public-cache.sqlite3", SCHEMA)
    with store.connect(write=True) as conn:
        conn.execute(
            "UPDATE source_versions SET body=? WHERE sha256=?", (b"tampered", digest(original))
        )
    with pytest.raises(AtlasError) as exc:
        source_history(base, tmp_path)
    assert exc.value.code == "SOURCE_UNAVAILABLE"


def test_import_respects_shared_archive_capacity_atomically(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch)
    monkeypatch.setattr(history_module, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(AtlasError) as exc:
        credit_snapshot(False, tmp_path)
    assert exc.value.code == "CACHE_FULL"
    listing = source_history({"url": CREDIT_RATINGS_URL}, tmp_path)
    assert listing["versions"] == listing["observations"] == []


def test_compact_projection_keeps_rating_scope_and_disabled_file_blocks_cached_pages(
    tmp_path, monkeypatch
):
    configured(tmp_path, monkeypatch)
    query = QUERY | {"response": {"view": "compact", "fields": ["rating"], "page_size": 1}}
    with Atlas("public") as atlas:
        result = atlas.get_research(query)
        row = result["records"][0]
        assert row["agency"] and row["rating_scope"] and row["rating_scale"]
        assert row["origin_verified"] is row["currentness_verified"] is False
        cursor = result["response"]["next_cursor"]
        assert cursor
        monkeypatch.delenv("ATLAS_CREDIT_RATINGS_FILE")
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(QUERY | {"response": {**query["response"], "cursor": cursor}})
        assert exc.value.code == "CONFIG_REQUIRED"
