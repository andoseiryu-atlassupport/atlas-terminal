"""Shared private content-addressed inputs, immutable models and portfolio computations."""

import json
from datetime import datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_connections import RiskConnectionRead, RiskConnections
from atlas_terminal.risk_engine import (
    LIMITATIONS,
    RiskPanel,
    analyze_model,
    fit_model,
    portfolio_risk,
    portfolio_weights,
)
from atlas_terminal.risk_math import ONE, covariance, dot, ewma_weights, text
from atlas_terminal.risk_models import (
    MAX_RISK_BYTES,
    METHODOLOGY,
    TIME_SERIES_METHODOLOGY,
    RiskAnalyzeQuery,
    RiskCatalogQuery,
    RiskConnectionsQuery,
    RiskFitQuery,
    RiskFundamental,
    RiskFundamentalChunk,
    RiskGetQuery,
    RiskManifest,
    RiskPanelInput,
    RiskPutQuery,
    RiskQuery,
    RiskResult,
    RiskRunQuery,
    RiskSession,
    RiskSessionChunk,
    RiskValidateQuery,
    TimeSeriesManifest,
    TimeSeriesPanelInput,
    TimeSeriesSession,
    TimeSeriesSessionChunk,
    parse_risk,
    utc_key,
)
from atlas_terminal.risk_time_series import TimeSeriesPanel, fit_time_series
from atlas_terminal.util import Database, canonical, digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_objects (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, body BLOB NOT NULL,
    depth INTEGER NOT NULL, synthetic INTEGER NOT NULL
);
"""
MAX_STATE_BYTES = 128 * 1024 * 1024
MAX_STATE_OBJECTS = 16384
MAX_MODELS = 128
MAX_CHAIN_CHUNKS = 1024


def risk_catalog() -> dict[str, Any]:
    return {
        "methodology_version": METHODOLOGY,
        "methods": {
            "cross_sectional": {
                "methodology_version": METHODOLOGY,
                "inputs": "lagged fundamentals, market caps and a broad stock estimation universe",
                "estimates": "factor returns for each cross section",
                "default_styles": ["size", "value", "profitability", "leverage"],
                "min_assets": 30,
            },
            "time_series": {
                "methodology_version": TIME_SERIES_METHODOLOGY,
                "inputs": "aligned asset returns and supplied observed factor returns",
                "estimates": "asset betas and diagnostic intercepts across time",
                "min_assets": 1,
                "max_factors": 12,
            },
        },
        "scope": "Native daily fundamental cross-sectional and observed-factor time-series risk",
        "actions": {
            "put": "Append a manifest, fundamentals or sessions chunk; retain returned chunk_id.",
            "fit": "Fit from head_id and an explicit as_of; returns an immutable model_id.",
            "get": "Read summary, factor covariance, selected exposures or estimation history.",
            "analyze": "NAV weights; volatility, contributions, active risk and shocks.",
            "validate": "20+ strictly subsequent sessions versus fixed model and EWMA baseline.",
            "connections": "List native inputs without opening datasets or probing APIs.",
            "run": "Read, validate, fit and analyze a named input; preview performs no data reads.",
        },
        "input_schema": RiskQuery.model_json_schema(),
        "limits": {
            "request_bytes": MAX_RISK_BYTES,
            "assets_by_method": {"cross_sectional": [30, 200], "time_series": [1, 200]},
            "sessions": 640,
            "lookback": [60, 252],
            "chunks_per_chain": MAX_CHAIN_CHUNKS,
            "state_bytes": MAX_STATE_BYTES,
            "models": MAX_MODELS,
        },
        "arithmetic": "Decimal, 42 significant digits; numeric inputs and outputs are strings",
        "timing": "Regression uses session-start information; forecast uses inputs known at as_of.",
        "styles": {
            "size": "ln(lagged market capitalization)",
            "value": "book equity / lagged market capitalization",
            "profitability": "trailing twelve-month net income / total assets",
            "leverage": "total debt / total assets",
            "momentum": "Compound 252 prior available sessions excluding the most recent 21",
            "beta": "126-session OLS beta to the equal-weight supplied-universe return proxy",
            "residual_volatility": "126-session residual standard deviation from the same OLS fit",
        },
        "industry": "Independent public two-digit SIC division aggregation; no GICS mapping",
        "limitations": LIMITATIONS,
    }


class RiskService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: Database | None = None
        self.connections = RiskConnections()

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = Database(self.path, SCHEMA)
        return self._db

    @staticmethod
    def _verified(row: Any) -> dict[str, Any]:
        body = bytes(row["body"])
        if digest(body) != row["id"]:
            raise AtlasError(
                "RISK_STATE_CORRUPT", "Saved risk object failed its content hash.", status=500
            )
        try:
            value = json.loads(body)
        except (ValueError, TypeError):
            raise AtlasError(
                "RISK_STATE_CORRUPT", "Saved risk object is invalid.", status=500
            ) from None
        if not isinstance(value, dict):
            raise AtlasError("RISK_STATE_CORRUPT", "Saved risk object is invalid.", status=500)
        return value

    def _read(self, object_id: str, kind: str | None = None) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM risk_objects WHERE id=?", (object_id,)).fetchone()
        if row is None or (kind is not None and row["kind"] != kind):
            raise AtlasError(
                "RISK_OBJECT_NOT_FOUND", "Use an object ID from this private state.", status=404
            )
        return self._verified(row)

    def _save(self, value: dict[str, Any], kind: str, synthetic: bool) -> str:
        body = canonical(value)
        object_id = digest(body)
        with self.db.connect(write=True) as conn:
            existing = conn.execute(
                "SELECT * FROM risk_objects WHERE id=?", (object_id,)
            ).fetchone()
            if existing is not None:
                self._verified(existing)
                if existing["kind"] != kind:
                    raise AtlasError(
                        "RISK_STATE_CORRUPT", "Saved risk object has a different kind."
                    )
                return object_id
            depth = 0
            if "previous" in value:
                previous = conn.execute(
                    "SELECT * FROM risk_objects WHERE id=? AND kind='chunk'", (value["previous"],)
                ).fetchone()
                if previous is None:
                    raise AtlasError(
                        "RISK_OBJECT_NOT_FOUND", "Append to a saved input chunk.", status=404
                    )
                self._verified(previous)
                depth, synthetic = previous["depth"] + 1, bool(previous["synthetic"])
            if depth >= MAX_CHAIN_CHUNKS:
                raise AtlasError("INVALID_INPUT", "Risk chain exceeds 1024 chunks.")
            usage = conn.execute(
                "SELECT count(*) AS count, coalesce(sum(length(body)),0) AS size, "
                "coalesce(sum(kind='model'),0) AS models FROM risk_objects"
            ).fetchone()
            if (
                usage["size"] + len(body) > MAX_STATE_BYTES
                or usage["count"] >= MAX_STATE_OBJECTS
                or (kind == "model" and usage["models"] >= MAX_MODELS)
            ):
                raise AtlasError(
                    "CACHE_FULL", "Private risk state quota reached; contact the operator."
                )
            conn.execute(
                "INSERT INTO risk_objects VALUES(?,?,?,?,?)",
                (object_id, kind, body, depth, int(synthetic)),
            )
        return object_id

    def panel(self, head_id: str) -> RiskPanel | TimeSeriesPanel:
        fundamentals: list[RiskFundamental] = []
        sessions: list[RiskSession] = []
        time_series_sessions: list[TimeSeriesSession] = []
        visited: set[str] = set()
        with self.db.connect() as conn:
            current = head_id
            while True:
                if current in visited or len(visited) >= MAX_CHAIN_CHUNKS:
                    raise AtlasError("INVALID_INPUT", "Risk input chain is cyclic or too deep.")
                visited.add(current)
                row = conn.execute(
                    "SELECT * FROM risk_objects WHERE id=? AND kind='chunk'", (current,)
                ).fetchone()
                if row is None:
                    raise AtlasError(
                        "RISK_OBJECT_NOT_FOUND", "Risk input chain is incomplete.", status=404
                    )
                value = self._verified(row)
                query = parse_risk({"action": "put", "chunk": value}).root
                assert isinstance(query, RiskPutQuery)
                chunk = query.chunk
                if isinstance(chunk, RiskManifest):
                    if time_series_sessions:
                        raise AtlasError(
                            "INVALID_INPUT", "Do not mix panel methods in an input chain."
                        )
                    panel = RiskPanel(chunk, fundamentals, sessions)
                    panel.validate()
                    return panel
                if isinstance(chunk, TimeSeriesManifest):
                    if fundamentals or sessions:
                        raise AtlasError(
                            "INVALID_INPUT", "Do not mix panel methods in an input chain."
                        )
                    time_panel = TimeSeriesPanel(chunk, time_series_sessions)
                    time_panel.validate()
                    return time_panel
                if isinstance(chunk, RiskFundamentalChunk):
                    fundamentals.extend(chunk.records)
                elif isinstance(chunk, RiskSessionChunk):
                    sessions.extend(chunk.records)
                elif isinstance(chunk, TimeSeriesSessionChunk):
                    time_series_sessions.extend(chunk.records)
                if len(fundamentals) > 10000 or len(sessions) + len(time_series_sessions) > 640:
                    raise AtlasError("INVALID_INPUT", "Risk panel exceeds observation bounds.")
                current = chunk.previous

    @staticmethod
    def parse_panel(value: dict[str, Any]) -> RiskPanelInput | TimeSeriesPanelInput:
        """Local bulk convenience; MCP/HTTP callers append bounded chunks instead."""
        from pydantic import ValidationError

        try:
            if not isinstance(value, dict) or not isinstance(value.get("manifest"), dict):
                raise ValueError("Panel and manifest objects required")
            body = canonical(value)
            if len(body) > 32 * 1024 * 1024:
                raise ValueError("Panel too large")
            with localcontext(Context(prec=42)):
                if value.get("manifest", {}).get("kind") == "time_series_manifest":
                    time_parsed = TimeSeriesPanelInput.model_validate_json(body)
                    TimeSeriesPanel(time_parsed.manifest, time_parsed.sessions).validate()
                    parsed: RiskPanelInput | TimeSeriesPanelInput = time_parsed
                else:
                    parsed = RiskPanelInput.model_validate_json(body)
                    RiskPanel(parsed.manifest, parsed.fundamentals, parsed.sessions).validate()
        except (ValidationError, ValueError, TypeError):
            raise AtlasError("INVALID_INPUT", "Invalid risk panel or input above 32 MiB.") from None
        return parsed

    def import_panel(self, value: dict[str, Any]) -> dict[str, Any]:
        parsed = self.parse_panel(value)
        result = self.execute({"action": "put", "chunk": parsed.manifest.model_dump(mode="json")})
        head = result["data"]["chunk_id"]
        fundamentals = parsed.fundamentals if isinstance(parsed, RiskPanelInput) else []
        for i in range(0, len(fundamentals), 40):
            result = self.execute(
                {
                    "action": "put",
                    "chunk": {
                        "kind": "fundamentals",
                        "previous": head,
                        "records": [r.model_dump(mode="json") for r in fundamentals[i : i + 40]],
                    },
                }
            )
            head = result["data"]["chunk_id"]
        for session in sorted(parsed.sessions, key=lambda s: s.end_at):
            result = self.execute(
                {
                    "action": "put",
                    "chunk": {
                        "kind": "sessions"
                        if isinstance(parsed, RiskPanelInput)
                        else "time_series_sessions",
                        "previous": head,
                        "records": [session.model_dump(mode="json")],
                    },
                }
            )
            head = result["data"]["chunk_id"]
        return {
            "head_id": head,
            "is_synthetic": parsed.manifest.is_synthetic,
            "assets": len(parsed.manifest.universe),
            "sessions": len(parsed.sessions),
        }

    def execute(self, value: RiskQuery | dict[str, Any]) -> dict[str, Any]:
        query = parse_risk(value).root
        with localcontext(Context(prec=42)):
            data: dict[str, Any]
            synthetic: bool | None = None
            methodology = None
            if isinstance(query, RiskCatalogQuery):
                data = risk_catalog()
            elif isinstance(query, RiskConnectionsQuery):
                data = self.connections.catalog()
            elif isinstance(query, RiskRunQuery):
                data, synthetic, methodology = self._run(query)
            elif isinstance(query, RiskPutQuery):
                chunk = query.chunk.model_dump(mode="json")
                if isinstance(query.chunk, (RiskManifest, TimeSeriesManifest)):
                    synthetic = query.chunk.is_synthetic
                else:
                    # Resolve the root assertion, rather than inheriting fixture-provider status.
                    with self.db.connect() as conn:
                        row = conn.execute(
                            "SELECT synthetic FROM risk_objects WHERE id=? AND kind='chunk'",
                            (query.chunk.previous,),
                        ).fetchone()
                    if row is None:
                        raise AtlasError(
                            "RISK_OBJECT_NOT_FOUND", "Append to a saved input chunk.", status=404
                        )
                    synthetic = bool(row["synthetic"])
                data = {"chunk_id": self._save(chunk, "chunk", synthetic), "kind": chunk["kind"]}
            elif isinstance(query, RiskFitQuery):
                data = self._fit(query)
                synthetic = data["is_synthetic"]
                methodology = data["methodology_version"]
            else:
                artifact = self._read(query.model_id, "model")
                synthetic = bool(artifact["summary"]["is_synthetic"])
                methodology = artifact["summary"]["methodology_version"]
                if isinstance(query, RiskAnalyzeQuery):
                    data = analyze_model(artifact, query)
                elif isinstance(query, RiskGetQuery):
                    data = self._get(artifact, query)
                else:
                    panel = self.panel(query.head_id)
                    synthetic = synthetic or panel.manifest.is_synthetic
                    data = self._validate(artifact, panel, query)
            return RiskResult(
                action=query.action,
                is_synthetic=synthetic,
                data=data,
                methodology_version=methodology,
            ).model_dump(mode="json")

    def _fit(self, query: RiskFitQuery, ingestion_id: str | None = None) -> dict[str, Any]:
        panel = self.panel(query.head_id)
        artifact = (
            fit_model(panel, query)
            if isinstance(panel, RiskPanel)
            else fit_time_series(panel, query)
        )
        if ingestion_id is not None:
            artifact["summary"]["input_evidence_id"] = ingestion_id
        model_id = self._save(artifact, "model", panel.manifest.is_synthetic)
        return artifact["summary"] | {"model_id": model_id}

    def _run(self, query: RiskRunQuery) -> tuple[dict[str, Any], bool | None, Any]:
        provider = self.connections.get(query.connection_id)
        if provider.method != query.fit.method:
            raise AtlasError("INVALID_INPUT", "Connection and requested estimation methods differ.")
        plan = {
            "connection_id": query.connection_id,
            "connector_kind": provider.kind,
            "method": provider.method,
            "uses_network": provider.uses_network,
            "as_of": utc_key(query.fit.as_of),
            "lookback": query.fit.lookback,
            "estimation_universe": "from_connection_not_from_portfolio",
            "requested_portfolio_assets": [p.asset_id for p in query.positions],
            "stages": ["read", "normalize", "validate", "import", "fit", "analyze"],
        }
        if query.preview:
            return {"mode": "preview", "plan": plan, "data_reads_executed": False}, None, None
        if provider.uses_network and not query.allow_network:
            raise AtlasError(
                "CONFIG_REQUIRED", "This registered provider requires allow_network=true."
            )
        try:
            loaded = provider.read()
        except AtlasError:
            raise
        except Exception:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Configured native provider failed to read data.", status=502
            ) from None
        if not isinstance(loaded, RiskConnectionRead):
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Provider returned an invalid connection result.", status=502
            )
        parsed = self.parse_panel(loaded.panel)
        expected_method = "cross_sectional" if isinstance(parsed, RiskPanelInput) else "time_series"
        if provider.method != expected_method:
            raise AtlasError("INVALID_INPUT", "Connection returned the wrong panel method.")
        universe = {a.asset_id for a in parsed.manifest.universe}
        if {p.asset_id for p in query.positions + query.benchmark} - universe:
            raise AtlasError(
                "INVALID_INPUT", "Portfolio assets are outside the connection universe."
            )
        imported = self.import_panel(parsed.model_dump(mode="json"))
        evidence_id = self._save(
            {
                "connection_id": query.connection_id,
                "connector_kind": provider.kind,
                "input_head_id": imported["head_id"],
                "documents": loaded.documents,
                "snapshot_atomic": loaded.snapshot_atomic,
                "normalization": loaded.normalization,
                "upstream_rights_and_availability": "caller_asserted_unverified",
            },
            "ingestion",
            parsed.manifest.is_synthetic,
        )
        fitted = self._fit(
            RiskFitQuery(action="fit", head_id=imported["head_id"], **query.fit.model_dump()),
            evidence_id,
        )
        artifact = self._read(fitted["model_id"], "model")
        analyzed = analyze_model(
            artifact,
            RiskAnalyzeQuery(
                action="analyze",
                model_id=fitted["model_id"],
                positions=query.positions,
                benchmark=query.benchmark,
                shocks=query.shocks,
            ),
        )
        return (
            {
                "mode": "executed",
                "plan": plan,
                "model": fitted,
                "analysis": analyzed,
                "input_evidence": self._read(evidence_id, "ingestion"),
            },
            parsed.manifest.is_synthetic,
            fitted["methodology_version"],
        )

    def _get(self, artifact: dict[str, Any], query: RiskGetQuery) -> dict[str, Any]:
        if query.view == "summary":
            return artifact["summary"] | {"model_id": query.model_id}
        if query.view == "factors":
            return {
                "model_id": query.model_id,
                "factor_order": artifact["summary"]["factor_order"],
                "daily_factor_covariance": artifact["factor_covariance"],
                "transformations": artifact["transformations"],
            }
        if query.view == "history":
            return {
                "model_id": query.model_id,
                "factor_order": artifact["summary"]["factor_order"],
                "sessions": artifact["history"],
                "factor_return_origin": artifact["summary"].get("factor_return_origin"),
                "asset_regressions": artifact.get("regression_diagnostics"),
            }
        if query.view == "inputs":
            evidence_id = artifact["summary"].get("input_evidence_id")
            return {
                "model_id": query.model_id,
                "input_head_id": artifact["summary"].get(
                    "input_head_id", artifact["fit_query"]["head_id"]
                ),
                "sources": artifact["summary"]["sources"],
                "input_evidence_id": evidence_id,
                "input_evidence": None
                if evidence_id is None
                else self._read(evidence_id, "ingestion"),
            }
        if set(query.asset_ids) - set(artifact["asset_ids"]):
            raise AtlasError("INVALID_INPUT", "Selected assets are outside the model universe.")
        return {
            "model_id": query.model_id,
            "factor_order": artifact["summary"]["factor_order"],
            "assets": [
                {
                    "asset_id": asset,
                    "exposures": artifact["exposures"][i],
                    "daily_specific_variance": artifact["specific_variances"][i],
                    "vintage": artifact["vintages"][i],
                    **(
                        {
                            "alpha": artifact["alphas"][i],
                            "regression_diagnostics": artifact["regression_diagnostics"][i],
                        }
                        if "alphas" in artifact
                        else {}
                    ),
                }
                for i, asset in enumerate(artifact["asset_ids"])
                if asset in query.asset_ids
            ],
        }

    @staticmethod
    def _validate(
        artifact: dict[str, Any], panel: RiskPanel | TimeSeriesPanel, query: RiskValidateQuery
    ) -> dict[str, Any]:
        if panel.asset_ids != artifact["asset_ids"]:
            raise AtlasError("INVALID_INPUT", "Validation must use the same asset universe.")
        method = artifact["summary"].get("method", "cross_sectional")
        if (method == "time_series") != isinstance(panel, TimeSeriesPanel):
            raise AtlasError("INVALID_INPUT", "Validation must use the saved estimation method.")
        if isinstance(panel, TimeSeriesPanel) and (
            panel.manifest.currency != artifact["summary"]["currency"]
            or panel.manifest.factor_convention != artifact["summary"]["modeled_return"]
            or sorted(panel.manifest.factors) != artifact["summary"]["factor_order"]
        ):
            raise AtlasError(
                "INVALID_INPUT", "Validation currency, factors and return conventions must match."
            )
        as_of = datetime.fromisoformat(artifact["summary"]["as_of"])
        subsequent = [s for s in panel.sessions if s.start_at >= as_of]
        if len(subsequent) < 20:
            raise AtlasError(
                "RISK_INPUT_INCOMPLETE", "Supply at least 20 strictly subsequent sessions."
            )
        weights = portfolio_weights(artifact, query.positions)
        actual = []
        for session in subsequent:
            rf = (
                Decimal(session.risk_free_return or "0")
                if isinstance(session, TimeSeriesSession)
                else Decimal(0)
            )
            values = {r.asset_id: Decimal(r.total_return) - rf for r in session.observations}
            actual.append([dot([values[a] for a in artifact["asset_ids"]], weights)])
        realized = covariance(actual, [ONE / len(actual)] * len(actual))[0][0]
        historical = [
            [dot([Decimal(r) for r in row], weights)] for row in artifact["training_returns"]
        ]
        baseline = covariance(
            historical, ewma_weights(len(historical), Decimal(artifact["fit_query"]["decay"]))
        )[0][0]
        forecast = portfolio_risk(artifact, weights)
        predicted = Decimal(forecast["daily_variance"])
        return {
            "model_id": query.model_id,
            "as_of": utc_key(as_of),
            "validation_head_id": query.head_id,
            "currency": artifact["summary"]["currency"],
            "modeled_return": artifact["summary"].get("modeled_return", "total_return"),
            "sessions": len(subsequent),
            "start_at": utc_key(subsequent[0].start_at),
            "end_at": utc_key(subsequent[-1].end_at),
            "predicted_daily_variance": text(predicted),
            "realized_daily_sample_variance": text(realized),
            "historical_portfolio_ewma_baseline_variance": text(baseline),
            "realized_to_model_variance": text(realized / predicted) if predicted else None,
            "realized_to_baseline_variance": text(realized / baseline) if baseline else None,
            "limitations": artifact["summary"]["limitations"]
            + [
                "A single fixed-exposure holdout is descriptive, not a rolling calibration study.",
                "No automatic pass/fail, alpha claim or forecast accuracy certification.",
            ],
        }
