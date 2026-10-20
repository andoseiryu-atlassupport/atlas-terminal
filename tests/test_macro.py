"""Official-shaped fictional macro inputs, exact arithmetic and shared interfaces."""

import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal, localcontext
from importlib.resources import files
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import MACRO_SERIES
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import (
    BLS_BASE,
    BLS_SERIES,
    ECB_FX_URL,
    NYFED_RATES,
    TREASURY_BASE,
    TREASURY_REAL_BASE,
    PublicFixtures,
    PublicTransport,
)
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
FX_QUERY = {"dataset": "fx_rates", "base_currency": "USD", "quote_currency": "JPY"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


class Sources(PublicFixtures):
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def get(self, url, ttl):
        PublicTransport.validate_url(url)
        self.calls.append(url)
        if url in self.overrides:
            return Snapshot(
                self.overrides[url],
                "https://fixtures.atlas.invalid/modified",
                "2026-10-20T00:00:00Z",
            )
        return super().get(url, ttl)


def application(overrides=None):
    atlas = Atlas("fixture")
    atlas._research.public = Sources(overrides)
    return atlas


def xml_bytes(name, change):
    root = ET.fromstring(RESOURCE.joinpath(name).read_bytes())
    change(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def test_fx_direction_cross_inverse_units_sources_and_missing_dates():
    with application() as atlas:
        cross = atlas.get_research(FX_QUERY)
        direct = atlas.get_research(FX_QUERY | {"base_currency": "EUR", "quote_currency": "USD"})
        inverse = atlas.get_research(FX_QUERY | {"base_currency": "USD", "quote_currency": "EUR"})
    row = cross["records"][0]
    assert row["value"] == "120" and row["unit"] == "JPY_per_USD" and row["derived"]
    assert row["quote_convention"] == "quote_currency_per_one_base_currency"
    assert row["published_at"] is None and row["rate_type"] == "reference"
    assert {item["date"] for item in row["inputs"]} == {row["date"]}
    assert [item["value"] for item in row["inputs"]] == ["1.2500", "150.00"]
    assert all(item["source"]["xml_local_path"].endswith("/@rate") for item in row["inputs"])
    assert [item["date"] for item in cross["records"]] == ["2026-10-19", "2026-10-16", "2026-10-15"]
    assert cross["records"][-1]["value"] is None
    assert "same-date" in cross["records"][-1]["reason"]
    assert direct["records"][0]["value"] == "1.2500" and not direct["records"][0]["derived"]
    assert inverse["records"][0]["value"] == "0.8" and inverse["records"][0]["derived"]
    assert cross["coverage"]["upstream_window_limited"]
    assert cross["attributions"][0]["source_id"] == "ecb"


@pytest.mark.parametrize(
    "base,quote,expected",
    [
        ("EUR", "USD", "1.2500"),
        ("USD", "EUR", "0.8"),
        ("USD", "JPY", "120"),
        ("JPY", "USD", "0.008333333333333333333333333333"),
        ("GBP", "CHF", "1.125"),
        ("AUD", "CAD", "0.9375"),
        ("NZD", "CNY", "4"),
        ("CNY", "USD", "0.15625"),
    ],
)
def test_supported_currency_pairs_have_explicit_quote_units(base, quote, expected):
    with application() as atlas:
        result = atlas.get_research(
            FX_QUERY | {"base_currency": base, "quote_currency": quote, "limit": 1}
        )
    row = result["records"][0]
    assert row["base_currency"] == base and row["quote_currency"] == quote
    assert row["unit"] == f"{quote}_per_{base}" and row["value"] == expected


def test_missing_fx_attribute_retains_an_existing_source_locator():
    def edit(root):
        root.find("{*}Cube/{*}Cube/{*}Cube").attrib.pop("rate")

    body = xml_bytes("fx.xml", edit)
    with application({ECB_FX_URL: body}) as atlas:
        row = atlas.get_research(FX_QUERY)["records"][0]
    missing = row["inputs"][0]
    assert row["value"] is None and missing["status"] == "unavailable"
    assert missing["source"]["xml_local_path"] == "/Envelope/Cube/Cube[1]/Cube[1]"


@pytest.mark.parametrize(
    "change",
    [
        "date",
        "duplicate_day",
        "duplicate_currency",
        "zero",
        "negative",
        "nonfinite",
        "enormous",
        "root",
        "namespace",
    ],
)
def test_bad_ecb_observations_never_become_valid_rates(change):
    def edit(root):
        parent = root.find("{*}Cube")
        day = parent[0]
        if change == "date":
            day.set("time", "2026-99-99")
        elif change == "duplicate_day":
            parent.append(deepcopy(day))
        elif change == "duplicate_currency":
            day.append(deepcopy(day[0]))
        elif change in {"zero", "negative", "nonfinite", "enormous"}:
            day[0].set(
                "rate",
                {"zero": "0", "negative": "-1", "nonfinite": "NaN", "enormous": "1e999999999"}[
                    change
                ],
            )
        elif change == "root":
            root.tag = "Envelope"
        else:
            parent.tag = "Cube"

    body = xml_bytes("fx.xml", edit)
    with application({ECB_FX_URL: body}) as atlas, pytest.raises(AtlasError) as caught:
        atlas.get_research(FX_QUERY)
    assert caught.value.code in {"SOURCE_UNAVAILABLE", "INCOMPLETE_COVERAGE"}


@pytest.mark.parametrize("series", ["sofr", "effr"])
def test_reference_rates_keep_percentage_units_effective_dates_and_notices(series):
    with application() as atlas:
        result = atlas.get_research({"dataset": "reference_rates", "series": series})
    row = result["records"][0]
    assert row["unit"] == "percent_per_annum" and row["source_series"] == series.upper()
    assert row["date"] == "2026-10-19" and row["published_at"] is None
    assert Decimal(row["value"]) == Decimal("1.30" if series == "sofr" else "1.25")
    assert row["source"]["json_pointer"] == "/refRates/0/percentRate"
    notice = result["attributions"][0]
    assert "not affiliated" in notice["disclaimer"] and "Terms of Use" in notice["notice"]
    assert notice["copyright"].startswith("© 2026")
    if series == "effr":
        assert result["records"][1]["revision_indicator"] == "Y"


@pytest.mark.parametrize(
    "change",
    [
        "series",
        "duplicate",
        "date",
        "boolean",
        "nonfinite",
        "missing_field",
        "wrong_shape",
        "conflict",
        "window",
    ],
)
def test_reference_series_identity_and_shape_fail_closed(change):
    data = json.loads(RESOURCE.joinpath("sofr.json").read_bytes())
    row = data["refRates"][0]
    if change == "series":
        row["type"] = "EFFR"
    elif change == "duplicate":
        data["refRates"].append(deepcopy(row))
    elif change == "date":
        row["effectiveDate"] = "2026-10-32"
    elif change == "boolean":
        row["percentRate"] = True
    elif change == "nonfinite":
        row["percentRate"] = "Infinity"
    elif change == "missing_field":
        del row["percentRate"]
    elif change == "wrong_shape":
        data = {"data": data["refRates"]}
    elif change == "conflict":
        row["rateType"] = "EFFR"
    else:
        data["refRates"] *= 46
    with (
        application({NYFED_RATES["sofr"]: json.dumps(data).encode()}) as atlas,
        pytest.raises(AtlasError) as caught,
    ):
        atlas.get_research({"dataset": "reference_rates", "series": "sofr"})
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_real_yields_preserve_negative_null_and_absent_maturities():
    with application() as atlas:
        result = atlas.get_research({"dataset": "treasury_real_yields", "year": 2026})
    rows = {(row["date"], row["maturity"]): row for row in result["records"]}
    assert Decimal(rows["2026-10-16", "5YEAR"]["value"]) == Decimal("-0.10")
    assert rows["2026-10-19", "30YEAR"]["value"] is None
    missing = rows["2026-10-16", "30YEAR"]
    assert missing["value"] is None and missing["source"]["xml_local_path"].endswith("/properties")
    assert all(
        row["rate_type"] == "real_par" and row["published_at"] is None for row in rows.values()
    )
    assert result["coverage"]["observed_records"] == 10


@pytest.mark.parametrize(
    "change", ["year", "duplicate", "nominal", "nonfinite", "null_conflict", "location"]
)
def test_real_yield_source_must_match_curve_year_and_locator(change):
    def edit(root):
        props = root.find("{*}entry/{*}content/{*}properties")
        if change == "year":
            props.find("{*}NEW_DATE").text = "2025-10-19T00:00:00"
        elif change == "duplicate":
            root.append(deepcopy(root.find("{*}entry")))
        elif change == "nominal":
            for node in root.iter():
                node.tag = node.tag.replace("TC_", "BC_")
        elif change == "nonfinite":
            props.find("{*}TC_5YEAR").text = "NaN"
        elif change == "null_conflict":
            props.find("{*}TC_30YEAR").text = "5.0"
        else:
            root.find("{*}entry/{*}content").tag = "wrong"

    with (
        application({TREASURY_REAL_BASE + "2026": xml_bytes("real_yields.xml", edit)}) as atlas,
        pytest.raises(AtlasError) as caught,
    ):
        atlas.get_research({"dataset": "treasury_real_yields", "year": 2026})
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def bls_data(alias, rows):
    data = json.loads(RESOURCE.joinpath("bls_" + BLS_SERIES[alias][0] + ".json").read_bytes())
    data["Results"]["series"][0]["data"] = rows
    return BLS_BASE + BLS_SERIES[alias][0], json.dumps(data).encode()


def test_cpi_exact_calendar_year_baseline_excludes_annual_average_and_uses_full_input_window():
    rows = [
        {"year": "2026", "period": "M09", "value": "321.500"},
        {"year": "2025", "period": "M13", "value": "1"},
        {"year": "2025", "period": "M09", "value": "310.000"},
        {"year": "2025", "period": "M08", "value": "2"},
    ]
    url, body = bls_data("cpi", rows)
    with application({url: body}) as atlas:
        result = atlas.get_research({"dataset": "macro_analytics", "series": "cpi_yoy", "limit": 1})
    row = result["records"][0]
    with localcontext() as context:
        context.prec = 28
        assert row["value"] == str(Decimal("11.500") / Decimal("310.000"))
    assert row["comparison_period"] == {"year": "2025", "period": "M09", "period_kind": "month"}
    assert [item["value"] for item in row["inputs"]] == ["321.500", "310.000"]
    assert row["unit"] == "ratio" and row["status"] == "derived" and not row["seasonally_adjusted"]
    assert row["inputs"][1]["source"]["json_pointer"].endswith("/data/2")
    assert result["coverage"]["truncated"]


@pytest.mark.parametrize(
    "mode", ["missing_month", "missing_value", "zero", "negative", "duplicate"]
)
def test_cpi_does_not_substitute_a_different_month_or_invalid_baseline(mode):
    rows = [{"year": "2026", "period": "M09", "value": "320"}]
    rows.append(
        {
            "year": "2025",
            "period": "M08" if mode == "missing_month" else "M09",
            "value": {"zero": "0", "negative": "-1", "missing_value": "-"}.get(mode, "310"),
        }
    )
    if mode == "duplicate":
        rows.append(deepcopy(rows[1]))
    url, body = bls_data("cpi", rows)
    with application({url: body}) as atlas:
        if mode == "duplicate":
            with pytest.raises(AtlasError) as caught:
                atlas.get_research({"dataset": "macro_analytics", "series": "cpi_yoy"})
            assert caught.value.code == "SOURCE_UNAVAILABLE"
        else:
            result = atlas.get_research({"dataset": "macro_analytics", "series": "cpi_yoy"})
            assert result["records"][0]["value"] is None
            assert result["records"][0]["status"] == "unavailable"


def test_payroll_change_is_exact_absolute_thousands_and_requires_adjacent_months():
    rows = [
        {"year": "2026", "period": "M01", "value": "150000.100"},
        {"year": "2025", "period": "M12", "value": "150001.200"},
        {"year": "2025", "period": "M10", "value": "100000"},
    ]
    url, body = bls_data("payrolls", rows)
    with application({url: body}) as atlas:
        result = atlas.get_research({"dataset": "macro_analytics", "series": "payrolls_change_mom"})
    row = result["records"][0]
    assert row["value"] == "-1.100" and row["unit"] == "thousand_persons"
    assert (
        row["comparison_period"]["year"] == "2025" and row["comparison_period"]["period"] == "M12"
    )
    assert result["records"][1]["value"] is None


def test_curve_spread_uses_same_date_and_converts_percentage_points_to_basis_points():
    with application() as atlas:
        result = atlas.get_research(
            {"dataset": "macro_analytics", "series": "yield_spread_10y_2y", "year": 2026}
        )
    row = result["records"][0]
    assert row["value"] == "25.00" and row["unit"] == "basis_points"
    assert {item["date"] for item in row["inputs"]} == {row["date"]}
    assert result["source_id"] == "treasury" and result["source_terms_url"].startswith(
        "https://home.treasury.gov/"
    )

    def edit(root):
        props = root.find("{*}entry/{*}content/{*}properties")
        props.remove(props.find("{*}BC_2YEAR"))
        other = deepcopy(root.find("{*}entry"))
        other.find("{*}content/{*}properties/{*}NEW_DATE").text = "2026-10-15T00:00:00"
        other_props = other.find("{*}content/{*}properties")
        other_props.remove(other_props.find("{*}BC_10YEAR"))
        ET.SubElement(
            other_props, "{http://schemas.microsoft.com/ado/2007/08/dataservices}BC_2YEAR"
        ).text = "3.50"
        root.append(other)

    with application({TREASURY_BASE + "2026": xml_bytes("yields.xml", edit)}) as atlas:
        missing = atlas.get_research(
            {"dataset": "macro_analytics", "series": "yield_spread_10y_2y", "year": 2026}
        )
    assert all(row["value"] is None for row in missing["records"])


def test_macro_arithmetic_is_independent_of_the_callers_decimal_context():
    with application() as atlas:
        queries = [
            FX_QUERY,
            {"dataset": "macro_analytics", "series": "payrolls_change_mom"},
            {"dataset": "macro_analytics", "series": "yield_spread_10y_2y", "year": 2026},
        ]
        expected = [atlas.get_research(q)["records"] for q in queries]
        with localcontext() as context:
            context.prec = 2
            actual = [atlas.get_research(q)["records"] for q in queries]
    assert actual == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"dataset": "fx_rates", "base_currency": "USD", "quote_currency": "USD"},
        {"dataset": "fx_rates", "base_currency": "BTC", "quote_currency": "JPY"},
        {"dataset": "fx_rates", "base_currency": None, "quote_currency": "JPY"},
        {"dataset": "reference_rates", "series": "term_sofr"},
        {"dataset": "macro_analytics", "series": "invented"},
        {"dataset": "treasury_real_yields", "year": 2002},
        {"dataset": "macro_analytics", "series": "cpi_yoy", "base_currency": "USD"},
    ],
)
def test_invalid_macro_queries_fail_schema_and_runtime_before_retrieval(bad):
    with pytest.raises(ValidationError):
        Draft202012Validator(ResearchQuery.model_json_schema()).validate(bad)
    with application() as atlas, pytest.raises(AtlasError):
        atlas.get_research(bad)
    assert atlas._research.public.calls == []


