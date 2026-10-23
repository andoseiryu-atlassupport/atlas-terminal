"""Export runtime contracts without source requests or payment configuration."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from atlas_terminal.errors import ErrorEnvelope
from atlas_terminal.models import BudgetStatus, PurchasePreflight
from atlas_terminal.research_batch import ResearchBatchQuery, ResearchBatchResult
from atlas_terminal.research_diagnostics import ResearchDiagnosticsQuery, ResearchDiagnosticsResult
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_presets import ResearchPresetQuery, ResearchPresetResult
from atlas_terminal.research_views import EvidenceResult, ResearchResult, SourceEvidence
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
            ]
        )
    )
    write(application, schema)
    with TemporaryDirectory(prefix="atlas-schema-") as directory:
        app = create_app(provider="fixture", directory=Path(directory))
        try:
            write(ROOT / "schemas/openapi.json", app.openapi())
        finally:
            app.state.orders.close()


if __name__ == "__main__":
    main()
