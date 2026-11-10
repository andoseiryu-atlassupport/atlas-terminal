"""Explicit US CPI/CPS/LAUS definitions; selectors are separate marginal series."""

from typing import Any

CPI_COMPONENTS = {
    "goods_cpi": ("SAC", "Commodities (goods)", "goods"),
    "services_cpi": ("SAS", "Services, including shelter", "services"),
    "shelter_cpi": ("SAH1", "Shelter", "housing"),
    "rent_cpi": ("SEHA", "Rent of primary residence", "housing"),
    "owners_equivalent_rent_cpi": ("SEHC", "Owners' equivalent rent of residences", "housing"),
    "food_cpi": ("SAF1", "Food", "food_energy"),
    "energy_cpi": ("SA0E", "Energy", "food_energy"),
    "durables_cpi": ("SAD", "Durables", "goods"),
    "nondurables_cpi": ("SAN", "Nondurables", "goods"),
}

# FIPS state codes, including DC; territories are outside this initial registry.
STATES = {
    "al": ("01", "Alabama"),
    "ak": ("02", "Alaska"),
    "az": ("04", "Arizona"),
    "ar": ("05", "Arkansas"),
    "ca": ("06", "California"),
    "co": ("08", "Colorado"),
    "ct": ("09", "Connecticut"),
    "de": ("10", "Delaware"),
    "dc": ("11", "District of Columbia"),
    "fl": ("12", "Florida"),
    "ga": ("13", "Georgia"),
    "hi": ("15", "Hawaii"),
    "id": ("16", "Idaho"),
    "il": ("17", "Illinois"),
    "in": ("18", "Indiana"),
    "ia": ("19", "Iowa"),
    "ks": ("20", "Kansas"),
    "ky": ("21", "Kentucky"),
    "la": ("22", "Louisiana"),
    "me": ("23", "Maine"),
    "md": ("24", "Maryland"),
    "ma": ("25", "Massachusetts"),
    "mi": ("26", "Michigan"),
    "mn": ("27", "Minnesota"),
    "ms": ("28", "Mississippi"),
    "mo": ("29", "Missouri"),
    "mt": ("30", "Montana"),
    "ne": ("31", "Nebraska"),
    "nv": ("32", "Nevada"),
    "nh": ("33", "New Hampshire"),
    "nj": ("34", "New Jersey"),
    "nm": ("35", "New Mexico"),
    "ny": ("36", "New York"),
    "nc": ("37", "North Carolina"),
    "nd": ("38", "North Dakota"),
    "oh": ("39", "Ohio"),
    "ok": ("40", "Oklahoma"),
    "or": ("41", "Oregon"),
    "pa": ("42", "Pennsylvania"),
    "ri": ("44", "Rhode Island"),
    "sc": ("45", "South Carolina"),
    "sd": ("46", "South Dakota"),
    "tn": ("47", "Tennessee"),
    "tx": ("48", "Texas"),
    "ut": ("49", "Utah"),
    "vt": ("50", "Vermont"),
    "va": ("51", "Virginia"),
    "wa": ("53", "Washington"),
    "wv": ("54", "West Virginia"),
    "wi": ("55", "Wisconsin"),
    "wy": ("56", "Wyoming"),
}
REGIONS = {
    "northeast": ("91", "Northeast", "NE"),
    "midwest": ("92", "Midwest", "MW"),
    "south": ("93", "South", "S"),
    "west": ("94", "West", "W"),
}
AGES = {
    "16_19": ("000012", 16, 19),
    "20_24": ("000036", 20, 24),
    "25_54": ("000060", 25, 54),
    "55_plus": ("024230", 55, None),
}
OCCUPATIONS = {
    "management_professional": ("32201", "32215", "Management, professional, and related"),
    "service": ("32204", "32218", "Service"),
    "sales_office": ("32205", "32219", "Sales and office"),
    "natural_resources_construction": (
        "32208",
        "32222",
        "Natural resources, construction, and maintenance",
    ),
    "production_transportation": (
        "32212",
        "32226",
        "Production, transportation, and material moving",
    ),
}

BREAKDOWN_SERIES: dict[str, tuple[str, str, str, bool]] = {}
BREAKDOWN_DETAILS: dict[str, dict[str, Any]] = {}
CATEGORY_PATHS: dict[str, list[str]] = {}

for _key, (_item, _label, _group) in CPI_COMPONENTS.items():
    for _adjusted in (False, True):
        _name = _key + ("_sa" if _adjusted else "")
        _code = ("CUSR" if _adjusted else "CUUR") + "0000" + _item
        _oer = _item == "SEHC"
        BREAKDOWN_SERIES[_name] = (
            _code,
            "CPI-U: " + _label,
            "index_1982_12_100" if _oer else "index_1982_84_100",
            _adjusted,
        )
        CATEGORY_PATHS[_name] = ["cpi", _group]
        BREAKDOWN_DETAILS[_name] = {
            "frequency": "monthly",
            "annualized": False,
            "base_period": "1982-12" if _oer else "1982-1984",
            "population": "all_urban_consumers",
            "area": "US_city_average",
            "index_scope": _key.removesuffix("_cpi"),
            "definition_url": "https://data.bls.gov/timeseries/" + _code,
            "category_path": CATEGORY_PATHS[_name],
        }

