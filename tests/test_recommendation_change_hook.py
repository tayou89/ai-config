#!/usr/bin/env python3
"""Tests for scripts/recommendation-change-hook.py."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "recommendation-change-hook.py"
WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
PRE_TOOL_USE = "PreToolUse"
STOP = "Stop"
HOOK_OUTPUT_KEY = "hookSpecificOutput"
NOTICE_PREFIX = "[RECOMMENDATION CHANGE]"
TIMING_NOTE = "When it appeared is its place in order against the earlier recommendation, or a time read from a clock or a record, not an estimate."
BLOCK = "block"


class RecommendationChangeHookTest(unittest.TestCase):
    def test_notices_wrap_up_recap_that_changes_recommendation(self) -> None:
        tool_input = {
            "recap": "메뉴 반응 비교판에 E안을 더했고, 추천을 C에서 E로 바꿨습니다.",
            "ask": "E안으로 정할까요?",
        }
        output = self._run_pre(WRAP_UP_TOOL, tool_input)
        context = str(output[HOOK_OUTPUT_KEY]["additionalContext"])

        self.assertIn(NOTICE_PREFIX, context)
        self.assertIn("추천을 C에서 E로 바꿨", context)
        self.assertNotIn("permissionDecision", output[HOOK_OUTPUT_KEY])

    def test_notices_reply_label_and_english_recap(self) -> None:
        label_input = {"recap": "비교판을 고쳤습니다.", "actions": [{"label": "추천 바꿈 확인"}]}
        english_input = {"recap": "I changed my recommendation to the quiet option after the review."}

        self.assertIsNotNone(self._run_pre(WRAP_UP_TOOL, label_input))
        self.assertIsNotNone(self._run_pre(WRAP_UP_TOOL, english_input))

    def test_passes_wrap_up_without_change(self) -> None:
        tool_input = {
            "recap": "추천대로 규칙을 바꿨습니다. 추천안(1B 2A)대로 바꿨고, (추천): 카드 테두리를 구분선으로 바꿉니다.",
            "ask": "추천한 대로, 결함 두 개도 함께 고칠까요?",
            "actions": [{"label": "추천을 바꾸지 않음"}, {"label": "커밋", "action": "commit"}],
        }

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, tool_input))

    def test_passes_quoted_and_negated_change(self) -> None:
        tool_input = {"recap": '"추천을 B로 바꿨습니다" 같은 말을 찾는 훅입니다. We did not change the recommendation.'}

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, tool_input))

    def test_continues_closing_message_with_undated_change(self) -> None:
        message = "말씀을 듣고 다시 보니 이 안이 더 맞습니다.\n\n그래서 추천을 E로 바꾸겠습니다."
        output = self._run_stop(message, stop_hook_active=False)

        self.assertEqual(output["decision"], BLOCK)
        self.assertIn(NOTICE_PREFIX, output["reason"])
        self.assertIn("추천을 E로 바꾸겠", output["reason"])

    def test_says_where_the_timing_comes_from(self) -> None:
        recap_input = {"recap": "추천을 C에서 E로 바꿨습니다."}
        notice = str(self._run_pre(WRAP_UP_TOOL, recap_input)[HOOK_OUTPUT_KEY]["additionalContext"])
        reason = str(self._run_stop("그래서 추천을 E로 바꾸겠습니다.", stop_hook_active=False)["reason"])

        self.assertTrue(notice.endswith(TIMING_NOTE), notice)
        self.assertTrue(reason.endswith(TIMING_NOTE), reason)

    def test_continues_closing_message_with_changed_result_list(self) -> None:
        message = "검토 지적을 반영했습니다. 그 결과 추천이 1-C·2-B·3-B에서 1-D·2-D·3-C로 바뀌었습니다."

        self.assertEqual(self._run_stop(message, stop_hook_active=False)["decision"], BLOCK)

    def test_passes_closing_message_that_dates_its_evidence(self) -> None:
        dated_messages = [
            "추천을 B에서 C로 바꿉니다.\n\n근거는 B를 만든 뒤에 나온 1440px 캡처입니다. 눈 변화가 보이지 않았습니다.",
            "추천을 A로 바꿨습니다. 이번 비교에서 처음 얻은 결과 때문입니다.",
            "The recommendation changed to A because of a result that first appeared after the last board.",
        ]

        for message in dated_messages:
            with self.subTest(message=message):
                self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_passes_closing_message_without_change(self) -> None:
        message = "메뉴 반응은 추천대로 빛 없이 갑니다. 새 사실이 나오지 않는 한 추천을 바꾸지 않겠습니다."

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_continues_own_change_in_this_session_and_proposal_form(self) -> None:
        messages = [
            "이 세션에서 추천을 B로 바꿨습니다.",
            "추천안을 B로 바꿨습니다.",
            "In this session I changed my recommendation.",
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(self._run_stop(message, stop_hook_active=False)["decision"], BLOCK)

    def test_passes_other_words_questions_and_undated_file_names(self) -> None:
        messages = [
            "추천 이유를 바꿨습니다.",
            "추천 문구를 고쳤습니다.",
            "추천을 유지하고 다른 값을 바꿨습니다.",
            "추천을 바꿔야 할까요?",
            "Should I change my recommendation?",
            "This changes the recommendation engine.",
            "We now recommend reading the docs.",
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertIsNone(self._run_stop(message, stop_hook_active=False))
        self.assertIsNotNone(self._run_stop("추천을 B로 바꿨습니다. 기록은 report-2026-11-17.md에 있습니다.", False))

    def test_passes_relayed_change(self) -> None:
        relayed_messages = [
            "백업 조사 세션이 Codex 검토를 반영해 추천을 고쳤습니다.",
            "메인 세션에는 추천을 B로 바꿨다고 알렸습니다.",
            "그 세션에서 시험해 보니 추천이 A에서 A'로 바뀌었다고 합니다.",
            "리뷰어가 추천을 바꿨습니다.",
        ]

        for message in relayed_messages:
            with self.subTest(message=message):
                self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_passes_change_inside_code_block(self) -> None:
        message = "훅이 찾는 문장 예시입니다.\n\n```text\n추천을 B로 바꿨습니다\n```"

        self.assertIsNone(self._run_stop(message, stop_hook_active=False))

    def test_continues_only_once(self) -> None:
        self.assertIsNone(self._run_stop("추천을 E로 바꾸겠습니다.", stop_hook_active=True))

    def test_passes_unrelated_tool_and_shows_bad_input_as_a_hook_error(self) -> None:
        self.assertIsNone(self._run_pre("Bash", {"command": "echo 추천을 E로 바꿨습니다"}))
        completed = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual((completed.returncode, completed.stdout), (1, ""))
        self.assertIn("recommendation-change-hook:", completed.stderr)

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


if __name__ == "__main__":
    unittest.main()
