"""Fixed future families and explicit dated identifiers; no inferred expiry calendar."""

import re
from dataclasses import dataclass
from typing import Any

from atlas_terminal.errors import AtlasError

MONTH_CODES = dict(zip("FGHJKMNQUVXZ", range(1, 13), strict=True))


@dataclass(frozen=True)
class FutureSpec:
    title: str
    sector: str
    exchange: str
    quote_measure_unit: str


FUTURES = {
    "GC": FutureSpec("Gold", "metals", "CMX", "USD_per_troy_ounce"),
    "SI": FutureSpec("Silver", "metals", "CMX", "USD_per_troy_ounce"),
    "HG": FutureSpec("Copper", "metals", "CMX", "USD_per_pound"),
    "PL": FutureSpec("Platinum", "metals", "NYM", "USD_per_troy_ounce"),
    "PA": FutureSpec("Palladium", "metals", "NYM", "USD_per_troy_ounce"),
    "CL": FutureSpec("WTI crude oil", "energy", "NYM", "USD_per_barrel"),
    "BZ": FutureSpec("Brent crude oil", "energy", "NYM", "USD_per_barrel"),
    "NG": FutureSpec("Henry Hub natural gas", "energy", "NYM", "USD_per_million_Btu"),
    "RB": FutureSpec("RBOB gasoline", "energy", "NYM", "USD_per_gallon"),
    "HO": FutureSpec("Heating oil", "energy", "NYM", "USD_per_gallon"),
    "ZC": FutureSpec("Corn", "grains", "CBT", "US_cent_per_bushel"),
    "ZW": FutureSpec("Wheat", "grains", "CBT", "US_cent_per_bushel"),
    "ZS": FutureSpec("Soybeans", "grains", "CBT", "US_cent_per_bushel"),
    "ZM": FutureSpec("Soybean meal", "grains", "CBT", "USD_per_short_ton"),
    "ZL": FutureSpec("Soybean oil", "grains", "CBT", "US_cent_per_pound"),
    "KC": FutureSpec("Coffee", "softs", "NYB", "US_cent_per_pound"),
    "SB": FutureSpec("Sugar", "softs", "NYB", "US_cent_per_pound"),
    "CC": FutureSpec("Cocoa", "softs", "NYB", "USD_per_metric_tonne"),
    "OJ": FutureSpec("Orange juice", "softs", "NYB", "US_cent_per_pound"),
    "LE": FutureSpec("Live cattle", "livestock", "CME", "US_cent_per_pound"),
    "HE": FutureSpec("Lean hogs", "livestock", "CME", "US_cent_per_pound"),
    "GF": FutureSpec("Feeder cattle", "livestock", "CME", "US_cent_per_pound"),
    "ES": FutureSpec("E-mini S&P 500", "equity_index", "CME", "index_points"),
    "NQ": FutureSpec("E-mini Nasdaq 100", "equity_index", "CME", "index_points"),
    "YM": FutureSpec("E-mini Dow", "equity_index", "CBT", "index_points"),
    "RTY": FutureSpec("E-mini Russell 2000", "equity_index", "CME", "index_points"),
    "ZB": FutureSpec("US Treasury bond", "government_bond", "CBT", "points_per_100_face_value"),
    "ZN": FutureSpec(
        "10-year US Treasury note", "government_bond", "CBT", "points_per_100_face_value"
    ),
    "ZF": FutureSpec(
        "5-year US Treasury note", "government_bond", "CBT", "points_per_100_face_value"
    ),
    "ZT": FutureSpec(
        "2-year US Treasury note", "government_bond", "CBT", "points_per_100_face_value"
    ),
}
FUTURES_SYMBOL_PATTERN = (
    "(?:"
    + "|".join(
        re.escape(root) + r"(?:=F|[FGHJKMNQUVXZ][2-4][0-9]\." + spec.exchange + ")"
        for root, spec in FUTURES.items()
    )
    + ")"
)
DATED_FUTURES_PATTERNS = {
    root: re.escape(root) + r"[FGHJKMNQUVXZ][2-4][0-9]\." + spec.exchange
    for root, spec in FUTURES.items()
}
DATED_FUTURES_SYMBOL_PATTERN = "(?:" + "|".join(DATED_FUTURES_PATTERNS.values()) + ")"


def future_identity(symbol: str) -> tuple[str, str | None] | None:
    if not re.fullmatch(FUTURES_SYMBOL_PATTERN, symbol):
        return None
    if symbol.endswith("=F"):
        return symbol[:-2], None
    root = symbol.rsplit(".", 1)[0][:-3]
    month, year = symbol.rsplit(".", 1)[0][-3], symbol.rsplit(".", 1)[0][-2:]
    return root, f"20{year}-{MONTH_CODES[month]:02d}"


def future_context(symbol: str) -> dict[str, Any]:
    identity = future_identity(symbol)
    if identity is None:
        raise AtlasError("INVALID_INPUT", "Choose a registered front reference or dated future.")
    root, month = identity
    spec = FUTURES[root]
    return {
        "underlying_root": root,
        "future_sector": spec.sector,
        "contract_month": month,
        "contract_month_basis": "explicit_symbol_2000_century" if month else None,
        "contract_role": "dated_contract" if month else "rolling_front_reference",
        "quote_measure_unit": spec.quote_measure_unit,
        "contract_multiplier": None,
        "expiry_date": None,
        "settlement_price_verified": False,
        "roll_adjusted": False,
        "source_quote_convention_verified": False,
    }
