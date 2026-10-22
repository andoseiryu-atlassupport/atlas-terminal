# Discovery and installed-tool evaluations

Two distinct case sets are provided. `discovery-questions.json` measures whether a search-enabled assistant finds or cites public Atlas documentation. `research-workflows.json` checks whether an installed skill and MCP connection complete supported tasks and respect coverage limits. Neither file contains measured model results or triggers model requests.

For a manual observation record the date, product/client version, model, search setting, question ID, exact prompt, Atlas mention, cited URLs and result notes. Compare branded questions separately from unbranded questions. Search availability, personalization and nondeterministic retrieval limit comparisons; do not treat a single answer as a stable ranking.

For installed-tool cases record the configured provider, skill/MCP version, actual tools called, per-item outcomes, evidence resolution, unsupported-request handling and task completion. Keep fixture-only success separate from live source access. Do not store personal prompts, credentials or raw private logs in public results.

The default automated acceptance suite checks protocol, data contracts and transport using fixtures, without an LLM or external source requests. It does not measure citation rate, model task success or token savings. Publication, search inclusion, plugin installation and actual tool execution are separate events.

## Run the installed MCP contract evaluation

```sh
uv run --offline python scripts/run_research_workflow.py --output build/evals/fixture-workflow.json
```

This executable evaluation uses one official stdio MCP connection, four company/macro/FX queries and all returned evidence IDs. Seven checks cover the research-only tool surface, dataset discovery, configuration without probes, successful independent reads, evidence resolution, explicit quality rejection and unsupported-dataset rejection. CI retains the fictional JSON report as an artifact. The result is a protocol/contract evaluation, not a measured model answer.

Reports include per-call wall time and serialized response bytes, which are observations of that run, not comparative latency or token benchmarks. Dates, machine conditions, caches and transport affect them. Full query arguments, per-item quality/coverage, records and original evidence remain inspectable in the output file. No LLM is called.

The [runnable guide](../docs/company-macro-fx.md) documents real public-source opt-in and the actual remote URL mode. Those modes require `--allow-network`. A public remote evaluation checks non-synthetic health metadata and uses private environment authentication; it never embeds a token in the saved query. Failed workflows return nonzero. [Public checks](../docs/public-checks.md) run independently from search-discovery observations.
