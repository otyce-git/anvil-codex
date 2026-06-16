from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "evidence_forge.py"


class EvidenceForgeReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.bin = self.tmp / "bin"
        self.repo.mkdir()
        self.bin.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo, check=True, stdout=subprocess.DEVNULL)
        (self.repo / "sample.txt").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.txt"], cwd=self.repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "base"],
            cwd=self.repo,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        (self.repo / "sample.txt").write_text("after\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.txt"], cwd=self.repo, check=True, stdout=subprocess.DEVNULL)
        self._write_fake_codex()
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "EVIDENCE_FORGE_DB": str(self.tmp / "ledger.sqlite"),
        }
        self.run_cli("start", "--task", "review-test", "--request", "test reviews", "--size", "medium")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def _write_fake_codex(self) -> None:
        fake = self.bin / "codex"
        fake.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os
                import sys
                import time

                args = sys.argv[1:]
                prompt = sys.stdin.read()
                log_path = os.environ.get("FAKE_CODEX_LOG")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as handle:
                        handle.write("ARGS=" + " ".join(args) + "\\n")
                        handle.write("PROMPT_HAS_DIFF=" + str("diff --git" in prompt) + "\\n")
                mode = os.environ.get("FAKE_CODEX_MODE", "success")
                if mode == "sleep":
                    time.sleep(10)
                if mode == "fail":
                    print("review failed")
                    sys.exit(2)
                print("review ok")
                """
            ),
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            env=env or self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def report_json(self) -> dict[str, object]:
        result = self.run_cli("report", "--task", "review-test", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stdout)

    def review_checks(self) -> list[dict[str, object]]:
        report = self.report_json()
        return [check for check in report["checks"] if check["phase"] == "review"]

    def test_codex_review_uses_codex_review_and_stdin(self) -> None:
        log = self.tmp / "codex.log"
        env = {**self.env, "FAKE_CODEX_LOG": str(log)}
        result = self.run_cli("review", "--task", "review-test", "--reviewer", "codex", env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        checks = self.review_checks()
        self.assertEqual(checks[-1]["status"], "PASS")
        self.assertIn("codex --sandbox read-only review - <stdin>", checks[-1]["command"])
        self.assertIn("ARGS=--sandbox read-only review -", log.read_text(encoding="utf-8"))
        self.assertIn("PROMPT_HAS_DIFF=True", log.read_text(encoding="utf-8"))

    def test_gpt_review_uses_configured_model(self) -> None:
        env = {
            **self.env,
            "EVIDENCE_FORGE_GPT_REVIEW_MODEL": "gpt-review-test",
            "FAKE_CODEX_LOG": str(self.tmp / "codex.log"),
        }
        result = self.run_cli("review", "--task", "review-test", "--reviewer", "gpt", env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "PASS")
        self.assertIn("model=gpt-review-test", check["command"])
        self.assertIn("codex --sandbox read-only review", check["command"])

    def test_manual_record_passed_sets_pass_status(self) -> None:
        result = self.run_cli(
            "record",
            "--task",
            "review-test",
            "--phase",
            "review",
            "--check",
            "manual-review",
            "--tool",
            "subagent",
            "--passed",
            "--output",
            "looks good",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "PASS")
        self.assertEqual(check["passed"], 1)

    def test_manual_record_rejects_conflicting_status_and_passed(self) -> None:
        result = self.run_cli(
            "record",
            "--task",
            "review-test",
            "--phase",
            "review",
            "--check",
            "conflicting-review",
            "--tool",
            "subagent",
            "--status",
            "FAIL",
            "--passed",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--status and --passed disagree", result.stdout)

    def test_manual_record_skipped_exits_successfully(self) -> None:
        result = self.run_cli(
            "record",
            "--task",
            "review-test",
            "--phase",
            "review",
            "--check",
            "skipped-review",
            "--tool",
            "subagent",
            "--status",
            "SKIPPED",
            "--output",
            "reviewer unavailable",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "SKIPPED")
        self.assertEqual(check["passed"], 0)

    def test_recall_does_not_report_skipped_review_as_failure(self) -> None:
        add_file = self.run_cli("add-file", "--task", "review-test", "sample.txt")
        self.assertEqual(add_file.returncode, 0, add_file.stdout)
        record = self.run_cli(
            "record",
            "--task",
            "review-test",
            "--phase",
            "review",
            "--check",
            "skipped-review",
            "--tool",
            "subagent",
            "--status",
            "SKIPPED",
            "--output",
            "reviewer unavailable",
        )
        self.assertEqual(record.returncode, 0, record.stdout)
        recall = self.run_cli("recall", "--file", "sample.txt")
        self.assertEqual(recall.returncode, 0, recall.stdout)
        rows = json.loads(recall.stdout)
        self.assertEqual(rows[0]["task_id"], "review-test")
        self.assertIsNone(rows[0]["check_name"])
        self.assertIsNone(rows[0]["check_status"])

    def test_review_timeout_is_recorded(self) -> None:
        env = {**self.env, "FAKE_CODEX_MODE": "sleep"}
        result = self.run_cli(
            "review",
            "--task",
            "review-test",
            "--reviewer",
            "codex",
            "--timeout-seconds",
            "1",
            env=env,
        )
        self.assertEqual(result.returncode, 124, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "TIMEOUT")
        self.assertEqual(check["exit_code"], 124)

    def test_non_positive_timeout_records_failure(self) -> None:
        result = self.run_cli(
            "review",
            "--task",
            "review-test",
            "--reviewer",
            "codex",
            "--timeout-seconds",
            "0",
        )
        self.assertEqual(result.returncode, 2, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("timeout must be a positive", check["output_snippet"])

    def test_review_required_runs_codex_then_gpt(self) -> None:
        log = self.tmp / "codex.log"
        env = {**self.env, "FAKE_CODEX_LOG": str(log)}
        result = self.run_cli("review-required", "--task", "review-test", env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        checks = self.review_checks()
        self.assertEqual([check["check_name"] for check in checks[-2:]], ["review-codex", "review-gpt"])

    def test_review_required_records_both_skips_when_codex_is_unavailable(self) -> None:
        env = {**self.env, "PATH": "/usr/bin:/bin"}
        result = self.run_cli("review-required", "--task", "review-test", env=env)
        self.assertEqual(result.returncode, 127, result.stdout)
        checks = self.review_checks()
        self.assertEqual([check["check_name"] for check in checks[-2:]], ["review-codex", "review-gpt"])
        self.assertEqual([check["status"] for check in checks[-2:]], ["SKIPPED", "SKIPPED"])

    def test_large_diff_guard_records_failure(self) -> None:
        result = self.run_cli(
            "review",
            "--task",
            "review-test",
            "--reviewer",
            "codex",
            "--max-diff-chars",
            "10",
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("exceeds", check["output_snippet"])

    def test_large_diff_guard_counts_full_prompt(self) -> None:
        diff = subprocess.run(
            ["git", "--no-pager", "diff", "--staged", "--"],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
        result = self.run_cli(
            "review",
            "--task",
            "review-test",
            "--reviewer",
            "codex",
            "--max-diff-chars",
            str(len(diff) + 1),
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        check = self.review_checks()[-1]
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("Review prompt", check["output_snippet"])


if __name__ == "__main__":
    unittest.main()
