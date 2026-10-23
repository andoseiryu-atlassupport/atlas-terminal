# Data sources and terms

Research uses free public primary sources without data subscriptions. BEA national accounts require a free registered `BEA_API_KEY`; other data sources need no API key. SEC requests require an identifying User-Agent with actual contact details and compliance with SEC access rules. SEC contact settings are never sent to other sources.

## Sources

| Source                      | Data                                                              | Cost and authentication                                   | Coverage and conditions                                                                                                                                                          |
| --------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SEC EDGAR                   | Tickers, issuers, submissions, companyfacts and filing XML        | Free; no API key; identifying contact User-Agent required | Structured facts and filing references; third-party filings retain their own rights                                                                                              |
| BLS API V1                  | Consumer prices, employment, unemployment, wages and job openings | Free; no registration                                     | Default latest three years; dated requests in ten-year chunks, up to 30 calendar years; eight curated series; 25 local attempts/UTC day, with separate upstream shared-IP limits |
| Treasury interest rates XML | Daily nominal and TIPS-based real par yield curves                | Free; no API key                                          | One selected year: nominal from 1990, real from 2003; indicative par yields                                                                                                      |
| Treasury Fiscal Data        | Debt to the Penny                                                 | Free; no API key                                          | One page of at most 500 recent records with original dates and USD units                                                                                                         |
| Federal Reserve RSS         | Official release headlines, dates and links                       | Free; no API key                                          | Entries retained by the feed; no newswire articles or full article bodies                                                                                                        |
| ECB euro reference rates    | EUR reference observations and derived inverse/cross FX           | Free; no API key                                          | Full published history; 31 currencies with per-currency availability; attribution and modification notices; information purposes only                                            |
| New York Fed Markets API    | Overnight EFFR and SOFR                                           | Free; no API key                                          | Last 90 effective observations; retain copyright, reference-rate notice, disclaimer and terms                                                                                    |
| BEA NIPA API                | GDP, PCE indexes, personal income and consumption                 | Free registered API key                                   | Eight curated series; five-year upstream table chunks; explicit units/scales                                                                                                     |
| Bank of Japan API           | Rates, Tokyo FX, Tankan, money/credit and effective FX            | Free; no registration                                     | Eight curated series; preserve units, attribution and non-guarantee notice; operator service-publication notification requested                                                  |
| Statistical Dashboard       | National CPI and unemployment from Statistics Bureau surveys      | Free; no registration                                     | Two government indicators resolved from active metadata; preserve index base, adjustment, provisional flags and notices                                                          |

Official documentation and conditions:

