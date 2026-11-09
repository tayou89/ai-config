#!/usr/bin/env python3
"""Tests for scripts/reply-language-hook.py."""

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
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "reply-language-hook.py"
PLAN_TOOL = "mcp__ccd_turn__update_plan"
WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
QUESTION_TOOL = "AskUserQuestion"
CAPTION_TOOL = "SendUserFile"
SESSION_TOOL = "mcp__ccd_session__start_session"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
LANGUAGE_VARIABLE = "REPLY_LANGUAGE"
TEMPORARY_DIRECTORY_VARIABLE = "TMPDIR"
HOOK_OUTPUT_KEY = "hookSpecificOutput"
KOREAN = "ko"
DENY = "deny"
SESSION_ID = "test-session"
REMINDER_PREFIX = "[LANGUAGE REMINDER]"
ENGLISH_LINE = "Now editing the wrapper to add the token scan."
KOREAN_LINE = "이제 래퍼에 토큰 검사를 더하겠습니다."
TAIL_BYTES = 131072


class ReplyLanguageHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name)
        self._transcript = self._root / "transcript.jsonl"
        self._transcript.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_denies_english_task_list_item(self) -> None:
        items = [{"content": "보고서 쓰기", "status": "pending"}, {"content": "Run the tests", "status": "pending"}]
        output = self._run_pre(PLAN_TOOL, {"items": items})

        self.assertEqual(self._get_decision(output), DENY)
        self.assertIn("items[1].content", self._get_reason(output))
        self.assertNotIn("items[0].content", self._get_reason(output))

    def test_passes_korean_task_list(self) -> None:
        items = [
            {"content": "`settings.json`에 훅 등록", "status": "in_progress"},
            {"content": "ruff와 mypy 점검", "status": "pending"},
        ]

        self.assertIsNone(self._run_pre(PLAN_TOOL, {"items": items}))

    def test_passes_plan_advance_without_items(self) -> None:
        self.assertIsNone(self._run_pre(PLAN_TOOL, {"advance": True}))

    def test_skips_null_field(self) -> None:
        self.assertIsNone(self._run_pre(PLAN_TOOL, {"items": [{"content": None, "status": "pending"}]}))

    def test_denies_english_recap_and_reply_label(self) -> None:
        tool_input = {
            "recap": "The report is sent and nothing is committed yet.",
            "ask": "어느 안으로 할까요?",
            "actions": [{"label": "Yes, archive it"}, {"label": "보관", "action": "archive"}],
        }
        output = self._run_pre(WRAP_UP_TOOL, tool_input)

        self.assertEqual(self._get_decision(output), DENY)
        self.assertIn("recap", self._get_reason(output))
        self.assertIn("actions[0].label", self._get_reason(output))
        self.assertNotIn("ask:", self._get_reason(output))

    def test_ignores_label_of_app_action(self) -> None:
        tool_input = {"recap": "커밋 `bd981b7`을 푸시했습니다.", "actions": [{"label": "Commit", "action": "commit"}]}

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, tool_input))

    def test_passes_korean_recap_with_code_and_paths(self) -> None:
        recap = "`scripts/reply-language-hook.py`를 ~/Desktop/codes/settings/ai-config에 더했고 README.md도 고쳤습니다. 커밋 4653b64는 푸시했습니다."

        self.assertIsNone(self._run_pre(WRAP_UP_TOOL, {"recap": recap}))

    def test_name_only_field_passes_with_korean_word(self) -> None:
        denied = self._run_pre(SESSION_TOOL, {"title": "Redis"})

        self.assertEqual(self._get_decision(denied), DENY)
        self.assertIn("only a name", self._get_reason(denied))
        self.assertIsNone(self._run_pre(SESSION_TOOL, {"title": "Redis 설정"}))

    def test_denies_english_question_option(self) -> None:
        question = {
            "question": "어느 방식으로 할까요?",
            "header": "방식",
            "options": [{"label": "규칙만", "description": "Change the wording only"}],
        }
        output = self._run_pre(QUESTION_TOOL, {"questions": [question]})

        self.assertEqual(self._get_decision(output), DENY)
        self.assertIn("options[0].description", self._get_reason(output))

    def test_denies_english_caption_and_title(self) -> None:
        self.assertEqual(self._get_decision(self._run_pre(CAPTION_TOOL, {"caption": "Here is the report."})), DENY)
        self.assertEqual(self._get_decision(self._run_pre(SESSION_TOOL, {"title": "Fix login bug"})), DENY)
        self.assertIsNone(self._run_pre(CAPTION_TOOL, {"caption": "조사 보고서입니다."}))

    def test_ignores_other_tools(self) -> None:
        self.assertIsNone(self._run_pre("Bash", {"command": "echo hello world from the test"}))

    def test_does_nothing_without_known_language(self) -> None:
        tool_input = {"items": [{"content": "Run the tests", "status": "pending"}]}

        self.assertIsNone(self._run_pre(PLAN_TOOL, tool_input, language=None))
        self.assertIsNone(self._run_pre(PLAN_TOOL, tool_input, language="KO"))

    def test_fails_open_on_malformed_input(self) -> None:
        for raw_input in (
            "not json",
            json.dumps([1, 2]),
            json.dumps({"hook_event_name": PRE_TOOL_USE, "tool_name": PLAN_TOOL, "tool_input": [1]}),
        ):
            result = self._run_raw(raw_input, KOREAN)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_fails_open_on_missing_transcript(self) -> None:
        hook_input = {
            "hook_event_name": POST_TOOL_USE,
            "session_id": SESSION_ID,
            "transcript_path": str(self._root / "missing.jsonl"),
        }
        result = self._run_raw(json.dumps(hook_input), KOREAN)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_reminds_once_after_english_prose(self) -> None:
        self._append_text("a1", "m1", ENGLISH_LINE)
        first = self._run_post()
        second = self._run_post()

        self.assertIn(REMINDER_PREFIX, self._get_context(first))
        self.assertIsNone(second)

    def test_reminds_after_short_english_line(self) -> None:
        self._append_text("a1", "m1", "Checking the file.")
        self.assertIn(REMINDER_PREFIX, self._get_context(self._run_post()))

    def test_passes_korean_prose(self) -> None:
        self._append_text("a1", "m1", KOREAN_LINE)
        self.assertIsNone(self._run_post())

    def test_checks_text_written_after_last_check(self) -> None:
        self._append_text("a1", "m1", KOREAN_LINE)
        self._run_post()
        self._append_text("a2", "m2", ENGLISH_LINE)
        self.assertIn(REMINDER_PREFIX, self._get_context(self._run_post()))

    def test_skips_text_checked_before(self) -> None:
        self._append_text("a1", "m1", ENGLISH_LINE)
        self._run_post()
        self._append_text("a2", "m2", KOREAN_LINE)
        self.assertIsNone(self._run_post())

    def test_first_check_reads_only_latest_message(self) -> None:
        self._append_text("a1", "m1", "An old English line from much earlier in the session.")
        self._append_text("a2", "m2", KOREAN_LINE)
        self.assertIsNone(self._run_post())

    def test_first_check_reads_every_block_of_latest_message(self) -> None:
        self._append_text("a1", "m1", KOREAN_LINE)
        self._append_text("a2", "m1", ENGLISH_LINE)
        self.assertIn(REMINDER_PREFIX, self._get_context(self._run_post()))

    def test_ignores_subagent_text(self) -> None:
        self._append_text("a1", "m1", KOREAN_LINE)
        self._run_post()
        self._append_text("a2", "m2", ENGLISH_LINE, sidechain=True)
        self.assertIsNone(self._run_post())

    def test_reads_entry_with_unicode_line_separator(self) -> None:
        self._append_text("a1", "m1", "Now editing the wrapper to add the token scan.")
        self.assertIn(REMINDER_PREFIX, self._get_context(self._run_post()))

    def test_reads_tail_of_large_transcript(self) -> None:
        filler = {"type": "user", "message": {"content": "x" * 1000}}

        with self._transcript.open("a", encoding="utf-8") as transcript:
            for _ in range(TAIL_BYTES // 1000 + 10):
                transcript.write(json.dumps(filler) + "\n")
        self._append_text("a1", "m1", ENGLISH_LINE)
        self.assertIn(REMINDER_PREFIX, self._get_context(self._run_post()))

    def test_reminds_when_state_cannot_be_saved(self) -> None:
        (self._root / "claude-reply-language").write_text("not a directory", encoding="utf-8")
        self._append_text("a1", "m1", ENGLISH_LINE)
        self.assertIn(REMINDER_PREFIX, self._get_context(self._run_post()))

    def _append_text(self, uuid: str, message_id: str, text: str, sidechain: bool = False) -> None:
        message = {"id": message_id, "content": [{"type": "text", "text": text}]}
        entry = {"type": "assistant", "uuid": uuid, "isSidechain": sidechain, "message": message}

        with self._transcript.open("a", encoding="utf-8") as transcript:
            transcript.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _run_pre(self, tool_name: str, tool_input: object, language: str | None = KOREAN) -> dict[str, Any] | None:
        hook_input = {
            "hook_event_name": PRE_TOOL_USE,
            "session_id": SESSION_ID,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

        return self._run_hook(hook_input, language)

    def _run_post(self) -> dict[str, Any] | None:
        hook_input = {
            "hook_event_name": POST_TOOL_USE,
            "session_id": SESSION_ID,
            "transcript_path": str(self._transcript),
            "tool_name": "Bash",
            "tool_input": {},
        }

        return self._run_hook(hook_input, KOREAN)

    def _run_hook(self, hook_input: dict[str, Any], language: str | None) -> dict[str, Any] | None:
        result = self._run_raw(json.dumps(hook_input), language)

        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _run_raw(self, raw_input: str, language: str | None) -> subprocess.CompletedProcess[str]:
        environment = {key: value for key, value in os.environ.items() if key != LANGUAGE_VARIABLE}

        environment[TEMPORARY_DIRECTORY_VARIABLE] = str(self._root)
        if language:
            environment[LANGUAGE_VARIABLE] = language
        return subprocess.run(
            [sys.executable, "-I", str(HOOK_SCRIPT)],
            input=raw_input,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

    def _get_decision(self, output: dict[str, Any] | None) -> str:
        return str((output or {}).get(HOOK_OUTPUT_KEY, {}).get("permissionDecision", ""))

    def _get_reason(self, output: dict[str, Any] | None) -> str:
        return str((output or {}).get(HOOK_OUTPUT_KEY, {}).get("permissionDecisionReason", ""))

    def _get_context(self, output: dict[str, Any] | None) -> str:
        return str((output or {}).get(HOOK_OUTPUT_KEY, {}).get("additionalContext", ""))


if __name__ == "__main__":
    unittest.main()
