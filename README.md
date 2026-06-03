# Anvil Codex

An evidence-first coding workflow for Codex.

The included `evidence-forge` plugin surveys existing code before editing, captures a baseline,
records verification in SQLite, supports adversarial review, produces a structured evidence report,
and creates scoped commits with rollback instructions.

## Credit

This project is inspired by [Burke Holland's Anvil](https://github.com/burkeholland/anvil), an
evidence-first coding agent for GitHub Copilot CLI.

Burke Holland created the original Anvil concept and workflow. This repository is an independent
Codex-oriented implementation and is not affiliated with or endorsed by Burke Holland.

## Capabilities

- Understands the request and recalls prior Evidence Forge ledger entries for affected files.
- Searches for existing code patterns and pushes back on risky or duplicative approaches.
- Captures baseline build, test, lint, type-check, parse, or diagnostic evidence before edits.
- Records after-change verification in `.evidence-forge/ledger.sqlite`.
- Runs optional `codex`, `claude`, and `gemini` CLI reviewers when installed and authenticated.
- Generates reports from SQL evidence instead of unsupported claims.
- Commits only registered task files and refuses to include unrelated worktree changes.

## Install

Requirements:

- Codex CLI with plugin support
- Git
- Python 3.10 or newer
- Optional reviewer CLIs: `codex`, `claude`, and `gemini`

Add this GitHub repository as a Codex marketplace:

```bash
codex plugin marketplace add otyce-git/anvil-codex
```

Install the plugin:

```bash
codex plugin add evidence-forge@anvil-codex
```

Start a new Codex thread so the skill is loaded.

## Use

Ask Codex to use Evidence Forge for an implementation task:

```text
Use Evidence Forge to fix the login crash and commit the verified change.
```

Other example prompts:

```text
Run an evidence-first implementation of this feature.
Verify and commit this refactor with Evidence Forge.
Use Evidence Forge to review and fix this bug.
```

## Ledger CLI

The plugin includes a deterministic ledger CLI at:

```text
plugins/evidence-forge/scripts/evidence_forge.py
```

Example:

```bash
python3 plugins/evidence-forge/scripts/evidence_forge.py start \
  --task fix-login-crash \
  --request "Fix login crash" \
  --size medium

python3 plugins/evidence-forge/scripts/evidence_forge.py add-file \
  --task fix-login-crash \
  src/login.py

python3 plugins/evidence-forge/scripts/evidence_forge.py run \
  --task fix-login-crash \
  --phase baseline \
  --check tests \
  -- pytest -q

python3 plugins/evidence-forge/scripts/evidence_forge.py report \
  --task fix-login-crash
```

## Limitations

- Recall covers the current conversation and Evidence Forge's own project ledger. It does not claim
  access to unavailable Codex session-history databases.
- Multi-vendor review requires the corresponding CLI to be installed and authenticated.
- Automatic commit is intentionally disabled outside Git repositories or when unrelated changes are
  present.

## License

MIT
