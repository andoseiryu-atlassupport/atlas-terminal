# Official source checks

`atlas research check-sources` is an operator command for read-only checks of fixed official endpoints. It never signs, quotes, purchases or transfers funds. Calls require `--allow-network`; without it the command reports `NOT_RUN` and constructs no source clients or state store.

```sh
uv run atlas research check-sources --sources ecb,new_york_fed --allow-network --json
uv run atlas research check-sources --sources bls,treasury --allow-network --json
uv run atlas research check-sources --sources ecb --max-observation-lag-days 7 --allow-network --json
```

`--sources all` includes every source family. SEC requires a privately configured, identifying `SEC_USER_AGENT`; missing/invalid configuration reports `NOT_RUN`, independently of other probes. BEA requires a private free registered `BEA_API_KEY` and also reports `NOT_RUN` without it. Keep contact values and keys outside model inputs, source control and logs.

| Source ID         | Fixed probes                                                     |
| ----------------- | ---------------------------------------------------------------- |
| `sec`             | AAPL current issuer directory resolution and submissions profile |
| `bls`             | Registration-free CPI observations                               |
| `eurostat`        | Current ECOICOP-v2 euro-area HICP annual change                  |
| `ons`             | UK all-items CPI index                                           |
| `ecb`             | ECB reference USD/JPY using same-date EUR inputs                 |
| `new_york_fed`    | Overnight EFFR and SOFR                                          |
| `treasury`        | Current-year nominal curve, real curve and 10Y–2Y spread         |
| `fiscaldata`      | Debt to the Penny                                                |
| `federal_reserve` | Retained official release RSS metadata                           |
| `bea`             | Real GDP and core PCE price index                                |
| `boj`             | Overnight call rate and large-manufacturer Tankan                |
| `estat`           | National CPI and seasonally adjusted unemployment                |

Each run conditionally revalidates every used URL through the same private SEC/public cache stores, rate limits and BLS daily-attempt counter as ordinary research. A prior unexpired cache entry alone cannot satisfy the check; HTTP 304 is valid revalidation. Within a run, repeated URLs share the newly checked snapshot, including nominal Treasury data reused for the spread. Checked source bodies remain in the normal private cache with their original byte hashes and refreshed check times. Payment state is unaffected. Use the same `ATLAS_STATE_DIR` as ordinary research to share local quotas.

Successful probes require parsed observations, captured source bytes and source retrieval/check times within one hour. Reports include source URLs, byte SHA-256, bounded structural SHA-256, format, latest usable observation date, returned record count and elapsed milliseconds. They do not publish full source bodies, contact headers or credentials. Source conditions remain separate; metadata-only profiles have no observation date.

JSON structural fingerprints retain field names and types, excluding data values and repeated array items. Numeric-keyed SEC directory maps omit changing record IDs. XML fingerprints retain expanded element/attribute names and distinct child structures, excluding text/attribute values and repeated rows. Structural inspection stops after eight levels and bounds object field inspection to 256 keys. A new optional field or null variant can change a fingerprint without breaking the parser; drift is a review signal.

The private `ATLAS_STATE_DIR/source-checks.sqlite3` stores only the last successful report per fixed probe, with integrity hashes. A subsequent success reports `raw_bytes_changed`, `shape_changed`, `latest_observation_changed` and `latest_observation_regressed`. Initial comparisons are null. Failures retain the last-success baseline; integrity failures are explicit and do not overwrite it. This is not a historical value-difference feed or a reconstruction of past information availability.

`--max-observation-lag-days` optionally applies the same calendar-day threshold to selected probes using the shared research requirements. It accepts 0–3650 days. Choose a threshold appropriate to the selected source: daily reference rates and monthly CPI have different cadences. No holiday/release calendar or inferred publication timestamp is used. An undated profile cannot satisfy an observation-date requirement. Without this option, PASS establishes parser/access behavior for the selected response, not timeliness of its observations.

Root/probe statuses are `PASS`, `FAIL` and `NOT_RUN`. A fixture result is always `NOT_RUN` as live evidence. CLI exits 0 for all-pass, 3 when any probe fails and 4 when checks are incomplete/not run. Probe failures include the ordinary sanitized error contract; network restrictions, upstream errors and private-configuration requirements must be interpreted in their execution context. No paid fallback occurs.

IMF and OECD each add a dataflow-catalogue probe. A PASS for those probes checks metadata access/parser behavior, not observation retrieval; they have no observation date and cannot satisfy an observation-lag requirement. OECD reads share the persistent rolling hourly attempt counter with ordinary queries.

## Manual workflow

[Official source checks](../.github/workflows/source-checks.yml) is a separate `workflow_dispatch` workflow with an explicit `allow_network` boolean and selected source IDs. It uses pinned actions, read-only repository permissions and locked dependencies. The default source list excludes SEC; selecting it requires the repository's private `SEC_USER_AGENT` secret. Selecting BEA requires the private `BEA_API_KEY` secret. Structured reports are printed even when a probe step fails. Default offline CI and pull-request tests make no live source requests.

Runner-local baselines are temporary in this workflow. Persistent cross-run comparison uses the operator command on a persistent private state volume. No recurring schedule, external monitoring service or public status endpoint is installed.

## Recorded verification limits

Parser, fingerprint, failure-isolation, source-contact separation, baseline persistence and integrity tests use fictional inputs and HTTP MockTransport. They do not establish actual source connectivity. In the recorded implementation environment, the enforced outbound allowlist excludes official data hosts, so live probes remain unverified. An operator must allow the intended endpoints before running them; SEC additionally needs its private contact setting, and BEA needs its free registered key.

The required direct-request hosts include `www.ecb.europa.eu`, `markets.newyorkfed.org`, `api.bls.gov`, `home.treasury.gov`, `api.fiscaldata.treasury.gov`, `www.federalreserve.gov`, `www.sec.gov`, `data.sec.gov`, `apps.bea.gov`, `www.stat-search.boj.or.jp`, `dashboard.e-stat.go.jp`, `api.imf.org`, `sdmx.oecd.org`, `www.mof.go.jp`, `data-api.ecb.europa.eu`, `www.rba.gov.au`, `ec.europa.eu` and `www.ons.gov.uk`. Atlas still enforces its narrower fixed URL catalog and refuses redirects. Keep TLS verification and the execution environment's proxy path enabled.

Eurostat HICP and ONS CPI add two fixed observation probes. Select `--sources eurostat,ons` to check their live parsers on a host with permitted network access. These checks are distinct from fictional offline contract tests. [Statistical definitions and current HICP selection](economic-indicators.md).

[Agent requirements](agents.md#require-data-quality-explicitly) · [Source conditions](../DATA_SOURCES.md) · [Validation scope](verification.md)
