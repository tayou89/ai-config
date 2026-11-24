"""Tests for scripts/check-rule-size.py.

Each test copies the real script into a small repository with stand-in
rule files, and a Codex home whose session logs name project folders, then
runs it the way the commit check does.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SIZE_SCRIPT = REPOSITORY_ROOT / "scripts" / "check-rule-size.py"
SIZE_SPECIFICATION = importlib.util.spec_from_file_location("check_rule_size", SIZE_SCRIPT)
assert SIZE_SPECIFICATION is not None and SIZE_SPECIFICATION.loader is not None
check_rule_size = importlib.util.module_from_spec(SIZE_SPECIFICATION)
SIZE_SPECIFICATION.loader.exec_module(check_rule_size)
LIMIT = 65536
FAILURE_BYTES = int(LIMIT * check_rule_size.CODEX_FAILURE_SHARE)
CLAUDE_BYTES = 2_000
GLOBAL_BYTES = 10_000
OLD_SECONDS = check_rule_size.SESSION_WINDOW_SECONDS + 24 * 60 * 60
RECORD_NAME = check_rule_size.APPROVED_SIZES_NAME
RECORD_OPTION = check_rule_size.RECORD_OPTION
CLAUDE_KEY = "claude"
CODEX_GLOBAL_KEY = "codex_global"
SEED_RECORD = {CLAUDE_KEY: CLAUDE_BYTES, CODEX_GLOBAL_KEY: GLOBAL_BYTES}
UNUSABLE_RECORDS = {
    "not JSON": b"not json",
    "a list": b"[2000, 10000]",
    "a missing key": b'{"claude": 2000}',
    "an extra key": b'{"claude": 2000, "codex_global": 10000, "extra": 1}',
    "a repeated key": b'{"claude": 2000, "claude": 2000, "codex_global": 10000}',
    "a boolean": b'{"claude": true, "codex_global": 10000}',
    "a fraction": b'{"claude": 2000.0, "codex_global": 10000}',
    "a string": b'{"claude": "2000", "codex_global": 10000}',
    "a negative size": b'{"claude": -1, "codex_global": 10000}',
    "a size over the budget": b'{"claude": 40000, "codex_global": 10000}',
    "a byte order mark": b'\xef\xbb\xbf{"claude": 2000, "codex_global": 10000}',
    "invalid UTF-8": b'{"claude": 2000, "codex_global": 10000, "\xff": 1}',
    "deep nesting": b'{"claude": ' + b"[" * 1_100 + b"0" + b"]" * 1_100 + b', "codex_global": 10000}',
}
GLOBAL_BUDGET_RECORD = {CLAUDE_KEY: CLAUDE_BYTES, CODEX_GLOBAL_KEY: check_rule_size.CODEX_GLOBAL_BUDGET_BYTES}
OLD_RECORD_TEXT = "old record\n"


class RuleSizeFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repository = self.root / "repository"
        self.codex_home = self.root / "codex-home"
        self.session_count = 0

    def create(self) -> None:
        (self.repository / "scripts").mkdir(parents=True)
        shutil.copy2(SIZE_SCRIPT, self.repository / "scripts" / SIZE_SCRIPT.name)
        self.write_bytes("CLAUDE.md", 1_000)
        self.write_bytes("rules/collaboration-core.md", 1_000)
        self.write_bytes("codex/AGENTS.md", GLOBAL_BYTES)
        settings = {"config": {check_rule_size.CODEX_LIMIT_KEY: LIMIT}}
        (self.repository / "codex" / "settings.json").write_text(json.dumps(settings))
        self.write_record(SEED_RECORD)
        (self.codex_home / check_rule_size.CODEX_SESSIONS_DIRECTORY).mkdir(parents=True)

    def write_bytes(self, path: str, size: int) -> None:
        target = self.repository / path

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x" * size)

    def write_record(self, record: dict[str, int]) -> None:
        self.write_record_bytes(json.dumps(record).encode())

    def write_record_bytes(self, data: bytes) -> None:
        (self.repository / RECORD_NAME).write_bytes(data)

    def read_record_bytes(self) -> bytes:
        return (self.repository / RECORD_NAME).read_bytes()

    def add_project(self, name: str, sizes: dict[str, int]) -> Path:
        project = self.root / name

        (project / ".git").mkdir(parents=True)
        for relative, size in sizes.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text("x" * size)
        return project

    def add_session(self, folder: Path, age_seconds: int = 0) -> None:
        first_line = json.dumps({"type": "session_meta", "payload": {"cwd": str(folder)}})
        log = self.add_log(first_line)
        modified = time.time() - age_seconds

        os.utime(log, (modified, modified))

    def add_log(self, first_line: str) -> Path:
        self.session_count += 1
        log: Path = self.codex_home / check_rule_size.CODEX_SESSIONS_DIRECTORY / f"rollout-{self.session_count}.jsonl"

        log.write_text(first_line + "\n" + json.dumps({"type": "event"}) + "\n")
        return log

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, check_rule_size.CODEX_HOME_VARIABLE: str(self.codex_home)}

        return subprocess.run(
            [sys.executable, str(self.repository / "scripts" / SIZE_SCRIPT.name), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


class RuleSizeTestCase(unittest.TestCase):
    fixture: RuleSizeFixture

    def setUp(self) -> None:
        self.fixture = RuleSizeFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()


class CheckRuleSizeTest(RuleSizeTestCase):
    def test_passes_within_budgets(self) -> None:
        project = self.fixture.add_project("small", {"AGENTS.md": 5_000})
        self.fixture.add_session(project)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("warning", result.stdout)

    def test_claude_total_over_budget_fails(self) -> None:
        self.fixture.write_bytes("rules/writing.md", check_rule_size.CLAUDE_BUDGET_BYTES)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Claude always-loaded rules", result.stderr)

    def test_codex_project_over_default_limit_warns(self) -> None:
        project = self.fixture.add_project("medium", {"AGENTS.md": check_rule_size.CODEX_DEFAULT_LIMIT_BYTES + 1})
        self.fixture.add_session(project)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"warning: Codex project instructions in {project}", result.stdout)

    def test_codex_project_over_failure_share_fails(self) -> None:
        project = self.fixture.add_project("large", {"AGENTS.md": FAILURE_BYTES + 1})
        self.fixture.add_session(project)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Codex project instructions in {project}", result.stderr)

    def test_codex_global_is_not_added_to_the_project_limit(self) -> None:
        self.fixture.write_bytes("codex/AGENTS.md", check_rule_size.CODEX_GLOBAL_BUDGET_BYTES)
        self.fixture.write_record(GLOBAL_BUDGET_RECORD)
        project = self.fixture.add_project("beside", {"AGENTS.md": check_rule_size.CODEX_DEFAULT_LIMIT_BYTES})
        self.fixture.add_session(project)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("warning", result.stdout)

    def test_codex_global_over_budget_fails(self) -> None:
        self.fixture.write_bytes("codex/AGENTS.md", check_rule_size.CODEX_GLOBAL_BUDGET_BYTES + 1)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Codex global instructions", result.stderr)

    def test_counts_one_file_per_directory_from_root_to_folder(self) -> None:
        sizes = {
            "AGENTS.md": FAILURE_BYTES,
            "AGENTS.override.md": 1_000,
            "app/AGENTS.md": 1_000,
            "app/web/AGENTS.md": 1_000,
            "other/AGENTS.md": FAILURE_BYTES,
        }
        project = self.fixture.add_project("nested", sizes)
        self.fixture.add_session(project / "app" / "web")
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("largest 3,000 bytes", result.stdout)

    def test_ignores_old_sessions_and_missing_folders(self) -> None:
        project = self.fixture.add_project("old", {"AGENTS.md": FAILURE_BYTES})
        self.fixture.add_session(project, age_seconds=OLD_SECONDS)
        self.fixture.add_session(self.fixture.root / "removed")
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("largest 0 bytes", result.stdout)

    def test_reports_each_project_once(self) -> None:
        project = self.fixture.add_project("shared", {"AGENTS.md": check_rule_size.CODEX_DEFAULT_LIMIT_BYTES + 1})
        (project / "a").mkdir()
        (project / "b").mkdir()
        self.fixture.add_session(project / "a")
        self.fixture.add_session(project / "b")
        result = self.fixture.run()

        self.assertEqual(result.stdout.count("warning:"), 1, result.stdout)

    def test_skips_logs_it_cannot_read_or_parse(self) -> None:
        project = self.fixture.add_project("counted", {"AGENTS.md": 5_000})
        sessions = self.fixture.codex_home / check_rule_size.CODEX_SESSIONS_DIRECTORY
        self.fixture.add_session(project)
        for first_line in ("[1]", '{"payload": "text"}', '{"payload": {"cwd": 5}}', "not json"):
            self.fixture.add_log(first_line)
        (sessions / "broken.jsonl").symlink_to(sessions / "missing.jsonl")
        (sessions / "folder.jsonl").mkdir()
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("largest 5,000 bytes", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "root can read a file without read permission")
    def test_skips_a_log_without_read_permission(self) -> None:
        log = self.fixture.add_log("{}")
        log.chmod(0)
        try:
            result = self.fixture.run()
        finally:
            log.chmod(0o600)

        self.assertEqual(result.returncode, 0, result.stderr)


class ApprovedSizeTest(RuleSizeTestCase):
    def test_sizes_that_match_the_record_pass(self) -> None:
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(RECORD_NAME, result.stdout)

    def test_claude_growth_past_the_record_fails(self) -> None:
        self.fixture.write_bytes("rules/added.md", 100)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Claude always-loaded rules grew to 2,100 bytes, 100 over the approved 2,000", result.stderr)
        self.assertIn(RECORD_OPTION, result.stderr)

    def test_codex_global_growth_past_the_record_fails(self) -> None:
        self.fixture.write_bytes("codex/AGENTS.md", GLOBAL_BYTES + 50)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Codex global instructions grew to 10,050 bytes, 50 over the approved 10,000", result.stderr)

    def test_shrink_below_the_record_fails(self) -> None:
        self.fixture.write_bytes("CLAUDE.md", 900)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Claude always-loaded rules shrank to 1,900 bytes, 100 under the approved 2,000", result.stderr)
        self.assertIn(RECORD_OPTION, result.stderr)

    def test_shrink_with_a_lowered_record_passes(self) -> None:
        self.fixture.write_bytes("CLAUDE.md", 900)
        self.fixture.write_record({CLAUDE_KEY: 1_900, CODEX_GLOBAL_KEY: GLOBAL_BYTES})
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_growth_after_a_lowered_record_fails(self) -> None:
        self.fixture.write_record({CLAUDE_KEY: 1_900, CODEX_GLOBAL_KEY: GLOBAL_BYTES})
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("grew to 2,000 bytes, 100 over the approved 1,900", result.stderr)

    def test_reports_each_direction_apart(self) -> None:
        self.fixture.write_bytes("CLAUDE.md", 900)
        self.fixture.write_bytes("codex/AGENTS.md", GLOBAL_BYTES + 1)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Claude always-loaded rules shrank", result.stderr)
        self.assertIn("Codex global instructions grew", result.stderr)

    def test_merged_growth_from_two_branches_fails(self) -> None:
        self.fixture.write_bytes("rules/from-one-branch.md", 100)
        self.fixture.write_bytes("rules/from-another-branch.md", 100)
        self.fixture.write_record({CLAUDE_KEY: CLAUDE_BYTES + 100, CODEX_GLOBAL_KEY: GLOBAL_BYTES})
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("grew to 2,200 bytes, 100 over the approved 2,100", result.stderr)

    def test_merged_shrinks_from_two_branches_pass_once_recorded(self) -> None:
        self.fixture.write_bytes("CLAUDE.md", 900)
        self.fixture.write_bytes("rules/collaboration-core.md", 900)
        self.fixture.write_record({CLAUDE_KEY: 1_900, CODEX_GLOBAL_KEY: GLOBAL_BYTES})
        merged = self.fixture.run()
        recorded = self.fixture.run(RECORD_OPTION)
        checked = self.fixture.run()

        self.assertEqual(merged.returncode, 1)
        self.assertIn("shrank to 1,800 bytes, 100 under the approved 1,900", merged.stderr)
        self.assertIn("1,900 -> 1,800 bytes", recorded.stdout)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_missing_record_fails(self) -> None:
        (self.fixture.repository / RECORD_NAME).unlink()
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"the approved sizes in {RECORD_NAME} cannot be used", result.stderr)
        self.assertIn("restore the file from the last commit", result.stderr)

    def test_unusable_records_fail(self) -> None:
        for case, data in UNUSABLE_RECORDS.items():
            with self.subTest(case):
                self.fixture.write_record_bytes(data)
                result = self.fixture.run()

                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("cannot be used", result.stderr)
                self.assertIn("restore the file from the last commit", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_shared_totals_are_checked_without_codex_logs(self) -> None:
        shutil.rmtree(self.fixture.codex_home)
        self.fixture.write_bytes("rules/added.md", 10)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn("grew to 2,010 bytes", result.stderr)

    def test_record_writes_the_current_sizes(self) -> None:
        self.fixture.write_bytes("CLAUDE.md", 900)
        recorded = self.fixture.run(RECORD_OPTION)
        checked = self.fixture.run()
        expected = {CLAUDE_KEY: 1_900, CODEX_GLOBAL_KEY: GLOBAL_BYTES}

        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(json.loads(self.fixture.read_record_bytes()), expected)
        self.assertIn("2,000 -> 1,900 bytes", recorded.stdout)
        self.assertNotIn("an increase", recorded.stdout)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_record_marks_an_increase(self) -> None:
        self.fixture.write_bytes("rules/added.md", 100)
        result = self.fixture.run(RECORD_OPTION)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2,000 -> 2,100 bytes (an increase", result.stdout)

    def test_record_marks_only_the_total_that_grew(self) -> None:
        self.fixture.write_bytes("CLAUDE.md", 900)
        self.fixture.write_bytes("codex/AGENTS.md", GLOBAL_BYTES + 5)
        result = self.fixture.run(RECORD_OPTION)
        lines = result.stdout.splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("  Claude always-loaded rules: 2,000 -> 1,900 bytes", lines)
        self.assertIn("Codex global instructions: 10,000 -> 10,005 bytes (an increase", result.stdout)

    def test_record_creates_a_missing_record(self) -> None:
        (self.fixture.repository / RECORD_NAME).unlink()
        result = self.fixture.run(RECORD_OPTION)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("none -> 2,000 bytes", result.stdout)
        self.assertEqual(json.loads(self.fixture.read_record_bytes()), SEED_RECORD)

    def test_record_refuses_to_overwrite_an_unusable_record(self) -> None:
        self.fixture.write_record_bytes(b"{")
        result = self.fixture.run(RECORD_OPTION)

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{RECORD_NAME} exists but cannot be used", result.stderr)
        self.assertEqual(self.fixture.read_record_bytes(), b"{")

    def test_record_twice_writes_the_same_file(self) -> None:
        first_run = self.fixture.run(RECORD_OPTION)
        first = self.fixture.read_record_bytes()
        second_run = self.fixture.run(RECORD_OPTION)

        self.assertEqual(first_run.returncode, 0, first_run.stderr)
        self.assertEqual(second_run.returncode, 0, second_run.stderr)
        self.assertEqual(self.fixture.read_record_bytes(), first)
        self.assertTrue(first.endswith(b"\n"))

    def test_record_refuses_a_total_over_its_budget(self) -> None:
        before = self.fixture.read_record_bytes()

        self.fixture.write_bytes("codex/AGENTS.md", check_rule_size.CODEX_GLOBAL_BUDGET_BYTES + 1)
        result = self.fixture.run(RECORD_OPTION)

        self.assertEqual(result.returncode, 1)
        self.assertIn("over its 32,768-byte budget", result.stderr)
        self.assertEqual(self.fixture.read_record_bytes(), before)

    @unittest.skipIf(os.geteuid() == 0, "root can write into a read-only folder")
    def test_record_write_failure_keeps_the_previous_record(self) -> None:
        before = self.fixture.read_record_bytes()

        self.fixture.write_bytes("rules/added.md", 100)
        self.fixture.repository.chmod(0o555)
        try:
            result = self.fixture.run(RECORD_OPTION)
        finally:
            self.fixture.repository.chmod(0o755)

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"could not write {RECORD_NAME}", result.stderr)
        self.assertEqual(self.fixture.read_record_bytes(), before)
        self.assertEqual(list(self.fixture.repository.glob("*.tmp")), [])

    def test_unknown_argument_fails(self) -> None:
        before = self.fixture.read_record_bytes()
        result = self.fixture.run("--recrd")

        self.assertEqual(result.returncode, 1)
        self.assertIn("usage:", result.stderr)
        self.assertEqual(self.fixture.read_record_bytes(), before)


class ReplaceTextTest(unittest.TestCase):
    directory: tempfile.TemporaryDirectory[str]
    path: Path

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / RECORD_NAME
        self.path.write_text(OLD_RECORD_TEXT)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_replace_writes_the_text_readable_by_everyone(self) -> None:
        check_rule_size._replace_text(self.path, "new record\n")

        self.assertEqual(self.path.read_text(), "new record\n")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.list_temporary_files(), [])

    def test_failed_replace_keeps_the_old_record_and_no_temporary_file(self) -> None:
        with mock.patch.object(check_rule_size.os, "replace", side_effect=OSError("no room")):
            with self.assertRaises(OSError):
                check_rule_size._replace_text(self.path, "new record\n")

        self.assertEqual(self.path.read_text(), OLD_RECORD_TEXT)
        self.assertEqual(self.list_temporary_files(), [])

    def test_interrupted_write_keeps_the_old_record_and_no_temporary_file(self) -> None:
        with mock.patch.object(check_rule_size.Path, "write_text", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                check_rule_size._replace_text(self.path, "new record\n")

        self.assertEqual(self.path.read_text(), OLD_RECORD_TEXT)
        self.assertEqual(self.list_temporary_files(), [])

    def list_temporary_files(self) -> list[Path]:
        return sorted(Path(self.directory.name).glob("*.tmp"))


if __name__ == "__main__":
    unittest.main()
