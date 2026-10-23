"""Native operator-configured connectors; remote queries select IDs, never paths or SQL."""

import csv
import io
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_math import text
from atlas_terminal.risk_models import Identifier, Number, RiskBase, RiskMethod, timestamp, utc_key
from atlas_terminal.util import canonical, digest

MAX_PANEL_BYTES = 32 * 1024 * 1024
TABLE_LIMITS = {"sessions": 640, "returns": 128200, "fundamentals": 10000, "factors": 7680}
NUMBER = TypeAdapter(Number)
Table = Literal["sessions", "returns", "fundamentals", "factors"]
ColumnName = Annotated[str, Field(min_length=1, max_length=96)]
SourceAsset = Annotated[str, Field(min_length=1, max_length=64)]


class RiskConnectionSpec(RiskBase):
    connection_id: Identifier
    kind: Literal["json", "csv", "sqlite"]
    method: RiskMethod
    path: str = Field(min_length=1, max_length=4096)
    columns: dict[Table, dict[Identifier, ColumnName]] = Field(default_factory=dict)
    tables: dict[Table, Identifier] = Field(default_factory=dict)
    asset_map: dict[SourceAsset, Identifier] = Field(default_factory=dict, max_length=200)
    return_input: Literal["returns", "adjusted_prices"] = "returns"

    @model_validator(mode="after")
    def explicit_mapping(self) -> Self:
        if not Path(self.path).is_absolute():
            raise ValueError("Configure an absolute local path")
        if self.kind == "json" and (
            self.columns or self.tables or self.asset_map or self.return_input != "returns"
        ):
            raise ValueError("JSON already uses canonical field and asset names")
        if self.kind != "sqlite" and self.tables:
            raise ValueError("Table mapping applies only to SQLite")
        if len(self.asset_map.values()) != len(set(self.asset_map.values())):
            raise ValueError("Asset aliases must not collapse distinct supplied identifiers")
        for mapping in self.columns.values():
            if len(mapping.values()) != len(set(mapping.values())):
                raise ValueError("Column mappings must be one-to-one")
        return self


class RiskConnectionConfig(RiskBase):
    schema_version: Literal["0.1.0"]
    connections: list[RiskConnectionSpec] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_connections(self) -> Self:
        ids = [row.connection_id for row in self.connections]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate connection IDs")
        return self


@dataclass
class RiskConnectionRead:
    panel: dict[str, Any]
    documents: list[dict[str, Any]]
    snapshot_atomic: bool
    normalization: dict[str, Any] = field(default_factory=dict)


class RiskConnector(Protocol):
    """Trusted Python extensions can implement API access without exposing URLs as tool inputs."""

    @property
    def method(self) -> RiskMethod: ...

    @property
    def kind(self) -> str: ...

    @property
    def uses_network(self) -> bool: ...

    def read(self) -> RiskConnectionRead: ...


def bounded_file(path: Path, remaining: int) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(remaining + 1)
        if len(data) > remaining:
            raise AtlasError("INVALID_INPUT", "Connection input exceeds 32 MiB.")
        return data
    except OSError:
        raise AtlasError(
            "CONFIG_REQUIRED", "Configured input is unavailable to this operator."
        ) from None


def document(label: str, body: bytes, encoding: str) -> dict[str, Any]:
    return {"label": label, "sha256": digest(body), "bytes": len(body), "hash_encoding": encoding}


def session_key(value: Any) -> str:
    try:
        parsed = timestamp(value)
        if parsed.utcoffset() is None:
            raise ValueError("Timezone required")
        return utc_key(parsed)
    except (TypeError, ValueError, OverflowError):
        raise AtlasError(
            "INVALID_INPUT", "Use timezone-aware session timestamps in connection tables."
        ) from None


