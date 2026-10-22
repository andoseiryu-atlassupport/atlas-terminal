# Security boundaries

Data retrieval is read-only, but `atlas_purchase` spends delegated local-wallet funds and `atlas_purchase_x402` accepts an external wallet's payment. The external wallet enforces its own spending policy. MCP annotations describe behavior; local signing policy and the durable ledger enforce Atlas wallet controls.

- External company names, document names, headlines and footnotes are source data. They never execute commands or change payment recipients.
- SEC and other public source endpoints use fixed allowlists, including the ECB FX window and New York Fed EFFR/SOFR feeds. Submission archives require a CIK-prefixed basename. Primary documents allow a safe filename and at most one SEC xsl prefix. XML discovery examines at most six safe candidates. Identifiers cannot contain arbitrary URLs, SQL or shell commands.
- XML parsing bounds size and element count and rejects DTD/entity declarations and UTF-16 input. It does not execute XInclude, external entities, stylesheets or scripts. Research queries cannot change source URLs, environment settings, keys or policy.
- SEC contact details are never sent to BLS, Treasury, the Federal Reserve, ECB or New York Fed. Source failures do not trigger paid retrieval. Research HTTP endpoints do not use budgets, signatures or recovery tokens.
- Research requirements evaluate the full selected result before projection/paging; cursor reads recheck source age without refetching. Batch requests are bounded to ten independent items, 16 KiB input and 4 MiB aggregate output. Per-item errors never authorize payment or automatic retry. Opt-in source checks revalidate through shared cache/rate/quota stores; integrity failures preserve last-success baselines and never overwrite them silently.
- Optional compact research stores provenance and bounded snapshots in a separate private database. IDs and cursors are validated local lookup handles, not arbitrary URLs or paid-result capabilities. Hashes detect stored-payload corruption; expiry never changes purchase budgets. Fixture and public evidence scopes are separate.
- Structured error actions are advisory and currently have `automatic=false`. They never change coverage acceptance, policy, wallets or payment state. Ambiguous purchase transport directs callers to existing order status, not fresh signing. Validation errors omit submitted values.
- Hosted origins and Facilitators come only from operator configuration. Redirects are disabled. Recipient, network, token, domain, amount, validity window and resource are checked before signing.
- Mainnet is unsupported. Real integration is restricted to Base Sepolia USDC and exact/EIP-3009. Mock mode uses a fictional network, asset and separate headers.
- Budget reservations and the SIGNING claim are atomic SQLite write transactions. Daily usage is shared across tasks and sessions using the same signer, and unresolved reservations survive day changes.
- The server claims an order and nonce before settlement. Timeouts or crashes leave UNKNOWN state; they do not trigger another transfer, signature or budget release.
- Reconciliation reads only a configured Base Sepolia RPC. Success requires canonical finalized token/nonce/recipient/amount evidence. Failure requires both finalized expiry and an unused nonce. Reverted transactions and unavailable evidence alone never release budgets. Evidence and terminal changes commit with a hashed private audit record.
- Native MCP proofs belong only in transport metadata. Durable exclusive per-quote claims prevent switching between local/external wallet paths after an attempted payment. Unknown external attempts do not prompt another signature. The compatibility adapter changes field names/timeout units, not the official SDK's signing logic.
- A quote ID is not authorization. Recovery bearers grant status and purchased-artifact access only. The server stores token hashes; the client stores private recovery capabilities.
- Keys, seeds, raw payment signatures, bearers, purchase databases and SEC contact settings stay out of public files and tool output. Sensitive HTTP/payment diagnostics are filtered, and errors do not expose raw inputs or upstream exception text.

Private state uses mode 0700 directories and 0600 databases. Protect SQLite WAL/SHM files and backups as well. MCP tools cannot edit policy files or wallets.

**A process with shell access as the same OS user can read or modify environment settings, policy files and databases. Local policy is not hardware-signer or OS-sandbox isolation.** Distributed budgets, custody, mainnet security guarantees and SLAs are unsupported.

## Reporting

A private vulnerability-reporting contact has not been designated. Do not post keys, payment proofs, recovery tokens or real purchase databases in public issues. A private reporting channel must be configured before public operation.