- SEC: [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Developer Resources](https://www.sec.gov/about/developer-resources), [Form 13F](https://www.sec.gov/files/form13f.pdf).
- BLS: [API V1](https://www.bls.gov/developers/api_signature.htm), [API features and limits](https://www.bls.gov/developers/api_features.htm), [linking and copyright](https://www.bls.gov/bls/linksite.htm).
- Treasury yields: [CSV / XML archive](https://home.treasury.gov/interest-rates-data-csv-archive), [XML fields and endpoints](https://home.treasury.gov/developer-notice-xml-changes), [site policy](https://home.treasury.gov/footer/privacy-act).
- Fiscal Data: [API documentation](https://fiscaldata.treasury.gov/api-documentation/), [about](https://fiscaldata.treasury.gov/about-us/).
- Federal Reserve: [feeds](https://www.federalreserve.gov/feeds/feeds.htm), [disclaimer](https://www.federalreserve.gov/disclaimer.htm).
- ECB: [reference rates and downloads](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), [copyright and conditions](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html).
- New York Fed: [Markets API](https://markets.newyorkfed.org/static/docs/markets-api.html), [terms of use and reference-rate notice](https://www.newyorkfed.org/privacy/termsofuse).

- BEA: [API guide](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf), [free key registration](https://apps.bea.gov/api/signup/), [policies](https://www.bea.gov/about/policies-and-information).
- BOJ: [API manual](https://www.stat-search.boj.or.jp/info/api_manual_en.pdf), [API notice](https://www.stat-search.boj.or.jp/info/api_notice_en.pdf), [major series codes](https://www.stat-search.boj.or.jp/ssi/docs/info/nme_Mdframe_en.html).
- Statistical Dashboard: [API documentation](https://dashboard.e-stat.go.jp/static/api), [site policy](https://dashboard.e-stat.go.jp/static/sitePolicy). Only the curated Statistics Bureau CPI and Labour Force Survey data are selected, not third-party dashboard content.

The BOJ notice requests a notification when an API-backed service is published; service operators must handle that step. Atlas makes no notification or account-registration request. BOJ and Dashboard non-guarantee notices are returned in `attributions`. Preserve them and the original survey attribution when republishing results.

**Validation scope:** Response shapes, conditional caching, limits, missing values, units and provenance are tested with fictional fixtures and mock HTTP. ECB reference-rate documentation and conditions, New York Fed reference-rate terms, and Treasury XML documentation were inspected; BEA, BOJ, BLS and Statistical Dashboard API specifications were also inspected. Live source responses and runtime availability remain unverified; the NY Fed API documentation page could not be read fully. Other current terms pages and final dataset-specific redistribution checks remain unverified. Follow current source terms and limits before operation. See [validation scope](docs/verification.md) for test evidence.

## International macro sources

IMF and OECD public SDMX endpoints provide dataflow metadata and single-series monthly, quarterly and annual observations. Atlas does not require a key for these reads. Dataset-specific availability, attribution, original-producer rights and third-party notices still apply; free access is not a blanket redistribution license.

- IMF: [API documentation](https://data.imf.org/en/Resource-Pages/IMF-API), [terms](https://www.imf.org/external/terms.htm). Preserve source observation status, units, scales and dataset definitions.
- OECD: [API documentation](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html), [API limits](https://www.oecd.org/en/data/insights/data-explainers/2024/11/Api-best-practices-and-recommendations.html), [terms](https://www.oecd.org/en/about/terms-conditions.html). Metadata is cached; 60 local HTTP attempts per rolling hour include retries, with separate upstream shared-IP restrictions.

SDMX observations retain original XML bytes/hashes and metadata references. Decimal values are not silently rescaled; missing unit, scale or adjustment metadata remains null. Publisher status can identify provisional values or forecasts. [International discovery and panel methodology](docs/international-macro.md). Official API documentation was inspected; actual source responses and final dataset-specific redistribution checks remain unverified.

## Retrieval and processing

Atlas accepts no arbitrary source URLs or mirrors and follows no redirects. Refresh failures never present expired cache entries as current data or switch to a paid provider. HTTP uses shared rate limits, at most three attempts and response size/time bounds. BLS daily attempts are persisted.

Optional research requirements can reject source age, insufficient latest observation dates, missing values, derived records or explicit truncation without expanding upstream history. Batches share source caches and quotas and return independent item statuses. [Operator source checks](docs/source-checks.md) explicitly revalidate approved endpoints through the same persistent rate/quota stores and retain private last-success structure/date baselines. These checks do not establish redistribution rights or historical availability.

`statements` normalizes 37 annual, quarterly and YTD US-GAAP concepts with explicit tags and units. `financials` returns five-metric annual snapshots. `financial_analytics` calculates five YoY growth rates, FCF and FCF margin from those reported inputs without additional data providers. Raw-context `company_facts`, derived calculations and filing-discovery metadata are distinct outputs. See [methodology](docs/methodology.md) and the [research guide](docs/research.md).

`fx_rates` identifies ECB attribution and terms and marks inverse/cross calculations as modifications. Each calculation retains the same-day EUR inputs and original XML locators. `reference_rates` retains the New York Fed copyright, required reference-rate notice, terms and a normalization disclosure. Keep the root-level `attributions` entries when redistributing results, including compact responses. The New York Fed does not sponsor or endorse Atlas. EFFR/SOFR values are overnight rates in percent per annum; the effective date is distinct from retrieval time. See [macro and FX methodology](docs/macro.md).

Free viewing and redistribution permission are different conditions. Unofficial Yahoo Finance APIs, daily-price sources without verified service redistribution terms, paid exchange feeds, consensus forecasts, newswire articles and earnings-call transcripts are not integrated. The service does not provide prices, order books, universe-wide adjustment factors or PIT data.

## SEC endpoints

| Data                          | Source                                                                       |
| ----------------------------- | ---------------------------------------------------------------------------- |
| Current ticker / CIK mapping  | https://www.sec.gov/files/company_tickers.json                               |
| Issuer and submission history | https://data.sec.gov/submissions/CIK{cik}.json                               |
| Additional history            | Validated archive filenames listed by submissions                            |
| XBRL facts                    | https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json                     |
| Filing XML and directories    | Validated CIK/accession paths under https://www.sec.gov/Archives/edgar/data/ |

Additional references: [Accessing EDGAR](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) and [Developer Resources](https://www.sec.gov/about/developer-resources). Endpoint behavior is tested with HTTP MockTransport; live responses and the latest official pages remain unverified.

SEC data is not treated as complete coverage of all issuers, concepts or documents. The current ticker directory is not a historical symbol map. 13F retrieval reads individual manager filings, not every investor's real-time holdings.

BEA may echo its API key in raw response metadata. Raw BEA responses must stay private; returned source URLs and records exclude credentials. Original JSON/XML bytes are cached privately and traced by hashes and JSON pointers or XML local paths. Output includes structured facts, filing metadata, limited footnotes and original references, not wholesale redistribution of third-party documents, images or audio. The code's Apache-2.0 license does not automatically apply to source material.

Companies, CIKs, amounts and observations under `resources/sec_like`, `resources/research` and `tests/fixtures` are fictional. Fixture source links use `.invalid` and are never fetched. Retrieval time is fixed at 2026-10-20T00:00:00Z, and hashes are calculated from packaged bytes. Government-shaped fixtures are never presented as real observations.
