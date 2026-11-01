"""Official instrument identity, sparse CSV columns, dates and source-cell contracts."""

from importlib.resources import files

import httpx
import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.policy_sources import POLICY_SERIES, parse_policy, policy_identity, policy_url
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_models import parse_research

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
DATES = {"start_date": "2026-10-28", "end_date": "2026-10-30"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def snap(series="ecb_deposit_facility", body=None):
    name = "policy_" + series + ".csv"
    return Snapshot(
        body if body is not None else RESOURCE.joinpath(name).read_bytes(),
        "https://fixtures.atlas.invalid/research/" + name,
        "2026-10-30T12:00:00Z",
    )


@pytest.mark.parametrize("series", POLICY_SERIES)
def test_policy_read_has_source_cells_explicit_instruments_missing_values_and_no_filled_dates(
    series,
):
    with Atlas("fixture") as atlas:
        result = atlas.get_research({"dataset": "policy_rates", "series": series, **DATES})
    assert [r["date"] for r in result["records"]] == ["2026-10-30", "2026-10-29", "2026-10-28"]
    assert result["records"][0]["value"] is None
    assert result["records"][1]["value"] == ("3.15" if series.startswith("rba") else "2.15")
    row = result["records"][1]
    assert row["rate_type"] == "central_bank_policy_rate" and not row["derived"]
    assert row["unit"] == "percent_per_annum" and row["policy_instrument"]
    assert row["source"]["csv_row"] >= 2 and row["source"]["csv_column"] >= 2
    assert result["source_id"] == POLICY_SERIES[series]["source_id"]
    assert not result["coverage"]["point_in_time"]


@pytest.mark.parametrize(
    "before,after",
    [
        (b"FM.D.U2.EUR.4F.KR.DFR.LEV", b"FM.D.U2.EUR.4F.KR.MLFR.LEV"),
        (b",D,U2,", b",B,U2,"),
        (b",EUR,4F,", b",USD,4F,"),
        (b",PCPA,0", b",PCPA,2"),
        (b",2.10,A,", b",2.10,M,"),
        (b",2.10,A,", b",NaN,A,"),
        (b",2.10,A,", b",1e999,A,"),
        (b"2026-10-28", b"2026-02-30"),
        (b"2026-10-29", b"2026-10-28"),
        (b",OBS_VALUE,", b",OBS_STATUS,"),
    ],
)
def test_ecb_identity_unit_status_numeric_and_date_changes_are_rejected(before, after):
    raw = snap().body
    assert before in raw
    with pytest.raises(AtlasError):
        parse_policy(snap(body=raw.replace(before, after)), "ecb_deposit_facility")


@pytest.mark.parametrize(
    "before,after",
    [
        (b"Cash Rate Target", b"Interbank Overnight Cash Rate"),
        (b"Units,Per cent", b"Units,Index"),
        (b"Source,RBA", b"Source,ASX"),
        (b"FIRMMCRTD", b"FIRMMCRID"),
        (b"28-Oct-2026,3.10", b"28-Oct-2026"),
        (b"28-Oct-2026", b"31-Feb-2026"),
    ],
)
def test_rba_cannot_substitute_an_interbank_rate_or_drop_the_target_column(before, after):
    raw = snap("rba_cash_target").body
    with pytest.raises(AtlasError):
        parse_policy(snap("rba_cash_target", raw.replace(before, after)), "rba_cash_target")


def test_rba_trailing_unselected_empty_columns_may_be_omitted_and_exact_decimals_are_preserved():
    raw = (
        snap("rba_cash_target")
        .body.replace(b"Series ID,FIRMMCRTD", b"Series ID,FIRMMCRTD,UNSELECTED")
        .replace(b"28-Oct-2026,3.10", b"28-Oct-2026,3.100000000000000001")
    )
    rows = parse_policy(snap("rba_cash_target", raw), "rba_cash_target")
    assert rows[0]["value"] == "3.100000000000000001"
    assert rows[0]["source"]["csv_column"] == 2


@pytest.mark.parametrize("series", POLICY_SERIES)
def test_fixed_policy_urls_round_trip_and_refuse_extra_hosts_queries_and_credentials(series):
    url = policy_url(series, DATES["start_date"], DATES["end_date"])
    assert policy_identity(url)[0] == series
    PublicTransport.validate_url(url)
    for other in [
        url + "&api_key=private",
        url.replace("https://", "http://"),
        url.replace(".gov.au/", ".gov.au.evil/"),
        url + "#fragment",
    ]:
        if other == url:
            continue
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(other)


def test_public_transport_uses_normal_client_and_source_validation_before_cache(tmp_path):
    requests = []
    body = snap().body

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "text/csv"})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    transport = PublicTransport(cache_path=tmp_path / "policy.sqlite3", client=client, interval=0)
    url = policy_url("ecb_deposit_facility", **{"start": "2026-10-28", "end": "2026-10-30"})
    try:
        first = transport.get(url, 3600)
        second = transport.get(url, 3600)
    finally:
        transport.close()
    assert first.body == second.body == body and len(requests) == 1
    assert str(requests[0].url) == url and requests[0].headers["accept"] == "text/csv"


@pytest.mark.parametrize(
    "change",
    [{"series": "sofr"}, {"series": "rba_interbank"}, {"start_date": "2026-10-29"}, {"year": 2026}],
)
def test_unsupported_instruments_and_inapplicable_arguments_are_rejected(change):
    q = {"dataset": "policy_rates", "series": "ecb_deposit_facility"}
    with pytest.raises(AtlasError):
        parse_research(q | change)


def test_policy_templates_are_searchable_and_panel_aggregation_is_explicit_without_forward_fill():
    with Atlas("fixture") as atlas:
        catalog = atlas.research_catalog("policy_rates")
        result = atlas.get_research(
            {
                "dataset": "macro_panel",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
                "frequency": "monthly",
                "allow_partial": True,
                "items": [
                    {
                        "id": "deposit",
                        "query": {"dataset": "policy_rates", "series": "ecb_deposit_facility"},
                        "aggregation": "last",
                    }
                ],
            }
        )
    assert len(catalog["series_search"]["records"]) == 4
    assert catalog["datasets"][0]["source_ids"] == ["ecb", "rba"]
    row = result["records"][0]
    assert "2.15" in str(row)
