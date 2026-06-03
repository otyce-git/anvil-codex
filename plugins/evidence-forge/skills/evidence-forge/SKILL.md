---
name: evidence-forge
description: Use an evidence-first software engineering workflow for implementation, bug fixes, refactors, or code review tasks that should survey existing patterns, capture a baseline, record verification in a SQLite ledger, run adversarial reviewers, produce a structured evidence report, and create a scoped commit with rollback instructions.
---

# Evidence Forge

Use this workflow when the user asks to implement or fix code and wants strong verification. The
ledger CLI is at `../../scripts/evidence_forge.py` relative to this file.

This Codex-oriented workflow is inspired by Burke Holland's Anvil:
https://github.com/burkeholland/anvil

## Core rules

- Read the codebase before editing. Search for at least two relevant patterns or call sites.
- Push back before editing when the requested approach creates duplication, breaks an established
  contract, hides the real problem, or introduces unsafe behavior.
- Never claim a check, review, or recall result unless it was actually run and recorded.
- Never overwrite or commit unrelated user changes.
- Do not auto-commit outside a Git repository or when unrelated changes are present.
- Use `.evidence-forge/ledger.sqlite` as the SQL evidence ledger. Recommend adding
  `.evidence-forge/` to `.gitignore`; do not commit the database.

## Task sizing

- Small: narrow documentation, rename, typo, or one-line configuration change. Verify, but ledger
  and adversarial review are optional unless risk is high.
- Medium: bug fix, feature, or refactor. Full workflow and at least one reviewer.
- Large: cross-module change, public API, auth, security, payments, deletion, migration, or
  concurrency. Full workflow and up to three reviewers.

Treat uncertain tasks as Medium.

## Workflow

1. **Understand and recall**
   - Parse the request into goal, acceptance criteria, assumptions, and likely files.
   - Check current conversation history for earlier work and failures.
   - For each likely file, run `recall`. If the ledger has no history, say nothing.

2. **Survey and pushback**
   - Search for existing implementations, conventions, tests, and dependents.
   - Surface a reuse opportunity when extending existing code is materially simpler.
   - If pushback is needed, explain the issue and wait for confirmation before editing.

3. **Start and baseline**
   - Create a task slug and start the ledger task.
   - Record likely files with `add-file`.
   - Run applicable build, type check, lint, tests, diagnostics, or parse checks with `run` using
     phase `baseline`.
   - Do not edit before at least one baseline row exists for Medium or Large tasks.

4. **Implement**
   - Follow neighboring code and existing abstractions.
   - Keep scope narrow and add tests when infrastructure exists.
   - Record every changed file with `add-file`.

5. **The Forge**
   - Run every applicable verification command with phase `after`.
   - Minimum after-change signals: two for Medium, three for Large.
   - Fix failures and rerun them. Do not present newly broken code.
   - Stage only task files before review.
   - Prefer available Codex subagents for independent review. When vendor CLIs are configured, the
     ledger CLI can run `codex`, `claude`, and `gemini` reviewers with the `review` command.
   - Record skipped or unavailable reviewers honestly. Never describe them as passed.

6. **Evidence and commit**
   - Generate the report from SQL with `report`.
   - Summarize changes, regressions, review findings, confidence, and unresolved risks.
   - For Medium and Large tasks, use `commit` after verification. It must refuse if unrelated
     changes exist. Include the returned rollback command.

## CLI examples

```bash
python3 /path/to/evidence_forge.py start \
  --task fix-login-crash --request "Fix login crash" --size medium

python3 /path/to/evidence_forge.py add-file --task fix-login-crash src/login.py

python3 /path/to/evidence_forge.py run \
  --task fix-login-crash --phase baseline --check tests -- pytest -q

python3 /path/to/evidence_forge.py review \
  --task fix-login-crash --reviewer codex

python3 /path/to/evidence_forge.py report --task fix-login-crash

python3 /path/to/evidence_forge.py commit \
  --task fix-login-crash --message "Fix login crash"
```

Read [workflow.md](references/workflow.md) when you need the SQL schema, report interpretation, or
reviewer behavior.
