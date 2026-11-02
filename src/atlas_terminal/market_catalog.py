"""Explicit security venues and source quote-unit contracts, separate from risk taxonomy."""

from dataclasses import dataclass

TOKYO_SYMBOL_PATTERN = r"[0-9][0-9A-Z]{3}\.T"
US_SYMBOL_PATTERN = r"[A-Z][A-Z0-9]{0,5}(?:-[A-Z])?"
EUROPE_STEM = r"[A-Z0-9][A-Z0-9-]{0,19}"


@dataclass(frozen=True)
class SecurityMarket:
    country: str
    currency: str
    timezone: str
    exchanges: tuple[str, ...]
    pattern: str
    example: str
    title: str


SECURITY_MARKETS = {
    "us": SecurityMarket(
        "US",
        "USD",
        "America/New_York",
        ("NMS", "NGM", "NCM", "NYQ", "PCX", "ASE", "BTS"),
        US_SYMBOL_PATTERN,
        "AAPL",
        "US supported exchanges",
    ),
    "tokyo": SecurityMarket(
        "JP", "JPY", "Asia/Tokyo", ("JPX", "TYO"), TOKYO_SYMBOL_PATTERN, "7203.T", "Tokyo"
    ),
    "kospi": SecurityMarket(
        "KR", "KRW", "Asia/Seoul", ("KSC",), r"[0-9A-Z]{6}\.KS", "005930.KS", "Korea KOSPI"
    ),
    "kosdaq": SecurityMarket(
        "KR", "KRW", "Asia/Seoul", ("KOE",), r"[0-9A-Z]{6}\.KQ", "247540.KQ", "Korea KOSDAQ"
    ),
    "london": SecurityMarket(
        "GB", "GBP", "Europe/London", ("LSE",), EUROPE_STEM + r"\.L", "VOD.L", "London"
    ),
    "xetra": SecurityMarket(
        "DE", "EUR", "Europe/Berlin", ("GER",), EUROPE_STEM + r"\.DE", "SAP.DE", "Xetra"
    ),
    "paris": SecurityMarket(
        "FR", "EUR", "Europe/Paris", ("PAR",), EUROPE_STEM + r"\.PA", "TTE.PA", "Paris"
    ),
    "amsterdam": SecurityMarket(
        "NL", "EUR", "Europe/Amsterdam", ("AMS",), EUROPE_STEM + r"\.AS", "ASML.AS", "Amsterdam"
    ),
    "milan": SecurityMarket(
        "IT", "EUR", "Europe/Rome", ("MIL",), EUROPE_STEM + r"\.MI", "ENI.MI", "Milan"
    ),
    "madrid": SecurityMarket(
        "ES", "EUR", "Europe/Madrid", ("MCE",), EUROPE_STEM + r"\.MC", "SAN.MC", "Madrid"
    ),
    "swiss": SecurityMarket(
        "CH", "CHF", "Europe/Zurich", ("EBS",), EUROPE_STEM + r"\.SW", "NESN.SW", "SIX Swiss"
    ),
    "stockholm": SecurityMarket(
        "SE", "SEK", "Europe/Stockholm", ("STO",), EUROPE_STEM + r"\.ST", "VOLV-B.ST", "Stockholm"
    ),
    "copenhagen": SecurityMarket(
        "DK", "DKK", "Europe/Copenhagen", ("CPH",), EUROPE_STEM + r"\.CO", "NOVO-B.CO", "Copenhagen"
    ),
    "oslo": SecurityMarket(
        "NO", "NOK", "Europe/Oslo", ("OSL",), EUROPE_STEM + r"\.OL", "EQNR.OL", "Oslo"
    ),
    "helsinki": SecurityMarket(
        "FI", "EUR", "Europe/Helsinki", ("HEL",), EUROPE_STEM + r"\.HE", "NOKIA.HE", "Helsinki"
    ),
    "brussels": SecurityMarket(
        "BE", "EUR", "Europe/Brussels", ("BRU",), EUROPE_STEM + r"\.BR", "ABI.BR", "Brussels"
    ),
    # This feed reports Amsterdam, not a verified Vienna exchange calendar.
    "vienna": SecurityMarket(
        "AT",
        "EUR",
        "Europe/Amsterdam",
        ("VIE",),
        EUROPE_STEM + r"\.VI",
        "OMV.VI",
        "Vienna (source reports Amsterdam timezone)",
    ),
    "dublin": SecurityMarket(
        "IE", "EUR", "Europe/Dublin", ("ISE",), EUROPE_STEM + r"\.IR", "KRX.IR", "Dublin"
    ),
    "lisbon": SecurityMarket(
        "PT", "EUR", "Europe/Lisbon", ("LIS",), EUROPE_STEM + r"\.LS", "EDP.LS", "Lisbon"
    ),
    "warsaw": SecurityMarket(
        "PL", "PLN", "Europe/Warsaw", ("WSE",), EUROPE_STEM + r"\.WA", "PKN.WA", "Warsaw"
    ),
}
SECURITY_SYMBOL_PATTERN = "(?:" + "|".join(spec.pattern for spec in SECURITY_MARKETS.values()) + ")"
