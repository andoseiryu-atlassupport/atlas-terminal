"""Offline source-probe instrumentation; no test establishes live connectivity."""

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from importlib.resources import files

import httpx
import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import (
    BLS_BASE,
    BLS_SERIES,
    ECB_FX_URL,
    NYFED_RATES,
    PublicTransport,
)
from atlas_terminal.regional_bond_sources import ecb_curve_identity
from atlas_terminal.source_checks import (
    SCHEMA,
    RecordingTransport,
    check_client,
    check_sources,
    json_shape,
    source_shapes,
)
from atlas_terminal.util import Database

RESOURCE = files("atlas_terminal").joinpath("resources", "research")


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(
        "atlas_terminal.research_fixed_income.utcnow", lambda: datetime(2026, 10, 20, tzinfo=UTC)
    )


def mocked_factory(mode, requests):
    def factory(directory, snapshots):
        clock = [1000.0]

        def handler(request):
            requests.append(request)
            if selected := ecb_curve_identity(str(request.url)):
                return httpx.Response(
                    200, content=RESOURCE.joinpath(selected[0] + "_2026.csv").read_bytes()
                )
            if str(request.url) == ECB_FX_URL:
                return httpx.Response(200, content=RESOURCE.joinpath("fx.xml").read_bytes())
            name = "effr" if str(request.url) == NYFED_RATES["effr"] else "sofr"
            data = json.loads(RESOURCE.joinpath(name + ".json").read_bytes())
            if mode[0] != "original":
                data["refRates"][0]["percentRate"] = 1.31
            if mode[0] in {"field", "malformed"}:
                data["refRates"][0]["newField"] = "extra metadata"
            if mode[0] == "older":
                data["refRates"][0]["effectiveDate"] = "2026-10-18"
            if mode[0] == "malformed":
                data["refRates"][0]["type"] = "UNKNOWN"
            return httpx.Response(200, json=data)

        atlas = Atlas("public", directory=directory)
        atlas._research.public = RecordingTransport(
            PublicTransport(
                cache_path=directory / "public.sqlite3",
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            ),
            snapshots,
        )
        return atlas

    return factory


def test_disabled_probes_never_construct_a_client_or_store(tmp_path):
    def forbidden(*args):
        pytest.fail("Disabled check constructed a client")

    result = check_sources(["ecb", "new_york_fed"], directory=tmp_path, client_factory=forbidden)
    assert result["status"] == "NOT_RUN" and len(result["checks"]) == 5
    assert all(row["status"] == "NOT_RUN" for row in result["checks"])
    assert not (tmp_path / "source-checks.sqlite3").exists()


def test_shape_fingerprints_ignore_values_and_repeated_rows_but_detect_field_and_type_changes():
    one = {"rows": [{"date": "2026-10-19", "value": "1.30"}]}
    two = {"rows": [{"date": "2026-10-20", "value": "2.40"}] * 3}
    assert json_shape(one) == json_shape(two)
    assert json_shape(one) != json_shape({"rows": [{"date": "2026-10-19", "value": 1.30}]})
    assert json_shape({"1": {"ticker": "A"}}) == json_shape(
        {"123": {"ticker": "B"}, "456": {"ticker": "C"}}
    )
    a = Snapshot(
        b'<feed><rate value="1"/></feed>',
        "https://fixtures.atlas.invalid/a",
        "2026-10-20T00:00:00Z",
    )
    b = Snapshot(b'<feed><rate value="2"/><rate value="3"/></feed>', a.url, a.retrieved_at)
    c = Snapshot(b'<feed><rate value="2" extra="1"/></feed>', a.url, a.retrieved_at)
    assert source_shapes([a])[0]["shape_sha256"] == source_shapes([b])[0]["shape_sha256"]
    assert source_shapes([a])[0]["shape_sha256"] != source_shapes([c])[0]["shape_sha256"]
    bom = Snapshot(b"\xef\xbb\xbf" + a.body, a.url, a.retrieved_at)
    assert source_shapes([a])[0]["shape_sha256"] == source_shapes([bom])[0]["shape_sha256"]


def test_mock_http_probes_record_changes_and_preserve_last_success_after_failures(tmp_path):
    mode, requests = ["original"], []
    factory = mocked_factory(mode, requests)

    def run():
        return check_sources(
            ["ecb", "new_york_fed"], allow_network=True, directory=tmp_path, client_factory=factory
        )

    first = run()
    assert first["status"] == "PASS" and len(requests) == 5
    assert all(row["changes"]["shape_changed"] is None for row in first["checks"])
    assert all(row["latest_observation_date"] == "2026-10-19" for row in first["checks"])
    mode[0] = "values"
    changed = run()
    rate = next(row for row in changed["checks"] if row["probe_id"] == "nyfed_sofr")
    assert rate["changes"]["raw_bytes_changed"] and not rate["changes"]["shape_changed"]
    mode[0] = "field"
    drift = run()
    assert drift["status"] == "PASS"
    assert next(row for row in drift["checks"] if row["probe_id"] == "nyfed_sofr")["changes"][
        "shape_changed"
    ]
    mode[0] = "malformed"
    failed = run()
    assert failed["status"] == "FAIL" and failed["checks"][0]["status"] == "PASS"
    mode[0] = "field"
    restored = run()
    assert restored["status"] == "PASS"
    assert (
        next(row for row in restored["checks"] if row["probe_id"] == "nyfed_sofr")["changes"][
            "shape_changed"
        ]
        is False
    )
    assert not first["real_transfer"]
    mode[0] = "older"
    regressed = run()
    assert next(row for row in regressed["checks"] if row["probe_id"] == "nyfed_sofr")["changes"][
        "latest_observation_regressed"
    ]
    assert all("@" not in request.headers["user-agent"] for request in requests)


