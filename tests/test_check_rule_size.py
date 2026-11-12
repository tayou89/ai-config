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

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SIZE_SCRIPT = REPOSITORY_ROOT / "scripts" / "check-rule-size.py"
SIZE_SPECIFICATION = importlib.util.spec_from_file_location("check_rule_size", SIZE_SCRIPT)
assert SIZE_SPECIFICATION is not None and SIZE_SPECIFICATION.loader is not None
check_rule_size = importlib.util.module_from_spec(SIZE_SPECIFICATION)
SIZE_SPECIFICATION.loader.exec_module(check_rule_size)
LIMIT = 65536
FAILURE_BYTES = int(LIMIT * check_rule_size.CODEX_FAILURE_SHARE)
GLOBAL_BYTES = 10_000
OLD_SECONDS = check_rule_size.SESSION_WINDOW_SECONDS + 24 * 60 * 60


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
        (self.codex_home / check_rule_size.CODEX_SESSIONS_DIRECTORY).mkdir(parents=True)

    def write_bytes(self, path: str, size: int) -> None:
        target = self.repository / path

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x" * size)

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

    def run(self) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, check_rule_size.CODEX_HOME_VARIABLE: str(self.codex_home)}

        return subprocess.run(
            [sys.executable, str(self.repository / "scripts" / SIZE_SCRIPT.name)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


class CheckRuleSizeTest(unittest.TestCase):
    fixture: RuleSizeFixture

    def setUp(self) -> None:
        self.fixture = RuleSizeFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

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

    def test_codex_total_over_default_limit_warns(self) -> None:
        project = self.fixture.add_project("medium", {"AGENTS.md": check_rule_size.CODEX_WARNING_BYTES})
        self.fixture.add_session(project)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"warning: Codex instructions in {project}", result.stdout)

    def test_codex_total_over_failure_share_fails(self) -> None:
        project = self.fixture.add_project("large", {"AGENTS.md": FAILURE_BYTES - GLOBAL_BYTES + 1})
        self.fixture.add_session(project)
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Codex instructions in {project}", result.stderr)

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
        self.assertIn("largest project 3,000", result.stdout)

    def test_ignores_old_sessions_and_missing_folders(self) -> None:
        project = self.fixture.add_project("old", {"AGENTS.md": FAILURE_BYTES})
        self.fixture.add_session(project, age_seconds=OLD_SECONDS)
        self.fixture.add_session(self.fixture.root / "removed")
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("largest project 0", result.stdout)

    def test_reports_each_project_once(self) -> None:
        project = self.fixture.add_project("shared", {"AGENTS.md": check_rule_size.CODEX_WARNING_BYTES})
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
        self.assertIn("largest project 5,000", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "root can read a file without read permission")
    def test_skips_a_log_without_read_permission(self) -> None:
        log = self.fixture.add_log("{}")
        log.chmod(0)
        try:
            result = self.fixture.run()
        finally:
            log.chmod(0o600)

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
