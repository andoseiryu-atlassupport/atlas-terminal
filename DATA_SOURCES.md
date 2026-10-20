# Data sources and terms

Research uses free public primary sources without data subscriptions or API keys. SEC requests require an identifying User-Agent with actual contact details and compliance with SEC access rules. SEC contact settings are never sent to other sources.

## Sources

| Source                      | Data                                                              | Cost and authentication                                   | Coverage and conditions                                                                                         |
| --------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| SEC EDGAR                   | Tickers, issuers, submissions, companyfacts and filing XML        | Free; no API key; identifying contact User-Agent required | Structured facts and filing references; third-party filings retain their own rights                             |
| BLS API V1                  | Consumer prices, employment, unemployment, wages and job openings | Free; no registration                                     | Latest three years, eight registered series; 25 local attempts/UTC day, with separate upstream shared-IP limits |
| Treasury interest rates XML | Daily nominal and TIPS-based real par yield curves                | Free; no API key                                          | One selected year: nominal from 1990, real from 2003; indicative par yields                                     |
| Treasury Fiscal Data        | Debt to the Penny                                                 | Free; no API key                                          | One page of at most 500 recent records with original dates and USD units                                        |
| Federal Reserve RSS         | Official release headlines, dates and links                       | Free; no API key                                          | Entries retained by the feed; no newswire articles or full article bodies                                       |
| ECB euro reference rates    | EUR reference observations and derived inverse/cross FX           | Free; no API key                                          | Latest 90 calendar days; nine currencies; attribution and modification notices; information purposes only       |
| New York Fed Markets API    | Overnight EFFR and SOFR                                           | Free; no API key                                          | Last 90 effective observations; retain copyright, reference-rate notice, disclaimer and terms                   |

Official documentation and conditions:

- SEC: [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Developer Resources](https://www.sec.gov/about/developer-resources), [Form 13F](https://www.sec.gov/files/form13f.pdf).
- BLS: [API V1](https://www.bls.gov/developers/api_signature_v1.htm), [API features and limits](https://www.bls.gov/developers/api_features.htm), [linking and copyright](https://www.bls.gov/bls/linksite.htm).
- Treasury yields: [CSV / XML archive](https://home.treasury.gov/interest-rates-data-csv-archive), [XML fields and endpoints](https://home.treasury.gov/developer-notice-xml-changes), [site policy](https://home.treasury.gov/footer/privacy-act).
- Fiscal Data: [API documentation](https://fiscaldata.treasury.gov/api-documentation/), [about](https://fiscaldata.treasury.gov/about-us/).
- Federal Reserve: [feeds](https://www.federalreserve.gov/feeds/feeds.htm), [disclaimer](https://www.federalreserve.gov/disclaimer.htm).
- ECB: [reference rates and downloads](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), [copyright and conditions](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html).
- New York Fed: [Markets API](https://markets.newyorkfed.org/static/docs/markets-api.html), [terms of use and reference-rate notice](https://www.newyorkfed.org/privacy/termsofuse).

**Validation scope:** Response shapes, conditional caching, limits, missing values, units and provenance are tested with fictional fixtures and mock HTTP. ECB reference-rate documentation and conditions, New York Fed reference-rate terms, and Treasury XML documentation were inspected for this expansion. Live source responses and runtime availability remain unverified; the NY Fed API documentation page could not be read fully. Other current terms pages and final dataset-specific redistribution checks remain unverified. Follow current source terms and limits before operation. See [validation scope](docs/verification.md) for test evidence.

## Retrieval and processing

Atlas accepts no arbitrary source URLs or mirrors and follows no redirects. Refresh failures never present expired cache entries as current data or switch to a paid provider. HTTP uses shared rate limits, at most three attempts and response size/time bounds. BLS daily attempts are persisted.

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

Original JSON/XML bytes are cached privately and traced by hashes and JSON pointers or XML local paths. Output includes structured facts, filing metadata, limited footnotes and original references, not wholesale redistribution of third-party documents, images or audio. The code's Apache-2.0 license does not automatically apply to source material.

Companies, CIKs, amounts and observations under `resources/sec_like`, `resources/research` and `tests/fixtures` are fictional. Fixture source links use `.invalid` and are never fetched. Retrieval time is fixed at 2026-10-20T00:00:00Z, and hashes are calculated from packaged bytes. Government-shaped fixtures are never presented as real observations.
