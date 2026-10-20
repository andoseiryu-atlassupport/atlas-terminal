"""The shared application service and the public Python SDK."""

import os
from dataclasses import asdict
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import (
    METRICS,
    Company,
    FinancialQuery,
    FinancialResult,
    PurchaseResult,
    Quote,
    parse_query,
)
from atlas_terminal.normalize import normalize
from atlas_terminal.providers import FixtureTransport, SECProvider, SecTransport


class Atlas:
    def __init__(self, provider: str = "sec", *, source: SECProvider | None = None) -> None:
        if provider not in {"sec", "fixture", "hosted"}:
            raise AtlasError("INVALID_INPUT", "Unknown provider.")
        self.provider = provider
        self.source = source
        self._hosted: Any = None
        if source is None and provider == "fixture":
            self.source = SECProvider(FixtureTransport())
        elif source is None and provider == "sec":
            self.source = SECProvider(SecTransport(os.environ.get("SEC_USER_AGENT", "")))
        elif provider == "hosted":
            from atlas_terminal.hosted import HostedClient

            self._hosted = HostedClient.from_env()

    def resolve_company(self, query: str) -> Company:
        if self._hosted:
            return self._hosted.resolve_company(query)
        assert self.source is not None
        return self.source.resolve(query)

    def get_financials(self, query: FinancialQuery | dict[str, Any]) -> FinancialResult:
        query = query if isinstance(query, FinancialQuery) else parse_query(query)
        if self.provider == "hosted":
            raise AtlasError(
                "QUOTE_REQUIRED", "Hosted financials require explicit quote and purchase."
            )
        assert self.source is not None
        company, snapshot, filings, truncated = self.source.financial_inputs(query.identifier)
        return normalize(
            query,
            company,
            snapshot,
            filings,
            synthetic=self.source.transport.synthetic,
            history_truncated=truncated,
        )

    def list_filings(self, query: str, form: str = "10-K", limit: int = 3) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise AtlasError("INVALID_INPUT", "Filing limit must be 1 through 100.")
        if form not in {"10-K", "10-K/A", "10-Q", "20-F", "20-F/A", "8-K", "all"}:
            raise AtlasError("INVALID_INPUT", "Unsupported filing form filter.")
        if self._hosted:
            return self._hosted.list_filings(query, form, limit)
        assert self.source is not None
        company, history, truncated = self.source.history(self.resolve_company(query))
        return {
            "schema_version": "0.1.0",
            "company": company.model_dump(),
            "filings": [asdict(f) for f in history if form == "all" or f.form == form][:limit],
            "history_truncated": truncated,
            "is_synthetic": self.source.transport.synthetic,
        }

    def quote(self, query: FinancialQuery | dict[str, Any]) -> Quote:
        if not self._hosted:
            raise AtlasError(
                "CONFIG_REQUIRED", "Quote uses the explicitly configured hosted provider."
            )
        return self._hosted.quote(
            query if isinstance(query, FinancialQuery) else parse_query(query)
        )

    def purchase(self, quote_id: str, *, max_cost: str | None = None) -> PurchaseResult:
        if not self._hosted:
            raise AtlasError("CONFIG_REQUIRED", "Purchase requires a configured hosted provider.")
        return self._hosted.purchase(quote_id, max_cost=max_cost)

    def purchase_status(self, quote_id: str) -> dict[str, Any]:
        if not self._hosted:
            raise AtlasError("CONFIG_REQUIRED", "Status requires a configured hosted provider.")
        return self._hosted.status(quote_id)

    def close(self) -> None:
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
    }
