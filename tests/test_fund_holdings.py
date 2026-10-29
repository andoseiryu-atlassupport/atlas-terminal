"""Fictional N-PORT tests for issuer/date identity, values, provenance and bounded XML."""

import json
from dataclasses import replace
from importlib.resources import files
from xml.etree import ElementTree as ET

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Filing, SECProvider, Snapshot
from atlas_terminal.research import ResearchSecFixtures
from atlas_terminal.research_funds import fund_holdings, parse_fund_holdings
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_views import ResearchResult

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
BODY = RESOURCE.joinpath("fund.xml").read_bytes()
FILING = Filing(
    "9999999999-26-000008",
    "NPORT-P",
    "2026-10-13",
    "2026-08-31",
    None,
    "fund.xml",
    "https://fixtures.atlas.invalid/fund.xml",
    "primary_document",
)


def snapshot(body=BODY):
    return Snapshot(body, "https://fixtures.atlas.invalid/fund.xml", "2026-10-20T00:00:00Z")


def parse(body=BODY, *, filing=FILING, limit=100):
    return parse_fund_holdings(snapshot(body), filing, "9999999999", limit)


def source_cell(source, body=BODY):
    root = ET.fromstring(body)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    path, _, attribute = source["xml_local_path"].partition("/@")
    path = path.removeprefix("/edgarSubmission/")
    node = root.find(path)
    assert node is not None
    return node.attrib[attribute] if attribute else node.text


def test_reported_portfolio_keeps_currency_basis_short_values_and_identifiers():
    result = parse()
    assert result["fund"]["report_date"] == "2026-08-31"
    assert result["fund"]["fiscal_year_end"] == "2026-12-31"
    assert result["fund"]["net_assets"] == "1000"
    assert result["fund"]["monetary_unit"] == "USD"
    us, japan, swap = result["records"]
    assert us["reported_percentage"] == "60"
    assert us["percentage_unit"] == "percent_of_net_assets"
    assert japan["denomination_currency"] == "JPY"
    assert japan["value_unit"] == "USD" and japan["reported_value"] == "250"
    assert japan["balance"] == "1000.125"
    assert japan["asset_category"] == "OTHER"
    assert japan["asset_category_description"] == "Fictional category"
    assert japan["identifiers"][1]["value"] == "000A"
    assert swap["reported_value"] == "-10" and swap["reported_percentage"] == "-1"
    assert swap["derivative_details_reported"] and swap["payoff_profile"] == "Short"
    assert not us["derivative_details_reported"]
    assert result["coverage"]["point_in_time"] is False
    assert all(record["accession"] == FILING.accession for record in result["records"])


def test_every_reported_scalar_resolves_to_original_xml_text_or_attribute():
    result = parse()
    for record in result["records"] + [result["fund"]]:
        for key, source in record["field_sources"].items():
            assert source_cell(source) == record[key]
            assert source["raw_sha256"] == snapshot().sha256
        for identifier in record.get("identifiers", []):
            assert source_cell(identifier["source"]) == identifier["value"]


def test_no_normalizing_to_returned_positions_and_explicit_truncation():
    result = parse(limit=1)
    assert len(result["records"]) == 1
    assert result["records"][0]["reported_percentage"] == "60"
    assert result["coverage"]["matching_records"] == 3
    assert result["coverage"]["truncated"] is True


def test_missing_numeric_values_remain_unavailable_and_zero_remains_reported():
    body = BODY.replace(b"<valUSD>600</valUSD>", b"")
    body = body.replace(b"<valUSD>250</valUSD>", b"<valUSD>0</valUSD>")
    result = parse(body)
    first, second = result["records"][:2]
    assert first["reported_value"] is None and first["status"] == "unavailable"
    assert "reported_value" not in first["field_sources"]
    assert second["reported_value"] == "0" and second["status"] == "reported"
    assert result["coverage"]["missing_reported_values"] == 1


@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"1e999999", b"1,000", b"--1", b"9" * 41])
def test_nonfinite_ambiguous_or_unbounded_decimal_fails(value):
    with pytest.raises(AtlasError, match="decimal"):
        parse(BODY.replace(b"<valUSD>600</valUSD>", b"<valUSD>" + value + b"</valUSD>"))


