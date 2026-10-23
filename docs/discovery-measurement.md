# Record search discovery and cited answers

Atlas Terminal includes twenty fixed public research questions for manual observations in search-enabled assistants. Record the exact product, model, timestamp, prompt and cited URLs. Branded and unbranded questions are summarized separately. A tool listing, an HTTP check or a search-engine result is not a measured assistant citation.

## Prepare a controlled run

```sh
uv run --offline python scripts/summarize_discovery.py --template --output build/evals/discovery-observations.json
```

This creates twenty `not_run` records linked to the SHA-256 of the [question set](../evals/discovery-questions.json). It makes no model requests. Use a new conversation without Atlas-specific instructions or attached project context. Keep the search setting and product/model version recorded; repeat observations across dates before drawing conclusions.

For an answer actually observed, set `status` to `observed`, complete the product/model/time/search fields, retain the answer's SHA-256 in `response_sha256`, review `atlas_mentioned` and copy the actual cited URLs into `cited_urls`. Judge whether the answer identifies this project rather than a similarly named product. Keep the full answer evidence in an operator-controlled log. Use only the predefined public prompts in shareable records; private research questions, credentials and personal history do not belong in this dataset.

Review accuracy as `correct`, `incorrect` or `unassessed`. Record `task_completed` only after actually testing the requested installed-tool workflow; otherwise retain `null`. The summary validates exact prompts and timezone-aware timestamps and rejects duplicate observations or credential-bearing citation URLs.

```sh
uv run --offline python scripts/summarize_discovery.py --input build/evals/discovery-observations.json --output build/evals/discovery-summary.json
```

## Read the report

- Citation rate uses actually observed answers as its denominator. Pending records are excluded; an empty run has an unknown rate, rather than zero.
- Mentions and citations are distinct. Recognized citation URLs belong to the documentation site or the exact Atlas GitHub repository.
- Accuracy is calculated only over reviewed answers. Task completion is counted only when observed.
- Search probes, synthetic workflows and HTTP access checks remain separate evidence.

Manual records are operator attestations, not independent validation of model behavior. Search settings, personalization, model changes and retrieval variation affect comparisons. No model citation or task-success measurements have been established by the shipped template.

The [2026-10-23 search-result probes](../evals/search-probes-2026-10-23.json) cover the same twenty questions. The exact Atlas repository/site URL was absent from those returned results. These observations do not establish search-index exclusion and are excluded from model citation rates.

[Installed MCP evaluation](../evals/README.md) · [Public HTTP checks](public-checks.md) · [Research cases](research-examples.md)
