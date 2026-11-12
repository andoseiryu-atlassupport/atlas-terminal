"""CLI adapters only: all data selection lives in the shared SDK."""

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import BaseModel, ValidationError
from typer.exceptions import TyperException

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import Currency
from atlas_terminal.models import parse_query
from atlas_terminal.research_batch import MAX_BATCH_REQUEST_BYTES
from atlas_terminal.research_models import Dataset
from atlas_terminal.risk_markets import EquityMarket
from atlas_terminal.risk_models import MAX_RISK_BYTES, RiskMethod
from atlas_terminal.seller_cli import seller_app

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
app.add_typer(seller_app, name="seller")


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


@app.command()
def showcase(
    scenario: Literal["research", "japan-risk"],
    provider: Literal["fixture", "public"] = "fixture",
    allow_network: bool = False,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Try one complete research or Japanese risk workflow through an actual MCP session."""
    from atlas_terminal.showcase import run_showcase, transcript

    result = run_showcase(scenario, provider, allow_network=allow_network)
    if json_output:
        output(result)
    else:
        typer.echo(transcript(result))


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
    method: RiskMethod = "cross_sectional",
    country: EquityMarket = "US",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run a native connection-to-model workflow and evaluate 20 later fictional sessions."""
    from atlas_terminal.risk_demo import run_demo

    with Atlas("public") as atlas:
        output(run_demo(atlas, method, country))


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
    frequency: Literal["daily", "weekly", "monthly", "quarterly", "annual"] | None = None,
    source_id: str | None = None,
    unit: str | None = None,
    topic: Literal["cpi", "employment"] | None = None,
    breakdown: str | None = None,
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
            topic=topic,
            breakdown=breakdown,
        )
    )