@pytest.mark.parametrize(
    "before,after",
    [
        (b"<regCik>9999999999</regCik>", b"<regCik>9999999998</regCik>"),
        (b"<repPdDate>2026-08-31</repPdDate>", b"<repPdDate>2026-09-30</repPdDate>"),
        (b"<repPdDate>2026-08-31</repPdDate>", b"<repPdDate>2026-02-30</repPdDate>"),
        (
            b"<submissionType>NPORT-P</submissionType>",
            b"<submissionType>NPORT-P/A</submissionType>",
        ),
        (b"<valUSD>600</valUSD>", b"<valUSD>600</valUSD><valUSD>700</valUSD>"),
        (
            b"<assetCat>EC</assetCat>",
            b'<assetCat>EC</assetCat><assetConditional assetCat="OTHER"/>',
        ),
        (
            b"<name>Atlas Fictional US Company (SYNTHETIC)</name>",
            b"<name><nested>bad</nested></name>",
        ),
        (b"http://www.sec.gov/edgar/nport", b"https://untrusted.invalid/nport"),
        (b"<valUSD>600</valUSD>", b'<valUSD xmlns="https://untrusted.invalid/">600</valUSD>'),
    ],
)
def test_identity_repeated_fields_and_namespace_conflicts_fail(before, after):
    with pytest.raises(AtlasError):
        parse(BODY.replace(before, after))


def test_report_period_must_not_be_later_than_filing_date():
    with pytest.raises(AtlasError, match="reporting date"):
        parse(filing=replace(FILING, filed_on="2026-08-01"))


def test_empty_and_missing_portfolio_are_distinct():
    root = ET.fromstring(BODY)
    form = root.find("{*}formData")
    investments = form.find("{*}invstOrSecs")
    investments.clear()
    result = parse(ET.tostring(root))
    assert result["records"] == [] and result["coverage"]["matching_records"] == 0
    form.remove(investments)
    with pytest.raises(AtlasError, match="investment list"):
        parse(ET.tostring(root))


def test_xml_declaration_and_position_bounds_are_enforced(monkeypatch):
    with pytest.raises(AtlasError, match="declarations"):
        parse(b'<!DOCTYPE x [<!ENTITY danger "text">]>' + BODY)
    with pytest.raises(AtlasError, match="UTF-8"):
        parse(BODY.decode().encode("utf-16"))
    monkeypatch.setattr("atlas_terminal.research_funds.MAX_POSITIONS", 2)
    with pytest.raises(AtlasError, match="investment count"):
        parse()


class FundTransport(ResearchSecFixtures):
    def __init__(self, overrides=None):
        super().__init__(overrides)
        self.requests = []

    def get(self, url, ttl):
        self.requests.append(url)
        name = url.rsplit("/", 1)[-1]
        if name == "fund.xml":
            return snapshot(self.overrides.get(name, BODY))
        return super().get(url, ttl)


def query(**extra):
    return ResearchQuery.model_construct(
        dataset="fund_holdings", identifier="ATLASDEMO", accession=FILING.accession, **extra
    )


def test_exact_accession_read_retains_filing_and_index_evidence():
    transport = FundTransport()
    result = fund_holdings(SECProvider(transport), query())
    assert result["filing"]["accession"] == FILING.accession
    assert len(result["records"]) == 3
    assert result["coverage"]["history_truncated"] is False
    assert any("accessionNumber/8" in s.get("json_pointer", "") for s in result["sources"])
    assert any(s["raw_source_url"].endswith("index.json") for s in result["sources"])
    assert any("/999999999926000008/fund.xml" in url for url in transport.requests)


def test_wrong_or_missing_accession_cannot_fall_back_to_latest():
    for accession in [None, "9999999999-26-000005", "9999999999-26-000099"]:
        transport = FundTransport()
        q = query().model_copy(update={"accession": accession})
        with pytest.raises(AtlasError):
            fund_holdings(SECProvider(transport), q)
        assert not any("/Archives/" in url for url in transport.requests)


@pytest.mark.parametrize(
    "names", [["../fund.xml"], ["https://evil.invalid/fund.xml"], ["fund.xml", "fund.xml"]]
)
def test_unsafe_and_duplicate_directory_entries_fail_before_document_get(names):
    index = {"directory": {"item": [{"name": name} for name in names]}}
    transport = FundTransport({"index.json": json.dumps(index).encode()})
    with pytest.raises(AtlasError):
        fund_holdings(SECProvider(transport), query())
    assert not any(url.endswith("fund.xml") for url in transport.requests)


def test_explicit_amendment_does_not_merge_original_positions():
    submissions = json.loads(RESOURCE.joinpath("submissions.json").read_bytes())
    submissions["filings"]["recent"]["form"][8] = "NPORT-P/A"
    body = BODY.replace(
        b"<submissionType>NPORT-P</submissionType>", b"<submissionType>NPORT-P/A</submissionType>"
    )
    transport = FundTransport(
        {"submissions.json": json.dumps(submissions).encode(), "fund.xml": body}
    )
    result = fund_holdings(SECProvider(transport), query(limit=2))
    assert result["filing"]["form"] == "NPORT-P/A"
    assert result["records"][0]["reported_value"] == "600"
    assert result["coverage"]["returned_records"] == 2


