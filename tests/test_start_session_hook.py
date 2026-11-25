"""Tests for scripts/start-session-hook.py."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from typing import Any
from unittest import mock

from workflow_world import SCRIPTS_DIRECTORY, load_script

HOOK_SCRIPT = SCRIPTS_DIRECTORY / "start-session-hook.py"
start_session_hook = load_script("start-session-hook")
START_TOOL = "mcp__ccd_session__start_session"
PRE_TOOL_USE = "PreToolUse"
DENY = "deny"
ASK = "ask"
PRESSURE_NORMAL = 1
PRESSURE_WARN = 2
PRESSURE_CRITICAL = 4
DECISION_KEY = "permissionDecision"
REASON_KEY = "permissionDecisionReason"
CONTEXT_KEY = "additionalContext"
CODEX_MARKER = "Codex"
BRIEF = "brief"
CPUS = 10
CALM = (4.0, CPUS)
OVERLOADED = (137.0, CPUS)
AT_LIMIT = (15.0, CPUS)
FULL_START = {
    "initiation": "user_asked",
    "title": "보고서 쓰기",
    "prompt": BRIEF,
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
        tool_input = {"initiation": "own_initiative", "title": "조사", "prompt": BRIEF}
        decision = start_session_hook.decide(tool_input, CALM)

        self.assertEqual(decision[DECISION_KEY], DENY)
        self.assertIn("`model` and `effort`", decision[REASON_KEY])

    def test_denies_missing_levels_even_when_overloaded(self) -> None:
        tool_input = {key: value for key, value in FULL_START.items() if key != "model"}

        self.assertEqual(start_session_hook.decide(tool_input, OVERLOADED)[DECISION_KEY], DENY)

    def test_passes_full_start_when_calm(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, CALM))

    def test_passes_brief_with_or_without_a_codex_line(self) -> None:
        briefs = (BRIEF, "brief\nCodex: 검토만 함", "brief mentions Codex: later", "")

        for brief in briefs:
            with self.subTest(brief=brief):
                self.assertIsNone(start_session_hook.decide(dict(FULL_START, prompt=brief), CALM))

    def test_missing_levels_reason_does_not_ask_for_a_codex_line(self) -> None:
        for missing in ("model", "effort"):
            with self.subTest(missing=missing):
                tool_input = {key: value for key, value in FULL_START.items() if key != missing}
                decision = start_session_hook.decide(tool_input, CALM)

                self.assertEqual(decision[DECISION_KEY], DENY)
                self.assertIn(f"`{missing}`", decision[REASON_KEY])
                self.assertNotIn(CODEX_MARKER, decision[REASON_KEY])

    def test_overloaded_brief_without_codex_line_gets_only_the_load_note(self) -> None:
        decision = start_session_hook.decide(FULL_START, OVERLOADED)

        self.assertNotIn(DECISION_KEY, decision)
        self.assertNotIn(CODEX_MARKER, decision[CONTEXT_KEY])

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

    def test_proposal_start_without_prompt_needs_no_levels(self) -> None:
        self.assertIsNone(start_session_hook.decide({"proposal_id": "task_1"}, CALM))

    def test_offer_without_prompt_passes(self) -> None:
        tool_input = {key: value for key, value in FULL_START.items() if key != "prompt"}

        tool_input["initiation"] = "offer"
        self.assertIsNone(start_session_hook.decide(tool_input, CALM))

    def test_proposal_start_gets_load_note_when_overloaded(self) -> None:
        decision = start_session_hook.decide({"proposal_id": "task_1"}, OVERLOADED)

        self.assertNotIn(DECISION_KEY, decision)
        self.assertIn(CONTEXT_KEY, decision)

    def test_unknown_load_only_checks_levels(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, None))

    def test_asks_user_when_memory_pressure_is_critical(self) -> None:
        decision = start_session_hook.decide(FULL_START, CALM, pressure=PRESSURE_CRITICAL)

        self.assertEqual(decision[DECISION_KEY], ASK)
        self.assertIn("level 4", decision[REASON_KEY])

    def test_asks_user_when_memory_pressure_warns_and_load_is_high(self) -> None:
        decision = start_session_hook.decide(FULL_START, OVERLOADED, pressure=PRESSURE_WARN)

        self.assertEqual(decision[DECISION_KEY], ASK)
        self.assertIn("137", decision[REASON_KEY])
        self.assertNotIn("without asking", decision.get(CONTEXT_KEY, ""))

    def test_does_not_ask_when_memory_pressure_warns_and_load_is_low(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, CALM, pressure=PRESSURE_WARN))

    def test_notes_load_only_when_memory_pressure_is_normal(self) -> None:
        decision = start_session_hook.decide(FULL_START, OVERLOADED, pressure=PRESSURE_NORMAL)

        self.assertNotIn(DECISION_KEY, decision)
        self.assertIn("without asking", decision[CONTEXT_KEY])

    def test_notes_load_only_when_memory_pressure_is_unknown(self) -> None:
        decision = start_session_hook.decide(FULL_START, OVERLOADED, pressure=None)

        self.assertNotIn(DECISION_KEY, decision)
        self.assertIn("without asking", decision[CONTEXT_KEY])

    def test_card_is_not_asked_about_when_memory_is_short(self) -> None:
        tool_input = dict(FULL_START, initiation="own_initiative")

        self.assertIsNone(start_session_hook.decide(tool_input, OVERLOADED, pressure=PRESSURE_CRITICAL))

    def test_proposal_start_is_asked_about_when_memory_is_short(self) -> None:
        decision = start_session_hook.decide({"proposal_id": "task_1"}, CALM, pressure=PRESSURE_CRITICAL)

        self.assertEqual(decision[DECISION_KEY], ASK)

    def test_denies_missing_levels_even_when_memory_is_short(self) -> None:
        tool_input = {key: value for key, value in FULL_START.items() if key != "effort"}
        decision = start_session_hook.decide(tool_input, OVERLOADED, pressure=PRESSURE_CRITICAL)

        self.assertEqual(decision[DECISION_KEY], DENY)

    def test_critical_memory_asks_even_when_the_load_is_unknown(self) -> None:
        decision = start_session_hook.decide(FULL_START, None, pressure=PRESSURE_CRITICAL)

        self.assertEqual(decision[DECISION_KEY], ASK)
        self.assertIn("could not be read", decision["permissionDecisionReason"])

    def test_a_memory_warning_with_an_unknown_load_does_not_ask(self) -> None:
        self.assertIsNone(start_session_hook.decide(FULL_START, None, pressure=PRESSURE_WARN))

    def test_make_output_wraps_memory_question(self) -> None:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": START_TOOL, "tool_input": FULL_START}
        output = start_session_hook.make_output(hook_input, CALM, PRESSURE_CRITICAL)

        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], PRE_TOOL_USE)
        self.assertEqual(output["hookSpecificOutput"][DECISION_KEY], ASK)

    def test_reads_memory_pressure_level(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="2\n", stderr="")

        with mock.patch.object(start_session_hook.subprocess, "run", return_value=completed):
            self.assertEqual(start_session_hook._read_pressure(), PRESSURE_WARN)

    def test_unreadable_memory_pressure_is_unknown(self) -> None:
        failures = (
            OSError("no sysctl"),
            subprocess.CalledProcessError(1, "sysctl"),
            subprocess.TimeoutExpired("sysctl", 2),
        )

        for failure in failures:
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch.object(start_session_hook.subprocess, "run", side_effect=failure),
            ):
                self.assertIsNone(start_session_hook._read_pressure())

    def test_non_numeric_memory_pressure_is_unknown(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="unknown\n", stderr="")

        with mock.patch.object(start_session_hook.subprocess, "run", return_value=completed):
            self.assertIsNone(start_session_hook._read_pressure())

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

    def test_malformed_input_exits_1_so_the_settings_entry_blocks_the_start(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn("start-session-hook:", completed.stderr)

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
