"""Named country CPI/rate observations with source definitions checked before selection."""

from datetime import date
from typing import Any

from atlas_terminal.country_macro_catalog import selection
from atlas_terminal.errors import AtlasError
from atlas_terminal.global_macro_sources import GLOBAL_AREAS
from atlas_terminal.providers import Transport
from atlas_terminal.research_macro import source_error
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_sdmx import international_economics, structure


def country_macro(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.country is not None and q.series is not None
    selected = selection(q.country, q.series)
    if selected is None:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "No registered source for this country/series. "
            "Inspect country_series; no alternate rate, maturity or country is substituted.",
        )
    spec = structure(transport, selected["source_id"], selected["dataflow"])
    dimensions = {part["id"]: part for part in spec.dimensions}
    if list(dimensions) != list(selected["dimensions"]):
        raise source_error("Country-series dimension identity or order changed.")
    labels = {
        (key, code): dimensions[key]["codes"].get(code, {}).get("label")
        for key, code in selected["dimensions"].items()
    }
    cpi = q.series.startswith("cpi_")
    oecd = selected["source_id"] == "oecd"
    checks = (
        {
            "REF_AREA": selected["source_country_label"],
            "FREQ": "Monthly",
            "MEASURE": selected["source_series_label"],
            "UNIT_MEASURE": "Percent per annum",
            "METHODOLOGY": "National",
            "ACTIVITY": "Not applicable",
            "ADJUSTMENT": "Not applicable",
            "TRANSFORMATION": "Not applicable",
            "TIME_HORIZ": "Not applicable",
        }
        if oecd
        else {
            "COUNTRY": selected["source_country_label"],
            "INDEX_TYPE": "Consumer price index (CPI)",
            "COICOP_1999": "All Items",
            "TYPE_OF_TRANSFORMATION": selected["source_series_label"],
            "FREQUENCY": "Quarterly" if selected["frequency"] == "quarterly" else "Monthly",
        }
        if cpi
        else {
            "COUNTRY": selected["source_country_label"],
            "INDICATOR": selected["source_series_label"],
            "FREQUENCY": "Monthly",
        }
    )
    if any(labels[(key, selected["dimensions"][key])] != label for key, label in checks.items()):
        raise source_error("Country-series source label, unit or definition changed.")
    today = date.today()
    internal = ResearchQuery(
        dataset="international_economics",
        source_id=selected["source_id"],
        dataflow=spec.flow,
        dimensions=selected["dimensions"],
        start_date=q.start_date or f"{today.year - 3}-01-01",
        end_date=q.end_date or today.isoformat(),
        limit=q.limit,
    )
    result = international_economics(transport, internal, resolved_structure=spec)
    derivations: dict[str, Any] = next(
        (part["codes"] for part in spec.attributes if part["id"] == "DERIVATION_TYPE"), {}
    )
    for row in result["records"]:
        attributes = row["source_attributes"]
        if attributes.get("UNIT_MULT" if oecd else "SCALE") != "0":
            raise source_error("Country-series source scale is absent or differs from units.")
        expected_unit = (
            "PA" if oecd else "IX" if q.series == "cpi_index" else "PT" if cpi else "PT_A"
        )
        supplied_units = [
            row["unit"],
            *(attributes.get(k) for k in ("UNIT", "UNIT_MEASURE", "UNIT_OF_MEASURE")),
        ]
        if any(unit not in {None, expected_unit} for unit in supplied_units) or attributes.get(
            "UNIT_MULT"
        ) not in {None, "0"}:
            raise source_error("Country-series source unit attributes contradict its definition.")
        row.update(
            country=q.country,
            country_iso3=GLOBAL_AREAS[q.country]["iso3"],
            country_name=GLOBAL_AREAS[q.country]["name"],
            series=q.series,
            source_series=selected["source_series"],
            source_unit_label=selected["source_series_label"],
            unit=selected["unit"],
            unit_multiplier=0,
            unit_basis="checked_source_indicator_definition",
            base_period=attributes.get("REFERENCE_PERIOD") if cpi else row.get("base_period"),
            common_reference_period=attributes.get("COMMON_REFERENCE_PERIOD"),
            benchmark_tenor_years=selected["benchmark_tenor_years"],
            rate_period_basis=selected["rate_period_basis"],
            source_maturity=attributes.get("FI_MATURITY"),
            source_currency=attributes.get("CURRENCY"),
            source_unit_code=row["unit"],
            source_derivation_type=attributes.get("DERIVATION_TYPE"),
            source_derivation_label=derivations.get(attributes.get("DERIVATION_TYPE"), {}).get(
                "label"
            ),
            benchmark_tenor_basis="OECD_indicator_definition_not_instrument_maturity"
            if oecd
            else None,
        )
        projection = (
            attributes.get("DERIVATION_TYPE") == "SP" or attributes.get("OBS_STATUS") == "F"
        )
        if projection or row["date"] > today.isoformat():
            row.update(
                source_value=row["value"],
                value=None,
                status="unavailable",
                reason="Forecast/projection values and future periods are unavailable "
                "for historical use.",
            )
    result["coverage"]["unavailable_records"] = sum(
        row["value"] is None for row in result["records"]
    )
    result["limitations"].extend(
        [
            "National definitions and reporting lags differ; no source, maturity or "
            "frequency fallback.",
            "IMF monetary-policy-related rates are not verified current policy targets. "
            "Singapore's exchange-rate policy is not replaced with an interest-rate target.",
            "IMF government/bill rates do not establish a uniform maturity or monthly fixing. "
            "OECD IRLT follows its approximately ten-year government-bond/"
            "monthly-average definition. "
            "These are statistical rates, not individual-bond prices, tradable quotes "
            "or full curves.",
            "Original CPI bases and New Zealand quarterly frequency remain intact. "
            "No rebasing, interpolation, forecast replacement or publisher PIT is inferred.",
        ]
    )
    return result
