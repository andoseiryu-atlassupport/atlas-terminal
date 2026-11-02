"""Global long-term grade references, never an issuer-rating assignment model."""

CREDIT_RATINGS_URL = "https://local.atlas.invalid/credit-ratings"
SP_GRADES = (
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC+",
    "CCC",
    "CCC-",
    "CC",
    "C",
    "SD",
    "D",
)
MOODYS_GRADES = (
    "Aaa",
    "Aa1",
    "Aa2",
    "Aa3",
    "A1",
    "A2",
    "A3",
    "Baa1",
    "Baa2",
    "Baa3",
    "Ba1",
    "Ba2",
    "Ba3",
    "B1",
    "B2",
    "B3",
    "Caa1",
    "Caa2",
    "Caa3",
    "Ca",
    "C",
)
CREDIT_SCALES = {
    "sp_global": {
        "grades": SP_GRADES,
        "reference_url": "https://www.spglobal.com/ratings/en/credit-ratings/about/understanding-credit-ratings",
    },
    "moodys": {
        "grades": MOODYS_GRADES,
        "reference_url": "https://www.moodys.com/web/en/us/solutions/ratings/understanding-ratings.html",
    },
    "fitch": {
        "grades": tuple(g for g in SP_GRADES if g != "SD")[:-1] + ("RD", "D"),
        "reference_url": "https://www.fitchratings.com/products/rating-definitions",
    },
}


def credit_scale_catalog() -> dict:
    return {
        agency: {
            "scope": "global_long_term",
            "grades": list(spec["grades"]),
            "lowest_investment_grade": spec["grades"][9],
            "reference_url": spec["reference_url"],
            "issuer_ratings_included": False,
        }
        for agency, spec in CREDIT_SCALES.items()
    }
