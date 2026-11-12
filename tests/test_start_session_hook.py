"""Tests for scripts/start-session-hook.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "start-session-hook.py"
HOOK_SPECIFICATION = importlib.util.spec_from_file_location("start_session_hook", HOOK_SCRIPT)
start_session_hook = importlib.util.module_from_spec(HOOK_SPECIFICATION)
HOOK_SPECIFICATION.loader.exec_module(start_session_hook)
START_TOOL = "mcp__ccd_session__start_session"
PRE_TOOL_USE = "PreToolUse"
DENY = "deny"
DECISION_KEY = "permissionDecision"
REASON_KEY = "permissionDecisionReason"
CONTEXT_KEY = "additionalContext"
CPUS = 10
CALM = (4.0, CPUS)
OVERLOADED = (137.0, CPUS)
AT_LIMIT = (15.0, CPUS)
FULL_START = {
    "initiation": "user_asked",
    "title": "보고서 쓰기",
    "prompt": "brief",
    "model": "claude-opus-5-5",
    "effort": "high",
}


class StartSessionHookTest(unittest.TestCase):
    def test_denies_start_without_effort(self) -> None:
        tool_input = {key: value for key, value in FULL_START.items() if key != "effort"}
        decision = start_session_hook.decide(tool_input, CALM)

        self.assertEqual(decision[DECISION_KEY], DENY)
        self.assertIn("`effort`", decision[REASON_KEY])
        self.assertNotIn("`model`", decision[REASON_KEY])

    def test_denies_card_without_model_and_effort(self) -> None:
        tool_input = {"initiation": "own_initiative", "title": "조사", "prompt": "brief"}
        decision = start_session_hook.decide(tool_input, CALM)

        self.assertEqual(decision[DECISION_KEY], DENY)
        self.assertIn("`model` and `effort`", decision[REASON_KEY])

    def test_denies_missing_levels_even_when_overloaded(self) -> None:
        tool_input = {key: value for key, value in FULL_START.items() if key != "model"}

        self.assertEqual(start_session_hook.decide(tool_input, OVERLOADED)[DECISION_KEY], DENY)

    def test_passes_full_start_when_calm(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, CALM))

    def test_notes_load_without_asking_when_overloaded(self) -> None:
        decision = start_session_hook.decide(FULL_START, OVERLOADED)

        self.assertNotIn(DECISION_KEY, decision)
        self.assertIn("137", decision[CONTEXT_KEY])
        self.assertIn("without asking", decision[CONTEXT_KEY])

    def test_passes_at_the_limit(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, AT_LIMIT))

    def test_card_gets_no_load_note_when_overloaded(self) -> None:
        tool_input = dict(FULL_START, initiation="own_initiative")

        self.assertIsNone(start_session_hook.decide(tool_input, OVERLOADED))

    def test_offer_gets_load_note_when_overloaded(self) -> None:
        tool_input = dict(FULL_START, initiation="offer")

        self.assertIn(CONTEXT_KEY, start_session_hook.decide(tool_input, OVERLOADED))

    def test_proposal_start_needs_no_levels(self) -> None:
        self.assertIsNone(start_session_hook.decide({"proposal_id": "task_1"}, CALM))

    def test_proposal_start_gets_load_note_when_overloaded(self) -> None:
        decision = start_session_hook.decide({"proposal_id": "task_1"}, OVERLOADED)

        self.assertNotIn(DECISION_KEY, decision)
        self.assertIn(CONTEXT_KEY, decision)

    def test_unknown_load_only_checks_levels(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, None))

    def test_ignores_other_tools(self) -> None:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": "Bash", "tool_input": {}}

        self.assertIsNone(start_session_hook.make_output(hook_input, OVERLOADED))

    def test_main_prints_deny_decision(self) -> None:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": START_TOOL, "tool_input": {"title": "조사"}}
        output = self._run(hook_input)

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], PRE_TOOL_USE)
        self.assertEqual(output["hookSpecificOutput"][DECISION_KEY], DENY)

    def test_make_output_wraps_load_note(self) -> None:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": START_TOOL, "tool_input": FULL_START}
        output = start_session_hook.make_output(hook_input, OVERLOADED)

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], PRE_TOOL_USE)
        self.assertIn(CONTEXT_KEY, output["hookSpecificOutput"])
        self.assertNotIn(DECISION_KEY, output["hookSpecificOutput"])

    def test_main_fails_open_on_malformed_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")

    def _run(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            check=True,
        )

        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()
