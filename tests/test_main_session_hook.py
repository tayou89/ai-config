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
LEAD_SKILL = "session-management"
SKILL_PATH = Path("skills") / LEAD_SKILL / "SKILL.md"
ENCODING = "utf-8"
EVENT_KEY = "hook_event_name"
TOOL_NAME_KEY = "tool_name"
TOOL_INPUT_KEY = "tool_input"
HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
SWITCH_VARIABLE = "MAIN_SESSION_NOTICE"
NOTICE_TAG = "[MAIN SESSION]"
TOOL_NOTICE_TEXT = "file changes and long commands go to a session of their own"
APPROVED_EXCEPTION_TEXT = "apart from an exception its project's rules record with the user's approval"
NOT_LOADED_TEXT = f"has not loaded the {LEAD_SKILL} skill"
SIZE_TEXT = "Its context is about"
MAIN_SESSION = "local_main"
CHILD_SESSION = "local_child"
GRANDCHILD_SESSION = "local_grandchild"
OTHER_SESSION = "local_other"
ROLE_NOTICE_TAG = "[ROLE SESSION]"
ROLE_TOOL_NOTICE_TEXT = "where file changes and long commands go to a task session under it"
ROLE_TOOL_NOTICE = f"{ROLE_NOTICE_TAG} This is a role session, {ROLE_TOOL_NOTICE_TEXT} unless the tool allows neither starting nor proposing one."
SKILL_CHANGED_TEXT = "skill file was modified after this session last called the Skill tool for it"
MANAGEMENT_ROLE = "웹사이트 관리"
DESIGN_ROLE = "웹사이트 디자인"
DEVELOPMENT_ROLE = "웹사이트 개발"
AGENTS_FILE = "AGENTS.md"
CLAUDE_FILE = "CLAUDE.md"
MANAGEMENT_ITEM = f"- `{MANAGEMENT_ROLE}`: the main session."
DESIGN_ITEM = f"- `{DESIGN_ROLE}` (not main): design work, which opens short-lived sub-sessions."
DEVELOPMENT_ITEM = f"- `{DEVELOPMENT_ROLE}`: code, opened under the master."
ROLE_LIST = "\n".join(
    ["## Role sessions (user decisions, 2026-11-13)", "", MANAGEMENT_ITEM, DESIGN_ITEM, DEVELOPMENT_ITEM, ""]
)
MISPLACED_ROLE_LIST = "\n".join(["## Role sessions", "", MANAGEMENT_ITEM, "", "## Notes", "", DESIGN_ITEM, ""])
FIRST_REQUEST_TOKENS = 90_000
SKILL_LOADED_AT = "2026-11-11T09:28:44.763Z"
SKILL_LOADED_EPOCH = 1_794_389_324.763
USER_PROMPT_SUBMIT = "UserPromptSubmit"
PRE_TOOL_USE = "PreToolUse"
SESSION_START = "SessionStart"
OTHER_SIZE_TEXT = "This session's context is about"
COMPACTED_TEXT = "This session was just compacted"
MODEL = "claude-opus-5-5"
WINDOW_KEY = "autoCompactWindow"
WINDOW_VARIABLE = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
PERCENT_OVERRIDE_VARIABLE = "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"
DISABLE_VARIABLE = "DISABLE_AUTO_COMPACT"
COMPACTION_VARIABLES = (WINDOW_VARIABLE, PERCENT_OVERRIDE_VARIABLE, DISABLE_VARIABLE)
TRIAL_WINDOW = 350_000
TRIAL_THRESHOLD = 297_500
PAST_TEXT = "past the 85% context threshold"
BELOW_TEXT = "below the 85% context threshold"
WRITING_TOOLS = ("Edit", "Write", "NotebookEdit", "Monitor", "mcp__terminal__run_in_terminal")


