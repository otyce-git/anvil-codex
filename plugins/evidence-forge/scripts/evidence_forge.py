#!/usr/bin/env python3
"""SQLite-backed evidence ledger and verification runner for Evidence Forge."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
from typing import Sequence


PHASES = ("baseline", "after", "review", "commit")
REVIEWERS = {
    "codex": ["codex", "exec", "--sandbox", "read-only", "--color", "never"],
    "claude": ["claude", "-p", "--permission-mode", "plan"],
    "gemini": ["gemini", "-p"],
}


def run_process(command: Sequence[str], cwd: pathlib.Path, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def project_root() -> pathlib.Path:
    result = run_process(["git", "rev-parse", "--show-toplevel"], pathlib.Path.cwd())
    if result.returncode == 0:
        return pathlib.Path(result.stdout.strip()).resolve()
    return pathlib.Path.cwd().resolve()


def db_path(root: pathlib.Path) -> pathlib.Path:
    override = os.environ.get("EVIDENCE_FORGE_DB")
    return pathlib.Path(override).expanduser().resolve() if override else root / ".evidence-forge" / "ledger.sqlite"


def connect(root: pathlib.Path) -> sqlite3.Connection:
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            request TEXT NOT NULL,
            size TEXT NOT NULL CHECK(size IN ('small', 'medium', 'large')),
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            commit_sha TEXT,
            rollback_command TEXT
        );
        CREATE TABLE IF NOT EXISTS task_files (
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            PRIMARY KEY(task_id, file_path)
        );
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            phase TEXT NOT NULL CHECK(phase IN ('baseline', 'after', 'review', 'commit')),
            check_name TEXT NOT NULL,
            tool TEXT NOT NULL,
            command TEXT,
            exit_code INTEGER,
            output_snippet TEXT,
            passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS checks_task_phase_idx ON checks(task_id, phase, id);
        """
    )
    return connection


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def require_task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise SystemExit(f"Unknown task: {task_id}")
    return row


def normalize_file(root: pathlib.Path, value: str) -> str:
    path = pathlib.Path(value)
    absolute = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        return absolute.relative_to(root).as_posix()
    except ValueError as exc:
        raise SystemExit(f"File is outside project root: {value}") from exc


def snippet(text: str, limit: int = 4000) -> str:
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else cleaned[-limit:]


