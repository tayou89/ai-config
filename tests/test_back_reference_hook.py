#!/usr/bin/env python3
"""Tests for scripts/back-reference-hook.py."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "back-reference-hook.py"
WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
QUESTION_TOOL = "AskUserQuestion"
PRE_TOOL_USE = "PreToolUse"
STOP = "Stop"
HOOK_OUTPUT_KEY = "hookSpecificOutput"
NOTICE_PREFIX = "[BACK-REFERENCE]"
DENY = "deny"
BLOCK = "block"


class BackReferenceHookTest(unittest.TestCase):
    def test_notices_wrap_up_question_pointing_to_earlier_draft(self) -> None:
        tool_input = {
            "recap": "운영 준비 템플릿 문안은 아직 검토를 기다립니다.",
            "ask": "운영 준비 칸을 앞서 보여 드린 문안대로 세 템플릿에 넣을까요?",
            "actions": [{"label": "문안대로 반영"}, {"label": "고칠 곳 있음"}],
        }
        output = self._run_pre(WRAP_UP_TOOL, tool_input)

        self.assertIn(NOTICE_PREFIX, self._get_context(output))
        self.assertIn("앞서 보여", self._get_context(output))
        self.assertNotIn("permissionDecision", output[HOOK_OUTPUT_KEY])

    def test_notices_wrap_up_question_pointing_within_message(self) -> None:
        output = self._run_pre(WRAP_UP_TOOL, {"ask": "줄인 문안을 포함한 아래 내용이 맞나요?"})

        self.assertIn("아래 내용", self._get_context(output))

    def test_notices_wrap_up_question_pointing_to_report_section(self) -> None:
        output = self._run_pre(WRAP_UP_TOOL, {"ask": "B안을 보고서 7절 문구대로 반영할까요?"})

        self.assertIn("7절", self._get_context(output))

    def test_passes_word_ending_in_wi(self) -> None:
        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, {"ask": "탐색 순위 목록을 어느 쪽으로 할까요?"}))

    def test_notices_reply_label_pointing_back(self) -> None:
        tool_input = {"ask": "운영 준비 칸 문안을 반영할까요?", "actions": [{"label": "앞서 드린 문안대로"}]}

        self.assertIn(NOTICE_PREFIX, self._get_context(self._run_pre(WRAP_UP_TOOL, tool_input)))

    def test_passes_self_contained_wrap_up_question(self) -> None:
        tool_input = {
            "recap": "앞서 권한 보존 기간은 거두었습니다.",
            "ask": "질문할 때 검토할 문안을 다시 싣게 하는 규칙 보강이 맞을까요?",
            "actions": [{"label": "맞아요"}, {"label": "보관", "action": "archive"}],
        }

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, tool_input))

    def test_passes_pointer_inside_quotes(self) -> None:
        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, {"ask": '"앞서 보여 드린" 같은 말을 찾는 훅을 넣을까요?'}))

    def test_denies_question_dialog_pointing_back_without_text(self) -> None:
        tool_input = {
            "questions": [
                {
                    "question": "앞서 말씀드린 두 안 가운데 어느 쪽으로 할까요?",
                    "header": "방식",
                    "options": [
                        {"label": "첫째 안", "description": "추천"},
                        {"label": "둘째 안", "description": "대안"},
                    ],
                    "multiSelect": False,
                },
            ],
        }
        output = self._run_pre(QUESTION_TOOL, tool_input)

        self.assertEqual(output[HOOK_OUTPUT_KEY]["permissionDecision"], DENY)
        self.assertIn("앞서 말씀", output[HOOK_OUTPUT_KEY]["permissionDecisionReason"])

    def test_passes_question_dialog_with_preview(self) -> None:
        tool_input = {
            "questions": [
                {
                    "question": "앞서 보여 드린 문안대로 할까요?",
                    "header": "문안",
                    "options": [{"label": "문안대로", "description": "그대로", "preview": "운영 준비: 로그·경보·복구"}],
                    "multiSelect": False,
                },
            ],
        }

        self.assertIsNone(self._run_pre(QUESTION_TOOL, tool_input))

    def test_passes_question_dialog_with_file_link(self) -> None:
        tool_input = {
            "questions": [
                {
                    "question": "앞서 정리한 문안([보고서](notes/report.md))대로 할까요?",
                    "header": "문안",
                    "options": [{"label": "그대로", "description": "반영"}, {"label": "고침", "description": "수정"}],
                    "multiSelect": False,
                },
            ],
        }

        self.assertIsNone(self._run_pre(QUESTION_TOOL, tool_input))

    def test_blocks_closing_message_asking_about_earlier_draft(self) -> None:
        message = (
            "커밋은 마쳤습니다.\n\n- **운영 준비 템플릿:** 세 템플릿을 앞서 보여 드린 문안대로 고칠지 알려 주십시오."
        )
        output = self._run_stop(message, stop_hook_active=False)

        self.assertEqual(output["decision"], BLOCK)
        self.assertIn("앞서 보여", output["reason"])

    def test_passes_closing_message_with_linked_text(self) -> None:
        message = "앞서 보여 드린 문안은 [보고서](</notes/report one.md>) 2절에 있습니다. 이대로 고칠지 알려 주세요."

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_passes_closing_message_with_quoted_text(self) -> None:
        message = "앞서 보여 드린 문안은 다음과 같습니다. 이대로 넣을까요?\n> 운영 준비: 로그·경보·복구"

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_passes_closing_message_pointing_back_without_question(self) -> None:
        self.assertIsNone(self._run_stop("앞서 말씀드린 대로 반영하고 푸시했습니다.", stop_hook_active=False))

    def test_passes_pointer_and_question_in_different_sentences(self) -> None:
        message = "앞서 말씀드린 항목은 모두 반영했습니다. 다음 작업을 진행할까요?"

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_passes_text_shown_in_next_paragraph(self) -> None:
        message = "앞서 보여 드린 문안대로 반영할까요?\n\n```\n운영 준비: 로그·경보·복구\n```"

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_passes_sentence_introducing_the_text(self) -> None:
        message = "앞서 보여 드린 문안은 아래와 같습니다.\n1. 로그\n2. 경보\n이대로 반영할까요?"

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_blocks_question_in_list_of_open_items(self) -> None:
        message = "남은 일입니다.\n- 커밋은 마쳤습니다.\n- 세 템플릿을 앞선 문안대로 고쳐도 됩니까"
        output = self._run_stop(message, stop_hook_active=False)

        self.assertEqual(output["decision"], BLOCK)
        self.assertIn("앞선 문안", output["reason"])

    def test_blocks_when_nearby_link_is_only_a_session_link(self) -> None:
        message = (
            "[설정 관리](#local_e1edc100) 세션에 카드를 올렸습니다.\n\n"
            "- **운영 준비 템플릿:** 세 템플릿을 앞서 보여 드린 문안대로 고칠지 알려 주십시오."
        )

        self.assertEqual(self._run_stop(message, stop_hook_active=False)["decision"], BLOCK)

    def test_blocks_when_fraction_looks_like_path(self) -> None:
        output = self._run_stop("방금 보여 드린 안을 1/2.5 비율로 할까요?", stop_hook_active=False)

        self.assertEqual(output["decision"], BLOCK)

    def test_blocks_english_pointer(self) -> None:
        output = self._run_stop("Shall I apply the draft I showed earlier?", stop_hook_active=False)

        self.assertEqual(output["decision"], BLOCK)

    def test_notices_box_pointer_forms(self) -> None:
        for ask in ("이 메시지로 커밋할까요?", "Is the text below right?", "B안을 보고서 4·5절 문구대로 반영할까요?"):
            with self.subTest(ask=ask):
                self.assertIn(NOTICE_PREFIX, self._get_context(self._run_pre(WRAP_UP_TOOL, {"ask": ask})))

    def test_passes_box_words_that_are_not_pointers(self) -> None:
        for ask in ("본문 규칙을 고칠까요?", "above-the-fold 영역을 줄일까요?"):
            with self.subTest(ask=ask):
                self.assertIsNone(self._run_pre(WRAP_UP_TOOL, {"ask": ask}))

    def test_denies_pointer_in_option_description(self) -> None:
        tool_input = {
            "questions": [
                {
                    "question": "운영 준비 칸을 어떻게 할까요?",
                    "header": "문안",
                    "options": [
                        {"label": "그대로", "description": "앞서 정리한 문안대로 넣기"},
                        {"label": "보류", "description": "나중에 정하기"},
                    ],
                    "multiSelect": False,
                },
            ],
        }
        output = self._run_pre(QUESTION_TOOL, tool_input)

        self.assertEqual(output[HOOK_OUTPUT_KEY]["permissionDecision"], DENY)
        self.assertTrue(output[HOOK_OUTPUT_KEY]["permissionDecisionReason"].startswith(NOTICE_PREFIX))

    def test_settings_register_hook_for_its_events(self) -> None:
        hooks = json.loads((REPOSITORY_ROOT / "settings.json").read_text(encoding="utf-8"))["hooks"]
        registered = {
            (event, entry.get("matcher", ""))
            for event, entries in hooks.items()
            for entry in entries
            for hook in entry.get("hooks", [])
            if HOOK_SCRIPT.name in hook.get("command", "")
        }

        self.assertEqual(registered, {(PRE_TOOL_USE, f"{WRAP_UP_TOOL}|{QUESTION_TOOL}"), (STOP, "")})

    def test_passes_closing_message_when_already_continued(self) -> None:
        message = "앞서 보여 드린 문안대로 고칠지 알려 주십시오."

        self.assertIsNone(self._run_stop(message, stop_hook_active=True))

    def test_passes_unrelated_tool_and_bad_input(self) -> None:
        self.assertIsNone(self._run_pre("Bash", {"command": "echo 앞서 보여 드린 문안대로 할까요?"}))
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")

    def _run_pre(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        return self._run({"hook_event_name": PRE_TOOL_USE, "tool_name": tool_name, "tool_input": tool_input})

    def _run_stop(self, message: str, stop_hook_active: bool) -> Any:
        hook_input = {"hook_event_name": STOP, "stop_hook_active": stop_hook_active, "last_assistant_message": message}

        return self._run(hook_input)

    def _run(self, hook_input: dict[str, Any]) -> Any:
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            check=True,
        )

        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def _get_context(self, output: Any) -> str:
        return str(output[HOOK_OUTPUT_KEY]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
