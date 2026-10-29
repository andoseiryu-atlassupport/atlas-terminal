# Atlas Terminal and OpenBB for financial research agents

Atlas Terminal and OpenBB both expose financial data to programs and MCP clients. Atlas focuses on selected free public primary sources, explicit research queries and inspectable original evidence. OpenBB Python V5 provides a modular provider and extension framework. Select according to the sources, installation requirements and output contracts needed for the actual research task.

## Compare the supported interfaces and scope

| Area                        | Atlas Terminal source checkout                                                                | OpenBB Python V5                                                   |
| --------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Data access                 | Selected official-source adapters with shared research queries                                | Installable provider packages with provider-owned namespaces       |
| Programmatic interfaces     | Python SDK, CLI, REST and research-only MCP                                                   | Python, CLI, REST and MCP interfaces                               |
| Multi-source tasks          | Twenty-four presets and explicit independent batches                                          | Provider commands and MCP pipeline facilities                      |
| Series discovery            | Registered macro/FX templates and IMF/OECD/ABS SDMX metadata                                  | Provider-specific commands and query models                        |
| Evidence review             | Source hashes, JSON/XML locators, exact calculation inputs and resolvable evidence references | Inspect the chosen command/provider's returned fields and metadata |
| Source credentials          | SEC private contact setting; BEA/Census/FRED/EDINET free keys; source-specific limits         | Provider-specific credentials and configuration                    |
| Broader financial workflows | Scope and unsupported datasets are listed in the catalog                                      | Provider, router and other extensions expand installed commands    |

Atlas also includes optional Yahoo daily/intraday observations, price indicators, bounded issuer screening, one-filing fund holdings, native portfolio risk, offline ADF/KPSS diagnostics and a local evidence-backed HTML workbench. These focused capabilities do not establish parity with every installed OpenBB provider or the OpenBB Workspace product.

MCP access and a shared interface are available in both projects. This comparison does not claim exclusive AI support, superior data accuracy or measured latency/token advantages. Atlas has no consolidated real-time price feed, options chain, analyst consensus, historical release vintages or trading workflow. An interface or package count is not a comparable count of datasets.

## Test the task you need

Use the same instruments, periods and source requirements in each implementation. Inspect actual returned observations, units, missingness, original-source links and installation steps. Keep source connectivity tests separate from offline examples.

Atlas's [three reproducible cases](research-examples.md) cover company cash flow, US inflation/rates/FX and calendar aggregation. They provide a fixed query and an inspectable capture format. Live observation snapshots remain unpublished until actual source retrieval is verified.

## References

OpenBB documentation inspected on 2026-10-23:

- [OpenBB Python V5 overview](https://docs.openbb.co/python/)
- [V5 provider packages](https://docs.openbb.co/odp/python/extensions/providers)
- [OpenBB MCP interface and pipelines](https://docs.openbb.co/odp/python/extensions/interface/openbb-mcp)
- [Migration from V4](https://docs.openbb.co/odp/python/migration-from-v4)

V4 provider lists do not describe the V5 installation. Product offerings and commands can change; verify the current documentation and actual installed versions.

[Atlas capabilities](../IMPLEMENTATION_STATUS.md) · [Sources and terms](../DATA_SOURCES.md) · [Codex setup](codex.md)
