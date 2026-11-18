#!/usr/bin/env python3
"""Tests for scripts/bulletin-hook.py.

Each test runs the hook as Claude Code would, with the UserPromptSubmit
input on stdin and the home and Claude folders pointed at a temporary
folder, so nothing on this computer is read or written.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "bulletin-hook.py"
BULLETIN_PATH = Path("Library") / "Application Support" / "Claude" / "side-session-notes" / "bulletin.md"
NOTICE_TAG = "[BULLETIN]"
SESSION_ID = "session-a"
OTHER_SESSION_ID = "session-b"


def make_entry(days_ago: int, text: str) -> str:
    date = datetime.date.today() - datetime.timedelta(days=days_ago)

    return f"- {date.isoformat()} · ai-config abc1234 · {text} · 할 일 없음 · 자세히: notes.md"


class BulletinHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name).resolve()
        self._home = self._root / "home"
        self._claude_home = self._root / "claude"
        self._bulletin = self._home / BULLETIN_PATH
        self._claude_home.mkdir()
        self._bulletin.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_first_turn_shows_only_the_last_week(self) -> None:
        self._write_bulletin("# Bulletin", make_entry(10, "old change"), make_entry(2, "recent change"))
        context = self._read_context(SESSION_ID)

        self.assertIn(NOTICE_TAG, context)
        self.assertIn("recent change", context)
        self.assertNotIn("old change", context)
        self.assertNotIn("# Bulletin", context)

    def test_week_is_today_and_the_six_days_before(self) -> None:
        self._write_bulletin(make_entry(7, "eight days back"), make_entry(6, "seven days back"))
        context = self._read_context(SESSION_ID)

        self.assertIn("seven days back", context)
        self.assertNotIn("eight days back", context)

    def test_long_entry_is_cut(self) -> None:
        self._write_bulletin(make_entry(1, "x" * 1000))
        shown = self._read_context(SESSION_ID).splitlines()[1]

        self.assertEqual(len(shown), 300)

    def test_record_that_cannot_be_written_shows_nothing(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        record_directory = self._claude_home / "bulletin-notice"

        record_directory.mkdir()
        record_directory.chmod(0o500)
        try:
            self.assertEqual(self._run_hook(SESSION_ID), "")
            self.assertEqual(self._run_hook(SESSION_ID), "")
        finally:
            record_directory.chmod(0o700)

    def test_entries_are_shown_once_per_session(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))

        self.assertIn("first change", self._read_context(SESSION_ID))
        self.assertEqual(self._run_hook(SESSION_ID), "")
        self.assertIn("first change", self._read_context(OTHER_SESSION_ID))

    def test_later_entry_is_shown_alone(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        self._run_hook(SESSION_ID)
        self._write_bulletin(make_entry(1, "first change"), make_entry(0, "second change"))
        context = self._read_context(SESSION_ID)

        self.assertIn("second change", context)
        self.assertNotIn("first change", context)

    def test_many_entries_show_the_latest_five_and_a_count(self) -> None:
        self._write_bulletin(*(make_entry(1, f"change {number}") for number in range(1, 9)))
        context = self._read_context(SESSION_ID)

        self.assertIn("change 8", context)
        self.assertIn("change 4", context)
        self.assertNotIn("change 3", context)
        self.assertIn("3 more new entries", context)

    def test_record_lives_in_the_claude_folder(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        self._run_hook("session/with:odd*characters")
        records = list((self._claude_home / "bulletin-notice").glob("*.json"))

        self.assertEqual(len(records), 1)
        self.assertNotIn("/", records[0].stem)

    def test_new_session_removes_expired_records(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        self._run_hook(SESSION_ID)
        expired = self._claude_home / "bulletin-notice" / f"{SESSION_ID}.json"
        three_weeks_ago = time.time() - 21 * 24 * 60 * 60

        leftover = expired.with_name(f"{SESSION_ID}.123.tmp")

        leftover.write_text("{}", encoding="utf-8")
        for path in (expired, leftover):
            os.utime(path, (three_weeks_ago, three_weeks_ago))
        self._run_hook(OTHER_SESSION_ID)
        self.assertFalse(expired.exists())
        self.assertFalse(leftover.exists())

    def test_damaged_record_shows_the_week_again(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        record = self._claude_home / "bulletin-notice" / f"{SESSION_ID}.json"

        record.parent.mkdir()
        for damaged in ("{not json", "[" * 100000, '{"seen": 5}'):
            record.write_text(damaged, encoding="utf-8")
            self.assertIn("first change", self._read_context(SESSION_ID))

    def test_missing_bulletin_is_silent(self) -> None:
        self.assertEqual(self._run_hook(SESSION_ID), "")

    def test_unreadable_bulletin_never_fails_the_turn(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        self._bulletin.chmod(0)
        try:
            self.assertEqual(self._run_hook(SESSION_ID), "")
        finally:
            self._bulletin.chmod(0o600)

    def test_bulletin_that_is_a_folder_is_silent(self) -> None:
        self._bulletin.mkdir()

        self.assertEqual(self._run_hook(SESSION_ID), "")

    def test_unreadable_input_never_fails_the_turn(self) -> None:
        self._write_bulletin(make_entry(1, "first change"))
        for stdin in ("not json", "[]", "null", '{"session_id": 5}', "{}"):
            result = self._run(stdin=stdin)

            self.assertEqual(result.returncode, 0, stdin)
            self.assertEqual(result.stdout, "", stdin)

    def _write_bulletin(self, *lines: str) -> None:
        self._bulletin.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _read_context(self, session_id: str) -> str:
        output = json.loads(self._run_hook(session_id))

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        return str(output["hookSpecificOutput"]["additionalContext"])

    def _run_hook(self, session_id: str) -> str:
        hook_input = {"hook_event_name": "UserPromptSubmit", "session_id": session_id, "prompt": "hello"}
        result = self._run(stdin=json.dumps(hook_input))

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _run(self, stdin: str) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "CLAUDE_CONFIG_DIR": str(self._claude_home), "HOME": str(self._home)}

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
