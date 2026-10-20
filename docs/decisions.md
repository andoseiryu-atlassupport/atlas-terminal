# Technical design

| Choice                                           | Reason                                                                                                       |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| Direct SEC REST                                  | Centralize endpoint selection, period rules, rates and caching                                               |
| Free government primary sources                  | Use SEC, BLS, Treasury and Federal Reserve data without unverified redistribution rights for market feeds    |
| Shared research query                            | Apply dataset-specific arguments, limits, provenance and coverage rules across all interfaces                |
| Distinct reported, derived and discovery outputs | Separate quarter/YTD facts, trace ratio inputs and avoid inferring Q4 or 13F value units                     |
| Bounded public transport                         | Keep SEC contact settings separate from other sources and persist shared caching, rates and BLS daily limits |
| Shared application service                       | Use the same financial selection logic in CLI, SDK, MCP and HTTP                                             |
| MCP 2.3.0 MCPServer                              | Use the selected official SDK's public API                                                                   |
| Low-level x402 Facilitator client                | Make one settlement call per durable claim without implicit pending retries                                  |
| EIP-3009 scope                                   | Verify domain, chain, token, recipient, amount and validity at the signing boundary                          |
| Pending unknown settlements                      | Avoid speculative transfers and budget releases; automatic reconciliation is unsupported                     |
| SQLite BLOB snapshots                            | Store immutable bytes and the ledger in one private volume; multiple replicas are unsupported                |

See the [dependency audit](../DEPENDENCY_AUDIT.md) for SDK versions, imports, wire handling and dependency licenses, [LICENSE](../LICENSE) for the code license, and [DATA_SOURCES](../DATA_SOURCES.md) for source-data terms.
