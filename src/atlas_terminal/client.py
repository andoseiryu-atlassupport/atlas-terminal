"""The shared application service and the public Python SDK."""

import os
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import (
    METRICS,
    BudgetStatus,
    Company,
    FinancialQuery,
    FinancialResult,
    PurchasePreflight,
    PurchaseResult,
    Quote,
    ReconciliationResult,
    parse_query,
)
from atlas_terminal.normalize import normalize
from atlas_terminal.providers import FixtureTransport, SECProvider, SecTransport
from atlas_terminal.research import ResearchSecFixtures, ResearchService, research_catalog
from atlas_terminal.research_batch import ResearchBatchQuery, ResearchBatchResult, research_batch
from atlas_terminal.research_diagnostics import ResearchDiagnosticsQuery
from atlas_terminal.research_models import Dataset, ResearchQuery
from atlas_terminal.research_presets import (
    ResearchPresetQuery,
    ResearchPresetResult,
    item_overview,
    parse_preset,
    preset_plan,
)
from atlas_terminal.risk_connections import RiskConnector
from atlas_terminal.risk_models import RiskQuery
from atlas_terminal.risk_service import RiskService
from atlas_terminal.util import canonical, stamp


class Atlas:
    def __init__(
        self,
        provider: str = "sec",
        *,
        source: SECProvider | None = None,
        research_source: SECProvider | None = None,
        directory: Path | None = None,
    ) -> None:
        if provider not in {"sec", "fixture", "hosted", "public"}:
            raise AtlasError("INVALID_INPUT", "Unknown provider.")
        self.provider = provider
        self.source = source
        self.research_source = research_source
        self._research = ResearchService(
            self._research_source, synthetic=provider == "fixture", directory=directory
        )
        self._risk = RiskService(self._research.view_path.parent / "risk-models.sqlite3")
        self._hosted: Any = None
        if source is None and provider == "fixture":
            self.source = SECProvider(FixtureTransport())
        elif provider == "hosted":
            from atlas_terminal.hosted import HostedClient

            self._hosted = HostedClient.from_env()

    def _local_source(self) -> SECProvider:
        if self.source is None:
            self.source = SECProvider(
                SecTransport(
                    os.environ.get("SEC_USER_AGENT", ""),
                    cache_path=self._research.view_path.parent / "sec-cache.sqlite3",
                )
            )
        return self.source

    def _research_source(self) -> SECProvider:
        if self.research_source is None:
            self.research_source = (
                SECProvider(ResearchSecFixtures())
                if self.provider == "fixture"
                else self._local_source()
            )
        return self.research_source

    def get_research(self, query: ResearchQuery | dict[str, Any]) -> dict[str, Any]:
        """Free public-source research; never quotes, signs or purchases."""
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Research uses local public or fixture providers.")
        try:
            return self._research.get(query)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AtlasError(
                "SOURCE_UNAVAILABLE",
                "Source response failed research schema validation.",
                status=502,
            ) from None

    def get_margin_status(
        self, identifier: str, *, observation_date: str | None = None
    ) -> dict[str, Any]:
        """Read one exact JPX daily report; omitting the date selects the latest linked one."""
        return self.get_research(
            {
                "dataset": "margin_balances",
                "identifier": identifier,
                **({"observation_date": observation_date} if observation_date is not None else {}),
            }
        )

    def get_spread(
        self,
        series: str,
        *,
        year: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 1,
        response: dict[str, Any] | None = None,
        requirements: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get one latest yield-curve spread, or a bounded history, through shared research."""
        options = {
            "year": year,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
            "response": response,
            "requirements": requirements,
        }
        return self.get_research(
            {
                "dataset": "interest_spreads",
                "series": series,
                **{key: value for key, value in options.items() if value is not None},
            }
        )

    def iter_price_history(
        self, identifier: str, *, start_date: str, end_date: str
    ) -> Iterator[dict[str, Any]]:
        """Read complete annual stock/ETF results, preserving each shared query and evidence."""
        from atlas_terminal.equity_history import iter_price_history, price_history_queries

        return iter_price_history(
            price_history_queries(identifier, start_date, end_date), self.get_research
        )

    def export_price_history(
        self, identifier: str, *, start_date: str, end_date: str, output: Path
    ) -> dict[str, Any]:
        """Save a private multi-year CSV, original annual results and matching source bytes."""
        from atlas_terminal.equity_history import export_price_history, price_history_queries
        from atlas_terminal.equity_sources import yahoo_url

        def read_raw(result: dict[str, Any]) -> bytes:
            assert self._research.public is not None
            query = result["query"]
            return self._research.public.get(
                yahoo_url(query["identifier"], query["start_date"], query["end_date"]), 900
            ).body

        return export_price_history(
            price_history_queries(identifier, start_date, end_date),
            output,
            self.get_research,
            read_raw,
        )

    def export_technicals(
        self,
        history: Path,
        *,
        output: Path,
        metrics: list[str] | None = None,
        price_field: str = "adjusted_close",
    ) -> dict[str, Any]:
        """Calculate saved stock-history indicators without source requests."""
        from atlas_terminal.technical_indicators import export_technicals

        return export_technicals(history, output, metrics=metrics, price_field=price_field)

    def get_order_book(
        self, identifier: str, *, depth: int = 10, source: str = "bitbank"
    ) -> dict[str, Any]:
        return self.get_research(
            {
                "dataset": "order_book",
                "identifier": identifier,
                "depth": depth,
                "book_source": source,
            }
        )

    def get_reports(
        self,
        *,
        source: str = "fisco",
        identifier: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        options = {"identifier": identifier, "search": search}
        return self.get_research(
            {
                "dataset": "analyst_reports",
                "report_source": source,
                "limit": limit,
                **{k: v for k, v in options.items() if v is not None},
            }
        )

    def get_report_text(
        self, report_id: str, *, search: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        return self.get_research(
            {
                "dataset": "analyst_report_text",
                "report_id": report_id,
                "limit": limit,
                **({"search": search} if search is not None else {}),
            }
        )

    def get_news(
        self,
        *,
        feeds: list[str] | None = None,
        country: str | None = None,
        search: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
        allow_partial: bool = True,
        digest: bool = False,
        response: dict[str, Any] | None = None,
        requirements: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Official headline metadata or a deterministic source-linked briefing."""
        options = {
            "feeds": feeds,
            "country": country,
            "search": search,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
            "allow_partial": allow_partial,
            "response": response,
            "requirements": requirements,
        }
        return self.get_research(
            {
                "dataset": "news_digest" if digest else "news_feed",
                **{key: value for key, value in options.items() if value is not None},
            }
        )

    def watch_news(
        self,
        query: dict[str, Any] | None = None,
        *,
        interval_seconds: int = 900,
        max_polls: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Foreground polling with stable IDs; restarting replays the initial selection."""
        from atlas_terminal.news_stream import watch_news

        return watch_news(
            self.get_research,
            query or {"dataset": "news_feed"},
            interval_seconds=interval_seconds,
            max_polls=max_polls,
        )

    def risk(self, query: RiskQuery | dict[str, Any]) -> dict[str, Any]:
        """Import supplied observations, fit an independent model or analyze a saved portfolio.

        Native local inputs need no network. Registered API providers require explicit opt-in.
        No data purchase or signing. Synthetic status comes from the input manifest.
        """
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Risk models use a local private state directory.")
        return self._risk.execute(query)

    def import_risk_panel(self, panel: dict[str, Any]) -> dict[str, Any]:
        """Local bulk import; remote callers use risk put with content-addressed chunks."""
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Risk models require local private state.")
        return self._risk.import_panel(panel)

    def register_risk_connection(self, connection_id: str, connector: RiskConnector) -> None:
        """Register a trusted native Python provider in this local application instance."""
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Risk connections require local private state.")
        self._risk.connections.register(connection_id, connector)

    @staticmethod
    def research_catalog(
        dataset: Dataset | None = None,
        *,
        view: Literal["full", "compact"] = "full",
        search: str | None = None,
        country: str | None = None,
        frequency: Literal["daily", "weekly", "monthly", "quarterly", "annual"] | None = None,
        source_id: str | None = None,
        unit: str | None = None,
    ) -> dict[str, Any]:
        return research_catalog(
            dataset,
            view,
            search=search,
            country=country,
            frequency=frequency,
            source_id=source_id,
            unit=unit,
        )

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        """Read saved provenance without fetching a URL or initiating payment."""
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Evidence uses local public or fixture providers.")
        return self._research.views.evidence(evidence_id)

    def get_research_batch(self, query: ResearchBatchQuery | dict[str, Any]) -> dict[str, Any]:
        """Read up to ten independent research queries without purchasing or signing."""
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Research uses local public or fixture providers.")
        return research_batch(query, self.get_research)

    def get_research_preset(self, query: ResearchPresetQuery | dict[str, Any]) -> dict[str, Any]:
        """Preview or execute an explicit recipe of independent free research reads."""
        if self.provider == "hosted":
            raise AtlasError("CONFIG_REQUIRED", "Presets use local public or fixture providers.")
        parsed = parse_preset(query)
        plan = preset_plan(parsed)
        batch = (
            None
            if parsed.preview
            else self.get_research_batch(
                {"items": [{"id": item.id, "query": item.query} for item in plan.items]}
            )
        )
        result = ResearchPresetResult(
            mode="preview" if parsed.preview else "executed",
            status="preview" if batch is None else batch["status"],
            generated_at=stamp(),
            is_synthetic=self.provider == "fixture",
            plan=plan,
            batch=None if batch is None else ResearchBatchResult.model_validate(batch),
            overview=[]
            if batch is None
            else [
                item_overview(item, planned)
                for item, planned in zip(batch["items"], plan.items, strict=True)
            ],
            limitations=[
                "Independent reads do not establish an atomic snapshot or historical availability.",
                "Observation periods, publication times and source retrieval times are distinct.",
                "Overview dates cover usable rows; BLS month/year dates are calendar period ends.",
                "Absent publication times stay unknown; no release/holiday calendar is inferred.",
                "Inspect overview and each item's coverage even when every read succeeds.",
                "FX context is US context, not a complete macro profile for either currency.",
            ],
        ).model_dump(mode="json")
        # Keep exact batch records/provenance, including their original optional fields.
        result["batch"] = batch
        if len(canonical(result)) > 5 * 1024 * 1024:
            raise AtlasError("RESPONSE_TOO_LARGE", "Reduce the preset selection or count.")
        return result

    def research_diagnostics(
        self, query: ResearchDiagnosticsQuery | dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Check private source configuration; network probes require explicit opt-in."""
        from atlas_terminal.research_diagnostics import research_diagnostics

        return research_diagnostics(
            {} if query is None else query,
            provider=self.provider,
            directory=self._research.view_path.parent,
        )

    def resolve_company(self, query: str) -> Company:
        if self._hosted:
            return self._hosted.resolve_company(query)
        return self._local_source().resolve(query)

    def get_financials(self, query: FinancialQuery | dict[str, Any]) -> FinancialResult:
        query = query if isinstance(query, FinancialQuery) else parse_query(query)
        if self.provider == "hosted":
            raise AtlasError(
                "QUOTE_REQUIRED", "Hosted financials require explicit quote and purchase."
            )
        source = self._local_source()
        company, snapshot, filings, truncated = source.financial_inputs(query.identifier)
        return normalize(
            query,
            company,
            snapshot,
            filings,
            synthetic=source.transport.synthetic,
            history_truncated=truncated,
        )

    def list_filings(self, query: str, form: str = "10-K", limit: int = 3) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise AtlasError("INVALID_INPUT", "Filing limit must be 1 through 100.")
        if form not in {"10-K", "10-K/A", "10-Q", "20-F", "20-F/A", "8-K", "all"}:
            raise AtlasError("INVALID_INPUT", "Unsupported filing form filter.")
        if self._hosted:
            return self._hosted.list_filings(query, form, limit)
        source = self._local_source()
        company, history, truncated = source.history(self.resolve_company(query))
        return {
            "schema_version": "0.1.0",
            "company": company.model_dump(),
            "filings": [asdict(f) for f in history if form == "all" or f.form == form][:limit],
            "history_truncated": truncated,
            "is_synthetic": source.transport.synthetic,
        }

    def quote(
        self, query: FinancialQuery | dict[str, Any], *, intent_id: str | None = None
    ) -> Quote:
        if not self._hosted:
            raise AtlasError(
                "CONFIG_REQUIRED", "Quote uses the explicitly configured hosted provider."
            )
        return self._hosted.quote(
            query if isinstance(query, FinancialQuery) else parse_query(query), intent_id=intent_id
        )

    def budget_status(self) -> BudgetStatus:
        if not self._hosted:
            raise AtlasError("CONFIG_REQUIRED", "Budget reads require a configured hosted client.")
        return self._hosted.budget_status()

    def purchase_preflight(
        self, quote_id: str, *, max_cost: str | None = None
    ) -> PurchasePreflight:
        if not self._hosted:
            raise AtlasError("CONFIG_REQUIRED", "Preflight requires a configured hosted client.")
        return self._hosted.preflight(quote_id, max_cost=max_cost)

    def purchase(self, quote_id: str, *, max_cost: str | None = None) -> PurchaseResult:
        if not self._hosted:
            raise AtlasError("CONFIG_REQUIRED", "Purchase requires a configured hosted provider.")
        return self._hosted.purchase(quote_id, max_cost=max_cost)

    def purchase_status(self, quote_id: str) -> dict[str, Any]:
        if not self._hosted:
            raise AtlasError("CONFIG_REQUIRED", "Status requires a configured hosted provider.")
        return self._hosted.status(quote_id)

    def reconcile_purchase(
        self, quote_id: str, *, transaction: str | None = None
    ) -> ReconciliationResult:
        """Read finalized chain evidence; never creates another payment authorization."""
        if not self._hosted:
            raise AtlasError(
                "CONFIG_REQUIRED", "Reconciliation requires a configured hosted provider."
            )
        return self._hosted.reconcile(quote_id, transaction)

    def close(self) -> None:
        self._research.close()
        if self.research_source and self.research_source is not self.source:
            self.research_source.close()
        if self.source:
            self.source.close()
        if self._hosted:
            self._hosted.close()

    def __enter__(self) -> "Atlas":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def catalog() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "metrics": list(METRICS),
        "periods": ["annual"],
        "taxonomy": "us-gaap",
        "unit": "USD",
        "max_count": 20,
        "selection_policy": "latest_primary_filing_per_fact_v1",
        "free": ["catalog", "resolve", "filings", "quote"],
        "product": "Immutable annual financial snapshot",
        "historical_queries": False,
        "research_catalog_path": "/v1/research/catalog",
        "free_research_path": "/v1/research",
    }
