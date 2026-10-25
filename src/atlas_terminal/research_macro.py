"""Dated public reference observations and exact, source-backed macro calculations."""

import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Any

from atlas_terminal.economic_dates import window_metadata, within
from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import CURVE_SERIES, MACRO_SERIES, REFERENCE_SERIES
from atlas_terminal.normalize import number
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.public_sources import (
    ECB_FX_URL,
    NYFED_RATES,
    SOURCE_CATALOG,
    TREASURY_REAL_BASE,
    local_name,
    xml_root,
)
from atlas_terminal.research_analytics import exact_difference
from atlas_terminal.research_financials import reference
from atlas_terminal.research_models import ResearchQuery

ECB_NS = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"
GESMES_NS = "http://www.gesmes.org/xml/2002-08-01"


def source_error(message: str) -> AtlasError:
    return AtlasError("SOURCE_UNAVAILABLE", message, status=502)


def numeric(value: Any, *, positive: bool = False) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise source_error("Malformed numeric reference observation.")
    text = str(value)
    if len(text) > 100:
        raise source_error("Numeric reference observation exceeds bounds.")
    try:
        result = Decimal(text)
        parts = result.as_tuple()
        if (
            not result.is_finite()
            or not isinstance(parts.exponent, int)
            or not -40 <= parts.exponent <= 40
            or len(parts.digits) > 80
            or (positive and result <= 0)
        ):
            raise ValueError("Invalid numeric observation")
    except (ValueError, InvalidOperation):
        raise source_error("Invalid numeric reference observation.") from None
    return format(result, "f")


