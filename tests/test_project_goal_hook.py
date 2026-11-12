#!/usr/bin/env python3
"""Tests for scripts/project-goal-hook.py.

Each test runs the hook as Claude Code would, with the SessionStart input
on stdin and the Claude, Codex, and home folders pointed at a temporary
folder, so nothing on this computer is read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "project-goal-hook.py"
NOTICE_TAG = "[PROJECT GOAL]"
RULES_WITHOUT_GOAL = "# Project Rules\n\n## Commands\n\nRun the tests.\n"
RULES_WITH_GOAL = "# Project Rules\n\n## Goal\n\nHelp travelers split costs.\n"


class ProjectGoalHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name).resolve()
        self._claude_home = self._root / "claude"
        self._codex_home = self._root / "codex"
        self._home = self._root / "home"

        for folder in (self._claude_home, self._codex_home, self._home):
            folder.mkdir()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_project_without_goal_gets_notice(self) -> None:
        project = self._make_project("app", {"AGENTS.md": RULES_WITHOUT_GOAL})
        output = json.loads(self._run_hook(project / "src"))

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(NOTICE_TAG, output["hookSpecificOutput"]["additionalContext"])
        self.assertIn("AGENTS.md", output["hookSpecificOutput"]["additionalContext"])

    def test_goal_in_any_rules_file_is_enough(self) -> None:
        project = self._make_project("app", {"CLAUDE.md": RULES_WITHOUT_GOAL, "AGENTS.md": RULES_WITH_GOAL})

        self.assertEqual(self._run_hook(project), "")

    def test_korean_goal_heading_counts(self) -> None:
        project = self._make_project("app", {"CLAUDE.md": "# 규칙\n\n## 프로젝트 목표\n\n여행 경비를 나눕니다.\n"})

        self.assertEqual(self._run_hook(project), "")

    def test_goal_word_inside_a_longer_heading_does_not_count(self) -> None:
        project = self._make_project("app", {"CLAUDE.md": "# Rules\n\n## Goals for the next release\n"})

        self.assertIn(NOTICE_TAG, self._run_hook(project))

    def test_folder_outside_a_repository_is_silent(self) -> None:
        folder = self._root / "notes"

        folder.mkdir()
        (folder / "CLAUDE.md").write_text(RULES_WITHOUT_GOAL, encoding="utf-8")
        self.assertEqual(self._run_hook(folder), "")

    def test_repository_without_rules_file_is_silent(self) -> None:
        project = self._make_project("clone", {})

        self.assertEqual(self._run_hook(project), "")

    def test_configuration_folders_are_silent(self) -> None:
        checkout = self._make_project("ai-config", {"CLAUDE.md": RULES_WITHOUT_GOAL})

        self._write_checkout_record(f"{checkout}\n")
        (self._claude_home / ".git").mkdir()
        (self._claude_home / "CLAUDE.md").write_text(RULES_WITHOUT_GOAL, encoding="utf-8")
        self.assertEqual(self._run_hook(checkout), "")
        self.assertEqual(self._run_hook(self._claude_home), "")

    def test_empty_checkout_record_excludes_nothing(self) -> None:
        project = self._make_project("app", {"AGENTS.md": RULES_WITHOUT_GOAL})

        self._write_checkout_record("  \n")
        self.assertIn(NOTICE_TAG, self._run_hook(project))

    def test_compaction_is_silent(self) -> None:
        project = self._make_project("app", {"AGENTS.md": RULES_WITHOUT_GOAL})

        self.assertEqual(self._run_hook(project, source="compact"), "")

    def test_unreadable_input_never_fails_the_session(self) -> None:
        for stdin in ("not json", "[]", "null", '{"cwd": 5}'):
            result = self._run(stdin=stdin)

            self.assertEqual(result.returncode, 0, stdin)
            self.assertEqual(result.stdout, "", stdin)

    def _make_project(self, name: str, rules: dict[str, str]) -> Path:
        project = self._root / name

        (project / ".git").mkdir(parents=True)
        (project / "src").mkdir()
        for file_name, text in rules.items():
            (project / file_name).write_text(text, encoding="utf-8")
        return project

    def _write_checkout_record(self, text: str) -> None:
        record = self._claude_home / ".ai-config-install" / "checkout"

        record.parent.mkdir()
        record.write_text(text, encoding="utf-8")

    def _run_hook(self, folder: Path, source: str = "startup") -> str:
        hook_input = {"hook_event_name": "SessionStart", "source": source, "cwd": str(folder)}
        result = self._run(stdin=json.dumps(hook_input))

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _run(self, stdin: str) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(self._claude_home),
            "CODEX_HOME": str(self._codex_home),
            "HOME": str(self._home),
        }

        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=stdin,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
