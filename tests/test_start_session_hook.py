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
ASK = "ask"
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
        verdict, reason = start_session_hook.decide(tool_input, CALM)

        self.assertEqual(verdict, DENY)
        self.assertIn("`effort`", reason)
        self.assertNotIn("`model`", reason)

    def test_denies_card_without_model_and_effort(self) -> None:
        tool_input = {"initiation": "own_initiative", "title": "조사", "prompt": "brief"}
        verdict, reason = start_session_hook.decide(tool_input, CALM)

        self.assertEqual(verdict, DENY)
        self.assertIn("`model` and `effort`", reason)

    def test_passes_full_start_when_calm(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, CALM))

    def test_asks_before_start_when_overloaded(self) -> None:
        verdict, reason = start_session_hook.decide(FULL_START, OVERLOADED)

        self.assertEqual(verdict, ASK)
        self.assertIn("137", reason)

    def test_passes_at_the_limit(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, AT_LIMIT))

    def test_card_does_not_ask_when_overloaded(self) -> None:
        tool_input = dict(FULL_START, initiation="own_initiative")

        self.assertIsNone(start_session_hook.decide(tool_input, OVERLOADED))

    def test_offer_asks_when_overloaded(self) -> None:
        tool_input = dict(FULL_START, initiation="offer")

        self.assertEqual(start_session_hook.decide(tool_input, OVERLOADED)[0], ASK)

    def test_proposal_start_needs_no_levels(self) -> None:
        self.assertIsNone(start_session_hook.decide({"proposal_id": "task_1"}, CALM))

    def test_proposal_start_asks_when_overloaded(self) -> None:
        self.assertEqual(start_session_hook.decide({"proposal_id": "task_1"}, OVERLOADED)[0], ASK)

    def test_unknown_load_only_checks_levels(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, None))

    def test_ignores_other_tools(self) -> None:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": "Bash", "tool_input": {}}

        self.assertIsNone(start_session_hook.make_output(hook_input, OVERLOADED))

    def test_main_prints_deny_decision(self) -> None:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": START_TOOL, "tool_input": {"title": "조사"}}
        output = self._run(hook_input)

        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], DENY)

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