def mapped_rows(
    spec: RiskConnectionSpec, table: Table, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    mapping = spec.columns.get(table)
    result = []
    for row in rows:
        if mapping:
            if set(mapping.values()) - set(row):
                raise AtlasError("INVALID_INPUT", "A configured source column is absent.")
            row = {
                canonical_name: row[source_name] for canonical_name, source_name in mapping.items()
            }
        else:
            row = dict(row)
        if "asset_id" in row:
            row["asset_id"] = spec.asset_map.get(row["asset_id"], row["asset_id"])
        result.append(row)
    return result


def assemble_panel(
    spec: RiskConnectionSpec, manifest: dict[str, Any], tables: dict[Table, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Join exact session instants and explicit asset mappings; never align/fill by row position."""
    sessions: dict[str, dict[str, Any]] = {}
    for raw in mapped_rows(spec, "sessions", tables["sessions"]):
        key = session_key(raw.get("end_at"))
        if key in sessions or "observations" in raw or "factor_returns" in raw:
            raise AtlasError(
                "INVALID_INPUT", "Duplicate session or nested data in relational input."
            )
        if spec.method == "time_series":
            if raw.get("risk_free_return") == "":
                raw.pop("risk_free_return")
            raw["factor_returns"] = {}
        raw["observations"] = []
        sessions[key] = raw
    returns = mapped_rows(spec, "returns", tables["returns"])
    if spec.return_input == "adjusted_prices":
        returns = price_returns(spec, sessions, returns)
    for row in returns:
        key = session_key(row.pop("end_at", None))
        if key not in sessions:
            raise AtlasError("INVALID_INPUT", "A return has no matching session.")
        sessions[key]["observations"].append(row)
    if spec.method == "time_series":
        for row in mapped_rows(spec, "factors", tables["factors"]):
            if set(row) != {"end_at", "factor_id", "return"}:
                raise AtlasError(
                    "INVALID_INPUT", "Use end_at, factor_id and return in the factor table."
                )
            key = session_key(row["end_at"])
            if key not in sessions or row["factor_id"] in sessions[key]["factor_returns"]:
                raise AtlasError("INVALID_INPUT", "Unknown session or duplicate factor return.")
            sessions[key]["factor_returns"][row["factor_id"]] = row["return"]
    result = {"manifest": manifest, "sessions": list(sessions.values())}
    if spec.method == "cross_sectional":
        result["fundamentals"] = mapped_rows(spec, "fundamentals", tables["fundamentals"])
    return result


def price_returns(
    spec: RiskConnectionSpec,
    sessions: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Explicit adjusted-close returns; market cap uses the separate unadjusted basis."""
    prices: dict[tuple[str, Any], dict[str, Any]] = {}
    assets_by_end: dict[str, set[str]] = {}
    allowed = {"end_at", "asset_id", "available_at", "adjusted_close"}
    if spec.method == "cross_sectional":
        allowed |= {"close", "shares_outstanding", "market_cap"}
    for row in rows:
        if set(row) - allowed or not {
            "end_at",
            "asset_id",
            "available_at",
            "adjusted_close",
        } <= set(row):
            raise AtlasError(
                "INVALID_INPUT",
                "Use declared canonical price columns or an explicit column mapping.",
            )
        key = (session_key(row["end_at"]), row["asset_id"])
        if key in prices:
            raise AtlasError("INVALID_INPUT", "Duplicate asset price at a session instant.")
        if timestamp(row["available_at"]).utcoffset() is None:
            raise ValueError("Price availability timezone required")
        if timestamp(row["available_at"]) < timestamp(row["end_at"]):
            raise ValueError("Price availability predates close")
        adjusted = Decimal(NUMBER.validate_python(row["adjusted_close"], strict=True))
        if adjusted <= 0:
            raise ValueError("Positive adjusted prices required")
        prices[key] = row
        assets_by_end.setdefault(key[0], set()).add(key[1])
    used: set[tuple[str, str]] = set()
    output = []
    with localcontext(Context(prec=80)):
        for end, session in sessions.items():
            start = session_key(session.get("start_at"))
            asset_ids = assets_by_end.get(end, set())
            if not asset_ids:
                raise AtlasError("RISK_INPUT_INCOMPLETE", "A session has no closing prices.")
            available = timestamp(session["available_at"])
            for asset in sorted(asset_ids):
                before, after = prices.get((start, asset)), prices[(end, asset)]
                if before is None:
                    raise AtlasError(
                        "RISK_INPUT_INCOMPLETE",
                        "Supply each asset's exact start and end adjusted prices.",
                    )
                used.update(((start, asset), (end, asset)))
                available = max(
                    available, timestamp(before["available_at"]), timestamp(after["available_at"])
                )
                value = (
                    Decimal(after["adjusted_close"]) / Decimal(before["adjusted_close"]) - 1
                ).quantize(Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
                result = {"end_at": end, "asset_id": asset, "total_return": text(value)}
                if spec.method == "cross_sectional":
                    if "market_cap" in after:
                        if "close" in after or "shares_outstanding" in after:
                            raise ValueError("Use reported cap or raw close and shares, not both")
                        cap = Decimal(NUMBER.validate_python(after["market_cap"], strict=True))
                    else:
                        close = Decimal(NUMBER.validate_python(after.get("close"), strict=True))
                        shares = Decimal(
                            NUMBER.validate_python(after.get("shares_outstanding"), strict=True)
                        )
                        if close <= 0 or shares <= 0 or shares != shares.to_integral_value():
                            raise ValueError("Use a positive raw close and whole reported shares")
                        cap = close * shares
                    if cap <= 0:
                        raise ValueError("Positive market capitalization required")
                    result["market_cap"] = NUMBER.validate_python(text(cap), strict=True)
                output.append(result)
            # A derived return cannot be available before either source price.
            session["available_at"] = utc_key(available)
    if used != set(prices):
        raise AtlasError("INVALID_INPUT", "Price rows include unmatched session instants.")
    return output


class NativeRiskConnector:
    uses_network = False

    def __init__(self, spec: RiskConnectionSpec) -> None:
        self.spec = spec

    @property
    def method(self) -> RiskMethod:
        return self.spec.method

    @property
    def kind(self) -> str:
        return self.spec.kind

    def read(self) -> RiskConnectionRead:
        try:
            if self.kind == "json":
                body = bounded_file(Path(self.spec.path), MAX_PANEL_BYTES)
                panel = json.loads(body)
                if not isinstance(panel, dict):
                    raise ValueError("Panel object required")
                result = RiskConnectionRead(
                    panel, [document("panel", body, "original_bytes")], False
                )
            elif self.kind == "sqlite":
                result = self._sqlite()
            else:
                result = self._csv()
            result.normalization = {
                "contract_version": "0.1.0",
                "return_input": self.spec.return_input,
                "mapping_sha256": digest(
                    canonical(
                        {
                            "columns": self.spec.columns,
                            "tables": self.spec.tables,
                            "asset_map": self.spec.asset_map,
                        }
                    )
                ),
                "return_formula": (
                    "adjusted_close_end / adjusted_close_start - 1; 18 decimal half-even"
                    if self.spec.return_input == "adjusted_prices"
                    else "supplied decimal simple returns"
                ),
                "capitalization_basis": (
                    "reported cap or unadjusted close times whole reported shares"
                    if self.method == "cross_sectional"
                    else "not_required"
                ),
            }
            return result
        except (ValueError, TypeError, KeyError, csv.Error, UnicodeError):
            raise AtlasError(
                "INVALID_INPUT", "Configured input does not match the native panel contract."
            ) from None

    def _csv(self) -> RiskConnectionRead:
        directory = Path(self.spec.path)
        body = bounded_file(directory / "manifest.json", MAX_PANEL_BYTES)
        manifest = json.loads(body)
        if not isinstance(manifest, dict):
            raise ValueError("Manifest object required")
        documents = [document("manifest", body, "original_bytes")]
        remaining = MAX_PANEL_BYTES - len(body)
        tables: dict[Table, list[dict[str, Any]]] = {}
        names: list[Table] = ["sessions", "returns"]
        names.append("fundamentals" if self.method == "cross_sectional" else "factors")
        for name in names:
            filename = (
                "prices"
                if name == "returns" and self.spec.return_input == "adjusted_prices"
                else name
            )
            body = bounded_file(directory / f"{filename}.csv", remaining)
            remaining -= len(body)
            reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True)
            fields = reader.fieldnames
            if not fields or len(fields) != len(set(fields)) or any(not f for f in fields):
                raise ValueError("Unique CSV headers required")
            rows = []
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("Incomplete CSV row")
                rows.append(row)
                if len(rows) > TABLE_LIMITS[name]:
                    raise ValueError("Too many rows")
            tables[name] = rows
            documents.append(document(name, body, "original_bytes"))
        return RiskConnectionRead(assemble_panel(self.spec, manifest, tables), documents, False)

    def _sqlite(self) -> RiskConnectionRead:
        conn = None
        deadline = time.monotonic() + 10
        try:
            conn = sqlite3.connect(Path(self.spec.path).as_uri() + "?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_PANEL_BYTES)
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            manifest_rows = conn.execute('SELECT body FROM "manifest" LIMIT 2').fetchall()
            if len(manifest_rows) != 1 or not isinstance(manifest_rows[0][0], str):
                raise ValueError("One manifest JSON row required")
            manifest = json.loads(manifest_rows[0][0])
            if not isinstance(manifest, dict):
                raise ValueError("Manifest object required")
            documents = [document("manifest", canonical(manifest), "canonical_rows")]
            remaining = MAX_PANEL_BYTES - len(canonical(manifest))
            tables: dict[Table, list[dict[str, Any]]] = {}
            names: list[Table] = ["sessions", "returns"]
            names.append("fundamentals" if self.method == "cross_sectional" else "factors")
            for name in names:
                default_table = (
                    "prices"
                    if name == "returns" and self.spec.return_input == "adjusted_prices"
                    else name
                )
                table = self.spec.tables.get(name, default_table)
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,47}", table):
                    raise ValueError("Invalid configured table name")
                rows: list[dict[str, Any]] = []
                for row in conn.execute(
                    f'SELECT * FROM "{table}" LIMIT ?', (TABLE_LIMITS[name] + 1,)
                ):
                    value = dict(row)
                    remaining -= len(canonical(value))
                    if remaining < 0 or len(rows) >= TABLE_LIMITS[name]:
                        raise ValueError("Too much data")
                    rows.append(value)
                # Logical rows are hashed in deterministic order, not the mutable database file.
                rows.sort(key=canonical)
                documents.append(document(name, canonical(rows), "canonical_rows"))
                tables[name] = rows
            return RiskConnectionRead(assemble_panel(self.spec, manifest, tables), documents, True)
        except (OSError, sqlite3.Error):
            raise AtlasError(
                "CONFIG_REQUIRED", "Configured read-only database is unavailable or incompatible."
            ) from None
        finally:
            if conn is not None:
                conn.close()


class RiskConnections:
    def __init__(self) -> None:
        self._providers: dict[str, RiskConnector] = {}
        self._configured = False

    def register(self, connection_id: str, provider: RiskConnector) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}", connection_id):
            raise AtlasError("INVALID_INPUT", "Use a bounded connection identifier.")
        if connection_id in self._providers or len(self._providers) >= 32:
            raise AtlasError(
                "INVALID_INPUT", "Connection ID exists or connection quota is reached."
            )
        if provider.method not in {"cross_sectional", "time_series"}:
            raise AtlasError("INVALID_INPUT", "Connection declares an unsupported method.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", provider.kind):
            raise AtlasError("INVALID_INPUT", "Use a bounded provider kind label.")
        if type(provider.uses_network) is not bool:
            raise AtlasError("INVALID_INPUT", "Provider must declare whether it uses the network.")
        self._providers[connection_id] = provider

    def _configure(self) -> None:
        if self._configured:
            return
        configured = os.environ.get("ATLAS_RISK_CONNECTIONS_FILE")
        if configured:
            try:
                path = Path(configured)
                if os.name != "nt" and path.stat().st_mode & 0o077:
                    raise ValueError("Private permissions required")
                config = RiskConnectionConfig.model_validate_json(bounded_file(path, 65536))
                if set(self._providers) & {c.connection_id for c in config.connections}:
                    raise ValueError("Duplicate connection IDs")
                if len(self._providers) + len(config.connections) > 32:
                    raise ValueError("Connection quota is reached")
                for spec in config.connections:
                    self.register(spec.connection_id, NativeRiskConnector(spec))
            except (OSError, ValidationError, ValueError):
                raise AtlasError(
                    "CONFIG_REQUIRED", "Configure a private valid risk connections file."
                ) from None
        self._configured = True

    def get(self, connection_id: str) -> RiskConnector:
        self._configure()
        provider = self._providers.get(connection_id)
        if provider is None:
            raise AtlasError(
                "CONFIG_REQUIRED", "Configure the selected native risk connection first."
            )
        return provider

    def catalog(self) -> dict[str, Any]:
        self._configure()
        return {
            "connections": [
                {
                    "connection_id": name,
                    "kind": provider.kind,
                    "method": provider.method,
                    "uses_network": provider.uses_network,
                    "readiness": "configured_not_probed",
                }
                for name, provider in sorted(self._providers.items())
            ],
            "native_formats": ["json", "csv", "sqlite"],
            "path_or_sql_arguments_allowed": False,
            "network_probe_executed": False,
        }
