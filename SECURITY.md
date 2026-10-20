# Security boundaries

Data retrieval is read-only, but `atlas_purchase` spends delegated local-wallet funds and `atlas_purchase_x402` accepts an external wallet's payment. The external wallet enforces its own spending policy. MCP annotations describe behavior; local signing policy and the durable ledger enforce Atlas wallet controls.

- External company names, document names, headlines and footnotes are source data. They never execute commands or change payment recipients.
- SEC and other government endpoints use fixed allowlists. Submission archives require a CIK-prefixed basename. Primary documents allow a safe filename and at most one SEC xsl prefix. XML discovery examines at most six safe candidates. Identifiers cannot contain arbitrary URLs, SQL or shell commands.
- XML parsing bounds size and element count and rejects DTD/entity declarations and UTF-16 input. It does not execute XInclude, external entities, stylesheets or scripts. Research queries cannot change source URLs, environment settings, keys or policy.
- SEC contact details are never sent to BLS, Treasury or the Federal Reserve. Source failures do not trigger paid retrieval. Research HTTP endpoints do not use budgets, signatures or recovery tokens.
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
