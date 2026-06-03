# Evidence Forge Reference

## Ledger location

The CLI resolves the project root with `git rev-parse --show-toplevel`. Outside Git it uses the
current working directory. The database is stored at:

```text
<project-root>/.evidence-forge/ledger.sqlite
```

## Tables

- `tasks`: request, size, status, timestamps, commit SHA, and rollback command.
- `task_files`: files associated with a task.
- `checks`: baseline, after-change, review, and commit evidence.

The report is generated only from these tables.

## Check phases

- `baseline`: evidence captured before edits.
- `after`: build, type check, lint, test, diagnostics, parse, or smoke evidence after edits.
- `review`: independent reviewer output, including explicit unavailable-reviewer records.
- `commit`: the commit result and rollback command.

## Reviewer behavior

`review --reviewer codex|claude|gemini` checks whether the requested CLI exists. If it does not, the
CLI records a failed review row with an unavailable message and exits non-zero. If it exists, the
reviewer receives the staged diff and a bug-focused prompt. Review output is evidence, not an
automatic pass: the primary agent must inspect findings, fix real issues, rerun verification, and
record another review.

Use built-in subagents instead when available, then record their verdict with `record`.

## Commit safety

`commit` is intentionally strict:

- It requires a Git repository.
- It requires at least two passing `after` checks.
- It refuses when tracked, staged, or untracked changes include files not registered for the task.
- It stages only registered task files.
- It stores the resulting SHA and rollback command in SQL.

The rollback command is `git revert <commit-sha>`.
