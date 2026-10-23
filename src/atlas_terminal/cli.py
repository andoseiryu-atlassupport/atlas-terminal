"""CLI adapters only: all data selection lives in the shared SDK."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

import typer
from pydantic import BaseModel, ValidationError
from typer.exceptions import TyperException

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import Currency
from atlas_terminal.models import parse_query
from atlas_terminal.research_batch import MAX_BATCH_REQUEST_BYTES
from atlas_terminal.research_models import Dataset
from atlas_terminal.risk_models import MAX_RISK_BYTES, RiskMethod

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)
company_app, financials_app, filings_app, quote_app, mcp_app = (typer.Typer() for _ in range(5))
app.add_typer(company_app, name="company")
app.add_typer(financials_app, name="financials")
app.add_typer(filings_app, name="filings")
app.add_typer(quote_app, name="quote")
app.add_typer(mcp_app, name="mcp")
research_app = typer.Typer(no_args_is_help=True)
app.add_typer(research_app, name="research")
risk_app = typer.Typer(no_args_is_help=True)
app.add_typer(risk_app, name="risk")


def output(value: Any, *, err: bool = False) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, ensure_ascii=False, allow_nan=False), err=err)


def query(identifier: str, metrics: str, period: str, count: int, allow_partial: bool) -> Any:
    return parse_query(
        {
            "identifier": identifier,
            "metrics": metrics.split(","),
            "period": period,
            "count": count,
            "allow_partial": allow_partial,
        }
    )


@app.command()
def demo(json_output: bool = typer.Option(False, "--json")) -> None:
    """Offline synthetic demo: no network, model key or wallet."""
    with Atlas("fixture") as atlas:
        output(atlas.get_financials(query("ATLASDEMO", "revenue,net_income", "annual", 3, False)))


def risk_file(path: Path, limit: int) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            body = stream.read(limit + 1)
        if len(body) > limit:
            raise ValueError("Too large")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Object required")
        return value
    except (OSError, ValueError):
        raise AtlasError(
            "INVALID_INPUT", "Read a bounded risk JSON object from a local file."
        ) from None


@risk_app.command("catalog")
def risk_catalog(json_output: bool = typer.Option(False, "--json")) -> None:
    """Describe independent risk inputs, model methods, limits and operations."""
    with Atlas("public") as atlas:
        output(atlas.risk({"action": "catalog"}))


@risk_app.command("demo")
def risk_demo(
    method: RiskMethod = "cross_sectional", json_output: bool = typer.Option(False, "--json")
) -> None:
    """Run a native connection-to-model workflow and evaluate 20 later fictional sessions."""
    from atlas_terminal.risk_demo import run_demo

    with Atlas("public") as atlas:
        output(run_demo(atlas, method))


@risk_app.command("connections")
def risk_connections(json_output: bool = typer.Option(False, "--json")) -> None:
    """List configured native inputs without exposing paths or reading their data."""
    with Atlas("public") as atlas:
        output(atlas.risk({"action": "connections"}))


@risk_app.command("import")
def risk_import(path: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    """Import up to 32 MiB of supplied, rights-cleared panel data into private state."""
    with Atlas("public") as atlas:
        output(atlas.import_risk_panel(risk_file(path, 32 * 1024 * 1024)))


@risk_app.command("run")
def risk_run(path: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    """Run a native risk query; registered API providers require explicit network opt-in."""
    with Atlas("public") as atlas:
        output(atlas.risk(risk_file(path, MAX_RISK_BYTES)))


@research_app.command("catalog")
def research_catalog(
    dataset: Dataset | None = None,
    view: Literal["full", "compact"] = "full",
    search: str | None = None,
    country: str | None = None,
    frequency: Literal["daily", "monthly", "quarterly", "annual"] | None = None,
    source_id: str | None = None,
    unit: str | None = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List free sources, datasets, units, query schema and unavailable coverage."""
    output(
        Atlas.research_catalog(
            dataset,
            view=view,
            search=search,
            country=country,
            frequency=frequency,
            source_id=source_id,
            unit=unit,
        )
    )


