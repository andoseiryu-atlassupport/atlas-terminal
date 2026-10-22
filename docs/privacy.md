# Privacy and data handling

This notice describes the Atlas Terminal open-source distribution and documentation. No official public Atlas remote MCP endpoint or user-account service is currently operated. An operator deploying the software must publish a deployment-specific notice covering their infrastructure and practices before public use.

## Local software

Atlas reads research queries and the process environment needed for the selected provider. It stores public-source response bytes, cache metadata, research snapshots, evidence and optional source-check baselines in the private state directory selected by the operator. The operator controls retention and deletion; evidence and pagination also have application-level expiry and size bounds described in the [agent contracts](agents.md).

Research requests go to the relevant public publisher. For SEC requests the identifying User-Agent supplied by the operator includes their actual contact email. That contact setting is not returned in research records or configuration diagnostics. Publishers and an operator's network infrastructure may process request metadata under their own policies.

Research does not require an LLM or model API key. Atlas does not include application analytics, advertising trackers or a telemetry upload service. A configured MCP client or hosting provider has its own data handling. The static documentation site uses no analytics scripts, cookies set by application code or external fonts; GitHub Pages may process ordinary hosting/access metadata under GitHub's policies.

## Self-hosted remote MCP

The remote ingress reads authentication, Host and Origin headers to check access. It does not persist those header values or research request bodies as an access log. The service shares its public-source research cache/evidence volume and is not tenant-isolated storage. Operators must separately document proxy/hosting logs, abuse prevention, retention, contact channels and applicable policies. Do not send personal portfolios, credentials, private contact values or unrelated personal data in research tool arguments.

The original optional snapshot-payment service has additional private order and recovery state. It is not exposed by the research-only remote service. Keep all payment configuration and databases private. [Security guidance](../SECURITY.md).

## Documentation support

Public GitHub issues are visible to others. Report reproducible technical problems without contact settings, credentials, private databases or personal data. Use the [support guidance](support.md) and GitHub's own account/privacy controls when sharing material.