def test_allocations_keep_exact_reported_denominator_and_derivative_market_values():
    allocations = parse()["allocations"]
    country = {
        row["reported_group"]: row
        for row in allocations["records"]
        if row["dimension"] == "investment_country"
    }
    assert country["US"]["value"] == "59.000000000000000000"
    assert country["US"]["reported_value_sum"] == "590"
    assert country["US"]["derivative_positions"] == 1
    assert country["JP"]["value"] == "25.000000000000000000"
    assert allocations["denominator"]["value"] == "1000"
    assert source_cell(allocations["denominator"]["source"]) == "1000"
    assert country["US"]["positions"] == [1, 3]
    assert allocations["basis"] == "reported_market_value_not_economic_exposure"
    for row in allocations["inputs"]:
        assert source_cell(row["source"]) == row["value"]
        for classification in row["classifications"].values():
            assert source_cell(classification["source"]) == classification["value"]


def test_allocation_can_exceed_100_and_remains_independent_of_decimal_context():
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 3
        result = parse(BODY.replace(b"<netAssets>1000</netAssets>", b"<netAssets>333</netAssets>"))
    us = next(
        row
        for row in result["allocations"]["records"]
        if row["dimension"] == "investment_country" and row["reported_group"] == "US"
    )
    assert us["value"] == "177.177177177177177177"


@pytest.mark.parametrize(
    "body,limit,reason",
    [
        (BODY, 1, "truncated_positions"),
        (BODY.replace(b"<valUSD>600</valUSD>", b""), 100, "missing_reported_values"),
        (
            BODY.replace(b"<netAssets>1000</netAssets>", b""),
            100,
            "missing_or_nonpositive_net_assets",
        ),
        (
            BODY.replace(b"<netAssets>1000</netAssets>", b"<netAssets>0</netAssets>"),
            100,
            "missing_or_nonpositive_net_assets",
        ),
    ],
)
def test_incomplete_allocations_do_not_silently_sum_the_available_subset(body, limit, reason):
    allocations = parse(body, limit=limit)["allocations"]
    assert allocations == {"status": "unavailable", "reason": reason, "records": []}


def test_currency_conditional_and_signature_namespace_remain_supported():
    body = BODY.replace(
        b"<curCd>JPY</curCd>", b'<currencyConditional curCd="JPY" exchangeRt="150.125"/>'
    ).replace(
        b"</edgarSubmission>",
        b'<signature xmlns="http://www.sec.gov/edgar/nportcommon"><nameOfApplicant>Fictional</nameOfApplicant></signature></edgarSubmission>',
    )
    japan = parse(body)["records"][1]
    assert japan["denomination_currency"] == "JPY"
    assert japan["reported_exchange_rate"] == "150.125"
    assert japan["reported_value"] == "250"
    assert source_cell(japan["field_sources"]["denomination_currency"], body) == "JPY"
    assert source_cell(japan["field_sources"]["reported_exchange_rate"], body) == "150.125"


def test_selected_fields_cannot_reuse_other_known_namespace():
    body = BODY.replace(
        b"<valUSD>600</valUSD>", b'<valUSD xmlns="http://www.sec.gov/edgar/common">600</valUSD>'
    )
    with pytest.raises(AtlasError, match="namespace"):
        parse(body)


@pytest.mark.parametrize(
    "header",
    [
        b"<filerInfo><filer><issuerCredentials><cik>9999999998</cik></issuerCredentials></filer></filerInfo>",
        b"<filerInfo><seriesClassInfo><seriesId>S000000001</seriesId></seriesClassInfo></filerInfo>",
    ],
)
def test_header_issuer_and_series_cannot_conflict_with_portfolio_identity(header):
    with pytest.raises(AtlasError, match="N-PORT header"):
        parse(BODY.replace(b"</headerData>", header + b"</headerData>"))


def test_shared_service_validates_envelope_compact_provenance_and_context_projection():
    with Atlas("fixture") as atlas:
        full = atlas.get_research(
            {
                "dataset": "fund_holdings",
                "identifier": "ATLASDEMO",
                "accession": FILING.accession,
            }
        )
        ResearchResult.model_validate(full)
        compact = atlas.get_research(
            {
                "dataset": "fund_holdings",
                "identifier": "ATLASDEMO",
                "accession": FILING.accession,
                "response": {"view": "compact", "fields": ["reported_value"]},
            }
        )
        ResearchResult.model_validate(compact)
    record = compact["records"][1]
    assert record["reported_value"] == "250"
    assert record["value_unit"] == "USD" and record["denomination_currency"] == "JPY"
    assert record["report_date"] == "2026-08-31"
    assert record["derivative_details_reported"] is False
    assert "evidence_id" in record["field_sources"]["reported_value"]
    assert compact["allocations"]["denominator"]["unit"] == "USD"
