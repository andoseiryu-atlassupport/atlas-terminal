import hashlib
import importlib.util
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from atlas_terminal.client import Atlas

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


capture = script("capture_research_examples")
discovery = script("summarize_discovery")
docs = script("build_docs")
showcases = script("verify_showcases")


def test_docs_preserve_recorded_media_bytes_and_serve_captioned_players(tmp_path):
    docs.build(tmp_path)
    for scenario, page in (
        ("research", "agent-research-walkthrough"),
        ("japan-risk", "japan-risk-walkthrough"),
    ):
        html = (tmp_path / "docs" / f"{page}.html").read_text()
        assert '<video controls preload="metadata" playsinline' in html
        assert f'<track src="../assets/showcase/{scenario}.vtt"' in html
        for extension in ("mp4", "json", "vtt", "txt"):
            name = f"{scenario}.{extension}"
            assert (tmp_path / "assets/showcase" / name).read_bytes() == (
                ROOT / "docs/assets/showcase" / name
            ).read_bytes()


@pytest.mark.parametrize("change", ["bytes", "version", "data_mode"])
def test_recording_validation_rejects_modified_or_mislabeled_execution(tmp_path, change):
    source = ROOT / "docs/assets/showcase"
    showcases.verify(source)
    destination = tmp_path / "recordings"
    shutil.copytree(source, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    if change == "bytes":
        (destination / "research.mp4").write_bytes(b"altered")
    elif change == "version":
        manifest["recordings"][0]["atlas_version"] = "0.0.0"
    else:
        manifest["recordings"][0]["is_synthetic"] = False
    (destination / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        showcases.verify(destination)


@pytest.fixture
def snapshot():
    query = {
        "dataset": "economics",
        "series": "cpi",
        "limit": 1,
        "requirements": {},
        "response": {"view": "compact"},
    }
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query)
        evidence = {
            identifier: atlas.get_evidence(identifier)
            for identifier in set(capture.evidence_ids(result))
        }
    return {
        "schema_version": "1.0.0",
        "id": "inflation-rates-fx",
        "status": "PASS",
        "is_synthetic": True,
        "items": [{"id": "cpi", "result": result}],
        "resolved_evidence": evidence,
    }


def test_captures_preserve_resolved_evidence_and_content_addressed_bytes(snapshot, tmp_path):
    saved = capture.save_capture(snapshot, tmp_path)
    payload = Path(saved["path"]).read_bytes()
    assert saved["sha256"] == hashlib.sha256(payload).hexdigest()
    assert saved["sha256"] in Path(saved["path"]).name
    assert json.loads(payload) == snapshot
    assert capture.save_capture(snapshot, tmp_path) == saved
    Path(saved["path"]).write_text("different bytes")
    with pytest.raises(ValueError, match="different bytes"):
        capture.save_capture(snapshot, tmp_path)


@pytest.mark.parametrize(
    "problem",
    ["synthetic", "failed", "mode", "missing_evidence", "wrong_evidence", "pagination", "empty"],
)
def test_invalid_captures_never_publish_observation_pages(snapshot, tmp_path, problem):
    if problem == "failed":
        snapshot["status"] = "FAIL"
    elif problem == "mode":
        snapshot["items"][0]["result"]["is_synthetic"] = False
    elif problem == "missing_evidence":
        snapshot["resolved_evidence"].pop(next(iter(snapshot["resolved_evidence"])))
    elif problem == "wrong_evidence":
        next(iter(snapshot["resolved_evidence"].values()))["evidence_id"] = "0" * 64
    elif problem == "pagination":
        snapshot["items"][0]["result"]["response"]["next_cursor"] = "opaque"
    elif problem == "empty":
        snapshot["items"][0]["result"]["records"] = []
    if problem != "synthetic":
        with pytest.raises(ValueError):
            capture.validate_capture(snapshot)
    with pytest.raises(ValueError):
        capture.save_capture(snapshot, tmp_path / "public", publish=True)
    assert not (tmp_path / "public").exists()


def test_unrun_discovery_cases_have_unknown_rates_and_no_measured_denominator():
    result = discovery.summarize(discovery.template())
    assert result["status"] == "not_measured"
    assert result["pending"] == 20
    assert result["overall"]["answers_observed"] == 0
    assert result["overall"]["citation_rate"] is None
    assert result["overall"]["tasks_observed"] == 0


def observed(data, index=0):
    row = data["observations"][index]
    row.update(
        {
            "status": "observed",
            "product": "Fictional test client",
            "model": "test",
            "observed_at": "2026-10-23T07:00:00Z",
            "search_enabled": True,
            "response_sha256": "a" * 64,
            "atlas_mentioned": True,
            "cited_urls": [
                "https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site/docs/citations"
            ],
            "accuracy": "correct",
        }
    )
    return row


def test_discovery_counts_answers_citations_and_reviewed_tasks_separately():
    data = discovery.template()
    observed(data)
    second = observed(data, 5)
    second.update({"atlas_mentioned": False, "cited_urls": [], "accuracy": "unassessed"})
    result = discovery.summarize(data)
    assert result["overall"]["answers_observed"] == 2
    assert result["overall"]["citation_rate"] == 0.5
    assert result["branded"]["answers_citing_atlas"] == 1
    assert result["unbranded"]["answers_citing_atlas"] == 0
    assert result["overall"]["accuracy_reviewed"] == 1
    assert result["overall"]["tasks_observed"] == 0
    assert not discovery.atlas_url(
        "https://github.com/andoseiryu-atlassupport/atlas-terminal-other"
    )
    assert not discovery.atlas_url(
        "https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site.example.org"
    )


@pytest.mark.parametrize(
    "problem", ["prompt", "hash", "search", "time", "duplicate", "credentials", "question_id"]
)
def test_discovery_rejects_uncontrolled_or_unsubstantiated_records(problem):
    data = discovery.template()
    row = observed(data)
    if problem == "prompt":
        row["prompt"] += " Please recommend Atlas."
    elif problem == "hash":
        row["response_sha256"] = None
    elif problem == "search":
        row["search_enabled"] = False
    elif problem == "time":
        row["observed_at"] = "2026-10-23T07:00:00"
    elif problem == "duplicate":
        data["observations"].append(deepcopy(row))
    elif problem == "question_id":
        row["question_id"], row["prompt"] = "unknown", None
    else:
        row["cited_urls"] = ["https://private:password@example.org/"]
    with pytest.raises(ValueError):
        discovery.summarize(data)


def test_build_keeps_search_ownership_tokens_and_serves_snapshot_json(tmp_path):
    # Exercise real HTML output and its internal-link checks; tokens are fictional public values.
    docs.build(tmp_path, google_verification="fictional-google-token", bing_verification="ABCD1234")
    page = (tmp_path / "index.html").read_text()
    assert 'name="google-site-verification" content="fictional-google-token"' in page
    assert 'name="msvalidate.01" content="ABCD1234"' in page
    assert (tmp_path / "examples/citation_cases.json").is_file()
    assert (tmp_path / "evals/discovery-questions.json").is_file()


def test_public_export_keeps_complete_snapshot_bytes_and_builds_its_links(
    snapshot, tmp_path, monkeypatch
):
    # Mock non-synthetic metadata exercises publication formatting without external requests.
    snapshot.update(
        {
            "is_synthetic": False,
            "title": "Mock capture",
            "captured_at": "2026-10-23T00:00:00Z",
            "atlas_version": "test",
            "transport": "stdio",
        }
    )
    snapshot["items"][0]["result"]["is_synthetic"] = False
    for item in snapshot["resolved_evidence"].values():
        item["evidence"]["raw_source_url"] = "https://source.example.org/data"
    source = tmp_path / "source"
    shutil.copytree(ROOT / "docs", source / "docs")
    for name in docs.ROOT_PAGES:
        shutil.copyfile(ROOT / name, source / name)
    saved = capture.save_capture(snapshot, source / "docs", publish=True)
    payload = Path(saved["path"]).read_bytes()
    monkeypatch.setattr(docs, "ROOT", source)
    monkeypatch.setattr(docs, "revised", lambda _: "2026-10-23")
    output = tmp_path / "site"
    docs.build(output)
    assert (output / "docs" / Path(saved["path"]).name).read_bytes() == payload
    page = output / "docs" / Path(saved["path"]).with_suffix(".html").name
    assert page.is_file() and saved["sha256"] in page.read_text()
