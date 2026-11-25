#!/usr/bin/env python3
"""Tests for scripts/plain-words-hook.py."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "plain-words-hook.py"
WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
QUESTION_TOOL = "AskUserQuestion"
PRE_TOOL_USE = "PreToolUse"
HOOK_OUTPUT_KEY = "hookSpecificOutput"
NOTICE_PREFIX = "[PLAIN WORDS]"
DENY = "deny"


class PlainWordsHookTest(unittest.TestCase):
    def test_denies_box_with_identifiers(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "commit number": {"recap": "규칙을 올렸습니다(커밋 170d2d4)."},
            "sentence-final commit number": {"recap": "규칙을 올린 커밋은 170d2d4. 다음은 설치입니다."},
            "command option": {"recap": "설치를 --from-upstream으로 마쳤습니다."},
            "exit code": {"recap": "커밋 도구가 종료 8로 멈췄습니다."},
            "English exit code": {"recap": "The tool stopped with exit code 7."},
            "file name": {"recap": "AGENTS.md 71행을 고쳤습니다."},
            "file path": {"recap": "scripts/install.py를 고쳤습니다."},
            "home path": {"recap": "~/.claude/rules/x.md를 고쳤습니다."},
            "hidden file name": {"ask": ".agent-workflow.json을 넣을까요?"},
            "code": {"actions": [{"label": "`main`에 넣기"}]},
        }

        for kind, tool_input in cases.items():
            with self.subTest(kind=kind):
                output = self._run_pre(WRAP_UP_TOOL, tool_input)[HOOK_OUTPUT_KEY]

                self.assertEqual(output["permissionDecision"], DENY)
                self.assertIn(NOTICE_PREFIX, output["permissionDecisionReason"])

    def test_names_the_words_it_found(self) -> None:
        output = self._run_pre(WRAP_UP_TOOL, {"recap": "baae498을 올리고 install.py로 설치했습니다."})
        reason = str(output[HOOK_OUTPUT_KEY]["permissionDecisionReason"])

        self.assertIn("baae498 (commit number)", reason)
        self.assertIn("install.py (file name)", reason)

    def test_denies_question_dialog_with_identifiers(self) -> None:
        tool_input = {
            "questions": [
                {
                    "question": "어느 쪽으로 할까요?",
                    "header": "방식",
                    "options": [{"label": "바로 넣기", "description": "--no-stage로 커밋합니다."}],
                },
            ],
        }
        output = self._run_pre(QUESTION_TOOL, tool_input)[HOOK_OUTPUT_KEY]

        self.assertEqual(output["permissionDecision"], DENY)

    def test_notices_plain_box_that_asks_decision(self) -> None:
        tool_input = {"recap": "계획을 썼고 아직 고친 것은 없습니다.", "ask": "이대로 진행할까요?", "actions": [{"label": "네, 진행"}]}
        output = self._run_pre(WRAP_UP_TOOL, tool_input)[HOOK_OUTPUT_KEY]

        self.assertNotIn("permissionDecision", output)
        self.assertIn(NOTICE_PREFIX, str(output["additionalContext"]))

    def test_passes_plain_box_without_decision(self) -> None:
        tool_input = {"recap": "규칙을 고쳐 설치했습니다. 새 세션부터 따릅니다.", "actions": [{"label": "커밋", "action": "commit"}]}

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, tool_input))

    def test_passes_numbers_links_and_session_ids(self) -> None:
        tool_input = {
            "recap": "시험 538개가 통과했고 20~30분 걸립니다. 2026-11-17에 [보고서](notes/report.md)를 남겼습니다. 색은 #1a2b3c입니다.",
            "ask": "",
            "actions": [{"label": "보고서 열기", "action": "verify"}],
        }

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, tool_input))
        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, {"recap": "local_c01e4481-76fe-4d8e 세션에 알렸습니다."}))
        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, {"recap": "화면은 Node.js와 Vue.js로 만듭니다."}))

    def test_passes_unrelated_tool_and_shows_bad_input_as_a_hook_error(self) -> None:
        self.assertIsNone(self._run_pre("Bash", {"command": "git show 170d2d4"}))
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual((completed.returncode, completed.stdout), (1, ""))
        self.assertIn("plain-words-hook:", completed.stderr)

    def _run_pre(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        hook_input = {"hook_event_name": PRE_TOOL_USE, "tool_name": tool_name, "tool_input": tool_input}

        return self._run(hook_input)

    def _run(self, hook_input: dict[str, Any]) -> Any:
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            check=True,
        )
        stdout = completed.stdout.strip()

        return json.loads(stdout) if stdout else None


if __name__ == "__main__":
    unittest.main()
