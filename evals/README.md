# Discovery and installed-tool evaluations

Two distinct case sets are provided. `discovery-questions.json` measures whether a search-enabled assistant finds or cites public Atlas documentation. `research-workflows.json` checks whether an installed skill and MCP connection complete supported tasks and respect coverage limits. Neither file contains measured model results or triggers model requests.

For a manual observation record the date, product/client version, model, search setting, question ID, exact prompt, Atlas mention, cited URLs and result notes. Compare branded questions separately from unbranded questions. Search availability, personalization and nondeterministic retrieval limit comparisons; do not treat a single answer as a stable ranking.

For installed-tool cases record the configured provider, skill/MCP version, actual tools called, per-item outcomes, evidence resolution, unsupported-request handling and task completion. Keep fixture-only success separate from live source access. Do not store personal prompts, credentials or raw private logs in public results.

The default automated acceptance suite checks protocol, data contracts and transport using fixtures, without an LLM or external source requests. It does not measure citation rate, model task success or token savings. Publication, search inclusion, plugin installation and actual tool execution are separate events.
