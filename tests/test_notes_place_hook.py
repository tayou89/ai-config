#!/usr/bin/env python3
"""Tests for scripts/notes-place-hook.py.

Each test runs the hook as Claude Code would, with the hook input on stdin and
the home folder pointed at a temporary folder, so the app's notes folder it
judges is the test's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "notes-place-hook.py"
APP_NOTES_PATH = Path("Library") / "Application Support" / "Claude" / "side-session-notes"
GATE_VARIABLE = "SKILL_GATE"
GATE_OFF = "off"
DENY = "deny"
NEW_FILE_TEXT = "would be a new file"
GUIDE_AGENT = {"agent_id": "g1", "agent_type": "claude-code-guide"}


class NotesPlaceHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._home = Path(self._directory.name).resolve()
        self._notes = self._home / APP_NOTES_PATH

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_new_files_in_app_session_folders_are_refused_for_every_agent(self) -> None:
        new_report = str(self._notes / "local_1" / "new.md")

        for agent in ({}, GUIDE_AGENT, {"agent_id": "x1"}):
            with self.subTest(agent=agent):
                reason = self._read_reason(self._decide("Write", {"file_path": new_report, "content": "x"}, agent))

                self.assertIn("`new.md` would be a new file", reason)
                self.assertIn("~/Documents/session-notes/<project>/", reason)

    def test_gate_off_lets_new_files_through(self) -> None:
        new_report = str(self._notes / "local_1" / "new.md")

        self.assertIsNone(self._decide("Write", {"file_path": new_report}, environment={GATE_VARIABLE: GATE_OFF}))

    def test_files_already_in_app_session_folders_can_still_be_edited(self) -> None:
        existing = self._notes / "local_1" / "INDEX.md"

        existing.parent.mkdir(parents=True)
        existing.write_text("목록\n", encoding="utf-8")
        edit = {"file_path": str(existing), "old_string": "목록", "new_string": "목록 고침"}

        self.assertIsNone(self._decide("Edit", edit))
        self.assertIsNone(self._decide("Write", {"file_path": str(existing), "content": "새 목록"}))
        self.assertIsNone(self._decide("Bash", {"command": f"echo row >> '{existing}'"}))

    def test_copies_bulletin_and_folders_the_app_keeps_are_not_refused(self) -> None:
        copy = self._home / "Documents" / "session-notes-backup" / "snapshot" / "side-session-notes"
        paths = (copy / "local_1" / "restored.json", self._notes / "bulletin.md", self._notes / ".obsidian" / "a.json")

        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(self._decide("Write", {"file_path": str(path), "content": "x"}))

    def test_shell_writes_of_new_files_in_app_session_folders_are_refused(self) -> None:
        folder = self._notes / "local_1"
        commands = (
            (f"cat > '{folder}/r.md' <<'EOF'\ntext\nEOF", str(self._home)),
            ("echo text > r.md", str(folder)),
            (f"echo text | tee '{folder}/log.txt'", str(self._home)),
        )

        for command, cwd in commands:
            with self.subTest(command=command):
                self.assertIn(NEW_FILE_TEXT, self._read_reason(self._decide("Bash", {"command": command}, cwd=cwd)))

    def test_writes_that_move_folder_inside_the_command_are_judged_where_they_land(self) -> None:
        folder = self._notes / "local_1"
        cases = (
            (f"cd '{folder}' && echo text > r.md", str(self._home), True),
            ("cd /tmp && echo text > r.md", str(folder), False),
        )

        for command, cwd, is_refused in cases:
            with self.subTest(command=command):
                reason = self._read_reason(self._decide("Bash", {"command": command}, cwd=cwd))

                self.assertEqual(NEW_FILE_TEXT in reason, is_refused)

    def test_broken_input_exits_1_so_the_settings_entry_blocks_the_call(self) -> None:
        for stdin in ("", "not json", "[1]", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Write"})):
            with self.subTest(stdin=stdin):
                result = self._run(stdin)

                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn("notes-place-hook:", result.stderr)

    def test_a_command_it_cannot_expand_is_refused(self) -> None:
        command = "echo $(" * 400 + "x" + ")" * 400
        output = self._decide("Bash", {"command": command})
        decision = output["hookSpecificOutput"] if output else {}

        self.assertEqual(decision.get("permissionDecision"), DENY)
        self.assertIn("could not check", decision.get("permissionDecisionReason", ""))

    def _decide(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        agent: dict[str, Any] | None = None,
        cwd: str = "/tmp",
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        hook_input = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input, "cwd": cwd}
        result = self._run(json.dumps({**hook_input, **(agent or {})}), environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _read_reason(self, output: dict[str, Any] | None) -> str:
        return str((output or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", ""))

    def _run(self, stdin: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        base = {key: value for key, value in os.environ.items() if key != GATE_VARIABLE}
        settings = {**base, "HOME": str(self._home), "CLAUDE_CONFIG_DIR": str(self._home / ".claude")}

        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=stdin,
            capture_output=True,
            text=True,
            env={**settings, **(environment or {})},
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
