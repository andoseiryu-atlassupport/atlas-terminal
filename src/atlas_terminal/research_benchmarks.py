"""Common index selection, exact-date FX conversion and portfolio comparison service."""

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from atlas_terminal.benchmark_catalog import ALIGNMENT, BENCHMARKS
from atlas_terminal.benchmark_metrics import portfolio_comparison
from atlas_terminal.benchmark_models import BenchmarkSettings
from atlas_terminal.benchmark_sources import topix_prices
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_equities import yahoo_equities
from atlas_terminal.research_macro import fx_rates
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.source_deadline import remaining_timeout, source_deadline
from atlas_terminal.util import canonical, digest


class FXReadCache:
    """Reuse one FX snapshot within a request; ownership stays with the shared transport."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.synthetic = transport.synthetic
        self.snapshots: dict[str, Snapshot] = {}

    def get(self, url: str, ttl: int) -> Snapshot:
        if url not in self.snapshots:
            self.snapshots[url] = self.transport.get(url, ttl)
        return self.snapshots[url]

    def close(self) -> None:
        pass


def year_windows(start: str, end: str) -> list[tuple[str, str]]:
    return [
        (max(start, f"{year}-01-01"), min(end, f"{year}-12-31"))
        for year in range(int(start[:4]), int(end[:4]) + 1)
    ]


def benchmark_prices(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    with source_deadline(120):
        return _prices(transport, q)


def _prices(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.start_date is not None and q.end_date is not None
    spec = BENCHMARKS[q.benchmark_id]
    settings = BenchmarkSettings(**{key: getattr(q, key) for key in BenchmarkSettings.model_fields})
    selection = settings.model_dump()
    native, target = spec["native_currency"], q.benchmark_currency
    sources: list[dict[str, Any]] = []
    prices: list[dict[str, Any]] = []
    upstream: dict[str, Any] = {}
    if q.benchmark_id == "topix":
        result = topix_prices(transport, q.start_date, q.end_date)
        prices, sources = result["records"], result["sources"]
        upstream = {
            key: value for key, value in result.items() if key not in {"records", "sources"}
        }
    else:
        windows = []
        for start, end in year_windows(q.start_date, q.end_date):
            remaining_timeout()
            try:
                result = yahoo_equities(
                    transport,
                    ResearchQuery(
                        dataset="equity_prices",
                        identifier=spec["symbol"],
                        start_date=start,
                        end_date=end,
                        limit=500,
                    ),
                )
            except AtlasError as exc:
                if exc.code != "INCOMPLETE_COVERAGE":
                    raise
                windows.append({"start": start, "end": end, "status": "no_completed_observations"})
                continue
            if result["coverage"]["truncated"]:
                raise AtlasError(
                    "INCOMPLETE_COVERAGE", "An annual index source result was truncated."
                )
            sources.extend(result["sources"])
            windows.append({"start": start, "end": end, **result["coverage"]})
            for row in result["records"]:
                prices.append(
                    {
                        "date": row["date"],
                        "value": row["close"],
                        "raw_value": row["close"],
                        "source": row["field_sources"]["close"],
                        "date_sources": [row["field_sources"]["date"]],
                        "observation_at": row["observation_at"],
                        "published_at": row["published_at"],
                        "source_kind": "secondary_aggregator",
                        "symbol": spec["symbol"],
                        "normalization_formula": "reported_close",
                        "metadata_source": result["instrument"]["metadata_source"],
                    }
                )
        upstream = {"history_scope": "requested_annual_windows", "windows": windows}
    prices.sort(key=lambda row: row["date"])
    if len({row["date"] for row in prices}) != len(prices):
        raise AtlasError("SOURCE_UNAVAILABLE", "Duplicate benchmark observation dates.")
    fx_by_date: dict[str, dict[str, Any]] = {}
    if native != target and prices:
        # Reuses the official ECB normalization and cross-rate inputs. A query-local
        # memo avoids rereading the full XML history for each bounded annual window.
        cached = FXReadCache(transport)
        for start, end in year_windows(q.start_date, q.end_date):
            remaining_timeout()
            try:
                fx = fx_rates(
                    cached,
                    ResearchQuery(
                        dataset="fx_rates",
                        base_currency=native,
                        quote_currency=target,
                        start_date=start,
                        end_date=end,
                        limit=500,
                    ),
                )
            except AtlasError as exc:
                if exc.code != "INCOMPLETE_COVERAGE":
                    raise
                continue
            if fx["coverage"]["truncated"]:
                raise AtlasError("INCOMPLETE_COVERAGE", "An annual FX result was truncated.")
            sources.extend(fx["sources"])
            fx_by_date.update({row["date"]: row for row in fx["records"]})
    records = []
    for price in prices:
        day = price["date"]
        fx_row = fx_by_date.get(day) if native != target else None
        fx_value = fx_row["value"] if fx_row is not None else None
        value, reason = price["value"], None
        if value is None:
            reason = "missing_index_close"
        elif native != target:
            if fx_value is None:
                value, reason = None, "missing_same_date_fx"
            else:
                with localcontext(Context(prec=256, rounding=ROUND_HALF_EVEN)):
                    value = format(Decimal(value) * Decimal(fx_value), "f")
        index_input = {
            **price,
            "role": "index_close",
            "currency": native,
            "unit": "index_points",
            "timezone": spec["timezone"],
            "dividend_treatment": q.dividend_treatment,
        }
        inputs = [index_input]
        if native != target:
            inputs.append(
                {
                    **(
                        fx_row
                        or {
                            "date": day,
                            "value": None,
                            "inputs": [],
                            "reason": "no_ECB_observation",
                        }
                    ),
                    "role": "conversion_fx",
                    "base_currency": native,
                    "quote_currency": target,
                    "unit": f"{target}_per_{native}",
                    "observation_timezone": "Europe/Berlin",
                }
            )
        records.append(
            {
                **selection,
                "date": day,
                "comparison_date": day,
                "comparison_cutoff": day + "T23:59:59Z",
                "currency": target,
                "native_currency": native,
                "unit": "index_points" if native == target else f"{target}_valued_index_points",
                "value": value,
                "index_value": price["value"],
                "fx_value": fx_value,
                "fx_date": fx_row["date"] if fx_row is not None else None,
                "index_observation_at": price["observation_at"],
                "date_basis": "source_local_session_date",
                "alignment_policy": ALIGNMENT["id"],
                "derived": native != target,
                "status": "unavailable"
                if value is None
                else "derived"
                if native != target
                else "reported",
                "reason": reason,
                "formula": "index_close * (quote_per_EUR / base_per_EUR)"
                if native != target
                else "index_close",
                "conversion_formula": f"{native}_index_close * {target}_per_{native}"
                if native != target
                else None,
                "decimal_precision": 28 if native != target else None,
                "rounding": "FX cross rate: 28 significant digits, ROUND_HALF_EVEN; exact product"
                if native != target
                else None,
                "inputs": inputs,
                "source": price["source"] if native == target else None,
            }
        )
    calendar = []
    current, final = date.fromisoformat(q.start_date), date.fromisoformat(q.end_date)
    by_date = {row["date"]: row for row in records}
    while current <= final:
        day = current.isoformat()
        row = by_date.get(day)
        if row is None or row["value"] is None:
            calendar.append(
                {
                    "date": day,
                    "reason": row["reason"]
                    if row
                    else "no_index_observation_non_session_or_missing",
                }
            )
        current += timedelta(days=1)
    valid = [row["date"] for row in records if row["value"] is not None]
    available = [row["date"] for row in records]
    return {
        "source_id": "multiple" if native != target else spec["source_id"],
        "benchmark": {**selection, **spec},
        "selection": selection,
        "selection_key": digest(canonical({"version": "benchmarks-v1", **selection})),
        "alignment_policy": ALIGNMENT,
        "records": list(reversed(records))[: q.limit],
        "sources": list({canonical(source): source for source in sources}.values()),
        "comparison": portfolio_comparison(q.portfolio, records)
        if q.portfolio is not None
        else None,
        "coverage": {
            "requested_start": q.start_date,
            "requested_end": q.end_date,
            "available_start": upstream.get(
                "available_start", min(available) if available else None
            ),
            "available_end": upstream.get("available_end", max(available) if available else None),
            "comparison_period": {
                "start": min(valid) if valid else None,
                "end": max(valid) if valid else None,
            },
            "observed_records": len(records),
            "usable_records": len(valid),
            "returned_records": min(len(records), q.limit),
            "truncated": len(records) > q.limit,
            "unavailable_records": len(records) - len(valid),
            "missing_dates": calendar,
            "partial": bool(calendar),
            "exchange_calendar_verified": False,
            "point_in_time": False,
            "upstream": upstream,
        },
        "limitations": [
            ALIGNMENT["description"],
            "Only price-return index levels are supported. Adjusted close, ETFs, futures and "
            "total-return indexes never substitute.",
            "JPY/USD conversion uses same-date ECB reference FX, not synchronized closing or "
            "executable FX. Currency-valued index points are a comparison level, not a "
            "monetary portfolio NAV.",
            "TOPIX covers the five currently published JPX historical slots. Requested older "
            "dates are unavailable; file-marker bracketing is not a publisher-atomic snapshot "
            "guarantee.",
            "No holiday/zero-return insertion, backward/forward fill, or missing-data repair "
            "is performed. Empty results do not establish successful price verification.",
            "Yahoo index access requires explicit personal-use enablement. JPX and other "
            "index/source reuse rights are separate from the OSS license.",
        ],
    }