@research_app.command("get")
def research_get(
    dataset: str,
    identifier: str | None = None,
    identifiers: str | None = None,
    metrics: str | None = None,
    period: str | None = None,
    count: int | None = None,
    limit: int | None = None,
    allow_partial: bool = False,
    taxonomy: str | None = None,
    concept: str | None = None,
    unit: str | None = None,
    form: str | None = None,
    accession: str | None = None,
    series: str | None = None,
    year: int | None = None,
    base_currency: Currency | None = None,
    quote_currency: Currency | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source_id: str | None = None,
    dataflow: str | None = None,
    search: str | None = None,
    dimension: str | None = None,
    dimensions: str | None = None,
    max_source_age_seconds: int | None = None,
    min_observation_date: str | None = None,
    allow_missing: bool | None = None,
    allow_derived: bool | None = None,
    allow_truncated: bool | None = None,
    view: str = "full",
    fields: str | None = None,
    page_size: int | None = None,
    cursor: str | None = None,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch free public research. Dataset-specific options are in research catalog."""
    data: dict[str, Any] = {"dataset": dataset}
    options = {
        "identifier": identifier,
        "period": period,
        "count": count,
        "limit": limit,
        "taxonomy": taxonomy,
        "concept": concept,
        "unit": unit,
        "form": form,
        "accession": accession,
        "series": series,
        "year": year,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "start_date": start_date,
        "end_date": end_date,
        "source_id": source_id,
        "dataflow": dataflow,
        "search": search,
        "dimension": dimension,
    }
    data.update({k: v for k, v in options.items() if v is not None})
    if dimensions is not None:
        try:
            if len(dimensions) > 8192:
                raise ValueError("Dimensions exceed bounds")
            data["dimensions"] = json.loads(dimensions)
        except ValueError:
            raise AtlasError(
                "INVALID_INPUT", "Provide dimensions as a bounded JSON object."
            ) from None
    if metrics is not None:
        data["metrics"] = metrics.split(",")
    if identifiers is not None:
        data["identifiers"] = identifiers.split(",")
    if allow_partial:
        data["allow_partial"] = True
    requirements = {
        "max_source_age_seconds": max_source_age_seconds,
        "min_observation_date": min_observation_date,
        "allow_missing": allow_missing,
        "allow_derived": allow_derived,
        "allow_truncated": allow_truncated,
    }
    if any(value is not None for value in requirements.values()):
        data["requirements"] = {
            key: value for key, value in requirements.items() if value is not None
        }
    if view != "full" or fields is not None or page_size is not None or cursor is not None:
        response: dict[str, Any] = {"view": view}
        if fields is not None:
            response["fields"] = fields.split(",")
        if page_size is not None:
            response["page_size"] = page_size
        if cursor is not None:
            response["cursor"] = cursor
        data["response"] = response
    with Atlas(provider) as atlas:
        output(atlas.get_research(data))


@research_app.command("run")
def research_run(
    input_file: str = typer.Option(..., "--input"),
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run any research query, including a macro panel, from a 16 KiB JSON file or stdin."""
    try:
        if input_file == "-":
            body = sys.stdin.buffer.read(MAX_BATCH_REQUEST_BYTES + 1)
        else:
            with Path(input_file).open("rb") as file:
                body = file.read(MAX_BATCH_REQUEST_BYTES + 1)
        if len(body) > MAX_BATCH_REQUEST_BYTES:
            raise ValueError("Request exceeds bounds")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
    except (OSError, ValueError):
        raise AtlasError(
            "INVALID_INPUT", "Read a research JSON object of at most 16 KiB."
        ) from None
    with Atlas(provider) as atlas:
        output(atlas.get_research(data))


@research_app.command("batch")
def research_batch(
    input_file: str = typer.Option(..., "--input"),
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Read independent research queries from a bounded JSON file, or stdin with --input -."""
    try:
        if input_file == "-":
            body = sys.stdin.buffer.read(MAX_BATCH_REQUEST_BYTES + 1)
        else:
            with Path(input_file).open("rb") as file:
                body = file.read(MAX_BATCH_REQUEST_BYTES + 1)
        if len(body) > MAX_BATCH_REQUEST_BYTES:
            raise ValueError("Request exceeds bounds")
        data = json.loads(body)
    except (OSError, ValueError):
        raise AtlasError(
            "INVALID_INPUT", "Read a valid batch JSON object of at most 16 KiB."
        ) from None
    with Atlas(provider) as atlas:
        result = atlas.get_research_batch(data)
    output(result)
    if result["summary"]["failed"]:
        raise SystemExit(1)


@research_app.command("preset")
def research_preset(
    preset: str | None = typer.Argument(None),
    identifier: str | None = None,
    period: str | None = None,
    count: int | None = None,
    year: int | None = None,
    base_currency: Currency | None = None,
    quote_currency: Currency | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include: str | None = None,
    view: Literal["full", "compact"] | None = None,
    preview: bool = False,
    input_file: str | None = typer.Option(None, "--input"),
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Preview/execute a named recipe, or load its reusable JSON query with --input FILE/- ."""
    options = {
        "preset": preset,
        "identifier": identifier,
        "period": period,
        "count": count,
        "year": year,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "include": include.split(",") if include is not None else None,
        "start_date": start_date,
        "end_date": end_date,
        "view": view,
    }
    data = {key: value for key, value in options.items() if value is not None}
    if input_file is not None:
        if data:
            raise AtlasError("INVALID_INPUT", "Use either --input or preset query arguments.")
        try:
            if input_file == "-":
                body = sys.stdin.buffer.read(MAX_BATCH_REQUEST_BYTES + 1)
            else:
                with Path(input_file).open("rb") as file:
                    body = file.read(MAX_BATCH_REQUEST_BYTES + 1)
            if len(body) > MAX_BATCH_REQUEST_BYTES:
                raise ValueError("Request exceeds bounds")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("Use an object")
        except (OSError, ValueError):
            raise AtlasError(
                "INVALID_INPUT", "Read a preset JSON object of at most 16 KiB."
            ) from None
    if preview:
        data["preview"] = True
    with Atlas(provider) as atlas:
        result = atlas.get_research_preset(data)
    output(result)
    if result["batch"] is not None and result["batch"]["summary"]["failed"]:
        raise SystemExit(1)


@research_app.command("doctor")
def research_doctor(
    sources: str = "all",
    allow_network: bool = False,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Check configuration; --allow-network explicitly runs fixed official-source probes."""
    with Atlas(provider) as atlas:
        result = atlas.research_diagnostics(
            {
                "sources": None if sources == "all" else sources.split(","),
                "allow_network": allow_network,
            }
        )
    output(result)
    if result["status"] in {"action_required", "degraded"}:
        raise SystemExit(4 if result["status"] == "action_required" else 3)


@research_app.command("evidence")
def research_evidence(
    evidence_id: str,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Read saved provenance for a compact result; never fetches or pays."""
    with Atlas(provider) as atlas:
        output(atlas.get_evidence(evidence_id))


@research_app.command("check-sources")
def research_check_sources(
    sources: str = "all",
    allow_network: bool = False,
    max_observation_lag_days: int | None = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Opt-in official-source checks; save private last-success shape/date baselines."""
    from atlas_terminal.source_checks import check_sources

    result = check_sources(
        None if sources == "all" else sources.split(","),
        allow_network=allow_network,
        max_observation_lag_days=max_observation_lag_days,
    )
    output(result)
    if result["status"] != "PASS":
        raise SystemExit(4 if result["status"] == "NOT_RUN" else 3)


@company_app.command("resolve")
def resolve(
    identifier: str, provider: str = "sec", json_output: bool = typer.Option(False, "--json")
) -> None:
    with Atlas(provider) as atlas:
        output(atlas.resolve_company(identifier))


@financials_app.command("get")
def financials(
    identifier: str,
    metrics: str = "revenue,net_income",
    period: str = "annual",
    count: int = 3,
    provider: str = "sec",
    allow_partial: bool = False,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    parsed = query(identifier, metrics, period, count, allow_partial)
    with Atlas(provider) as atlas:
        output(atlas.get_financials(parsed))


@filings_app.command("list")
def filings(
    identifier: str,
    form: str = "10-K",
    limit: int = 3,
    provider: str = "sec",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with Atlas(provider) as atlas:
        output(atlas.list_filings(identifier, form, limit))


@quote_app.command("financials")
def quote_financials(
    identifier: str,
    metrics: str = "revenue,net_income",
    period: str = "annual",
    count: int = 3,
    allow_partial: bool = False,
    intent_id: str | None = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    parsed = query(identifier, metrics, period, count, allow_partial)
    with Atlas("hosted") as atlas:
        output(atlas.quote(parsed, intent_id=intent_id))


@app.command("budget-status")
def budget_status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Read delegated budget totals without loading a wallet or reserving funds."""
    with Atlas("hosted") as atlas:
        output(atlas.budget_status())


@app.command("purchase-preflight")
def purchase_preflight(
    quote_id: str, max_cost: str | None = None, json_output: bool = typer.Option(False, "--json")
) -> None:
    """Check saved order and local policy without signing or authorizing a payment."""
    with Atlas("hosted") as atlas:
        output(atlas.purchase_preflight(quote_id, max_cost=max_cost))


@app.command()
def purchase(
    quote_id: str, max_cost: str | None = None, json_output: bool = typer.Option(False, "--json")
) -> None:
    with Atlas("hosted") as atlas:
        output(atlas.purchase(quote_id, max_cost=max_cost))


@app.command("purchase-status")
def purchase_status(quote_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    with Atlas("hosted") as atlas:
        output(atlas.purchase_status(quote_id))


@app.command("purchase-reconcile")
def purchase_reconcile(
    quote_id: str,
    transaction: str | None = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Reconcile finalized testnet evidence; never signs or sends another payment."""
    with Atlas("hosted") as atlas:
        output(atlas.reconcile_purchase(quote_id, transaction=transaction))


@mcp_app.command("serve")
def serve_mcp(
    provider: str = "sec", research_only: bool = False, enable_risk: bool = False
) -> None:
    from atlas_terminal.mcp_server import create_mcp

    create_mcp(provider, research_only=research_only, enable_risk=enable_risk).run(
        transport="stdio"
    )


@mcp_app.command("http")
def serve_remote_mcp(
    provider: str = "public",
    host: str = "127.0.0.1",
    port: int = 8001,
    allow_anonymous: bool = False,
    enable_risk: bool = False,
) -> None:
    """Serve research-only Streamable HTTP behind operator-managed HTTPS."""
    import uvicorn

    from atlas_terminal.remote_mcp import create_remote_mcp

    if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} or not 1 <= port <= 65535:
        raise AtlasError("CONFIG_REQUIRED", "Choose an explicit bind address and valid port.")
    public_origin = os.environ.get("ATLAS_MCP_PUBLIC_ORIGIN")
    if not public_origin:
        if host == "0.0.0.0":
            raise AtlasError("CONFIG_REQUIRED", "Configure the external HTTPS MCP origin.")
        public_origin = f"http://{'[::1]' if host == '::1' else host}:{port}"
    if host == "0.0.0.0" and not public_origin.startswith("https://"):
        raise AtlasError("CONFIG_REQUIRED", "External MCP binding requires an HTTPS origin.")
    api = create_remote_mcp(
        provider=provider,
        public_origin=public_origin,
        token=os.environ.get("ATLAS_MCP_AUTH_TOKEN"),
        allow_anonymous=allow_anonymous,
        enable_risk=enable_risk,
        allowed_origins=[
            value for value in os.environ.get("ATLAS_MCP_ALLOWED_ORIGINS", "").split(",") if value
        ],
    )
    uvicorn.run(api, host=host, port=port, workers=1, access_log=False, log_level="critical")


@app.command()
def serve(
    provider: str = "sec",
    payments: str = "off",
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_risk: bool = False,
) -> None:
    import uvicorn

    from atlas_terminal.server import create_app

    if not 1 <= port <= 65535 or host not in {"127.0.0.1", "localhost", "::1"}:
        raise AtlasError(
            "CONFIG_REQUIRED", "Reference API binds only to an explicit loopback address."
        )
    base_origin = os.environ.get(
        "ATLAS_PUBLIC_ORIGIN", f"http://{'[::1]' if host == '::1' else host}:{port}"
    )
    api = create_app(
        provider=provider,
        payments=payments,
        base_origin=base_origin,
        amount=os.environ.get("ATLAS_PRICE_ATOMIC", "10000"),
        enable_risk=enable_risk,
    )
    uvicorn.run(api, host=host, port=port, workers=1, access_log=False, log_level="critical")


def main() -> None:
    mcp_startup = sys.argv[1:3] == ["mcp", "serve"]
    try:
        app(standalone_mode=False)
    except AtlasError as exc:
        output(exc.envelope(), err=mcp_startup)
        raise SystemExit(exc.exit_code) from None
    except (TyperException, ValidationError, ValueError):
        output(
            AtlasError(
                "INVALID_INPUT", "Invalid command arguments; use --help for supported options."
            ).envelope(),
            err=mcp_startup,
        )
        raise SystemExit(2) from None
    except Exception:
        output(
            AtlasError("INTERNAL_ERROR", "Operation stopped due to an internal error.").envelope(),
            err=mcp_startup,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