for _key, _group in (
    ("cpi", "headline"),
    ("core_cpi", "core"),
    ("core_goods_cpi", "goods"),
    ("core_services_cpi", "services"),
):
    for _suffix in ("", "_sa"):
        CATEGORY_PATHS[_key + _suffix] = ["cpi", _group]


def register_labor(
    name: str,
    code: str,
    title: str,
    unit: str,
    adjusted: bool,
    path: list[str],
    **details: Any,
) -> None:
    BREAKDOWN_SERIES[name] = (code, title, unit, adjusted)
    CATEGORY_PATHS[name] = path
    BREAKDOWN_DETAILS[name] = {
        "frequency": "monthly",
        "annualized": False,
        "base_period": None,
        "population": "civilian_noninstitutional_age_16_plus",
        "count_basis": "persons",
        "category_path": path,
        "definition_url": "https://data.bls.gov/timeseries/" + code,
        **details,
    }


for _kind, _areas in (
    (
        "state",
        [(k, "ST" + v[0] + "00000000000", v[1], "US-" + k.upper()) for k, v in STATES.items()],
    ),
    (
        "region",
        [(k, "RD" + v[0] + "00000000000", v[1], "US-CENSUS-" + v[2]) for k, v in REGIONS.items()],
    ),
):
    for _area, _area_code, _label, _iso in _areas:
        for _measure, _measure_code, _unit in (
            ("employment", "05", "persons"),
            ("unemployment", "03", "percent"),
        ):
            register_labor(
                f"{_measure}_{_kind}_{_area}",
                "LAS" + _area_code + _measure_code,
                _label
                + ": "
                + ("employment level" if _measure == "employment" else "unemployment rate"),
                _unit,
                True,
                ["employment", "region", _kind],
                survey="LAUS",
                area_type="state" if _kind == "state" else "census_region",
                area_code=_area_code,
                region_code=_iso,
                region_name=_label,
                measure="employment_level" if _measure == "employment" else "unemployment_rate",
                definition="Residence-based LAUS estimate; not establishment payroll jobs.",
            )

for _age, (_ending, _low, _high) in AGES.items():
    for _measure, _prefix, _unit in (
        ("employment", "LNS12", "thousand_persons"),
        ("unemployment", "LNS14", "percent"),
    ):
        register_labor(
            f"{_measure}_age_{_age}",
            _prefix + _ending,
            _measure.capitalize()
            + (" level" if _measure == "employment" else " rate")
            + ", ages "
            + _age.replace("_", " "),
            _unit,
            True,
            ["employment", "age"],
            survey="CPS",
            area="US",
            age_min=_low,
            age_max=_high,
            measure="employment_level" if _measure == "employment" else "unemployment_rate",
            definition=(
                "CPS age group, all sexes; rate denominator is the group's civilian labor force."
            ),
        )

for _occupation, (_employment, _unemployment, _label) in OCCUPATIONS.items():
    for _measure, _code, _unit in (
        ("employment", "LNU020" + _employment, "thousand_persons"),
        ("unemployment", "LNU040" + _unemployment, "percent"),
    ):
        register_labor(
            f"{_measure}_occupation_{_occupation}",
            _code,
            _label
            + " occupations: "
            + ("employment level" if _measure == "employment" else "experienced unemployment rate"),
            _unit,
            False,
            ["employment", "occupation"],
            survey="CPS",
            area="US",
            occupation=_occupation,
            occupation_name=_label,
            measure="employment_level"
            if _measure == "employment"
            else "experienced_unemployment_rate",
            definition=(
                "Current occupation of employed persons."
                if _measure == "employment"
                else (
                    "Experienced labor force denominator; unemployed classified "
                    "by last occupation, excluding new entrants without prior "
                    "work."
                )
            ),
        )

for _key, _group in (
    ("payrolls", "industry"),
    ("private_payrolls", "industry"),
    ("government_payrolls", "industry"),
    ("manufacturing_payrolls", "industry"),
    ("unemployment", "labor_status"),
    ("unemployment_u6", "labor_status"),
    ("participation", "labor_status"),
    ("employment_population_ratio", "labor_status"),
    ("hourly_earnings", "wages"),
    ("average_weekly_hours", "hours"),
    ("long_term_unemployment", "labor_status"),
    ("job_openings", "job_flows"),
    ("job_hires", "job_flows"),
    ("job_quits", "job_flows"),
    ("job_layoffs", "job_flows"),
    ("employment_cost_compensation", "costs"),
    ("employment_cost_wages", "costs"),
    ("nonfarm_productivity_growth", "productivity"),
    ("unit_labor_cost_growth", "productivity"),
):
    CATEGORY_PATHS[_key] = ["employment", _group]
