"""Annual registered-country WDI observations with checked identities and original source cells."""

import re
from datetime import date
from decimal import Decimal
from typing import Any

from atlas_terminal.economic_dates import within, years
from atlas_terminal.global_macro_sources import (
    GLOBAL_AREAS,
    GLOBAL_INDICATORS,
    wdi_country_url,
    wdi_data_url,
    wdi_metadata_url,
)
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_economics import economic_page
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery


def metadata_row(snapshot: Snapshot) -> dict[str, Any]:
    raw = snapshot.data
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or not isinstance(raw[0], dict)
        or type(raw[0].get("page")) is not int
        or raw[0]["page"] != 1
        or type(raw[0].get("pages")) is not int
        or raw[0]["pages"] != 1
        or type(raw[0].get("total")) is not int
        or raw[0]["total"] != 1
        or not isinstance(raw[1], list)
        or len(raw[1]) != 1
        or not isinstance(raw[1][0], dict)
    ):
        raise source_error("Expected exactly one complete World Bank metadata record.")
    return raw[1][0]


def global_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.country in GLOBAL_AREAS and q.series in GLOBAL_INDICATORS
    area, spec = GLOBAL_AREAS[q.country], GLOBAL_INDICATORS[q.series]
    first, last = years(q.start_date, q.end_date)
    if q.start_date is None:
        first, last = date.today().year - 3, date.today().year - 1
    start, end = q.start_date or f"{first}-01-01", q.end_date or f"{last}-12-31"
    country_snapshot = transport.get(wdi_country_url(q.country), 86400)
    country = metadata_row(country_snapshot)
    if (
        country.get("id") != area["iso3"]
        or country.get("iso2Code") != q.country
        or not isinstance(country.get("name"), str)
        or not country["name"].strip()
        or not isinstance(country.get("region"), dict)
        or not isinstance(country["region"].get("id"), str)
        or not country["region"]["id"]
        or (country["region"]["id"] == "NA") != (area["kind"] == "regional_aggregate")
    ):
        raise source_error("World Bank country identity or aggregate classification changed.")
    meta = transport.get(wdi_metadata_url(q.series), 86400)
    detail = metadata_row(meta)
    if (
        detail.get("id") != spec["code"]
        or not isinstance(detail.get("source"), dict)
        or detail["source"].get("id") != "2"
        or detail.get("name") != spec["title"]
        or detail.get("unit") != ""
        or any(
            not isinstance(detail.get(k), str) or not detail[k].strip()
            for k in ("sourceNote", "sourceOrganization")
        )
    ):
        raise source_error("WDI indicator identity, unit/base or producer metadata changed.")
    snapshot = transport.get(wdi_data_url(q.country, q.series, first, last), 21600)
    raw = snapshot.data
    if not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], dict):
        raise source_error("Malformed World Bank observation response.")
    page, rows = raw
    if rows is None and page.get("total") == 0:
        rows = []
    if (
        not isinstance(rows, list)
        or len(rows) > 30
        or type(page.get("page")) is not int
        or page["page"] != 1
        or type(page.get("pages")) is not int
        or page["pages"] != (1 if rows else 0)
        or type(page.get("total")) is not int
        or page["total"] != len(rows)
        or page.get("sourceid") != "2"
        or not isinstance(page.get("lastupdated"), str)
    ):
        raise source_error("WDI observations are incomplete, paginated or from another source.")
    try:
        date.fromisoformat(page["lastupdated"])
    except ValueError:
        raise source_error("Invalid WDI dataset update date.") from None
    records, seen = [], set()
    for i, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or row.get("countryiso3code") != area["iso3"]
            or not isinstance(row.get("country"), dict)
            or row["country"].get("id") != q.country
            or not isinstance(row.get("indicator"), dict)
            or row["indicator"].get("id") != spec["code"]
            or row.get("unit") != detail["unit"]
            or not isinstance(row.get("date"), str)
            or not re.fullmatch(r"[0-9]{4}", row["date"])
            or not 1900 <= int(row["date"]) <= 2100
            or type(row.get("decimal")) is not int
            or not 0 <= row["decimal"] <= 40
            or not isinstance(row.get("obs_status"), str)
            or len(row["obs_status"]) > 40
            or "value" not in row
        ):
            raise source_error("WDI observation identity, annual label, unit or status is invalid.")
        year = row["date"]
        if year in seen:
            raise source_error("Duplicate WDI annual observation.")
        seen.add(year)
        if not first <= int(year) <= last and not transport.synthetic:
            raise source_error("WDI returned an observation outside the requested years.")
        observed = year + "-12-31"
        if not within(observed, start, end):
            continue
        source_value = numeric(row["value"]) if isinstance(row["value"], Decimal) else row["value"]
        value = numeric(row["value"])
        reason = None
        if row["obs_status"]:
            value, reason = None, "Source status flag makes this observation unavailable."
        elif int(year) > date.today().year:
            value, reason = None, "Future reference-year values are unavailable for historical use."
        elif value is None:
            reason = "The publisher reports no numeric observation."
        records.append(
            {
                "series": q.series,
                "series_id": spec["code"],
                "series_title": spec["title"],
                "country": q.country,
                "country_iso3": area["iso3"],
                "country_name": country["name"],
                "reference_area_kind": area["kind"],
                "date": observed,
                "date_basis": "source_year_label_mapped_to_december_31",
                "source_period": year,
                "reference_period_kind": "publisher_reference_year",
                "frequency": "annual",
                "unit": spec["unit"],
                "unit_multiplier": 0,
                "unit_basis": "curated_WDI_indicator_definition",
                "definition": spec["definition"],
                "seasonally_adjusted": False,
                "annualized": False,
                "value": value,
                "status": "reported" if value is not None else "unavailable",
                "reason": reason,
                "source_value": source_value,
                "source_unit": row["unit"],
                "source_display_decimals": row["decimal"],
                "observation_status": row["obs_status"],
                "source_last_updated": page["lastupdated"],
                "source_note": detail["sourceNote"],
                "original_producers": detail["sourceOrganization"],
                "published_at": None,
                "source": reference(snapshot, f"/1/{i}/value"),
                "metadata_source": reference(meta, "/1/0"),
                "country_metadata_source": reference(country_snapshot, "/1/0"),
            }
        )
    result = economic_page(
        q,
        records,
        [reference(country_snapshot, ""), reference(meta, ""), reference(snapshot, "")],
        source_id="world_bank",
        title=country["name"] + ": " + spec["title"],
        window=f"Annual WDI reference-year labels, {start} through {end}",
        limitations=[
            "Annual current/revised WDI data may contain publisher estimates and modeled series; "
            "it is not a domestic monthly/quarterly release or a forecast database.",
            "December 31 is a selection label for the source year, not a verified actual fiscal "
            "period end, publication date or population census date.",
            "Producer definitions, fiscal-year conventions, PPP bases and aggregate membership "
            "can differ. No implicit cross-country harmonization or currency conversion.",
            "Annual LCU exchange-rate units follow the publisher's denomination conventions; "
            "a current ISO currency is not assigned to historical observations.",
            "All nonempty observation status flags and future reference years stay unavailable; "
            "source cells are retained. Missing years are not filled or replaced.",
            "Coverage includes nineteen G20 countries, four additional economies and the EU. "
            "The African "
            "Union is also a G20 member but has no selected WDI aggregate; Africa and "
            "Sub-Saharan Africa are not substitutes. EU and member countries overlap.",
            "Dataset lastupdated is not an observation publication timestamp. Historical requests "
            "select current revised values, not release-time vintages or historical membership.",
        ],
    )
    expected = {str(y) for y in range(first, last + 1) if within(f"{y}-12-31", start, end)}
    result["coverage"].update(
        requested_country=q.country,
        country_iso3=area["iso3"],
        reference_area_kind=area["kind"],
        missing_years=sorted(expected - seen),
        source_rows=len(rows),
        observation_years=len(records),
    )
    from atlas_terminal.public_sources import SOURCE_CATALOG

    terms = SOURCE_CATALOG["world_bank"]
    result["attributions"] = [
        {
            "source_id": "world_bank",
            "notice": terms["use"],
            "terms_url": terms["terms"],
            "modifications": "Atlas normalizes fields, decimal strings and annual date labels; "
            "original definitions, producer notes, flags and source cells are retained.",
        }
    ]
    return result
