# Atlas Terminal: agent instructions

## Project context

- Read `README.md`, `docs/project-brief.md`, and `CONTRIBUTING.md` before making changes.
- This is a public repository, initially prepared for development in Codex Cloud.
- Product scope and the application stack are undecided. Follow the requested task without inventing product requirements or introducing an application framework without an agreed need.
- Node.js 24 currently supports repository tooling only.

## Working process

- Keep each change focused and reviewable. Prefer a task branch such as `codex/<short-task-name>` and a pull request for subsequent development.
- Preserve existing user changes and unrelated files.
- Write project documentation and user-facing explanations in Japanese unless the task specifies otherwise. Use clear English identifiers in code.
- When a decision changes the product scope or stack, update `docs/project-brief.md` and the affected setup instructions.
- Add dependencies only when the task needs them, and commit the corresponding lockfile.
- Use meaningful behavioral checks when executable features are introduced. Documentation changes need the existing documentation check.

## Commands

```sh
npm ci
npm run check
```

`npm run check` runs Prettier on documentation and configuration files, followed by `git diff --check`. There is no application build or test command yet.
After editing the GitHub Actions workflow, also review the YAML and verify the resulting workflow run when available.

## Public repository hygiene

- Commit only material intended for public access. Keep credentials, local configuration, personal data, and production data out of source control and logs.
- Use synthetic data if examples need data, and label it clearly.
- Keep actual credentials in the relevant environment's secret settings; `.env.example` may contain names and harmless placeholders only.
- Use the normal `pull_request` event for untrusted contributions. Keep CI permissions limited to the job's requirements and pin third-party actions to commit SHAs.

## Completion evidence

- Report what changed, the checks actually run, and any remaining blocker.
- Distinguish a local check, a successful GitHub Actions run, and a verified Codex Cloud task. One does not establish the others.
- Keep license status undecided until the repository owner selects a license.
