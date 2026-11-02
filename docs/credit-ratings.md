# Credit-rating observations

`credit_ratings` reads an **operator-supplied JSON book** of issuer or individual-issue credit observations. It supports the global long-term scales of S&P Global, Moody's and Fitch. It does not fetch an automatic agency feed, verify the operator's source assertions, certify current ratings, or substitute analyst Buy/Hold/Sell recommendations for credit grades.

## Configure an owned or licensed input

Set a private file on the service host:

```sh
export ATLAS_CREDIT_RATINGS_FILE=/operator/private/credit-ratings.json
atlas research get credit_ratings --identifier ATLASDEMO --agency sp_global --provider public --json
```

`identifier` is the operator's normalized issuer identifier. An optional `agency` filters `sp_global`, `moodys` or `fitch`; `limit` bounds output. There is no file-path or arbitrary-source-URL query parameter. SDK, MCP and HTTP use the same configured file and service. Configuring it allows those service clients to read selected observations and the retained original book through source history; configure access accordingly. Unsetting the variable disables reads of cached credit pages and retained books as well.

This example is entirely fictional:

```json
{
  "schema_version": "0.1.0",
  "is_synthetic": true,
  "ratings": [
    {
      "issuer_identifier": "ATLASDEMO",
      "issuer_name": "Fictional Atlas issuer",
      "agency": "sp_global",
      "rating_scope": "issuer",
      "rating_scale": "global_long_term",
      "currency_scope": "foreign_currency",
      "rating_status": "rated",
      "rating": "BBB+",
      "rating_date": "2026-10-20",
      "outlook": "stable",
      "watch": "none",
      "original_source_url": "https://fixtures.atlas.invalid/fictional-credit-observation",
      "published_at": "2026-10-20T09:00:00Z"
    }
  ]
}
```

Only use observations you own or are entitled to process. The project code's Apache-2.0 license does not license agency data. A public page or regulatory platform does not by itself establish bulk-feed or redistribution rights. The file's `is_synthetic` marker survives public-provider output; fixture mode is always synthetic. No real ratings are bundled.

## Meaning and validation

- `rating_scope` is `issuer` or `issue`. An issue requires `issue_identifier`; an issuer must not have one. Optional `debt_seniority` is retained. Issuer-specific `SD`/`RD` statuses cannot identify a single issue.
- `currency_scope` is `local_currency`, `foreign_currency` or `unspecified`; no currency or seniority equivalence is inferred.
- `rating_status` is `rated`, `withdrawn` or `not_rated`. A rated observation needs a grade valid on its stated agency scale. Withdrawal and not-rated observations require `rating=null`. An absent issuer/agency returns `INCOMPLETE_COVERAGE`; absence is not a not-rated assignment.
- Grade spelling is agency-specific, including Moody's capitalization/modifiers. Short-term, national and structured-finance scales are unsupported. Outlook and watch have separate explicit fields; neither changes the grade.
- `rating_date` is a valid nonfuture reference date. Optional `published_at` must include a timezone and cannot be in the future. The service does not manufacture publication timestamps or infer that publication preceded a historical decision.
- `original_source_url` is a public HTTPS reference without user credentials, query strings or fragments. It is retained as an operator assertion and is **not fetched**.

Every matching observation is returned, ordered by its reference date and agency; the service does not select a latest, best or current grade. `origin_verified=false` and `currentness_verified=false` remain explicit. `investment_grade` uses each supported agency's global long-term cutoff (`BBB-` or `Baa3`). `agency_grade_order` is an ordering within that scale only. These fields are not cross-agency equivalence, a default probability, or evidence that a default event occurred.

## Original bytes and local history

The reader accepts a regular file up to 4 MiB and 10,000 observations, rejects a symbolic link at the final path component where the operating system supports `O_NOFOLLOW`, and validates the entire input before archiving it. Configuration/input errors do not echo private paths or file contents. No referenced website is contacted.

Validated original bytes are retained with SHA-256 and a JSON pointer for each observation. `raw_source_url="https://local.atlas.invalid/credit-ratings"` is a reserved local content namespace, not a public URL that was fetched. Its hash describes the input file, not the agency page named in `original_source_url`.

```json
{
  "dataset": "source_history",
  "source_url": "https://local.atlas.invalid/credit-ratings",
  "history_action": "list"
}
```

The existing `get`, `diff` and inclusive local-observation `as_of` actions also work while the operator file is configured. Re-reading A → B → A retains two byte versions and three observation events. Hash checks, shared archive quotas and atomic quota failure apply. History begins with local imports, not with the rating date; `point_in_time=false` and no publisher PIT claim is made. [History details](source-history.md).

## Agency and access references

- [S&P understanding credit ratings](https://www.spglobal.com/ratings/en/credit-ratings/about/understanding-credit-ratings).
- [Moody's understanding ratings](https://www.moodys.com/web/en/us/solutions/ratings/understanding-ratings.html).
- [Fitch rating definitions](https://www.fitchratings.com/products/rating-definitions).
- [European Rating Platform catalog](https://data.europa.eu/data/datasets/eu-rp?locale=en) and [ESMA's discussion of rating access](https://www.esma.europa.eu/press-news/esma-news/esma-calls-legislative-changes-improve-access-and-use-credit-ratings).

No automatic issuer-rating feed has been live-verified in this implementation. [Validation scope](verification.md) distinguishes local input tests from actual agency access.
