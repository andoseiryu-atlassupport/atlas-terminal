"""The final research envelope is bounded across full, compact and cursor reads."""

from copy import deepcopy

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.util import canonical

BUDGET = 4 * 1024 * 1024
QUERY = {"dataset": "price_analytics", "identifier": "AAPL", "metrics": ["return_1d"]}
SOURCE = {
    "raw_source_url": "https://fixtures.atlas.invalid/bounded/chart.json",
    "raw_sha256": "1" * 64,
    "retrieved_at": "2026-10-29T00:00:00Z",
    "json_pointer": "/chart/result/0/indicators/adjclose/0/adjclose/0",
}


def install_source_result(atlas, monkeypatch):
    # Exercise the common service contract with complete source evidence. The
    # growing shared input table models large price windows, not extra records
    # removed by pagination. Parser/source-response bounds have separate tests.
    result = {
        "records": [
            {
                "date": "2026-10-26",
                "metric": "return_1d",
                "value": "0.01",
                "unit": "ratio",
                "status": "derived",
                "source": SOURCE,
            },
            {
                "date": "2026-10-23",
                "metric": "return_1d",
                "value": "0.02",
                "unit": "ratio",
                "status": "derived",
                "source": SOURCE,
            },
        ],
        "price_inputs": [{"index": 0, "value": "100", "source": SOURCE, "source_note": ""}],
        "sources": [SOURCE],
        "coverage": {"partial": False, "truncated": False},
        "limitations": ["Fictional source for response-size verification."],
    }
    monkeypatch.setattr(atlas._research, "public_data", lambda query: deepcopy(result))
    return result


def expect_limit(call):
    with pytest.raises(AtlasError) as error:
        call()
    assert error.value.code == "INVALID_INPUT"
    assert "4 MiB response limit" in error.value.message


def test_full_response_accepts_exact_4mib_and_rejects_one_extra_byte(monkeypatch, tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        source = install_source_result(atlas, monkeypatch)
        baseline = atlas.get_research(QUERY)
        source["price_inputs"][0]["source_note"] = "x" * (BUDGET - len(canonical(baseline)))
        exact = atlas.get_research(QUERY)
        assert len(canonical(exact)) == BUDGET
        source["price_inputs"][0]["source_note"] += "x"
        expect_limit(lambda: atlas.get_research(QUERY))


def test_compaction_cannot_bypass_oversized_shared_input_table(monkeypatch, tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        source = install_source_result(atlas, monkeypatch)
        baseline = atlas.get_research(QUERY)
        source["price_inputs"][0]["source_note"] = "x" * (BUDGET - len(canonical(baseline)) + 1)
        expect_limit(lambda: atlas.get_research(QUERY | {"response": {"view": "compact"}}))


def test_response_budget_includes_quality_envelope(monkeypatch, tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        source = install_source_result(atlas, monkeypatch)
        query = QUERY | {"requirements": {"allow_missing": True}}
        baseline = atlas.get_research(query)
        without_quality = {key: value for key, value in baseline.items() if key != "quality"}
        source["price_inputs"][0]["source_note"] = "x" * (BUDGET - len(canonical(without_quality)))
        expect_limit(lambda: atlas.get_research(query))


def test_final_compact_metadata_is_checked_after_render(monkeypatch, tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        source = install_source_result(atlas, monkeypatch)
        for row in source["records"]:
            row.pop("source")
            row["input_range"] = {"table": "price_inputs", "first": 0, "last": 0}
        original = atlas.get_research(QUERY)
        query = QUERY | {"response": {"view": "compact"}}
        compact = atlas.get_research(query)
        # With few source references, presentation metadata exceeds compaction
        # savings. Keep the original within budget and reject the final envelope.
        limit = len(canonical(compact)) - 1
        assert len(canonical(original)) < limit
        monkeypatch.setattr("atlas_terminal.research.MAX_RESEARCH_RESPONSE_BYTES", limit)
        expect_limit(lambda: atlas.get_research(query))


def test_cursor_response_checks_final_size_without_refetch(monkeypatch, tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        install_source_result(atlas, monkeypatch)
        query = QUERY | {"response": {"view": "compact", "page_size": 1}}
        first = atlas.get_research(query)
        cursor = first["response"]["next_cursor"]
        next_query = QUERY | {"response": {"view": "compact", "page_size": 1, "cursor": cursor}}
        second = atlas.get_research(next_query)
        assert second["response"]["offset"] == 1

        def no_fetch(query):
            pytest.fail("Cursor access must use its retained snapshot")

        monkeypatch.setattr(atlas._research, "public_data", no_fetch)
        monkeypatch.setattr(
            "atlas_terminal.research.MAX_RESEARCH_RESPONSE_BYTES", len(canonical(second)) - 1
        )
        expect_limit(lambda: atlas.get_research(next_query))
