"""Same-date government curves and exact, evidence-backed basis-point analytics."""

from collections.abc import Iterator
from datetime import date
from decimal import Context, Decimal, localcontext
from itertools import islice
from typing import Any

from atlas_terminal.economic_dates import window_metadata, within
from atlas_terminal.fixed_income_sources import (
    JGB_MATURITIES,
    JGB_URLS,
    JgbObservation,
    parse_jgb_csv,
)
from atlas_terminal.macro_catalog import CURVE_MARKETS, CURVE_SERIES, MACRO_SERIES
from atlas_terminal.normalize import number
from atlas_terminal.providers import Transport
from atlas_terminal.public_sources import SOURCE_CATALOG, TREASURY_BASE, TREASURY_REAL_BASE
from atlas_terminal.regional_bond_sources import (
    ECB_MATURITIES,
    EURO_SERIES,
    RBA_SERIES,
    RBA_URL,
    REGIONAL_CONTEXT,
    ecb_curve_url,
    parse_ecb_curve,
    parse_rba_curve,
)
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import result_page, treasury_rows
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.util import utcnow


def regional_curves(
    transport: Transport,
    curve: str,
    q: ResearchQuery,
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    results: dict[str, dict[str, dict[str, Any]]] = {}
    sources = []
    if curve == "rba":
        observations, document = parse_rba_curve(transport.get(RBA_URL, 21600))
        return observations, [document]
    first = int(q.start_date[:4]) if q.start_date else q.year or utcnow().year
    last = int(q.end_date[:4]) if q.end_date else first
    for year in range(first, last + 1):
        observations, document = parse_ecb_curve(
            transport.get(ecb_curve_url(curve, year), 21600),
            curve,
            year,
        )
        results.update(observations)
        sources.append({**document, "selected_year": year})
    return results, sources


def regional_attribution(curve: str) -> list[dict[str, Any]]:
    market = CURVE_MARKETS[curve]
    return [
        {
            "source_id": market["source_id"],
            "notice": "Source: Reserve Bank of Australia, F2 government bond yields."
            if curve == "rba"
            else "Source: European Central Bank, euro area government yield curves.",
            "terms_url": SOURCE_CATALOG[market["source_id"]]["terms"],
            "modifications": "Dates/tenors normalized; curve analytics are Atlas calculations.",
        }
    ]


def regional_yields(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    curve = "rba" if q.dataset == "australia_yields" else EURO_SERIES[q.series or ""]
    observations, sources = regional_curves(transport, curve, q)
    maturities = list(RBA_SERIES.values()) if curve == "rba" else list(ECB_MATURITIES.values())
    selected = [
        observed
        for observed in sorted(observations, reverse=True)
        if (q.year is None or int(observed[:4]) == q.year)
        and within(observed, q.start_date, q.end_date)
    ]

    def records() -> Iterator[dict[str, Any]]:
        for observed in selected:
            for maturity in maturities:
                yield observations[observed].get(maturity) or {
                    **REGIONAL_CONTEXT[curve],
                    "date": observed,
                    "maturity": maturity,
                    "value": None,
                    "unit": "percent_per_annum",
                    "frequency": "business_daily",
                    "status": "unavailable",
                    "reason": "No source observation for this tenor/date.",
                    "published_at": None,
                    "source": next(
                        (
                            source
                            for source in sources
                            if source.get("selected_year") == int(observed[:4])
                        ),
                        sources[0],
                    ),
                }

    total = len(selected) * len(maturities)
    result = result_page(
        list(islice(records(), q.limit)),
        sources,
        q.limit,
        window="RBA published F2 history"
        if curve == "rba"
        else "ECB selected calendar-year YC files",
        source_id=CURVE_MARKETS[curve]["source_id"],
        attributions=regional_attribution(curve),
        limitations=[
            "ECB fitted euro area spot curve; continuous zero-coupon rates."
            if curve != "rba"
            else "RBA interpolated 2/3/5/10-year nominal yields; indexed bonds excluded.",
            "No conversion across source conventions or currencies.",
            "One record is one tenor/date; no Atlas interpolation or holiday fill.",
            "Observation dates are not publication timestamps or historical information vintages.",
        ],
        **REGIONAL_CONTEXT[curve],
    )
    result["coverage"].update(
        window_metadata([{"date": observed} for observed in selected], q.start_date, q.end_date),
        observed_records=total,
        truncated=total > q.limit,
    )
    return result


def jgb_curves(
    transport: Transport,
) -> tuple[dict[str, JgbObservation], list[dict[str, Any]], int]:
    rows: dict[str, JgbObservation] = {}
    sources = []
    overlaps = 0
    for url in JGB_URLS:
        snapshot = transport.get(url, 21600)
        observations, document = parse_jgb_csv(snapshot)
        overlaps += len(rows.keys() & observations.keys())
        # Replace a complete dated row; never fill a current missing tenor from an older file.
        rows.update(observations)
        sources.append(document)
    return rows, sources, overlaps


def jgb_attribution() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "japan_mof",
            "notice": "Source: Ministry of Finance Japan, Interest Rate Information.",
            "terms_url": SOURCE_CATALOG["japan_mof"]["terms"],
            "modifications": (
                "Dates/tenors normalized by Atlas Terminal; yield values remain percent per annum. "
                "Spreads, butterflies and changes are Atlas calculations, not MOF publications."
            ),
        }
    ]