def test_baseline_integrity_failure_cannot_be_silently_overwritten(tmp_path):
    factory = mocked_factory(["original"], [])
    check_sources(["ecb"], allow_network=True, directory=tmp_path, client_factory=factory)
    store = Database(tmp_path / "source-checks.sqlite3", SCHEMA)
    with store.connect(write=True) as conn:
        conn.execute("UPDATE source_check_baselines SET body=?", (b"{}",))
    result = check_sources(["ecb"], allow_network=True, directory=tmp_path, client_factory=factory)
    assert result["status"] == "FAIL"
    assert result["checks"][0]["error"]["code"] == "LEDGER_UNAVAILABLE"
    with store.connect() as conn:
        assert conn.execute("SELECT body FROM source_check_baselines").fetchone()[0] == b"{}"


def test_synthetic_results_are_not_live_passes_and_do_not_become_baselines(tmp_path):
    result = check_sources(
        ["ecb"],
        allow_network=True,
        directory=tmp_path,
        client_factory=lambda directory, snapshots: Atlas("fixture", directory=directory),
    )
    assert result["status"] == "NOT_RUN" and result["checks"][0]["reason"] == "synthetic_result"
    with Database(tmp_path / "source-checks.sqlite3", SCHEMA).connect() as conn:
        assert conn.execute("SELECT count(*) FROM source_check_baselines").fetchone()[0] == 0


def test_checks_revalidate_unexpired_cache_and_share_the_normal_bls_daily_quota(
    tmp_path, monkeypatch
):
    requests = []
    clock = [1000.0]

    def handler(request):
        requests.append(request)
        if "if-none-match" in request.headers:
            return httpx.Response(304, headers={"etag": "synthetic"})
        body = RESOURCE.joinpath(
            "bls_" + request.url.path.rsplit("/", 1)[-1] + ".json"
        ).read_bytes()
        # This case tests cache/quotas with available observations for every probe.
        body = body.replace(b'"value": "-"', b'"value": "1.000"')
        return httpx.Response(
            200,
            content=body,
            headers={"etag": "synthetic"},
        )

    def transport(*, cache_path):
        return PublicTransport(
            cache_path=cache_path,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )

    monkeypatch.setattr("atlas_terminal.source_checks.PublicTransport", transport)
    cache = tmp_path / "public-cache.sqlite3"
    ordinary = transport(cache_path=cache)
    ordinary.get(BLS_BASE + BLS_SERIES["cpi"][0], 21600)
    ordinary.close()
    result = check_sources(["bls"], allow_network=True, directory=tmp_path)
    assert result["status"] == "PASS" and len(requests) == 5, result["checks"]
    assert requests[1].headers["if-none-match"] == "synthetic"
    with ordinary.db.connect(write=True) as conn:
        assert conn.execute("SELECT used FROM source_daily_usage").fetchone()[0] == 5
        conn.execute("UPDATE source_daily_usage SET used=25")
    blocked = check_sources(["bls"], allow_network=True, directory=tmp_path)
    assert blocked["checks"][0]["error"]["code"] == "SOURCE_RATE_LIMITED"
    assert len(requests) == 5


def test_missing_sec_contact_is_not_run_and_optional_observation_lag_fails_explicitly(
    tmp_path, monkeypatch
):
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 21)

    monkeypatch.setattr("atlas_terminal.source_checks.date", Today)
    result = check_sources(
        ["ecb"],
        allow_network=True,
        directory=tmp_path,
        max_observation_lag_days=0,
        client_factory=mocked_factory(["original"], []),
    )
    assert result["status"] == "FAIL"
    assert result["checks"][0]["error"]["code"] == "QUALITY_NOT_MET"
    missing = check_sources(
        ["sec"], allow_network=True, directory=tmp_path, client_factory=check_client
    )
    assert (
        missing["status"] == "NOT_RUN"
        and missing["checks"][0]["error"]["code"] == "CONFIG_REQUIRED"
    )


@pytest.mark.parametrize(
    "options",
    [
        {"sources": []},
        {"sources": ["ecb", "ecb"]},
        {"sources": ["unknown"]},
        {"allow_network": "yes"},
        {"max_observation_lag_days": True},
    ],
)
def test_invalid_probe_selection_and_optins_fail_before_network(options):
    with pytest.raises(AtlasError):
        check_sources(**options, client_factory=lambda *args: pytest.fail("Invalid probe fetched"))


def test_source_check_cli_requires_explicit_network_optin():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "check-sources",
            "--sources",
            "ecb",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 4 and not result.stderr
    assert json.loads(result.stdout)["status"] == "NOT_RUN"
