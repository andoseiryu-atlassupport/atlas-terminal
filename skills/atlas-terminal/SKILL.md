---
name: atlas-terminal
description: Retrieve SEC issuer, annual US-GAAP USD facts and filing metadata; explicitly quote and purchase optional hosted snapshots under preconfigured spending policy.
---

Use Atlas for source-backed annual facts. Resolve the issuer before retrieval. Do not use it for market prices, forecasts, news, monitoring, trading or point-in-time history.

1. For offline verification run `atlas demo --json`; synthetic issuer is ATLASDEMO.
2. For free local SEC retrieval use `atlas company resolve` and `atlas financials get` with `--provider sec`. SEC contact settings come from the operator, not a fabricated value.
3. MCP tools share these same services. Provider, service origin and private settings are chosen at startup.
4. Hosted ordinary financial retrieval returns QUOTE_REQUIRED. Call atlas_quote to inspect coverage, source limitations and fixed atomic-unit price. Do not treat the quote as permission to pay.
5. Call atlas_purchase only when the task intends a purchase under the operator's existing delegation. This tool spends funds. Never change the policy, service origin, wallet, recipient or network to make it succeed.
6. Use atlas_purchase_status after an unknown response. Never create another signature or quote to work around a pending settlement or budget denial.
7. Report each value with actual period, unit, tag, accession and source. Missing/ambiguous is not zero. Do not present current snapshots as complete historical availability or later restatements.

Never reveal keys, signatures, recovery capabilities, private databases or contact settings. Fixture/mock receipts are simulated. Official SDK local tests are not real testnet receipts. Mainnet is unsupported.
