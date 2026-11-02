"""Named IMF monthly reference prices with metadata-checked physical quote units."""

from datetime import date
from typing import Any

from atlas_terminal.commodity_catalog import COMMODITIES, PCPS_FLOW
from atlas_terminal.providers import Transport
from atlas_terminal.research_macro import source_error
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_sdmx import international_economics, structure


def commodity_prices(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series in COMMODITIES
    code, label, unit = COMMODITIES[q.series]
    spec = structure(transport, "imf", PCPS_FLOW)
    dimensions = {d["id"]: d for d in spec.dimensions}
    if (
        set(dimensions) != {"COUNTRY", "INDICATOR", "DATA_TRANSFORMATION", "FREQUENCY"}
        or dimensions["INDICATOR"]["codes"].get(code, {}).get("label") != label
        or dimensions["COUNTRY"]["codes"].get("G001", {}).get("label") != "World"
        or dimensions["DATA_TRANSFORMATION"]["codes"].get("USD", {}).get("label") != "US dollars"
        or dimensions["FREQUENCY"]["codes"].get("M", {}).get("label") != "Monthly"
    ):
        raise source_error("IMF commodity identity, unit definition or dimensions changed.")
    today = date.today()
    selected = ResearchQuery(
        dataset="international_economics",
        source_id="imf",
        dataflow=spec.flow,
        dimensions={
            "COUNTRY": "G001",
            "INDICATOR": code,
            "DATA_TRANSFORMATION": "USD",
            "FREQUENCY": "M",
        },
        start_date=q.start_date or f"{today.year - 3}-01-01",
        end_date=q.end_date or today.isoformat(),
        limit=q.limit,
    )
    result = international_economics(transport, selected, resolved_structure=spec)
    for row in result["records"]:
        if row["source_attributes"].get("SCALE") != "0":
            raise source_error("IMF commodity scale is absent or differs from the unscaled level.")
        row.update(
            series=q.series,
            source_series=code,
            unit=unit,
            source_unit_label=label,
            currency="USD",
            unit_multiplier=0,
            quote_unit_scale="0.01" if unit.startswith("US_cent_") else "1",
            unit_basis="checked_IMF_indicator_label",
            price_basis="IMF_monthly_reference_price",
            source_derivation_type=row["source_attributes"].get("DERIVATION_TYPE"),
        )
    result["limitations"].extend(
        [
            "Monthly publisher reference prices are distinct from intraday spot or futures quotes.",
            "Physical units and original subunits are retained. US cents are not "
            "silently converted to dollars.",
            "Benchmark, quality, geography and estimation conventions follow IMF "
            "source metadata; no missing month is filled.",
        ]
    )
    return result
