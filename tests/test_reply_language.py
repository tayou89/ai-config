#!/usr/bin/env python3
"""Tests for scripts/reply-language-hook.py."""

from __future__ import annotations

import hashlib
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
STOP = "Stop"
SESSION_START = "SessionStart"
COMPACT = "compact"
LANGUAGE_VARIABLE = "REPLY_LANGUAGE"
TEMPORARY_DIRECTORY_VARIABLE = "TMPDIR"
HOOK_OUTPUT_KEY = "hookSpecificOutput"
KOREAN = "ko"
DENY = "deny"
BLOCK = "block"
SESSION_ID = "test-session"
REMINDER_PREFIX = "[LANGUAGE REMINDER]"
CLOSING_PREFIX = "[REPLY LANGUAGE]"
ENGLISH_REMINDER = '[LANGUAGE REMINDER] Your last message to the user was not in Korean 존댓말: "{quote}". Write every line the user sees in Korean 존댓말 from now on, including progress lines between tool calls.'
NATIVE_REMINDER_START = "[LANGUAGE REMINDER] 방금 사용자에게 쓴 글이 한국어가 아니었습니다"
NATIVE_REMINDER = NATIVE_REMINDER_START + ': "{quote}". 도구 사이 진행 문장까지, 사용자가 읽는 글은 모두 한국어 존댓말로 씁니다.'
ENGLISH_SUMMARY_NOTE = "[LANGUAGE REMINDER] The conversation was just summarized. Every line the user reads stays in Korean 존댓말, including progress lines between tool calls."
NATIVE_SUMMARY_NOTE = "[LANGUAGE REMINDER] 대화가 방금 요약되었습니다. 도구 사이 진행 문장까지, 사용자가 읽는 글은 계속 한국어 존댓말로 씁니다."
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

    def test_malformed_input_shows_a_hook_error_and_lets_the_call_through(self) -> None:
        for raw_input in (
            "not json",
            json.dumps([1, 2]),
            json.dumps({"hook_event_name": PRE_TOOL_USE, "tool_name": PLAN_TOOL, "tool_input": [1]}),
        ):
            result = self._run_raw(raw_input, KOREAN)

            self.assertEqual((result.returncode, result.stdout), (1, ""))
            self.assertIn("reply-language-hook:", result.stderr)

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

    def test_continues_english_closing_once(self) -> None:
        output = self._run_stop(ENGLISH_LINE)

        self.assertEqual((output or {}).get("decision"), BLOCK)
        self.assertEqual(
            (output or {}).get("reason"),
            f'{CLOSING_PREFIX} The closing message was not in Korean 존댓말: "{ENGLISH_LINE}". The user reads it, so a short follow-up can give the same answer in Korean 존댓말, keeping code, paths, and names as they are.',
        )

    def test_quotes_start_of_long_english_closing(self) -> None:
        closing = "I found the cause. " * 20
        reason = str((self._run_stop(closing) or {}).get("reason", ""))

        self.assertIn(f'"{closing[:100]}"', reason)

    def test_passes_closing_that_needs_no_rewrite(self) -> None:
        code_closing = "```python\nprint('hello world from the test runner')\n```\n`scripts/reply-language-hook.py`"

        for closing in (KOREAN_LINE, "Done.", code_closing, "", "   "):
            self.assertIsNone(self._run_stop(closing), closing)

    def test_passes_missing_closing(self) -> None:
        hook_input = {"hook_event_name": STOP, "session_id": SESSION_ID, "stop_hook_active": False}

        self.assertIsNone(self._run_hook(hook_input, KOREAN))

    def test_does_not_continue_twice(self) -> None:
        self.assertIsNone(self._run_stop(ENGLISH_LINE, stop_hook_active=True))

    def test_closing_check_needs_known_language(self) -> None:
        self.assertIsNone(self._run_stop(ENGLISH_LINE, language=None))

    def test_continues_closing_without_readable_transcript(self) -> None:
        output = self._run_stop(ENGLISH_LINE, transcript_path=str(self._root / "missing.jsonl"))

        self.assertEqual((output or {}).get("decision"), BLOCK)

    def test_closing_check_moves_check_position(self) -> None:
        self._append_text("a1", "m1", ENGLISH_LINE)
        self._run_stop(ENGLISH_LINE)
        self.assertIsNone(self._run_post())

    def test_reminds_after_summary_in_session_half_language(self) -> None:
        for native in (True, False):
            session_id = self._find_session(native)
            context = self._get_context(self._run_session_start(COMPACT, session_id))

            self.assertEqual(context, NATIVE_SUMMARY_NOTE if native else ENGLISH_SUMMARY_NOTE, session_id)

    def test_other_session_starts_get_no_notice(self) -> None:
        session_id = self._find_session(native=True)

        for source in ("startup", "resume", "clear", ""):
            self.assertIsNone(self._run_session_start(source, session_id), source)
        self.assertIsNone(self._run_session_start(COMPACT, session_id, language=None))

    def test_summary_note_is_english_without_session_id(self) -> None:
        context = self._get_context(self._run_session_start(COMPACT, ""))

        self.assertEqual(context, ENGLISH_SUMMARY_NOTE)

    def test_reminder_is_written_in_session_half_language(self) -> None:
        for native in (True, False):
            session_id = self._find_session(native)
            expected = NATIVE_REMINDER if native else ENGLISH_REMINDER

            self._transcript.write_text("", encoding="utf-8")
            self._append_text(f"{session_id}-a1", "m1", ENGLISH_LINE)
            self.assertEqual(self._get_context(self._run_post(session_id)), expected.format(quote=ENGLISH_LINE))

    def test_session_keeps_its_reminder_language(self) -> None:
        session_id = self._find_session(native=True)

        self._append_text("a1", "m1", ENGLISH_LINE)
        first = self._get_context(self._run_post(session_id))
        self._append_text("a2", "m2", "Now running the whole test suite again.")
        second = self._get_context(self._run_post(session_id))

        self.assertTrue(first.startswith(NATIVE_REMINDER_START))
        self.assertTrue(second.startswith(NATIVE_REMINDER_START))

    def _run_stop(
        self,
        closing: str,
        stop_hook_active: bool = False,
        language: str | None = KOREAN,
        transcript_path: str | None = None,
    ) -> dict[str, Any] | None:
        hook_input = {
            "hook_event_name": STOP,
            "session_id": SESSION_ID,
            "transcript_path": transcript_path or str(self._transcript),
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": closing,
        }

        return self._run_hook(hook_input, language)

    def _run_session_start(self, source: str, session_id: str, language: str | None = KOREAN) -> dict[str, Any] | None:
        hook_input = {
            "hook_event_name": SESSION_START,
            "session_id": session_id,
            "transcript_path": str(self._transcript),
            "source": source,
        }

        return self._run_hook(hook_input, language)

    def _find_session(self, native: bool) -> str:
        candidates = (f"ab-test-{index}" for index in range(100))

        return next(session_id for session_id in candidates if self._is_native_half(session_id) == native)

    def _is_native_half(self, session_id: str) -> bool:
        """The hook's split: an even first byte of the id's SHA-256 gets reminders in the user's language."""
        return hashlib.sha256(session_id.encode("utf-8")).digest()[0] % 2 == 0

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

    def _run_post(self, session_id: str = SESSION_ID) -> dict[str, Any] | None:
        hook_input = {
            "hook_event_name": POST_TOOL_USE,
            "session_id": session_id,
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
            [sys.executable, "-E", "-s", str(HOOK_SCRIPT)],
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
