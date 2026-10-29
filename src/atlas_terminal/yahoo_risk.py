"""Operator-configured Yahoo prices to the existing observed-factor risk model."""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, model_validator

from atlas_terminal.equity_sources import (
    require_yahoo_enabled,
    yahoo_profile,
    yahoo_symbol,
    yahoo_timezone,
    yahoo_url,
)
from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_models import RiskBase, RiskMethod

if TYPE_CHECKING:
    from atlas_terminal.risk_connections import RiskConnectionRead


class YahooRiskSpec(RiskBase):
    connection_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
    kind: Literal["yahoo_finance"]
    method: Literal["time_series"] = "time_series"
    symbols: list[str] = Field(min_length=1, max_length=9)
    factor_symbol: str
    start_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    end_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    personal_use_acknowledged: Literal[True]

    @model_validator(mode="after")
    def valid_selection(self) -> "YahooRiskSpec":
        if len(set(self.symbols)) != len(self.symbols) or self.factor_symbol in self.symbols:
            raise ValueError("Use unique assets and a separate observed ETF factor")
        selected = [*self.symbols, self.factor_symbol]
        profile = yahoo_profile(selected[0])
        for symbol in selected:
            yahoo_symbol(symbol)
            other = yahoo_profile(symbol)
            if (
                other["asset_class"] != "security"
                or other["currency"] != profile["currency"]
                or other["timezone"] != profile["timezone"]
            ):
                raise ValueError("Use securities from one supported currency and market")
            yahoo_url(symbol, self.start_date, self.end_date)
        return self