def make_usage_record(total: int, sidechain: bool = False) -> dict[str, Any]:
    usage = {
        "input_tokens": min(total, 10),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": max(total - 10, 0),
    }

    message = {"role": "assistant", "model": MODEL, "usage": usage}

    return {"type": "assistant", "isSidechain": sidechain, "message": message}


def make_compaction_record() -> dict[str, Any]:
    return {"type": "system", "subtype": "compact_boundary", "isSidechain": False}


def make_skill_load_record(stamp: str, sidechain: bool = False) -> dict[str, Any]:
    block = {"type": "tool_use", "name": "Skill", "input": {"skill": LEAD_SKILL}}
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
        self._project = self._root / "project"
        self._records.mkdir(parents=True)
        self._claude_home.mkdir()
        subprocess.run(["git", "init", "-q", str(self._project)], check=True)

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
        self.assertIn(APPROVED_EXCEPTION_TEXT, context)
        self.assertIn(
            "about 210k, 22% of the 967k automatic compaction point its settings give, below the 85% context threshold",
            context,
        )

    def test_size_past_the_threshold_says_so(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(310_000),
        )

        self.assertIn("about 310k, 89% of the 350k automatic compaction point", self._read_turn_context())
        self.assertIn(PAST_TEXT, self._read_turn_context())

    def test_size_at_the_threshold_is_not_yet_past(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD))

        self.assertIn(BELOW_TEXT, self._read_turn_context())

    def test_size_just_over_the_threshold_is_past(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD + 1))

        self.assertIn(PAST_TEXT, self._read_turn_context())

    def test_window_variable_outranks_the_settings_files(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD + 1))
        context = self._read_context(
            MAIN_SESSION, USER_PROMPT_SUBMIT, extra_environment={WINDOW_VARIABLE: "500k"}
        )

        self.assertIn("of the 500k automatic compaction point", context)

    def test_window_variable_is_a_literal_token_count_raised_to_the_minimum(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_usage_record(90_000))
        context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, extra_environment={WINDOW_VARIABLE: "350"})

        self.assertIn("of the 100k automatic compaction point", context)

    def test_unusable_window_variable_falls_back_to_the_settings(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))
        context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, extra_environment={WINDOW_VARIABLE: "soon"})

        self.assertIn("of the 350k automatic compaction point", context)

    def test_project_settings_outrank_user_settings_and_local_outranks_project(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_settings(self._project / ".claude", {WINDOW_KEY: 400_000})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn("of the 400k automatic compaction point", self._read_turn_context())
        self._write_settings(self._project / ".claude", {WINDOW_KEY: 450_000}, name="settings.local.json")
        self.assertIn("of the 450k automatic compaction point", self._read_turn_context())

    def test_model_value_outranks_the_top_level_value_in_the_same_file(self) -> None:
        self._write_main_with_child()
        model_settings = {MODEL: {WINDOW_KEY: 300_000}}
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW, "modelSettings": model_settings})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn("of the 300k automatic compaction point", self._read_turn_context())

    def test_model_value_matches_its_long_context_spelling(self) -> None:
        self._write_main_with_child()
        model_settings = {f"{MODEL}[1m]": {WINDOW_KEY: 300_000}}
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW, "modelSettings": model_settings})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn("of the 300k automatic compaction point", self._read_turn_context())

    def test_model_value_auto_gives_the_tuned_default_over_the_top_level_value(self) -> None:
        self._write_main_with_child()
        model_settings = {MODEL: {WINDOW_KEY: "auto"}}
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW, "modelSettings": model_settings})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn("of the 967k automatic compaction point", self._read_turn_context())

    def test_higher_file_top_level_value_outranks_a_lower_file_model_value(self) -> None:
        self._write_main_with_child()
        model_settings = {MODEL: {WINDOW_KEY: 300_000}}
        self._write_settings(self._claude_home, {"modelSettings": model_settings})
        self._write_settings(self._project / ".claude", {WINDOW_KEY: 400_000})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn("of the 400k automatic compaction point", self._read_turn_context())

    def test_values_that_are_not_token_counts_are_skipped(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._project / ".claude", {WINDOW_KEY: "350k"})
        self._write_settings(self._claude_home, {WINDOW_KEY: True})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

        self.assertIn("of the 967k automatic compaction point", self._read_turn_context())

    def test_window_is_kept_between_the_minimum_and_the_context_window(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))
        cases = [(50_000, "100k"), (2_000_000, "1,000k")]

        for window, shown in cases:
            with self.subTest(window=window):
                self._write_settings(self._claude_home, {WINDOW_KEY: window})

                self.assertIn(f"of the {shown} automatic compaction point", self._read_turn_context())

    def test_percent_override_lowers_the_point(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: 400_000})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))
        context = self._read_context(
            MAIN_SESSION, USER_PROMPT_SUBMIT, extra_environment={PERCENT_OVERRIDE_VARIABLE: "50"}
        )

        self.assertIn("of the 200k automatic compaction point", context)

    def test_unusable_variable_values_are_skipped(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))
        cases = [{WINDOW_VARIABLE: "1e999"}, {WINDOW_VARIABLE: "inf"}, {PERCENT_OVERRIDE_VARIABLE: "²"}]

        for environment in cases:
            with self.subTest(environment=environment):
                context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, extra_environment=environment)

                self.assertIn("of the 967k automatic compaction point", context)

    def test_lowered_point_below_the_minimum_still_warns_other_sessions(self) -> None:
        self._write_settings(self._claude_home, {WINDOW_KEY: 100_000})
        self._write_transcript(make_usage_record(60_000))
        context = self._read_context(None, USER_PROMPT_SUBMIT, extra_environment={PERCENT_OVERRIDE_VARIABLE: "50"})

        self.assertTrue(context.startswith(OTHER_SIZE_TEXT))
        self.assertIn("of the 50k automatic compaction point", context)

    def test_compaction_turned_off_is_said_instead_of_a_threshold(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))
        cases: list[tuple[dict[str, Any], dict[str, str]]] = [
            ({"autoCompactEnabled": False}, {}),
            ({}, {DISABLE_VARIABLE: "1"}),
        ]

        for settings, environment in cases:
            with self.subTest(settings=settings, environment=environment):
                self._write_settings(self._claude_home, settings)
                context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, extra_environment=environment)

                self.assertIn("about 90k, and its settings turn automatic compaction off", context)

    def test_worktree_reads_the_original_checkout_settings(self) -> None:
        original = self._root / "original"
        worktree = self._root / "worktree"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.com"]
        subprocess.run(["git", "init", "-q", str(original)], check=True)
        subprocess.run(
            ["git", "-C", str(original), *identity, "commit", "-q", "--allow-empty", "-m", "start"],
            check=True,
        )
        subprocess.run(["git", "-C", str(original), "worktree", "add", "-q", str(worktree)], check=True)
        self._write_main_with_child()
        self._write_settings(original / ".claude", {WINDOW_KEY: 400_000})
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))
        context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, cwd=str(worktree))

        self.assertIn("of the 400k automatic compaction point", context)

    def test_size_after_the_last_compaction_counts_and_none_before_a_request(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(340_000), make_compaction_record())

        self.assertNotIn(SIZE_TEXT, self._read_turn_context())
        self._write_transcript(
            make_usage_record(340_000),
            make_compaction_record(),
            make_usage_record(120_000),
            make_usage_record(330_000),
            make_compaction_record(),
            make_usage_record(115_000),
        )
        self.assertIn("about 115k, 33% of the 350k", self._read_turn_context())

    def test_quoted_boundary_text_in_a_message_is_not_a_compaction(self) -> None:
        self._write_main_with_child()
        quoted = {"type": "user", "message": {"content": 'grep shows "subtype":"compact_boundary"'}}
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS), quoted)

        self.assertIn("about 90k,", self._read_turn_context())

    def test_size_is_found_before_a_long_tail(self) -> None:
        self._write_main_with_child()
        filler = {"type": "user", "message": {"content": "x" * 1_500_000}}
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS), filler)

        self.assertIn("about 90k,", self._read_turn_context())

    def test_session_outside_the_app_past_the_threshold_gets_the_context_sentence(self) -> None:
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD + 1))
        context = self._read_context(None, USER_PROMPT_SUBMIT)

        self.assertTrue(context.startswith(f"{OTHER_SIZE_TEXT} 298k"))
        self.assertIn(PAST_TEXT, context)

    def test_task_session_past_the_threshold_gets_the_context_sentence(self) -> None:
        self._write_main_with_child()
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD + 1))

        self.assertTrue(self._read_context(CHILD_SESSION, USER_PROMPT_SUBMIT).startswith(OTHER_SIZE_TEXT))

    def test_session_outside_the_app_below_the_threshold_gets_nothing(self) -> None:
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD))

        self.assertEqual(self._run_hook(None, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_subagent_past_the_threshold_gets_nothing(self) -> None:
        self._write_settings(self._claude_home, {WINDOW_KEY: TRIAL_WINDOW})
        self._write_transcript(make_usage_record(TRIAL_THRESHOLD + 1))

        self.assertEqual(self._run_hook(None, {EVENT_KEY: USER_PROMPT_SUBMIT, "agent_id": "agent"}), "")

    def test_lead_session_is_told_after_a_compaction(self) -> None:
        self._write_main_with_child()
        context = self._read_context(MAIN_SESSION, SESSION_START, source="compact")

        self.assertTrue(context.startswith(NOTICE_TAG))
        self.assertIn(COMPACTED_TEXT, context)

    def test_other_session_starts_get_nothing(self) -> None:
        self._write_main_with_child()
        cases = [(MAIN_SESSION, "startup"), (CHILD_SESSION, "compact"), (None, "compact")]

        for session_id, source in cases:
            with self.subTest(session_id=session_id, source=source):
                self.assertEqual(self._run_hook(session_id, {EVENT_KEY: SESSION_START, "source": source}), "")

    def test_sidechain_requests_do_not_count(self) -> None:
        self._write_main_with_child()
        self._write_transcript(
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(FIRST_REQUEST_TOKENS + 50_000),
            make_usage_record(900_000, sidechain=True),
        )

        self.assertIn("about 140k,", self._read_turn_context())

    def test_records_without_input_tokens_do_not_count(self) -> None:
        self._write_main_with_child()
        self._write_transcript(
            make_usage_record(0),
            make_usage_record(FIRST_REQUEST_TOKENS),
            make_usage_record(FIRST_REQUEST_TOKENS + 30_000),
        )

        self.assertIn("about 120k,", self._read_turn_context())

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

    def test_role_marked_not_main_is_told_it_is_a_role_session(self) -> None:
        self._write_record(MAIN_SESSION, spawned_from=OTHER_SESSION, detached=True, title=DESIGN_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)
        self._write_rules(AGENTS_FILE, ROLE_LIST)
        turn_context = self._read_turn_context()
        tool_context = self._read_context(MAIN_SESSION, PRE_TOOL_USE, tool_name="Edit", tool_input={})

        self.assertTrue(turn_context.startswith(ROLE_NOTICE_TAG))
        self.assertIn(f"`{DESIGN_ROLE}`", turn_context)
        self.assertNotIn(NOTICE_TAG, turn_context)
        self.assertIn(ROLE_TOOL_NOTICE_TEXT, tool_context)

    def test_role_session_under_a_master_gets_role_size_and_skill_sentences(self) -> None:
        self._write_record(MAIN_SESSION, title=MANAGEMENT_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION, title=DEVELOPMENT_ROLE)
        self._write_rules(AGENTS_FILE, ROLE_LIST)
        self._write_transcript(make_skill_load_record(SKILL_LOADED_AT), make_usage_record(812_345))
        self._write_skill(SKILL_LOADED_EPOCH + 3_600)
        context = self._read_context(CHILD_SESSION, USER_PROMPT_SUBMIT)

        self.assertTrue(context.startswith(ROLE_NOTICE_TAG))
        self.assertIn("belong to a task session under it, other sessions' requests included", context)
        self.assertIn("about 812k, 84% of the 967k automatic compaction point its settings give, below", context)
        self.assertIn(SKILL_CHANGED_TEXT, context)

    def test_role_session_under_a_master_gets_the_tool_notice(self) -> None:
        self._write_record(MAIN_SESSION, title=MANAGEMENT_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION, title=DEVELOPMENT_ROLE)
        self._write_rules(AGENTS_FILE, ROLE_LIST)
        context = self._read_context(CHILD_SESSION, PRE_TOOL_USE, tool_name="Write", tool_input={})

        self.assertEqual(context, ROLE_TOOL_NOTICE)

    def test_role_session_foreground_command_gets_nothing(self) -> None:
        self._write_master_with_development_role()
        hook_input = {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Bash", TOOL_INPUT_KEY: {"command": "git status"}}

        self.assertEqual(self._run_hook(CHILD_SESSION, hook_input), "")

    def test_task_session_outside_the_role_list_gets_nothing(self) -> None:
        self._write_master_with_development_role()
        self._write_record(GRANDCHILD_SESSION, spawned_from=CHILD_SESSION, title="결제 화면 고치기")

        self.assertEqual(self._run_hook(GRANDCHILD_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_task_session_with_a_role_title_gets_nothing(self) -> None:
        self._write_master_with_development_role()
        self._write_record(GRANDCHILD_SESSION, spawned_from=CHILD_SESSION, title=DEVELOPMENT_ROLE)
        hook_inputs: list[dict[str, Any]] = [
            {EVENT_KEY: USER_PROMPT_SUBMIT},
            {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Edit", TOOL_INPUT_KEY: {}},
        ]

        for hook_input in hook_inputs:
            with self.subTest(event=hook_input[EVENT_KEY]):
                self.assertEqual(self._run_hook(GRANDCHILD_SESSION, hook_input), "")

    def test_master_before_its_first_session_gets_nothing(self) -> None:
        self._write_record(MAIN_SESSION, title=MANAGEMENT_ROLE)
        self._write_rules(AGENTS_FILE, ROLE_LIST)

        self.assertEqual(self._run_hook(MAIN_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_listed_session_whose_parent_record_is_missing_gets_nothing(self) -> None:
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION, title=DEVELOPMENT_ROLE)
        self._write_rules(AGENTS_FILE, ROLE_LIST)

        self.assertEqual(self._run_hook(CHILD_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_role_title_outside_the_role_list_does_not_count(self) -> None:
        self._write_record(MAIN_SESSION, title=MANAGEMENT_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION, title=DESIGN_ROLE)
        self._write_rules(AGENTS_FILE, MISPLACED_ROLE_LIST)

        self.assertEqual(self._run_hook(CHILD_SESSION, {EVENT_KEY: USER_PROMPT_SUBMIT}), "")

    def test_subagent_of_a_role_session_gets_nothing(self) -> None:
        self._write_master_with_development_role()
        hook_input = {EVENT_KEY: PRE_TOOL_USE, TOOL_NAME_KEY: "Edit", TOOL_INPUT_KEY: {}, "agent_id": "agent-1"}

        self.assertEqual(self._run_hook(CHILD_SESSION, hook_input), "")

    def test_other_role_in_the_same_list_is_still_main(self) -> None:
        self._write_record(MAIN_SESSION, title=MANAGEMENT_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)
        self._write_rules(AGENTS_FILE, ROLE_LIST)

        self.assertIn(NOTICE_TAG, self._read_turn_context())

    def test_not_main_mark_outside_the_role_list_does_not_count(self) -> None:
        self._write_record(MAIN_SESSION, title=DESIGN_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)
        self._write_rules(AGENTS_FILE, MISPLACED_ROLE_LIST)

        self.assertIn(NOTICE_TAG, self._read_turn_context())

    def test_role_list_is_read_from_the_project_root_in_any_rules_file(self) -> None:
        folder = self._project / "src" / "pages"

        self._write_record(MAIN_SESSION, title=DESIGN_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)
        self._write_rules(CLAUDE_FILE, f"# Project\n\n#### Role sessions\n- `{DESIGN_ROLE}` (Not Main): design.\n")
        folder.mkdir(parents=True)
        context = self._read_context(MAIN_SESSION, USER_PROMPT_SUBMIT, cwd=str(folder))

        self.assertTrue(context.startswith(ROLE_NOTICE_TAG))

    def test_session_without_a_title_is_still_main(self) -> None:
        self._write_main_with_child()
        self._write_rules(AGENTS_FILE, ROLE_LIST)

        self.assertIn(NOTICE_TAG, self._read_turn_context())

    def test_session_outside_the_app_gets_nothing(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_usage_record(FIRST_REQUEST_TOKENS))

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

        self.assertIn(SKILL_CHANGED_TEXT, self._read_turn_context())

    def test_skill_loaded_after_install_adds_no_skill_sentence(self) -> None:
        self._write_main_with_child()
        self._write_transcript(make_skill_load_record(SKILL_LOADED_AT))
        self._write_skill(SKILL_LOADED_EPOCH - 3_600)
        context = self._read_turn_context()

        self.assertNotIn(SKILL_CHANGED_TEXT, context)
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

        self.assertIn("about 130k,", self._read_turn_context())

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

    def test_empty_input_shows_a_hook_error_and_lets_the_call_through(self) -> None:
        result = self._run_process(MAIN_SESSION, "")

        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("main-session-hook:", result.stderr)

    def _write_main_with_child(self) -> None:
        self._write_record(MAIN_SESSION)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION)

    def _write_master_with_development_role(self) -> None:
        self._write_record(MAIN_SESSION, title=MANAGEMENT_ROLE)
        self._write_record(CHILD_SESSION, spawned_from=MAIN_SESSION, title=DEVELOPMENT_ROLE)
        self._write_rules(AGENTS_FILE, ROLE_LIST)

    def _write_record(
        self,
        session_id: str,
        spawned_from: str | None = None,
        detached: bool = False,
        archived: bool = False,
        title: str | None = None,
    ) -> None:
        record: dict[str, Any] = {"sessionId": session_id, "isArchived": archived}

        if spawned_from is not None:
            record["spawnedFrom"] = {"sessionId": spawned_from, "sideSession": True}
        if detached:
            record["lineageDetached"] = True
        if title is not None:
            record["title"] = title
        (self._records / f"{session_id}.json").write_text(json.dumps(record), encoding=ENCODING)

    def _write_rules(self, name: str, text: str) -> None:
        (self._project / name).write_text(text, encoding=ENCODING)

    def _write_settings(self, folder: Path, settings: dict[str, Any], name: str = "settings.json") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(settings), encoding=ENCODING)

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

    def _read_context(
        self,
        session_id: str | None,
        event: str,
        extra_environment: dict[str, str] | None = None,
        **fields: Any,
    ) -> str:
        output = self._run_hook(session_id, {EVENT_KEY: event, **fields}, extra_environment)
        hook_output = json.loads(output)["hookSpecificOutput"]

        self.assertEqual(hook_output["hookEventName"], event)
        return str(hook_output["additionalContext"])

    def _run_hook(
        self,
        session_id: str | None,
        hook_input: dict[str, Any],
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        full_input = {"transcript_path": str(self._transcript), "cwd": str(self._project), **hook_input}
        result = self._run_process(session_id, json.dumps(full_input), extra_environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _run_process(
        self,
        session_id: str | None,
        stdin_text: str,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        removed = (HOST_SESSION_VARIABLE, SWITCH_VARIABLE, *COMPACTION_VARIABLES)
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