def observation_date(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise source_error("Reference observation has no valid date.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise source_error("Reference observation has no valid date.") from None


def result_page(
    records: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    limit: int,
    *,
    limitations: list[str],
    window: str,
    **metadata: Any,
) -> dict[str, Any]:
    if not records:
        raise AtlasError("INCOMPLETE_COVERAGE", "No eligible observations in the source window.")
    return {
        **metadata,
        "records": records[:limit],
        "sources": sources,
        "coverage": {
            "observed_records": len(records),
            "returned_records": min(limit, len(records)),
            "truncated": len(records) > limit,
            "upstream_window_limited": True,
            "upstream_window": window,
            "unavailable_records": sum(row.get("value") is None for row in records[:limit]),
        },
        "limitations": limitations,
    }


def fx_rates(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    snapshot = transport.get(ECB_FX_URL, 21600)
    root = xml_root(snapshot.body)
    if root.tag != f"{{{GESMES_NS}}}Envelope":
        raise source_error("Expected an ECB reference-rate envelope.")
    if len(root.findall(f"{{{ECB_NS}}}Cube/{{{ECB_NS}}}Cube")) > 12000:
        raise source_error("ECB reference window exceeds the selected feed bounds.")
    records: list[dict[str, Any]] = []
    dates: set[str] = set()
    for index, day in enumerate(root.findall(f"{{{ECB_NS}}}Cube/{{{ECB_NS}}}Cube"), 1):
        observed = observation_date(day.get("time"))
        if observed in dates:
            raise source_error("Duplicate ECB reference observation date.")
        dates.add(observed)
        if not within(observed, q.start_date, q.end_date):
            continue
        rates: dict[str, dict[str, Any]] = {}
        day_path = f"/Envelope/Cube/Cube[{index}]"
        for position, entry in enumerate(day.findall(f"{{{ECB_NS}}}Cube"), 1):
            currency = entry.get("currency", "")
            if not re.fullmatch(r"[A-Z]{3}", currency) or currency in rates:
                raise source_error("Invalid or duplicate ECB currency.")
            value = numeric(entry.get("rate"), positive=True)
            rates[currency] = {
                "base_currency": "EUR",
                "quote_currency": currency,
                "date": observed,
                "value": value,
                "unit": f"{currency}_per_EUR",
                "status": "reported" if value is not None else "unavailable",
                "rate_type": "reference",
                "published_at": None,
                "source": reference(
                    snapshot,
                    f"{day_path}/Cube[{position}]" + ("/@rate" if "rate" in entry.attrib else ""),
                    xml=True,
                ),
            }

        base, quote = q.base_currency, q.quote_currency
        assert base is not None and quote is not None
        inputs = []
        for currency, role in ((base, "base_per_EUR"), (quote, "quote_per_EUR")):
            if currency == "EUR":
                continue
            record = rates.get(currency) or {
                "base_currency": "EUR",
                "quote_currency": currency,
                "date": observed,
                "value": None,
                "unit": f"{currency}_per_EUR",
                "status": "unavailable",
                "reason": "Currency absent from this ECB date.",
                "rate_type": "reference",
                "published_at": None,
                "source": reference(snapshot, day_path, xml=True),
            }
            inputs.append({**record, "role": role})
        derived = base != "EUR"
        value = None
        if all(row["value"] is not None for row in inputs):
            if not derived:
                value = inputs[0]["value"]
            else:
                numerator = Decimal(1) if quote == "EUR" else Decimal(inputs[1]["value"])
                with localcontext(Context(prec=28)):
                    value = number(numerator / Decimal(inputs[0]["value"]))
        formula = (
            ("1 / base_per_EUR" if quote == "EUR" else "quote_per_EUR / base_per_EUR")
            if derived
            else None
        )
        records.append(
            {
                "base_currency": base,
                "quote_currency": quote,
                "date": observed,
                "value": value,
                "unit": f"{quote}_per_{base}",
                "quote_convention": "quote_currency_per_one_base_currency",
                "rate_type": "reference",
                "frequency": "business_daily",
                "published_at": None,
                "derived": derived,
                "status": ("derived" if derived else "reported")
                if value is not None
                else "unavailable",
                "reason": "Missing same-date reference input; no fill or date mixing."
                if value is None
                else None,
                "formula": formula,
                "decimal_precision": 28 if derived else None,
                "rounding": "ROUND_HALF_EVEN" if derived else None,
                "inputs": inputs,
                "source": inputs[0]["source"] if not derived else None,
            }
        )
    records.sort(key=lambda row: row["date"], reverse=True)
    result = result_page(
        records,
        [reference(snapshot, "/Envelope/Cube", xml=True)],
        q.limit,
        window="ECB full published reference history, filtered by inclusive observation dates",
        limitations=[
            "ECB information-purpose daily reference fixing, "
            "not real-time, bid/ask or executable FX quotes.",
            (
                "Coverage starts and ends separately for each currency; absent "
                "currencies/dates are not filled."
            ),
            "Cross and inverse rates are Atlas calculations using inputs from the same ECB date.",
        ],
        attributions=[
            {
                "source_id": "ecb",
                "notice": "Source: European Central Bank.",
                "terms_url": SOURCE_CATALOG["ecb"]["terms"],
                "modifications": (
                    "Field normalization by Atlas Terminal; inverse/cross "
                    "rates are explicitly derived, not ECB-published cross quotes."
                ),
            }
        ],
    )
    result["coverage"].update(window_metadata(records, q.start_date, q.end_date))
    return result


def reference_rates(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in REFERENCE_SERIES
    spec = REFERENCE_SERIES[q.series]
    snapshot = transport.get(NYFED_RATES[q.series], 3600)
    raw = snapshot.data
    if not isinstance(raw, dict) or not isinstance(raw.get("refRates"), list):
        raise source_error("Expected New York Fed reference-rate records.")
    if len(raw["refRates"]) > 90:
        raise source_error("Reference-rate window exceeds the requested bounds.")
    rows: list[dict[str, Any]] = []
    dates: set[str] = set()
    for index, row in enumerate(raw["refRates"]):
        if not isinstance(row, dict) or "percentRate" not in row:
            raise source_error("Malformed reference-rate record.")
        declared = [row[key] for key in ("type", "rateType") if key in row]
        if not declared or any(value != spec["type"] for value in declared):
            raise source_error("Reference-rate series does not match the request.")
        observed = observation_date(row.get("effectiveDate"))
        if observed in dates:
            raise source_error("Duplicate reference-rate effective date.")
        dates.add(observed)
        value = numeric(row["percentRate"])
        revision = row.get("revisionIndicator")
        if revision is not None and not isinstance(revision, str):
            raise source_error("Invalid reference-rate revision indicator.")
        rows.append(
            {
                "series": q.series,
                "source_series": spec["type"],
                "date": observed,
                "value": value,
                "unit": "percent_per_annum",
                "rate_type": spec["rate_type"],
                "status": "reported" if value is not None else "unavailable",
                "frequency": "business_daily",
                "published_at": None,
                "revision_indicator": revision,
                "source": reference(snapshot, f"/refRates/{index}/percentRate"),
            }
        )
    rows.sort(key=lambda row: row["date"], reverse=True)
    year = datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00")).year
    name = spec["type"]
    return result_page(
        rows,
        [reference(snapshot, "")],
        q.limit,
        window="New York Fed last 90 observations",
        limitations=[
            "Effective date is the observation date, not a publication timestamp.",
            "Rates can be revised; current response is not a release-time vintage.",
            "New York Fed overnight SOFR is distinct from forward-looking Term SOFR.",
        ],
        attributions=[
            {
                "source_id": "new_york_fed",
                "terms_url": SOURCE_CATALOG["new_york_fed"]["terms"],
                "copyright": (
                    f"© {year} Federal Reserve Bank of New York. Content from "
                    "the New York Fed subject to the Terms of Use at newyorkfed.org."
                ),
                "notice": (
                    f"The {name} data is subject to the Terms of Use posted at "
                    "newyorkfed.org. The New York Fed is not responsible for "
                    f"publication of the {name} data by Atlas Terminal, does not "
                    "sanction or endorse any particular republication, "
                    "and has no liability for your use."
                ),
                "disclaimer": (
                    "Atlas Terminal is not affiliated with the New York Fed. "
                    "The New York Fed does not sanction, endorse, or recommend "
                    "any products or services offered by Atlas Terminal."
                ),
                "modifications": (
                    "Field names and numeric representation normalized; "
                    "reference-rate values are unchanged."
                ),
            }
        ],
    )


def treasury_rows(snapshot: Snapshot, year: int, *, real: bool) -> list[dict[str, Any]]:
    root = xml_root(snapshot.body)
    if local_name(root) != "feed":
        raise source_error("Expected a Treasury yield feed.")
    prefix = "TC_" if real else "BC_"
    maturities = (
        ("5YEAR", "7YEAR", "10YEAR", "20YEAR", "30YEAR")
        if real
        else (
            "1MONTH",
            "1_5MONTH",
            "2MONTH",
            "3MONTH",
            "4MONTH",
            "6MONTH",
            "1YEAR",
            "2YEAR",
            "3YEAR",
            "5YEAR",
            "7YEAR",
            "10YEAR",
            "20YEAR",
            "30YEAR",
        )
    )
    records, dates, found = [], set(), False
    for index, entry in enumerate((n for n in root if local_name(n) == "entry"), 1):
        contents = [n for n in entry if local_name(n) == "content"]
        properties = [n for content in contents for n in content if local_name(n) == "properties"]
        if len(properties) != 1:
            raise source_error("Malformed Treasury yield properties.")
        props = {local_name(n): n for n in properties[0]}
        if len(props) != len(properties[0]):
            raise source_error("Duplicate Treasury yield field.")
        raw_date = props.get("NEW_DATE")
        observed = observation_date((raw_date.text or "")[:10] if raw_date is not None else None)
        if int(observed[:4]) != year or observed in dates:
            raise source_error("Treasury year mismatch or duplicate observation date.")
        dates.add(observed)
        for maturity in maturities:
            key = prefix + maturity
            node = props.get(key)
            path = f"/feed/entry[{index}]/content/properties"
            value = None
            if node is not None:
                found = True
                is_null = (
                    node.get("{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}null")
                    == "true"
                )
                if is_null and node.text and node.text.strip():
                    raise source_error("Conflicting Treasury null marker.")
                value = None if is_null else numeric(node.text)
                path += "/" + key
            records.append(
                {
                    "date": observed,
                    "maturity": maturity,
                    "value": value,
                    "unit": "percent_per_annum",
                    "rate_type": "real_par" if real else "nominal_par",
                    "status": "reported" if value is not None else "unavailable",
                    "reason": "Maturity absent or null in this dated Treasury row."
                    if value is None
                    else None,
                    "published_at": None,
                    "frequency": "business_daily",
                    "source": reference(snapshot, path, xml=True),
                }
            )
    if dates and not found:
        raise source_error("Treasury yield type does not match the requested curve.")
    return sorted(records, key=lambda row: (row["date"], row["maturity"]), reverse=True)


def real_yields(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    year = q.year or date.today().year
    snapshot = transport.get(TREASURY_REAL_BASE + str(year), 21600)
    return result_page(
        treasury_rows(snapshot, year, real=True),
        [reference(snapshot, "/", xml=True)],
        q.limit,
        window=f"Treasury selected calendar year {year}",
        limitations=[
            "Treasury TIPS-based real par yields, "
            "not inflation-adjusted prices or yields of specific securities.",
            "Supported maturities are 5, 7, 10, 20 and 30 years; "
            "missing fields remain unavailable.",
            "A record is one date/maturity; the limit may truncate a day's curve. "
            "No holiday filling.",
        ],
    )


def monthly_analytics(
    q: ResearchQuery,
    data: dict[str, Any],
) -> dict[str, Any]:
    assert q.series in MACRO_SERIES
    spec = MACRO_SERIES[q.series]
    months: dict[int, dict[str, Any]] = {}
    for row in data["records"]:
        if row["period_kind"] != "month":
            continue
        key = int(row["year"]) * 12 + int(row["period"][1:]) - 1
        if key in months:
            raise source_error("Duplicate monthly input; comparison is ambiguous.")
        months[key] = row
    records = []
    for key, current in sorted(months.items(), reverse=True):
        if q.year is not None and current["year"] != str(q.year):
            continue
        if not within(current["date"], q.start_date, q.end_date):
            continue
        prior = months.get(key - spec["lag_months"])
        role = "prior_year" if spec["lag_months"] == 12 else "previous_month"
        inputs = [{**current, "role": "current"}]
        if prior:
            inputs.append({**prior, "role": role})
        value, reason = None, None
        if current["value"] is None or prior is None or prior["value"] is None:
            reason = "Missing reported input at the exact required calendar-month lag."
        elif (
            current["unit"] != prior["unit"]
            or current["seasonally_adjusted"] != prior["seasonally_adjusted"]
        ):
            reason = "Input units or seasonal adjustment differ."
        elif q.series == "payrolls_change_mom":
            if min(Decimal(current["value"]), Decimal(prior["value"])) < 0:
                reason = "A reported employment level is negative."
            else:
                value = number(exact_difference(current["value"], prior["value"]))
        elif min(Decimal(current["value"]), Decimal(prior["value"])) <= 0:
            reason = "CPI change requires positive reported index levels."
        else:
            difference = exact_difference(current["value"], prior["value"])
            with localcontext(Context(prec=28)):
                value = number(difference / Decimal(prior["value"]))
        expected_year, expected_month = divmod(key - spec["lag_months"], 12)
        records.append(
            {
                "series": q.series,
                "year": current["year"],
                "date": current["date"],
                "period": current["period"],
                "period_kind": "month",
                "comparison_period": {
                    "year": str(expected_year),
                    "period": f"M{expected_month + 1:02d}",
                    "period_kind": "month",
                },
                "value": value,
                "unit": spec["unit"],
                "derived": True,
                "status": "derived" if value is not None else "unavailable",
                "reason": reason,
                "formula": spec["formula"],
                "inputs": inputs,
                "seasonally_adjusted": current["seasonally_adjusted"],
                "frequency": "monthly",
                "decimal_precision": 28 if spec["lag_months"] == 12 else None,
                "rounding": "ROUND_HALF_EVEN" if spec["lag_months"] == 12 else None,
                "published_at": None,
            }
        )
    result = result_page(
        records,
        data["sources"],
        q.limit,
        window=data["coverage"]["upstream_window"],
        source_id="bls",
        limitations=[
            "Derived from current/revised BLS snapshots, not release-time vintages or PIT data.",
            "M13 annual averages are excluded; comparisons require exact calendar-month lags.",
            "CPI YoY is a ratio (0.03 means 3%); "
            "payroll change is in thousand persons, not a percentage.",
            "Missing baselines are unavailable; "
            "no interpolation, annualization or seasonal conversion.",
        ],
    )
    result["coverage"].update(window_metadata(records, q.start_date, q.end_date))
    return result


def macro_analytics(
    transport: Transport,
    q: ResearchQuery,
    economics: Callable[[ResearchQuery], dict[str, Any]],
) -> dict[str, Any]:
    assert q.series in MACRO_SERIES
    if q.series in CURVE_SERIES:
        from atlas_terminal.research_fixed_income import curve_analytics

        return curve_analytics(transport, q)
    else:
        dates = {}
        if q.start_date and q.end_date:
            start = date.fromisoformat(q.start_date)
            # Fetch the full prior calendar year: the first requested monthly value
            # needs its exact lag, including when the selected interval starts midyear.
            dates = {"start_date": f"{max(1900, start.year - 1)}-01-01", "end_date": q.end_date}
        elif q.year is not None:
            dates = {"start_date": f"{q.year - 1}-01-01", "end_date": f"{q.year}-12-31"}
        underlying = ResearchQuery(
            dataset="economics",
            series=MACRO_SERIES[q.series]["source_series"],
            limit=500,
        )
        # Internally include one additional baseline year beyond the public 30-year bound.
        # Each source request is still validated and bounded to ten years.
        underlying = underlying.model_copy(update=dates)
        return monthly_analytics(q, economics(underlying))
