"""Export runtime contracts without source requests or payment configuration."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from atlas_terminal.errors import ErrorEnvelope
from atlas_terminal.models import BudgetStatus, PurchasePreflight, PurchaseResult, Quote
from atlas_terminal.research_batch import ResearchBatchQuery, ResearchBatchResult
from atlas_terminal.research_diagnostics import ResearchDiagnosticsQuery, ResearchDiagnosticsResult
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_presets import ResearchPresetQuery, ResearchPresetResult
from atlas_terminal.research_views import EvidenceResult, ResearchResult, SourceEvidence
from atlas_terminal.risk_connections import RiskConnectionConfig
from atlas_terminal.risk_models import RiskPanelInput, RiskQuery, RiskResult, TimeSeriesPanelInput
from atlas_terminal.seller_models import ProductData, ProductPurchase, ProductQuery, ProductQuote
from atlas_terminal.seller_server import create_seller_app
from atlas_terminal.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def definitions(models: list[tuple[str, type[BaseModel]]]) -> dict:
    result = {}
    for name, model in models:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        result.update(schema.pop("$defs", {}))
        result[name] = schema
    return result


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def main() -> None:
    research = ROOT / "schemas/research.schema.json"
    schema = json.loads(research.read_bytes())
    schema["$defs"] = definitions(
        [
            ("ResearchQuery", ResearchQuery),
            ("ResearchBatchQuery", ResearchBatchQuery),
            ("ResearchBatchResult", ResearchBatchResult),
            ("ResearchPresetQuery", ResearchPresetQuery),
            ("ResearchPresetResult", ResearchPresetResult),
            ("ResearchDiagnosticsQuery", ResearchDiagnosticsQuery),
            ("ResearchDiagnosticsResult", ResearchDiagnosticsResult),
            ("ResearchSource", SourceEvidence),
            ("ResearchResult", ResearchResult),
            ("EvidenceResult", EvidenceResult),
            ("RiskQuery", RiskQuery),
            ("RiskPanelInput", RiskPanelInput),
            ("RiskResult", RiskResult),
            ("TimeSeriesPanelInput", TimeSeriesPanelInput),
            ("RiskConnectionConfig", RiskConnectionConfig),
        ]
    )
    write(research, schema)
    application = ROOT / "schemas/atlas.schema.json"
    schema = json.loads(application.read_bytes())
    schema["$defs"].update(
        definitions(
            [
                ("Error", ErrorEnvelope),
                ("BudgetStatus", BudgetStatus),
                ("PurchasePreflight", PurchasePreflight),
                ("Quote", Quote),
                ("PurchaseResult", PurchaseResult),
            ]
        )
    )
    write(application, schema)
    with TemporaryDirectory(prefix="atlas-schema-") as directory:
        app = create_app(provider="fixture", directory=Path(directory), enable_risk=True)
        try:
            write(ROOT / "schemas/openapi.json", app.openapi())
        finally:
            app.state.orders.close()
        seller = create_seller_app(directory=Path(directory) / "seller")
        try:
            write(ROOT / "schemas/seller.openapi.json", seller.openapi())
        finally:
            seller.state.orders.close()
    write(
        ROOT / "schemas/seller.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://atlas-terminal.invalid/schemas/seller.schema.json",
            "title": "Atlas Terminal seller contracts",
            "$defs": definitions(
                [
                    (model.__name__, model)
                    for model in (
                        ProductQuery,
                        ProductData,
                        ProductQuote,
                        ProductPurchase,
                    )
                ]
            ),
            "oneOf": [
                {"$ref": f"#/$defs/{model.__name__}"}
                for model in (
                    ProductQuery,
                    ProductData,
                    ProductQuote,
                    ProductPurchase,
                )
            ],
        },
    )


if __name__ == "__main__":
    main()
