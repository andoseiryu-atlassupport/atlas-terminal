# Retrieve rates and FX through one MCP connection

This executable walkthrough retrieves USD/JPY reference FX and US SOFR, preserves their separate observation dates and resolves every returned source-evidence reference through one official MCP session. It requires no LLM, data account or wallet. The recorded results below are **fictional test observations**, not current or historical market facts.

## Watch the recorded execution

[Watch the 60-second research replay](assets/showcase/research.mp4)

[Transcript](assets/showcase/research.txt) · [Complete recorded JSON](assets/showcase/research.json) · [Captions](assets/showcase/research.vtt) · [Recording metadata and checksums](assets/showcase/manifest.json)

The video is a paced rendering of executed JSON output. Its playback duration is not the command's execution time or a performance benchmark. It contains no simulated agent conversation.

## Run the same workflow

Install Python 3.12+ and uv, then:

```sh
uv tool install 'git+https://github.com/andoseiryu-atlassupport/atlas-terminal.git@v0.7.0'
atlas showcase research
atlas showcase research --json > research-report.json
```

The command starts the installed Atlas research-only server, uses the official MCP client and calls `atlas_get_research_preset` with:

```json
{
  "preset": "fx_context",
  "base_currency": "USD",
  "quote_currency": "JPY",
  "include": ["fx", "us_sofr"],
  "view": "compact"
}
```

It then resolves the returned IDs with `atlas_get_evidence` in the same connection. The JSON report embeds those resolved references, the full preset plan, every selected record, source hashes and data-quality metadata. The client makes no purchase or LLM request.

## Inspect the recorded result

The included fictional execution returned:

```text
fx: 120 JPY_per_USD | observation 2026-10-19
us_sofr: 1.30 percent_per_annum | observation 2026-10-19
Resolved original evidence: 9 references
```

These invented values test interpretation and evidence handling. They must not be cited as market observations. The full result retains missing older observations and selection limits; `PASS` means the walkthrough executed, not that every historical observation is available.

USD/JPY is calculated as JPY per EUR divided by USD per EUR using the same ECB observation date. SOFR remains a percent-per-annum overnight rate. Equal dates in this fictional run do not imply that the two publishers update together. The output preserves the original dates and units rather than imposing a shared timestamp.

## Read actual primary-source data

From a host permitted to reach `www.ecb.europa.eu` and `markets.newyorkfed.org`, run:

```sh
atlas showcase research --provider public --allow-network --json > public-research-report.json
```

This opt-in reads actual public data. Results vary with source availability and observation dates. No real-source execution is represented by the included fictional recording. Cite the ECB and New York Fed for numerical facts, with the observation date and original link from the resulting evidence. Cite Atlas for selection and calculation methods.

Both modes use isolated temporary state and remove it on exit. Resolved evidence remains embedded in the report; its temporary lookup IDs cannot be reused in a different server. Configure a regular [MCP connection](codex.md) for persistent research sessions. The [three capture cases](research-examples.md) provide content-addressed observation export when actual-source results have been verified.

## Give an agent the same task

After following [Codex setup](codex.md), ask:

> Use Atlas to retrieve USD/JPY reference FX and SOFR. Show each observation date and unit, explain the cross-rate calculation, and resolve the original evidence. Report missing values and source failures explicitly.

The standalone replay does not measure an LLM's ability to follow that request. [Report an installation or workflow outcome](https://github.com/andoseiryu-atlassupport/atlas-terminal/issues/new?template=first-run.yml) after trying it.

[Japanese portfolio risk walkthrough](japan-risk-walkthrough.md) · [Data sources](../DATA_SOURCES.md) · [Citation rules](citations.md)
