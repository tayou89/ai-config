#!/usr/bin/env python3
"""Tests for scripts/sub-session-question-hook.py.

Each test runs the hook as Claude Code would, with the hook input on stdin
and the home folder pointed at a temporary folder holding fake app session
records, so nothing on this computer is read.
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
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "sub-session-question-hook.py"
RECORDS_PATH = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions" / "account" / "org"
ENCODING = "utf-8"
HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
SWITCH_VARIABLE = "SUB_SESSION_QUESTION"
NOTICE_TAG = "[SUB-SESSION QUESTION]"
PARENT_SESSION = "local_parent"
CHILD_SESSION = "local_child"
QUESTION_INPUT = {
    "hook_event_name": "PreToolUse",
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": [{"question": "어느 쪽으로 할까요?", "header": "방향", "options": []}]},
}


class SubSessionQuestionHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._home = Path(self._directory.name).resolve() / "home"
        self._records = self._home / RECORDS_PATH
        self._records.mkdir(parents=True)
        self._write_record(PARENT_SESSION)
        self._write_record(CHILD_SESSION, spawned_from=PARENT_SESSION)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_question_dialog_in_a_started_session_is_denied(self) -> None:
        decision = json.loads(self._run_hook(CHILD_SESSION, QUESTION_INPUT))["hookSpecificOutput"]

        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertTrue(decision["permissionDecisionReason"].startswith(NOTICE_TAG))
        self.assertIn("mcp__ccd_turn__wrap_up", decision["permissionDecisionReason"])

    def test_question_dialog_in_a_top_level_session_passes(self) -> None:
        self.assertEqual(self._run_hook(PARENT_SESSION, QUESTION_INPUT), "")

    def test_question_dialog_in_a_detached_session_passes(self) -> None:
        self._write_record(CHILD_SESSION, spawned_from=PARENT_SESSION, detached=True)

        self.assertEqual(self._run_hook(CHILD_SESSION, QUESTION_INPUT), "")

    def test_subagent_question_passes(self) -> None:
        self.assertEqual(self._run_hook(CHILD_SESSION, {**QUESTION_INPUT, "agent_id": "agent_1"}), "")

    def test_session_outside_the_app_passes(self) -> None:
        self.assertEqual(self._run_hook(None, QUESTION_INPUT), "")

    def test_session_without_its_own_record_passes(self) -> None:
        self.assertEqual(self._run_hook("local_unknown", QUESTION_INPUT), "")

    def test_other_tools_pass(self) -> None:
        wrap_up_input = {**QUESTION_INPUT, "tool_name": "mcp__ccd_turn__wrap_up"}

        self.assertEqual(self._run_hook(CHILD_SESSION, wrap_up_input), "")

    def test_switch_off_lets_the_dialog_through(self) -> None:
        self.assertEqual(self._run_hook(CHILD_SESSION, QUESTION_INPUT, {SWITCH_VARIABLE: "off"}), "")

    def test_other_events_pass(self) -> None:
        post_tool_input = {**QUESTION_INPUT, "hook_event_name": "PostToolUse"}

        self.assertEqual(self._run_hook(CHILD_SESSION, post_tool_input), "")

    def test_damaged_record_lets_the_dialog_through(self) -> None:
        (self._records / f"{CHILD_SESSION}.json").write_text("{not json", encoding=ENCODING)

        self.assertEqual(self._run_hook(CHILD_SESSION, QUESTION_INPUT), "")

    def test_record_that_is_not_an_object_lets_the_dialog_through(self) -> None:
        (self._records / f"{CHILD_SESSION}.json").write_text("[]", encoding=ENCODING)

        self.assertEqual(self._run_hook(CHILD_SESSION, QUESTION_INPUT), "")

    def test_empty_spawned_from_counts_as_top_level(self) -> None:
        record = {"sessionId": CHILD_SESSION, "spawnedFrom": None}

        (self._records / f"{CHILD_SESSION}.json").write_text(json.dumps(record), encoding=ENCODING)
        self.assertEqual(self._run_hook(CHILD_SESSION, QUESTION_INPUT), "")

    def test_empty_input_exits_cleanly(self) -> None:
        result = self._run_process(CHILD_SESSION, "")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def _write_record(self, session_id: str, spawned_from: str | None = None, detached: bool = False) -> None:
        record: dict[str, Any] = {"sessionId": session_id}

        if spawned_from is not None:
            record["spawnedFrom"] = {"sessionId": spawned_from, "sideSession": True}
        if detached:
            record["lineageDetached"] = True
        (self._records / f"{session_id}.json").write_text(json.dumps(record), encoding=ENCODING)

    def _run_hook(
        self,
        session_id: str | None,
        hook_input: dict[str, Any],
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        result = self._run_process(session_id, json.dumps(hook_input), extra_environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _run_process(
        self,
        session_id: str | None,
        stdin_text: str,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        removed = (HOST_SESSION_VARIABLE, SWITCH_VARIABLE)
        environment = {key: value for key, value in os.environ.items() if key not in removed}

        environment["HOME"] = str(self._home)
        if session_id is not None:
            environment[HOST_SESSION_VARIABLE] = session_id
        environment.update(extra_environment or {})
        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=stdin_text,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