@research_app.command("news")
def research_news(
    feeds: str | None = None,
    country: str | None = None,
    search: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
    allow_partial: bool = typer.Option(True, "--allow-partial/--strict"),
    digest: bool = False,
    watch: bool = False,
    interval_seconds: int = 900,
    max_polls: int | None = None,
    rss: bool = False,
    list_feeds: bool = typer.Option(False, "--list"),
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Official headlines, publisher-grouped briefings, RSS or foreground JSONL updates."""
    from atlas_terminal.news_catalog import news_catalog
    from atlas_terminal.research_news import rss_document

    if list_feeds:
        output(news_catalog())
        return
    if rss and (watch or json_output or digest):
        raise AtlasError("INVALID_INPUT", "Choose RSS, a briefing or JSON/JSONL output.")
    if max_polls is not None and not watch:
        raise AtlasError("INVALID_INPUT", "Use --max-polls with --watch.")
    options = {
        "feeds": feeds.split(",") if feeds is not None else None,
        "country": country,
        "search": search,
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "allow_partial": allow_partial,
    }
    data = {
        "dataset": "news_digest" if digest else "news_feed",
        **{k: v for k, v in options.items() if v is not None},
    }
    with Atlas(provider) as atlas:
        if watch:
            for event in atlas.watch_news(
                data, interval_seconds=interval_seconds, max_polls=max_polls
            ):
                output(event)
            return
        result = atlas.get_research(data)
    if rss:
        typer.echo(rss_document(result))
    elif json_output:
        output(result)
    else:
        if result["is_synthetic"]:
            typer.echo("SYNTHETIC: fictional official headlines")
        if digest:
            typer.echo(result["briefing_text"])
        else:
            for row in result["records"]:
                date = row["published_at"] or row["published_date"] or "date unavailable"
                typer.echo(f"{date}  {row['publisher']}")
                typer.echo(row["title"])
                typer.echo(row["url"])
        if result["feed_errors"]:
            typer.echo("Partial source coverage:")
            for error in result["feed_errors"]:
                typer.echo(f"{error['feed']}: {error['error']['code']}")
        if result["coverage"]["truncated"]:
            typer.echo("More retained headlines exist; increase --limit or narrow the filters.")


@research_app.command("spread")
def research_spread(
    series: str | None = typer.Argument(None),
    list_series: bool = typer.Option(False, "--list"),
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 1,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """One short name returns a latest spread; --limit selects history, --list is offline."""
    if list_series:
        from atlas_terminal.spread_catalog import SPREADS

        output({"series": SPREADS, "default_limit": 1, "unit": "basis_points"})
        return
    if series is None:
        raise AtlasError("INVALID_INPUT", "Choose a spread name or use --list.")
    with Atlas(provider) as atlas:
        result = atlas.get_spread(
            series, year=year, start_date=start_date, end_date=end_date, limit=limit
        )
    if json_output:
        output(result)
        return
    if result["is_synthetic"]:
        typer.echo("SYNTHETIC: fictional source observations")
    typer.echo(result["spread_definition"]["title"])
    for row in result["records"]:
        shown = "unavailable" if row["value"] is None else row["value"] + " bp"
        typer.echo(f"{row['date']}  {shown}")
        if row.get("reason"):
            typer.echo(row["reason"])
    typer.echo(
        "Long minus short; observation date, not a real-time quote. Use --json for evidence."
    )


@research_app.command("book")
def research_book(
    identifier: str,
    depth: int = 10,
    source: str = "bitbank",
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Read one venue's price-level snapshot or an operator-configured local book."""
    with Atlas(provider) as atlas:
        result = atlas.get_order_book(identifier, depth=depth, source=source)
    if json_output:
        output(result)
        return
    label = "fictional snapshot" if result["is_synthetic"] else "snapshot"
    typer.echo(f"{identifier}  {result['snapshot']['observed_at']}  ({label})")
    stats = result["book_analytics"]
    quantity_unit = (
        result["records"][0].get("base_asset") if result["records"] else None
    ) or stats["quantity_unit"]
    typer.echo(f"Price: {stats['quote_currency']} per {quantity_unit}; quantity: {quantity_unit}")
    for row in result["records"]:
        typer.echo(f"{row['side']:4} {row['level']:3}  {row['price']}  x {row['quantity']}")
    typer.echo(
        f"Spread: {stats['spread']} {stats['quote_currency']}  "
        f"Imbalance: {stats['quantity_imbalance']}"
    )
    typer.echo("One venue; selected depth only. Use --json for units, coverage and evidence.")


@research_app.command("polymarket")
def research_polymarket(
    search: str | None = typer.Argument(None),
    event_slug: str | None = None,
    limit: int = 3,
    include_closed: bool = False,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Find prediction events and read Gamma outcome prices as probability indicators."""
    with Atlas(provider) as atlas:
        result = atlas.get_prediction_markets(
            search, event_slug=event_slug, limit=limit, include_closed=include_closed
        )
    if json_output:
        output(result)
        return
    if result["is_synthetic"]:
        typer.echo("Fictional prediction-market data.")
    for event in result["records"]:
        typer.echo(f"{event['title']}  [{event['event_slug']}]")
        for market in event["markets"]:
            typer.echo(f"  {market['question']}  ({market['lifecycle']})")
            for outcome in market["outcomes"]:
                percent = outcome["probability_percent"]
                label = f"{percent}%" if percent is not None else "unavailable"
                typer.echo(
                    f"    {outcome['outcome']}: {label}  Gamma price: {outcome['reported_price']}"
                )
        typer.echo(event["event_url"])
    typer.echo("Gamma market-implied indicators; price time unverified. Use --json for evidence.")


@research_app.command("cpi-effects")
def research_cpi_effects(
    category: Annotated[list[str] | None, typer.Option("--category")] = None,
    category_set: str | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
    change: str = "mom",
    seasonal_adjustment: str = "sa",
    target: str = "all_items",
    provider: str = "public",
) -> None:
    """Read CPI effects (percentage points) and weights; emit the shared JSON response."""
    with Atlas(provider) as atlas:
        output(
            atlas.get_cpi_effects(
                categories=category or None,
                category_set=category_set,
                start_month=start_month,
                end_month=end_month,
                change=change,
                seasonal_adjustment=seasonal_adjustment,
                target=target,
            )
        )


@research_app.command("buffett")
def research_buffett(
    identifier: str,
    years: int = 5,
    country: str | None = None,
    financial_input: Annotated[Path | None, typer.Option("--input")] = None,
    valuation_input: Annotated[Path | None, typer.Option("--valuation-input")] = None,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Score annual business fundamentals with explicit valuation assumptions."""

    def read_input(path: Path | None) -> dict | None:
        if path is None:
            return None
        try:
            with path.open("rb") as stream:
                body = stream.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError("Input exceeds its bound")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("Input must be a JSON object")
            return value
        except (OSError, ValueError):
            raise AtlasError(
                "INVALID_INPUT", "Provide a bounded JSON financial/valuation file."
            ) from None

    with Atlas(provider) as atlas:
        result = atlas.get_buffett_score(
            identifier,
            years=years,
            country=country,
            financial_input=read_input(financial_input),
            valuation=read_input(valuation_input),
        )
    if json_output:
        output(result)
        return
    row = result["records"][0]
    if result["is_synthetic"]:
        typer.echo("Fictional fundamentals.")
    typer.echo(f"{identifier}  Buffett-inspired score  {row['period_end']}  {row['model_version']}")
    typer.echo(
        f"Total: {row['score'] or 'unavailable'}/100  "
        f"Quality: {row['quality_score'] or 'unavailable'}/80"
    )
    typer.echo(
        f"Fixed-weight bounds: {row['score_bounds']['lower']} to {row['score_bounds']['upper']}"
    )
    for component in row["components"]:
        typer.echo(
            f"{component['metric']}: {component['points'] or 'unavailable'}"
            f"/{component['max_points']}"
            + (f" ({component['reason']})" if component["reason"] else "")
        )
    typer.echo("Quantitative proxies; business understanding, moat and management require review.")


@research_app.command("edinet-index")
def research_edinet_index(
    start_date: Annotated[str, typer.Option("--start-date")],
    end_date: Annotated[str, typer.Option("--end-date")],
    provider: str = "public",
) -> None:
    """Index at most 31 EDINET submission dates in private state; resume using next_date."""
    with Atlas(provider) as atlas:
        output(atlas.sync_edinet_history(start_date, end_date))


@research_app.command("margin")
def research_margin(
    identifier: str,
    observation_date: str | None = None,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Read JPX daily credit buy/sell balances and a share-based margin ratio."""
    with Atlas(provider) as atlas:
        result = atlas.get_margin_status(identifier, observation_date=observation_date)
    if json_output:
        output(result)
        return
    row = result["records"][0]
    if result["is_synthetic"]:
        typer.echo("Fictional margin balances.")
    typer.echo(f"{identifier}  Application: {row['date']}  Published: {row['publication_date']}")
    shares = row["shares"]
    typer.echo(f"Buy balance: {shares['buy_balance']} shares  Change: {shares['buy_change']}")
    typer.echo(f"Sell balance: {shares['sell_balance']} shares  Change: {shares['sell_change']}")
    typer.echo(f"Margin ratio (buy / sell shares): {row['margin_ratio']['value'] or 'unavailable'}")
    typer.echo("JPX exchange margin balances; recent linked reports. Use --json for details.")


@research_app.command("reports")
def research_reports(
    identifier: str | None = None,
    source: str = "fisco",
    search: str | None = None,
    limit: int = 100,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Find public report links or metadata from privately imported documents."""
    with Atlas(provider) as atlas:
        result = atlas.get_reports(source=source, identifier=identifier, search=search, limit=limit)
    if json_output:
        output(result)
        return
    if result["is_synthetic"]:
        typer.echo("Fictional report metadata.")
    if source == "fisco":
        typer.echo("Company-commissioned research; individual document disclosures unverified.")
    for row in result["records"]:
        typer.echo(f"{row['published_date']}  {row['identifier'] or 'unlisted'}  {row['title']}")
        typer.echo(row["original_source_url"])
        if row["body_available"]:
            typer.echo("Imported document: " + row["report_id"])
    typer.echo(
        f"{len(result['records'])} selected reports. Original links; no remote PDF extraction."
    )


@research_app.command("import-report")
def research_import_report(
    document: Path, metadata: Annotated[Path, typer.Option()], file_type: str = "pdf"
) -> None:
    """Import a permitted local PDF/text and explicit metadata into private storage."""
    from atlas_terminal.analyst_reports import import_report
    from atlas_terminal.util import state_dir

    output(import_report(document, metadata, state_dir(), file_type=file_type))


@research_app.command("report-text")
def research_report_text(
    report_id: str, search: str | None = None, provider: str = "public"
) -> None:
    """Read source-addressed pages from a privately imported report."""
    with Atlas(provider) as atlas:
        output(atlas.get_report_text(report_id, search=search))


@research_app.command("technicals")
def research_technicals(
    input_directory: Annotated[Path, typer.Option("--input")],
    output_directory: Annotated[Path, typer.Option("--output")],
    metrics: str | None = None,
    price_field: str = "adjusted_close",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Calculate indicators offline from a completed stock-history export."""
    from atlas_terminal.technical_indicators import export_technicals

    output(
        export_technicals(
            input_directory,
            output_directory,
            metrics=metrics.split(",") if metrics is not None else None,
            price_field=price_field,
        )
    )


@research_app.command("price-history")
def research_price_history(
    identifier: str,
    start_date: Annotated[str, typer.Option()],
    end_date: Annotated[str, typer.Option()],
    output_directory: Annotated[Path | None, typer.Option("--output")] = None,
    preview: bool = False,
    provider: str = "public",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Export multi-year daily stock/ETF prices using bounded annual research reads."""
    from atlas_terminal.equity_history import price_history_queries

    if preview:
        output(
            {
                "queries": price_history_queries(identifier, start_date, end_date),
                "network_used": False,
            }
        )
        return
    if output_directory is None:
        raise AtlasError("INVALID_INPUT", "Choose a new private directory with --output.")
    with Atlas(provider) as atlas:
        output(
            atlas.export_price_history(
                identifier, start_date=start_date, end_date=end_date, output=output_directory
            )
        )


@research_app.command("get")
def research_get(
    dataset: str,
    benchmark_id: str | None = None,
    benchmark_currency: str | None = None,
    dividend_treatment: str | None = None,
    alignment: str | None = None,
    portfolio: str | None = None,
    event_slug: str | None = None,
    include_closed: bool = False,
    book_source: str | None = None,
    depth: int | None = None,
    report_source: str | None = None,
    report_id: str | None = None,
    country: str | None = None,
    agency: str | None = None,
    observation_date: str | None = None,
    identifier: str | None = None,
    identifiers: str | None = None,
    filters: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    metrics: str | None = None,
    price_field: str | None = None,
    period: str | None = None,
    count: int | None = None,
    limit: int | None = None,
    allow_partial: bool = False,
    taxonomy: str | None = None,
    concept: str | None = None,
    unit: str | None = None,
    form: str | None = None,
    accession: str | None = None,
    filing_date: str | None = None,
    document_id: str | None = None,
    document_name: str | None = None,
    section: str | None = None,
    table_index: int | None = None,
    source_url: str | None = None,
    history_action: str | None = None,
    history_scope: str | None = None,
    sha256: str | None = None,
    before_sha256: str | None = None,
    as_of: str | None = None,
    series: str | None = None,
    year: int | None = None,
    base_currency: Currency | None = None,
    quote_currency: Currency | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    interval: str | None = None,
    source_id: str | None = None,
    dataflow: str | None = None,
    search: str | None = None,
    feeds: str | None = None,
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
        "benchmark_id": benchmark_id,
        "benchmark_currency": benchmark_currency,
        "dividend_treatment": dividend_treatment,
        "alignment": alignment,
        "event_slug": event_slug,
        "book_source": book_source,
        "depth": depth,
        "report_source": report_source,
        "report_id": report_id,
        "identifier": identifier,
        "country": country,
        "agency": agency,
        "observation_date": observation_date,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "period": period,
        "count": count,
        "limit": limit,
        "price_field": price_field,
        "taxonomy": taxonomy,
        "concept": concept,
        "unit": unit,
        "form": form,
        "accession": accession,
        "filing_date": filing_date,
        "document_id": document_id,
        "document_name": document_name,
        "section": section,
        "table_index": table_index,
        "source_url": source_url,
        "history_action": history_action,
        "history_scope": history_scope,
        "sha256": sha256,
        "before_sha256": before_sha256,
        "as_of": as_of,
        "series": series,
        "year": year,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "start_date": start_date,
        "end_date": end_date,
        "interval": interval,
        "source_id": source_id,
        "dataflow": dataflow,
        "search": search,
        "dimension": dimension,
    }
    data.update({k: v for k, v in options.items() if v is not None})
    if portfolio is not None:
        try:
            if len(portfolio) > 65536:
                raise ValueError("Portfolio exceeds bounds")
            data["portfolio"] = json.loads(portfolio)
        except ValueError:
            raise AtlasError(
                "INVALID_INPUT", "Provide portfolio as a bounded JSON object."
            ) from None
    if filters is not None:
        try:
            if len(filters) > 8192:
                raise ValueError("Filters exceed bounds")
            data["filters"] = json.loads(filters)
        except ValueError:
            raise AtlasError("INVALID_INPUT", "Provide filters as a bounded JSON array.") from None
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
    if feeds is not None:
        data["feeds"] = feeds.split(",")
    if allow_partial:
        data["allow_partial"] = True
    if include_closed:
        data["include_closed"] = True
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


@research_app.command("chart")
def research_chart(
    input_file: str = typer.Option(..., "--input"),
    output_file: str = typer.Option(..., "--output"),
    field: str = "value",
    metric: str | None = None,
    title: str | None = None,
) -> None:
    """Render a saved full research result to a private standalone HTML chart, offline."""
    from atlas_terminal.research_chart import render_research_chart

    try:
        with Path(input_file).open("rb") as stream:
            body = stream.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("Input exceeds bounds")
        result = json.loads(body)
        html = render_research_chart(result, field=field, metric=metric, title=title)
        # Exclusive creation protects existing files; generated source data stays private.
        descriptor = os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(html)
    except (OSError, ValueError):
        raise AtlasError(
            "INVALID_INPUT", "Read a full result up to 4 MiB and choose a new writable HTML path."
        ) from None
    output({"output": str(output_file), "network_used": False, "published": False})


def _saved_research_result(path: str) -> dict[str, Any]:
    """Read one bounded local JSON object without creating a source client."""
    try:
        with Path(path).open("rb") as stream:
            body = stream.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("Input exceeds bounds")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value
    except (OSError, ValueError, RecursionError):
        raise AtlasError("INVALID_INPUT", "Read a full research JSON object up to 4 MiB.") from None


def _private_research_export(path: str, body: str) -> None:
    """Create a new private artifact; never overwrite existing files or follow symlinks."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(body)
    except OSError:
        raise AtlasError("INVALID_INPUT", "Choose a new writable local output path.") from None


@research_app.command("statistics")
def research_statistics(
    input_file: str = typer.Option(..., "--input"),
    output_file: str = typer.Option(..., "--output"),
    field: str = "value",
    metric: str | None = None,
    transform: str = "level",
    frequency: str = "observations",
    max_lag: int = 10,
    adf_lags: int = 0,
    kpss_lags: int = 0,
    deterministic: str = "constant",
) -> None:
    """Analyze one saved complete series, retaining source evidence, entirely offline."""
    from atlas_terminal.research_statistics import analyze_research_series

    result = analyze_research_series(
        _saved_research_result(input_file),
        field=field,
        metric=metric,
        transform=transform,
        frequency=frequency,
        max_lag=max_lag,
        adf_lags=adf_lags,
        kpss_lags=kpss_lags,
        deterministic=deterministic,
    )
    _private_research_export(output_file, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    output({"output": output_file, "network_used": False, "published": False})


@research_app.command("workbench")
def research_workbench(
    input_files: Annotated[list[str], typer.Option("--input")],
    output_file: str = typer.Option(..., "--output"),
    field: str | None = None,
    metric: str | None = None,
    title: str = "Research workbench",
) -> None:
    """Combine up to eight saved full results in a private offline HTML workbench."""
    from atlas_terminal.research_workbench import WorkbenchPanel, render_research_workbench

    if not 1 <= len(input_files) <= 8:
        raise AtlasError("INVALID_INPUT", "Select between one and eight saved results.")
    panels = [
        WorkbenchPanel(_saved_research_result(path), field=field, metric=metric)
        for path in input_files
    ]
    html = render_research_workbench(panels, title=title)
    _private_research_export(output_file, html)
    output(
        {"output": output_file, "panels": len(panels), "network_used": False, "published": False}
    )


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
    country: str | None = None,
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
        "country": country,
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
    mcp_startup = sys.argv[1:3] in (["mcp", "serve"], ["seller", "mcp"])
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
