"""Short stable names for existing source-local yield-curve spread definitions."""

from typing import TypedDict

from atlas_terminal.macro_catalog import CURVE_MARKETS, CURVE_SERIES, MACRO_SERIES


class SpreadSpec(TypedDict):
    legacy_series: str
    country: str
    currency: str
    source_id: str
    title: str
    curve: str
    long_maturity: str
    short_maturity: str
    timezone: str


CURVE_NAMES = {
    "nominal": ("us", "America/New_York"),
    "jgb": ("jp", "Asia/Tokyo"),
    "ecb_aaa": ("ea_aaa", "Europe/Berlin"),
    "ecb_all": ("ea_all", "Europe/Berlin"),
    "rba": ("au", "Australia/Sydney"),
}
SPREADS: dict[str, SpreadSpec] = {}
for _name, _spec in CURVE_SERIES.items():
    _terms = _spec["terms"]
    if (
        _spec["previous_observation"]
        or len(_terms) != 2
        or _terms[0]["curve"] != _terms[1]["curve"]
        or [_term["coefficient"] for _term in _terms] != [1, -1]
        or _terms[0]["curve"] not in CURVE_NAMES
    ):
        continue
    _curve = _terms[0]["curve"]
    _prefix, _timezone = CURVE_NAMES[_curve]
    _long, _short = [_term["maturity"] for _term in _terms]
    _long_label = _long.replace("YEAR", "y").replace("MONTH", "m")
    _short_label = _short.replace("YEAR", "y").replace("MONTH", "m")
    SPREADS[f"{_prefix}_{_long_label}_{_short_label}"] = {
        "legacy_series": _name,
        "country": CURVE_MARKETS[_curve]["country"],
        "currency": CURVE_MARKETS[_curve]["currency"],
        "source_id": CURVE_MARKETS[_curve]["source_id"],
        "title": MACRO_SERIES[_name]["title"],
        "curve": _curve,
        "long_maturity": _long,
        "short_maturity": _short,
        "timezone": _timezone,
    }