def jgb_yields(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    observations, sources, overlaps = jgb_curves(transport)
    selected = [
        row
        for observed, row in sorted(observations.items(), reverse=True)
        if (q.year is None or int(observed[:4]) == q.year)
        and within(observed, q.start_date, q.end_date)
    ]
    total = len(selected) * len(JGB_MATURITIES)
    records = list(
        islice((row.record(maturity) for row in selected for maturity in JGB_MATURITIES), q.limit)
    )
    result = result_page(
        records,
        sources,
        q.limit,
        window="MOF published historical and current-month CSV files",
        country="JP",
        currency="JPY",
        attributions=jgb_attribution(),
        limitations=[
            "MOF constant-maturity government yields, not individual bond prices, bids or offers.",
            "One record is one date/maturity; a limit can truncate a dated curve. No holiday fill.",
            "Current-month rows replace complete overlapping historical rows, including nulls.",
            "CSV evidence uses one-based logical row/column positions, including title and header.",
            "Percent units remain unchanged; FX conversion and publication time are not inferred.",
        ],
    )
    result["coverage"].update(
        window_metadata([{"date": row.observed} for row in selected], q.start_date, q.end_date),
        overlapping_dates=overlaps,
        observed_records=total,
        truncated=total > q.limit,
    )
    return result


def exact_basis_points(inputs: list[dict[str, Any]]) -> str | None:
    if any(row["value"] is None for row in inputs):
        return None
    values = [Decimal(row["value"]) for row in inputs]
    precision = max(
        28,
        max(value.adjusted() for value in values)
        - min(int(value.as_tuple().exponent) for value in values)
        + 8,
    )
    with localcontext(Context(prec=precision)):
        return number(
            sum(
                (value * row["coefficient"] for value, row in zip(values, inputs, strict=True)),
                Decimal(0),
            )
            * 100
        )


def curve_analytics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in CURVE_SERIES
    spec = CURVE_SERIES[q.series]
    macro = MACRO_SERIES[q.series]
    japanese = macro["source_id"] == "japan_mof"
    curve_name = spec["terms"][0]["curve"]
    regional = curve_name in REGIONAL_CONTEXT
    curves: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    documents: dict[tuple[str, int], dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    if japanese:
        observations, sources, _ = jgb_curves(transport)
        available_dates = sorted(observations)
        needed_dates = set()
        for index, observed in enumerate(available_dates):
            if (q.year is None or int(observed[:4]) == q.year) and within(
                observed, q.start_date, q.end_date
            ):
                needed_dates.add(observed)
                if spec["previous_observation"] and index:
                    needed_dates.add(available_dates[index - 1])
        maturities = {term["maturity"] for term in spec["terms"]}
        curves["jgb"] = {
            observed: {maturity: row.record(maturity) for maturity in maturities}
            for observed, row in observations.items()
            if observed in needed_dates
        }
    elif regional:
        regional_observations, sources = regional_curves(transport, curve_name, q)
        curves[curve_name] = regional_observations
    else:
        first = int(q.start_date[:4]) if q.start_date else q.year or utcnow().year
        last = int(q.end_date[:4]) if q.end_date else first
        for curve in dict.fromkeys(term["curve"] for term in spec["terms"]):
            curves[curve] = {}
            for year in range(first, last + 1):
                snapshot = transport.get(
                    (TREASURY_REAL_BASE if curve == "real" else TREASURY_BASE) + str(year),
                    21600,
                )
                document = reference(snapshot, "/feed", xml=True)
                sources.append(document)
                documents[curve, year] = document
                for row in treasury_rows(snapshot, year, real=curve == "real"):
                    if row["maturity"] in {term["maturity"] for term in spec["terms"]}:
                        curves[curve].setdefault(row["date"], {})[row["maturity"]] = row

    dates = sorted({observed for curve in curves.values() for observed in curve})
    records = []
    for index, observed in enumerate(dates):
        if (q.year is not None and int(observed[:4]) != q.year) or not within(
            observed, q.start_date, q.end_date
        ):
            continue
        previous = dates[index - 1] if index else None
        inputs = []
        for term in spec["terms"]:
            input_date = previous if term["role"] == "previous" else observed
            source_row = curves[term["curve"]].get(input_date or "", {}).get(term["maturity"])
            if source_row is None:
                source_row = {
                    "date": input_date,
                    "maturity": term["maturity"],
                    "value": None,
                    "unit": "percent_per_annum",
                    "status": "unavailable",
                    "reason": "No reported input at the required observation date.",
                    "source": documents.get((term["curve"], int(observed[:4]))),
                }
            inputs.append({**source_row, **term})
        value = exact_basis_points(inputs)
        record: dict[str, Any] = {
            "series": q.series,
            "date": observed,
            "value": value,
            "unit": "basis_points",
            "country": macro["country"],
            "currency": CURVE_MARKETS[curve_name]["currency"],
            "derived": True,
            "status": "derived" if value is not None else "unavailable",
            "reason": "Missing dated yield; no date mixing, null skipping or interpolation."
            if value is None
            else None,
            "formula": macro["formula"],
            "inputs": inputs,
            "interpretation": spec["interpretation"],
            "published_at": None,
            "frequency": "business_daily",
            "decimal_precision": None,
            **(REGIONAL_CONTEXT[curve_name] if regional else {}),
        }
        if spec["previous_observation"]:
            record["comparison_date"] = previous
            record["elapsed_calendar_days"] = (
                (date.fromisoformat(observed) - date.fromisoformat(previous)).days
                if previous
                else None
            )
        records.append(record)
    records.reverse()
    result = result_page(
        records,
        sources,
        q.limit,
        window="MOF historical and current-month files"
        if japanese
        else "RBA published F2 history"
        if curve_name == "rba"
        else "ECB selected calendar-year YC files"
        if regional
        else f"Treasury selected calendar years {first}–{last}",
        source_id=macro["source_id"],
        attributions=jgb_attribution()
        if japanese
        else regional_attribution(curve_name)
        if regional
        else [],
        limitations=[
            spec["interpretation"],
            "Source yields are percent per annum; 100 basis points is one percentage point.",
            "Same-date terms must all be reported; absent dates and maturities remain unavailable.",
            "No security prices, total returns, duration, convexity or executable trade quotes.",
            "Observation dates and retrieval times do not establish publication-time vintages.",
            "US changes use the selected years; a baseline before those years is unavailable.",
        ],
    )
    result["coverage"].update(window_metadata(records, q.start_date, q.end_date))
    return result
