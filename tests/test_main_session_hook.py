#!/usr/bin/env python3
"""Tests for scripts/main-session-hook.py.

Each test runs the hook as Claude Code would, with the hook input on stdin
and the home and Claude folders pointed at a temporary folder holding fake
app session records, a fake transcript, and a fake installed skill, so
nothing on this computer is read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "main-session-hook.py"
RECORDS_PATH = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions" / "account" / "org"
SKILL_PATH = Path("skills") / "decisions-and-collaboration" / "SKILL.md"
ENCODING = "utf-8"
EVENT_KEY = "hook_event_name"
TOOL_NAME_KEY = "tool_name"
TOOL_INPUT_KEY = "tool_input"
HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
SWITCH_VARIABLE = "MAIN_SESSION_NOTICE"
NOTICE_TAG = "[MAIN SESSION]"
TOOL_NOTICE_TEXT = "file changes and long commands go to a session of their own"
NOT_LOADED_TEXT = "has not loaded the decisions-and-collaboration skill"
SIZE_TEXT = "conversation part"
MAIN_SESSION = "local_main"
CHILD_SESSION = "local_child"
GRANDCHILD_SESSION = "local_grandchild"
OTHER_SESSION = "local_other"
FIRST_REQUEST_TOKENS = 90_000
SKILL_LOADED_AT = "2026-11-11T09:28:44.763Z"
SKILL_LOADED_EPOCH = 1_794_389_324.763
USER_PROMPT_SUBMIT = "UserPromptSubmit"
PRE_TOOL_USE = "PreToolUse"
WRITING_TOOLS = ("Edit", "Write", "NotebookEdit", "Monitor", "mcp__terminal__run_in_terminal")


def make_usage_record(total: int, sidechain: bool = False) -> dict[str, Any]:
    usage = {
        "input_tokens": min(total, 10),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": max(total - 10, 0),
    }

    return {"type": "assistant", "isSidechain": sidechain, "message": {"role": "assistant", "usage": usage}}


def make_skill_load_record(stamp: str, sidechain: bool = False) -> dict[str, Any]:
    block = {"type": "tool_use", "name": "Skill", "input": {"skill": "decisions-and-collaboration"}}
    message = {"role": "assistant", "content": [block]}

    return {"type": "assistant", "isSidechain": sidechain, "timestamp": stamp, "message": message}


class MainSessionHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name).resolve()
        self._home = self._root / "home"
        self._claude_home = self._root / "claude"
        self._records = self._home / RECORDS_PATH
        self._transcript = self._root / "transcript.jsonl"
        self._records.mkdir(parents=True)
        self._claude_home.mkdir()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_top_level_session_that_started_one_is_told_its_role_and_size(self) -> None:
        self._write_main_with_child()
        self._write_transcript(
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(FIRST_REQUEST_TOKENS + 120_000),
        )
        context = self._read_turn_context()

        self.assertTrue(context.startswith(NOTICE_TAG))
        self.assertIn("started 1 other session and", context)
        self.assertIn("about 120k of the 200k replacement threshold", context)

    def test_size_past_the_threshold_says_so(self) -> None:
        self._write_main_with_child()
        self._write_transcript(
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(FIRST_REQUEST_TOKENS + 266_402),
        )

        self.assertIn("about 266k tokens, past the 200k replacement threshold", self._read_turn_context())

    def test_sidechain_requests_do_not_count(self) -> None:
        self._write_main_with_child()
        self._write_transcript(
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(FIRST_REQUEST_TOKENS + 50_000),
            make_usage_record(900_000, sidechain=True),
        )

        self.assertIn("about 50k of the", self._read_turn_context())

    def test_records_without_input_tokens_do_not_count(self) -> None:
        self._write_main_with_child()
        self._write_transcript(
            make_usage_record(0),
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(FIRST_REQUEST_TOKENS + 30_000),
        )

        self.assertIn("about 30k of the", self._read_turn_context())

    def test_counts_every_session_it_started_including_archived(self) -> None:
        self._write_record(MAIN_SESSION)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)
        self._write_record(OTHER_SESSION, spawned_from=MAIN_SESSION, archived=True)

        self.assertIn("started 2 other sessions", self._read_turn_context())

    def test_session_under_another_is_not_main_even_with_its_own_sessions(self) -> None:
        self._write_main_with_child()
        self._write_record(GRANDCHILD_SESSION, spawned_from=CHILD_SESSION)

        self.assertEqual(self._run_hook(CHILD_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_detached_session_with_sessions_is_main(self) -> None:
        self._write_record(CHILD_SESSION, spawned_from=OTHER_SESSION, detached=True)
        self._write_record(GRANDCHILD_SESSION, spawned_from=CHILD_SESSION)

        self.assertIn(NOTICE_TAG, self._read_context(CHILD_SESSION, USER_PROMPT_SUBMIT))

    def test_top_level_session_without_sessions_is_not_main(self) -> None:
        self._write_record(MAIN_SESSION)
        self._write_record(OTHER_SESSION)

        self.assertEqual(self._run_hook(MAIN_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_session_outside_the_app_gets_nothing(self) -> None:
        self._write_main_with_child()

        self.assertEqual(self._run_hook(None, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_session_without_its_own_record_gets_nothing(self) -> None:
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)

        self.assertEqual(self._run_hook(MAIN_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_skill_never_loaded_is_named(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn(NOT_LOADED_TEXT, self._read_turn_context())

    def test_skill_load_in_a_subagent_does_not_count(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_skill_load_record(SKILL_LOADED_AT, sidechain=True))

        self.assertIn(NOT_LOADED_TEXT, self._read_turn_context())

    def test_skill_installed_after_the_load_is_named(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_skill_load_record(SKILL_LOADED_AT))
        self._write_skill(SKILL_LOADED_EPOCH + 3_600)

        self.assertIn("skill changed after this session last loaded it", self._read_turn_context())

    def test_skill_loaded_after_install_adds_no_skill_sentence(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_skill_load_record(SKILL_LOADED_AT))
        self._write_skill(SKILL_LOADED_EPOCH - 3_600)
        context = self._read_turn_context()

        self.assertNotIn("skill changed", context)
        self.assertNotIn(NOT_LOADED_TEXT, context)

    def test_unreadable_transcript_gives_only_the_role(self) -> None:
        self._write_main_with_child()
        context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, transcript_path=str(self._root / "missing"))

        self.assertIn("started 1 other session", context)
        self.assertNotIn(SIZE_TEXT, context)
        self.assertNotIn(NOT_LOADED_TEXT, context)

    def test_damaged_lines_are_skipped(self) -> None:
        self._write_main_with_child()
        (self._records / "local_broken.json").write_text("{not json", encoding=ENCODING)
        self._write_transcript(
            make_usage_record(FIRST_REQUEST_TOKENS), make_usage_record(FIRST_REQUEST_TOKENS + 40_000)
        )
        with self._transcript.open("a", encoding=ENCODING) as stream:
            stream.write('{"type":"assistant", broken\n')

        self.assertIn("about 40k of the", self._read_turn_context())

    def test_file_changes_and_long_commands_in_a_main_session_get_the_tool_notice(self) -> None:
        self._write_main_with_child()
        for tool_name in WRITING_TOOLS:
            with self.subTest(tool=tool_name):
                context = self._read_context(MAIN_SESSION, PRE_TOOL_USE, tool_name=tool_name, tool_input={})

                self.assertIn(TOOL_NOTICE_TEXT, context)

    def test_background_command_gets_the_tool_notice(self) -> None:
        self._write_main_with_child()
        tool_input = {"command": "sleep 600", "run_in_background": True}
        context = self._read_context(MAIN_SESSION, PRE_TOOL_USE, tool_name="Bash", tool_input=tool_input)

        self.assertIn(TOOL_NOTICE_TEXT, context)

    def test_foreground_commands_and_odd_inputs_get_nothing(self) -> None:
        self._write_main_with_child()
        tool_inputs: list[Any] = [
            {"command": "git status"},
            {"command": "git status", "run_in_background": False},
            ["not", "a", "dict"],
        ]

        for tool_input in tool_inputs:
            with self.subTest(tool_input=tool_input):
                hook_input = {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Bash", TOOL_INPUT_KEY: tool_input}

                self.assertEqual(self._run_hook(MAIN_SESSION, hook_input), "")

    def test_subagent_of_a_main_session_gets_nothing(self) -> None:
        self._write_main_with_child()
        hook_input = {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Edit", TOOL_INPUT_KEY: {}, "agent_id": "agent-1"}

        self.assertEqual(self._run_hook(MAIN_SESSION, hook_input), "")

    def test_edit_in_a_session_under_the_main_gets_nothing(self) -> None:
        self._write_main_with_child()
        hook_input = {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Write", TOOL_INPUT_KEY: {}}

        self.assertEqual(self._run_hook(CHILD_SESSION, hook_input), "")

    def test_switch_off_adds_nothing(self) -> None:
        self._write_main_with_child()
        hook_inputs: list[dict[str, Any]] = [
            {EVENT_KEY: USER_PROMPT_SUBMIT},
            {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Edit", TOOL_INPUT_KEY: {}},
        ]

        for hook_input in hook_inputs:
            with self.subTest(event=hook_input[EVENT_KEY]):
                output = self._run_hook(MAIN_SESSION, hook_input, extra_environment={SWITCH_VARIABLE: "off"})

                self.assertEqual(output, "")

    def test_empty_input_exits_cleanly(self) -> None:
        result = self._run_process(MAIN_SESSION, "")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def _write_main_with_child(self) -> None:
        self._write_record(MAIN_SESSION)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)

    def _write_record(
        self,
        session_id: str,
        spawned_from: str | None = None,
        detached: bool = False,
        archived: bool = False,
    ) -> None:
        record: dict[str, Any] = {"sessionId": session_id, "isArchived": archived}

        if spawned_from is not None:
            record["spawnedFrom"] = {"sessionId": spawned_from, "sideSession": True}
        if detached:
            record["lineageDetached"] = True
        (self._records / f"{session_id}.json").write_text(json.dumps(record), encoding=ENCODING)

    def _write_transcript(self, *records: dict[str, Any]) -> None:
        lines = [json.dumps(record, separators=(",", ":")) for record in records]

        self._transcript.write_text("\n".join(lines) + "\n", encoding=ENCODING)

    def _write_skill(self, modified_at: float) -> None:
        skill = self._claude_home / SKILL_PATH

        skill.parent.mkdir(parents=True)
        skill.write_text("# skill\n", encoding=ENCODING)
        os.utime(skill, (time.time(), modified_at))

    def _read_turn_context(self) -> str:
        return self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT)

    def _read_context(self, session_id: str, event: str, **fields: Any) -> str:
        output = self._run_hook(session_id, {EVENT_KEY: event, **fields})
        hook_output = json.loads(output)["hookSpecificOutput"]

        self.assertEqual(hook_output["hookEventName"], event)
        return str(hook_output["additionalContext"])

    def _run_hook(
        self,
        session_id: str | None,
        hook_input: dict[str, Any],
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        full_input = {"transcript_path": str(self._transcript), **hook_input}
        result = self._run_process(session_id, json.dumps(full_input), extra_environment)

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

        environment.update({"HOME": str(self._home), "CLAUDE_CONFIG_DIR": str(self._claude_home)})
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