def test_curated_urls_and_new_hosts_never_receive_private_sec_contact(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "PRIVATE_CONTACT research@operator.invalid")
    assert TREASURY_BASE.startswith("https://home.treasury.gov/resource-center/data-chart-center/")
    for url in [ECB_FX_URL, *NYFED_RATES.values(), TREASURY_REAL_BASE + "2026"]:
        PublicTransport.validate_url(url)
    for url in [
        ECB_FX_URL + "?redirect=private",
        ECB_FX_URL.replace("www.ecb", "evil"),
        NYFED_RATES["sofr"].replace("90", "5000"),
        TREASURY_REAL_BASE + "2002",
        TREASURY_REAL_BASE + "2026&key=private",
    ]:
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(url)
    requests = []
    now = [1000.0]

    def handler(request):
        requests.append(request)
        body = RESOURCE.joinpath(
            "fx.xml" if str(request.url) == ECB_FX_URL else "sofr.json"
        ).read_bytes()
        return httpx.Response(200, content=body, headers={"etag": "synthetic"})

    transport = PublicTransport(
        cache_path=tmp_path / "public.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    with Atlas("public") as atlas:
        atlas._research.public = transport
        first = atlas.get_research(FX_QUERY)
        assert atlas.get_research(FX_QUERY) == first
        atlas.get_research({"dataset": "reference_rates", "series": "sofr"})
        assert atlas.source is None and len(requests) == 2
        assert all("@" not in request.headers["user-agent"] for request in requests)


def test_new_macro_queries_share_cli_sdk_http_and_compact_evidence(tmp_path):
    queries = [
        FX_QUERY,
        {"dataset": "reference_rates", "series": "sofr"},
        {"dataset": "treasury_real_yields", "year": 2026},
        {"dataset": "macro_analytics", "series": "yield_spread_10y_2y", "year": 2026},
    ]
    with (
        TestClient(create_app(provider="fixture", directory=tmp_path / "server")) as http,
        Atlas("fixture") as atlas,
    ):
        for q in queries:
            expected = atlas.get_research(q)
            actual = http.post("/v1/research", json=q)
            assert actual.status_code == 200 and actual.json() == expected
        compact = http.post(
            "/v1/research",
            json=FX_QUERY | {"response": {"view": "compact", "fields": ["value"], "page_size": 1}},
        ).json()
        row = compact["records"][0]
        assert row["base_currency"] == "USD" and row["quote_currency"] == "JPY"
        assert row["quote_convention"] and row["rounding"] and row["inputs"]
        evidence_id = row["inputs"][0]["source"]["evidence_id"]
        evidence = http.get("/v1/research/evidence/" + evidence_id).json()
        assert digest(canonical(evidence["evidence"])) == evidence_id
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "fx_rates",
            "--base-currency",
            "USD",
            "--quote-currency",
            "JPY",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    with Atlas("fixture") as atlas:
        assert json.loads(result.stdout) == atlas.get_research(FX_QUERY)


async def test_real_mcp_discovers_new_query_schemas_and_returns_all_four_macro_datasets(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        queries = [
            FX_QUERY,
            {"dataset": "reference_rates", "series": "effr"},
            {"dataset": "treasury_real_yields", "year": 2026},
            {"dataset": "macro_analytics", "series": "payrolls_change_mom"},
        ]
        with Atlas("fixture") as atlas:
            for q in queries:
                result = await session.call_tool("atlas_get_research", {"query": q})
                assert not result.is_error and result.structured_content == atlas.get_research(q)
        catalog = await session.call_tool(
            "atlas_research_catalog", {"dataset": "macro_analytics", "view": "compact"}
        )
        assert set(catalog.structured_content["macro_series"]) == set(MACRO_SERIES)
        assert set(catalog.structured_content["sources"]) == {"bls", "treasury"}