def panel_from_prices(spec: YahooRiskSpec, results: list[dict[str, Any]]) -> "RiskConnectionRead":
    from atlas_terminal.risk_connections import RiskConnectionRead
    from atlas_terminal.risk_models import utc_key
    from atlas_terminal.util import canonical, digest

    symbols = [*spec.symbols, spec.factor_symbol]
    profile = yahoo_profile(symbols[0])
    timezone = yahoo_timezone(symbols[0])
    currency = profile["currency"]
    if len(results) != len(symbols):
        raise AtlasError("RISK_INPUT_INCOMPLETE", "Supply every selected price result.")
    series, sources, documents = {}, [], []
    for symbol, result in zip(symbols, results, strict=True):
        if (
            result["instrument"]["symbol"] != symbol
            or result["instrument"]["currency"] != currency
            or result["instrument"].get("exchange_timezone") != profile["timezone"]
            or result["instrument"]["source_instrument_type"] not in {"EQUITY", "ETF"}
            or result["coverage"]["truncated"]
        ):
            raise AtlasError(
                "RISK_INPUT_INCOMPLETE", "Require complete selected same-market price histories."
            )
        if symbol == spec.factor_symbol and result["instrument"]["source_instrument_type"] != "ETF":
            raise AtlasError(
                "INVALID_INPUT", "The observed factor must be a source-identified ETF."
            )
        prices_by_date: dict[str, Any] = {}
        for row in result["records"]:
            try:
                observed = date.fromisoformat(row["date"])
            except (ValueError, TypeError):
                raise AtlasError(
                    "RISK_INPUT_INCOMPLETE", "Require valid observation dates."
                ) from None
            if (
                row["date"] in prices_by_date
                or not spec.start_date <= observed.isoformat() <= spec.end_date
                or row["adjusted_close"] is None
                or row["price_basis"] != "yahoo_split_adjusted"
                or row["adjusted_close_basis"] != "yahoo_split_and_dividend_adjusted"
                or row["session_status"] != "completed"
            ):
                raise AtlasError(
                    "RISK_INPUT_INCOMPLETE", "Require unique completed adjusted prices."
                )
            price = Decimal(row["adjusted_close"])
            if not price.is_finite() or price <= 0:
                raise AtlasError("RISK_INPUT_INCOMPLETE", "Require positive adjusted prices.")
            prices_by_date[row["date"]] = row
        series[symbol] = prices_by_date
        source = result["sources"][0]
        sources.append(
            {
                "source_id": symbol,
                "uri": source["raw_source_url"].split("?")[0],
                "raw_sha256": source["raw_sha256"],
                "retrieved_at": source["retrieved_at"],
                "usage_basis": "personal_use_yahoo_terms",
            }
        )
        body = canonical(result)
        documents.append(
            {
                "label": symbol,
                "sha256": digest(body),
                "bytes": len(body),
                "hash_encoding": "canonical_research_result",
                "source": source,
            }
        )
    dates = sorted(series[symbols[0]])
    if len(dates) < 61 or len(dates) > 500 or any(set(series[s]) != set(dates) for s in symbols):
        raise AtlasError(
            "RISK_INPUT_INCOMPLETE", "Require 61–500 exact common dates; no gap filling."
        )
    try:
        captures = [datetime.fromisoformat(s["retrieved_at"]) for s in sources]
        if any(t.tzinfo is None for t in captures):
            raise ValueError("Timezone required")
        available = max(captures)
    except (ValueError, TypeError):
        raise AtlasError("RISK_INPUT_INCOMPLETE", "Require timezone-aware capture times.") from None
    sessions = []
    with localcontext(Context(prec=80)):
        for before, after in zip(dates, dates[1:], strict=False):
            # Explicit calendar boundaries label daily return periods, not exchange close times.
            start = datetime.combine(
                datetime.fromisoformat(before).date() + timedelta(days=1), time(), timezone
            )
            end = datetime.combine(
                datetime.fromisoformat(after).date() + timedelta(days=1), time(), timezone
            )
            if end > available:
                raise AtlasError(
                    "RISK_INPUT_INCOMPLETE", "Wait until the selected source-local day has ended."
                )
            returns = {}
            for symbol in symbols:
                left, right = series[symbol][before], series[symbol][after]
                value = Decimal(right["adjusted_close"]) / Decimal(left["adjusted_close"]) - 1
                returns[symbol] = format(
                    value.quantize(Decimal("1e-18"), rounding=ROUND_HALF_EVEN), "f"
                )
            sessions.append(
                {
                    "start_at": utc_key(start),
                    "end_at": utc_key(end),
                    "available_at": utc_key(available),
                    "source_id": symbols[0],
                    "locator": f"adjusted_close/{before}/{after}",
                    "factor_source_id": spec.factor_symbol,
                    "factor_locator": f"adjusted_close/{before}/{after}",
                    "observations": [
                        {"asset_id": s, "total_return": returns[s]} for s in spec.symbols
                    ],
                    "factor_returns": {"etf_proxy": returns[spec.factor_symbol]},
                }
            )
    manifest = {
        "kind": "time_series_manifest",
        "schema_version": "0.1.0",
        "is_synthetic": any(r["is_synthetic"] for r in results),
        "currency": currency,
        "frequency": "daily",
        "return_unit": "decimal_return",
        "return_convention": "split_and_dividend_adjusted_simple_total_return",
        "factor_convention": "total_return",
        "factors": ["etf_proxy"],
        "sources": sources,
        "universe": [
            {
                "asset_id": s,
                "currency": currency,
                "security_type": "etf"
                if r["instrument"]["source_instrument_type"] == "ETF"
                else "other",
            }
            for s, r in zip(spec.symbols, results, strict=False)
        ],
    }
    return RiskConnectionRead(
        panel={"manifest": manifest, "sessions": sessions},
        documents=documents,
        snapshot_atomic=False,
        normalization={
            "formula": "adjusted_close[t] / adjusted_close[t-1] - 1",
            "rounding": "18 decimal places, ROUND_HALF_EVEN",
            "session_boundary_basis": profile["timezone"]
            + " calendar day end, not verified market close",
            "availability_basis": (
                "latest source retrieval; no historical first availability inferred"
            ),
            "factor_basis": "selected ETF proxy, not an official market factor",
            "security_type_basis": (
                "source ETF identified; Yahoo EQUITY does not establish common-share class"
            ),
            "point_in_time": False,
            "gap_filling": False,
            "re_adjustment": False,
            "price_evidence": {
                s: [
                    {
                        "date": row["date"],
                        "adjusted_close": row["adjusted_close"],
                        "source": row["field_sources"]["adjusted_close"],
                    }
                    for row in r["records"]
                ]
                for s, r in zip(symbols, results, strict=True)
            },
        },
    )


class YahooRiskConnector:
    kind = "yahoo_finance"
    method: RiskMethod = "time_series"
    uses_network = True

    def __init__(self, spec: YahooRiskSpec, directory: Any = None) -> None:
        self.spec, self.directory = spec, directory

    def read(self) -> "RiskConnectionRead":
        from atlas_terminal.client import Atlas

        require_yahoo_enabled()
        with Atlas("public", directory=self.directory) as atlas:
            results = [
                atlas.get_research(
                    {
                        "dataset": "equity_prices",
                        "identifier": symbol,
                        "start_date": self.spec.start_date,
                        "end_date": self.spec.end_date,
                        "limit": 500,
                    }
                )
                for symbol in [*self.spec.symbols, self.spec.factor_symbol]
            ]
        return panel_from_prices(self.spec, results)
