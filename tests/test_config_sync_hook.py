#!/usr/bin/env python3
"""Tests for scripts/config-sync-hook.py.

Each test runs the hook as Claude Code would, against a temporary Claude
folder and a stand-in checkout whose install.py prints a fixed report and
exits with a fixed code, so nothing on this computer is read or changed.
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
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "config-sync-hook.py"
REPORT = "Only on this computer:\n  - settings.json: extraKnownMarketplaces.added-here"
DIFFERENCES_FOUND = 7
DAY_SECONDS = 24 * 60 * 60
STAND_IN_SCRIPT = """import sys
print({report!r})
sys.exit({code})
"""


class ConfigSyncHookTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self._claude_home = root / "claude"
        self._checkout = root / "checkout"

        (self._checkout / "scripts").mkdir(parents=True)
        (self._claude_home / ".ai-config-install").mkdir(parents=True)
        (self._claude_home / ".ai-config-install" / "checkout").write_text(f"{self._checkout}\n", encoding="utf-8")

    def tearDown(self):
        self._directory.cleanup()

    def test_differences_become_session_context(self):
        self._write_stand_in(DIFFERENCES_FOUND)
        output = json.loads(self._run_hook())

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("[CONFIG SYNC]", output["hookSpecificOutput"]["additionalContext"])
        self.assertIn("added-here", output["hookSpecificOutput"]["additionalContext"])

    def test_same_report_is_not_repeated_within_a_day(self):
        self._write_stand_in(DIFFERENCES_FOUND)

        self.assertTrue(self._run_hook())
        self.assertEqual(self._run_hook(), "")

    def test_changed_report_is_shown_again(self):
        self._write_stand_in(DIFFERENCES_FOUND)
        self._run_hook()
        self._write_stand_in(DIFFERENCES_FOUND, REPORT + " and more")

        self.assertTrue(self._run_hook())

    def test_same_report_repeats_after_a_day(self):
        self._write_stand_in(DIFFERENCES_FOUND)
        self._run_hook()
        record = self._claude_home / ".ai-config-install" / "last-notice.json"
        previous = json.loads(record.read_text(encoding="utf-8"))
        aged = {**previous, "shown_at": previous["shown_at"] - DAY_SECONDS - 1}

        record.write_text(json.dumps(aged), encoding="utf-8")

        self.assertTrue(self._run_hook())

    def test_unreadable_record_does_not_silence_the_notice(self):
        self._write_stand_in(DIFFERENCES_FOUND)
        (self._claude_home / ".ai-config-install" / "last-notice.json").write_text("{half", encoding="utf-8")

        self.assertTrue(self._run_hook())

    def test_matching_computer_adds_nothing(self):
        self._write_stand_in(0)

        self.assertEqual(self._run_hook(), "")

    def test_failed_check_is_reported(self):
        self._write_stand_in(3)
        context = json.loads(self._run_hook())["hookSpecificOutput"]["additionalContext"]

        self.assertIn("could not run", context)
        self.assertIn("exit code 3", context)

    def test_recorded_checkout_without_install_script_is_reported(self):
        context = json.loads(self._run_hook())["hookSpecificOutput"]["additionalContext"]

        self.assertIn("could not run", context)
        self.assertIn("has no scripts/install.py", context)

    def test_unknown_checkout_adds_nothing(self):
        (self._claude_home / ".ai-config-install" / "checkout").unlink()

        self.assertEqual(self._run_hook(), "")

    def _write_stand_in(self, code: int, report: str = REPORT) -> None:
        script = STAND_IN_SCRIPT.format(report=report, code=code)

        (self._checkout / "scripts" / "install.py").write_text(script, encoding="utf-8")

    def _run_hook(self) -> str:
        environment = {**os.environ, "CLAUDE_CONFIG_DIR": str(self._claude_home)}
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=True,
        )

        return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