def insert_check(
    connection: sqlite3.Connection,
    task_id: str,
    phase: str,
    check_name: str,
    tool: str,
    command: str | None,
    exit_code: int | None,
    output: str,
    passed: bool,
) -> None:
    require_task(connection, task_id)
    connection.execute(
        """
        INSERT INTO checks(task_id, phase, check_name, tool, command, exit_code, output_snippet, passed, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, phase, check_name, tool, command, exit_code, snippet(output), int(passed), now()),
    )
    connection.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (now(), task_id))
    connection.commit()


def cmd_start(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    timestamp = now()
    connection.execute(
        """
        INSERT INTO tasks(task_id, request, size, status, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET request = excluded.request, size = excluded.size, updated_at = excluded.updated_at
        """,
        (args.task, args.request, args.size, timestamp, timestamp),
    )
    connection.commit()
    print(f"Started {args.size} task {args.task}")
    print(f"Ledger: {db_path(root)}")
    return 0


def cmd_add_file(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    require_task(connection, args.task)
    for value in args.files:
        file_path = normalize_file(root, value)
        connection.execute(
            "INSERT OR IGNORE INTO task_files(task_id, file_path) VALUES (?, ?)",
            (args.task, file_path),
        )
        print(file_path)
    connection.commit()
    return 0


def cmd_recall(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    file_path = normalize_file(root, args.file)
    rows = connection.execute(
        """
        SELECT t.task_id, t.request, t.status, t.updated_at, c.phase, c.check_name, c.passed, c.output_snippet
        FROM task_files f
        JOIN tasks t ON t.task_id = f.task_id
        LEFT JOIN checks c ON c.task_id = t.task_id AND c.passed = 0
        WHERE f.file_path = ?
        ORDER BY t.updated_at DESC, c.id DESC
        LIMIT 20
        """,
        (file_path,),
    ).fetchall()
    print(json.dumps([dict(row) for row in rows], indent=2))
    return 0


def cmd_record(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    del root
    output = args.output or ""
    passed = args.passed if args.passed is not None else args.exit_code == 0
    insert_check(connection, args.task, args.phase, args.check, args.tool, args.command, args.exit_code, output, passed)
    print(f"Recorded {args.phase}/{args.check}: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def cmd_run(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("run requires a command after --")
    result = run_process(command, root)
    command_text = " ".join(command)
    insert_check(
        connection,
        args.task,
        args.phase,
        args.check,
        command[0],
        command_text,
        result.returncode,
        result.stdout,
        result.returncode == 0,
    )
    sys.stdout.write(result.stdout)
    print(f"\nRecorded {args.phase}/{args.check}: exit {result.returncode}")
    return result.returncode


def staged_diff(root: pathlib.Path) -> str:
    result = run_process(["git", "--no-pager", "diff", "--staged", "--"], root)
    if result.returncode != 0:
        raise SystemExit(result.stdout.strip() or "Unable to read staged diff")
    if not result.stdout.strip():
        raise SystemExit("No staged diff to review")
    return result.stdout


def cmd_review(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    require_task(connection, args.task)
    executable = REVIEWERS[args.reviewer][0]
    if shutil.which(executable) is None:
        message = f"Reviewer unavailable: {executable} is not installed or not on PATH."
        insert_check(connection, args.task, "review", f"review-{args.reviewer}", args.reviewer, None, 127, message, False)
        print(message)
        return 127

    diff = staged_diff(root)
    prompt = (
        "Review this staged diff for bugs, security vulnerabilities, logic errors, race conditions, "
        "edge cases, missing error handling, and architectural violations. Ignore style-only issues. "
        "For each issue explain impact and a concrete fix. If there are no issues, say so.\n\n"
        f"{diff}"
    )
    command = REVIEWERS[args.reviewer]
    result = run_process(command + [prompt], root)
    insert_check(
        connection,
        args.task,
        "review",
        f"review-{args.reviewer}",
        args.reviewer,
        " ".join(command) + " <prompt>",
        result.returncode,
        result.stdout,
        result.returncode == 0,
    )
    sys.stdout.write(result.stdout)
    return result.returncode


def changed_files(root: pathlib.Path) -> set[str]:
    commands = [
        ["git", "diff", "--name-only", "--"],
        ["git", "diff", "--cached", "--name-only", "--"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    files: set[str] = set()
    for command in commands:
        result = run_process(command, root)
        if result.returncode != 0:
            raise SystemExit(result.stdout.strip() or f"Failed: {' '.join(command)}")
        files.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return {file_path for file_path in files if not file_path.startswith(".evidence-forge/")}


def cmd_commit(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    task = require_task(connection, args.task)
    git_check = run_process(["git", "rev-parse", "--show-toplevel"], root)
    if git_check.returncode != 0:
        raise SystemExit("Cannot commit outside a Git repository")

    passing_after = connection.execute(
        "SELECT COUNT(*) AS count FROM checks WHERE task_id = ? AND phase = 'after' AND passed = 1",
        (args.task,),
    ).fetchone()["count"]
    minimum = 1 if task["size"] == "small" else 2
    if passing_after < minimum:
        raise SystemExit(f"Need at least {minimum} passing after-change checks before commit; found {passing_after}")

    task_files = {
        row["file_path"]
        for row in connection.execute("SELECT file_path FROM task_files WHERE task_id = ?", (args.task,)).fetchall()
    }
    if not task_files:
        raise SystemExit("No task files registered")
    unrelated = changed_files(root) - task_files
    if unrelated:
        raise SystemExit("Refusing to commit unrelated changes:\n" + "\n".join(sorted(unrelated)))

    add_result = run_process(["git", "add", "--", *sorted(task_files)], root)
    if add_result.returncode != 0:
        raise SystemExit(add_result.stdout.strip() or "git add failed")
    commit_result = run_process(["git", "commit", "-m", args.message], root)
    if commit_result.returncode != 0:
        insert_check(connection, args.task, "commit", "git-commit", "git", "git commit", commit_result.returncode, commit_result.stdout, False)
        sys.stdout.write(commit_result.stdout)
        return commit_result.returncode

    sha_result = run_process(["git", "rev-parse", "HEAD"], root)
    sha = sha_result.stdout.strip()
    rollback = f"git revert {sha}"
    connection.execute(
        "UPDATE tasks SET status = 'committed', commit_sha = ?, rollback_command = ?, updated_at = ? WHERE task_id = ?",
        (sha, rollback, now(), args.task),
    )
    connection.commit()
    insert_check(connection, args.task, "commit", "git-commit", "git", "git commit", 0, commit_result.stdout + f"\nRollback: {rollback}", True)
    sys.stdout.write(commit_result.stdout)
    print(f"Rollback: {rollback}")
    return 0


def cmd_report(args: argparse.Namespace, root: pathlib.Path, connection: sqlite3.Connection) -> int:
    del root
    task = require_task(connection, args.task)
    files = [row["file_path"] for row in connection.execute(
        "SELECT file_path FROM task_files WHERE task_id = ? ORDER BY file_path", (args.task,)
    ).fetchall()]
    checks = [dict(row) for row in connection.execute(
        "SELECT phase, check_name, tool, command, exit_code, passed, output_snippet, created_at "
        "FROM checks WHERE task_id = ? ORDER BY id", (args.task,)
    ).fetchall()]
    payload = {"task": dict(task), "files": files, "checks": checks}
    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return 0

    print(f"# Evidence Forge Report: {task['task_id']}")
    print()
    print(f"- Size: {task['size']}")
    print(f"- Status: {task['status']}")
    print(f"- Request: {task['request']}")
    if task["commit_sha"]:
        print(f"- Commit: {task['commit_sha']}")
        print(f"- Rollback: `{task['rollback_command']}`")
    print()
    print("## Files")
    for file_path in files:
        print(f"- `{file_path}`")
    for phase in PHASES:
        phase_checks = [check for check in checks if check["phase"] == phase]
        if not phase_checks:
            continue
        print()
        print(f"## {phase.title()}")
        print("| Check | Result | Tool | Command |")
        print("|---|---|---|---|")
        for check in phase_checks:
            result = "PASS" if check["passed"] else "FAIL"
            command = (check["command"] or "").replace("|", "\\|")
            print(f"| {check['check_name']} | {result} | {check['tool']} | `{command}` |")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="subcommand", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--task", required=True)
    start.add_argument("--request", required=True)
    start.add_argument("--size", choices=("small", "medium", "large"), required=True)
    start.set_defaults(func=cmd_start)

    add_file = subparsers.add_parser("add-file")
    add_file.add_argument("--task", required=True)
    add_file.add_argument("files", nargs="+")
    add_file.set_defaults(func=cmd_add_file)

    recall = subparsers.add_parser("recall")
    recall.add_argument("--file", required=True)
    recall.set_defaults(func=cmd_recall)

    record = subparsers.add_parser("record")
    record.add_argument("--task", required=True)
    record.add_argument("--phase", choices=PHASES, required=True)
    record.add_argument("--check", required=True)
    record.add_argument("--tool", required=True)
    record.add_argument("--command")
    record.add_argument("--exit-code", type=int)
    record.add_argument("--output")
    record.add_argument("--passed", action=argparse.BooleanOptionalAction)
    record.set_defaults(func=cmd_record)

    run = subparsers.add_parser("run")
    run.add_argument("--task", required=True)
    run.add_argument("--phase", choices=("baseline", "after"), required=True)
    run.add_argument("--check", required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)

    review = subparsers.add_parser("review")
    review.add_argument("--task", required=True)
    review.add_argument("--reviewer", choices=tuple(REVIEWERS), required=True)
    review.set_defaults(func=cmd_review)

    commit = subparsers.add_parser("commit")
    commit.add_argument("--task", required=True)
    commit.add_argument("--message", required=True)
    commit.set_defaults(func=cmd_commit)

    report = subparsers.add_parser("report")
    report.add_argument("--task", required=True)
    report.add_argument("--format", choices=("markdown", "json"), default="markdown")
    report.set_defaults(func=cmd_report)
    return result


def main() -> int:
    args = parser().parse_args()
    root = project_root()
    connection = connect(root)
    try:
        return args.func(args, root, connection)
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
