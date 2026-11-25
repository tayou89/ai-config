#!/usr/bin/env python3
"""Tests for scripts/hook_failure.py and for how every hook that runs before an action fails.

The registration tests read settings.json, so a new PreToolUse hook cannot be
added without a failure policy its entry agrees with. The fault tests break
each hook on purpose: input it cannot read, an error inside its judgment,
and a missing helper.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
SETTINGS_FILE = REPOSITORY_ROOT / "settings.json"
PRE_TOOL_USE = "PreToolUse"
SCRIPT_COMMAND = re.compile(r'^python3 "\$HOME/\.claude/scripts/([a-z_-]+\.py)"$')
BLOCK = "block"
CONTINUE = "continue"
# The Git role moves these to run_hook with its decision on how they fail (guard-fail-open report, 2026-11-24);
# until then they are excused from both the policy and a stated onFailure, and its task session removes them here.
GIT_ROLE_LATER = {
    "worktree-guard-hook.py": "Git role, guard failure task (local_cd713324)",
    "shell-guard-hook.py": "Git role, guard failure task (local_cd713324)",
    "session-writes-hook.py": "Git role, guard failure task (local_cd713324)",
}
TOOL_CALL = {"hook_event_name": PRE_TOOL_USE, "tool_name": "Bash", "tool_input": {"command": "true"}}
UNREADABLE_INPUTS = (
    "",
    "not json",
    "[]",
    "{}",
    json.dumps({"hook_event_name": ""}),
    json.dumps({"hook_event_name": PRE_TOOL_USE, "tool_name": "Bash"}),
    json.dumps({"hook_event_name": PRE_TOOL_USE, "tool_name": "", "tool_input": {}}),
)
# The function each hook's judgment calls, or the callback main hands to run_hook, that a fault test breaks;
# every adopted hook needs an entry, so a new one cannot skip the test.
JUDGMENT_PARTS = {
    "back-reference-hook.py": "_make_output",
    "main-session-hook.py": "_make_hook_output",
    "notes-place-hook.py": "_find_notes_place_reason",
    "plain-words-hook.py": "_make_output",
    "plan-reminder-hook.py": "_is_plan_file",
    "recommendation-change-hook.py": "_make_output",
    "reply-language-hook.py": "_make_output",
    "session-notes-hook.py": "_copy_app_notes",
    "skill-gate-hook.py": "handle_event",
    "start-session-hook.py": "make_output",
    "sub-session-question-hook.py": "_make_hook_output",
}
BACKGROUND_KEYS = ("async", "asyncRewake")


def load_settings() -> dict[str, Any]:
    settings: dict[str, Any] = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))

    return settings


def list_pre_tool_use_hooks() -> list[dict[str, Any]]:
    return [hook for entry in load_settings()["hooks"][PRE_TOOL_USE] for hook in entry["hooks"]]


def read_script_name(hook: dict[str, Any]) -> str:
    match = SCRIPT_COMMAND.match(hook["command"])

    if match:
        return match.group(1)
    else:
        raise AssertionError(f"a PreToolUse entry runs a command this test cannot read: {hook['command']}")


def list_adopted_scripts() -> list[str]:
    names = {read_script_name(hook) for hook in list_pre_tool_use_hooks()}

    return sorted(name for name in names if name not in GIT_ROLE_LATER)


def load_script(name: str) -> ModuleType:
    """Load a script by path, since most hook names are not importable; dataclasses need it in sys.modules first."""
    module_name = name.removesuffix(".py").replace("-", "_")
    specification = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIRECTORY / name)

    if str(SCRIPTS_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIRECTORY))

    if specification is not None and specification.loader is not None:
        module = importlib.util.module_from_spec(specification)

        sys.modules[module_name] = module
        specification.loader.exec_module(module)
        return module
    else:
        raise AssertionError(f"cannot load {name}")


hook_failure = load_script("hook_failure.py")


def run_in_process(make_output: Any, policy: Any, stdin: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch("sys.stdin", io.StringIO(stdin)),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        code = hook_failure.run_hook(make_output, policy)
    return code, stdout.getvalue(), stderr.getvalue()


def raise_error(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    raise RuntimeError("broken on purpose")


class RunHookTest(unittest.TestCase):
    deny_policy = hook_failure.FailurePolicy.blocking(
        "test-hook", hook_failure.Decision.DENY, "could not check ({error})"
    )
    ask_policy = hook_failure.FailurePolicy.blocking(
        "test-hook", hook_failure.Decision.ASK, "could not check ({error})"
    )
    notice_policy = hook_failure.FailurePolicy.notice("test-hook")

    def test_the_judgment_is_printed_and_nothing_is_printed_for_no_answer(self) -> None:
        answer = {"hookSpecificOutput": {"hookEventName": PRE_TOOL_USE, "additionalContext": "note"}}

        self.assertEqual(
            run_in_process(lambda hook_input: answer, self.deny_policy, json.dumps(TOOL_CALL))[:2],
            (0, json.dumps(answer) + "\n"),
        )
        self.assertEqual(run_in_process(lambda hook_input: None, self.deny_policy, json.dumps(TOOL_CALL)), (0, "", ""))

    def test_input_that_is_not_a_hook_input_exits_1_whatever_the_policy(self) -> None:
        for policy in (self.deny_policy, self.notice_policy):
            for stdin in UNREADABLE_INPUTS:
                with self.subTest(policy=policy.hook_name, stdin=stdin):
                    code, stdout, stderr = run_in_process(lambda hook_input: None, policy, stdin)

                    self.assertEqual((code, stdout), (1, ""))
                    self.assertIn("test-hook: ", stderr)

    def test_a_blocking_policy_answers_its_own_error_on_a_tool_call(self) -> None:
        for policy, decision in ((self.deny_policy, "deny"), (self.ask_policy, "ask")):
            with self.subTest(decision=decision):
                code, stdout, _ = run_in_process(raise_error, policy, json.dumps(TOOL_CALL))
                output = json.loads(stdout)["hookSpecificOutput"]

                self.assertEqual(code, 0)
                self.assertEqual(output["permissionDecision"], decision)
                self.assertEqual(
                    output["permissionDecisionReason"], "could not check (RuntimeError: broken on purpose)"
                )

    def test_an_error_on_another_event_exits_1_for_a_blocking_policy(self) -> None:
        code, stdout, stderr = run_in_process(raise_error, self.deny_policy, json.dumps({"hook_event_name": "Stop"}))

        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("RuntimeError", stderr)

    def test_a_notice_policy_exits_1_so_the_call_goes_ahead_with_a_notice(self) -> None:
        code, stdout, stderr = run_in_process(raise_error, self.notice_policy, json.dumps(TOOL_CALL))

        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(stderr, "test-hook: RuntimeError: broken on purpose\n")


class JudgeChecksTest(unittest.TestCase):
    def test_a_failed_check_keeps_the_others_findings_and_a_denial_outranks_a_question(self) -> None:
        checks = [
            hook_failure.Check(raise_error_now, hook_failure.Decision.ASK, "production check failed: {error}"),
            hook_failure.Check(
                lambda: [deny("commit refused")], hook_failure.Decision.DENY, "commit check failed: {error}"
            ),
        ]
        output = hook_failure.judge_checks(checks)

        self.assertEqual(read_decision(output), ("deny", "commit refused"))

    def test_a_failed_check_alone_gives_its_own_answer(self) -> None:
        for decision in (hook_failure.Decision.ASK, hook_failure.Decision.DENY):
            with self.subTest(decision=decision):
                output = hook_failure.judge_checks([hook_failure.Check(raise_error_now, decision, "failed: {error}")])

                self.assertEqual(read_decision(output), (decision.value, "failed: RuntimeError: broken on purpose"))

    def test_questions_join_once_each_and_no_finding_is_no_answer(self) -> None:
        question = hook_failure.Finding(hook_failure.Decision.ASK, "asks")
        checks = [hook_failure.Check(lambda: [question, question], hook_failure.Decision.DENY, "x {error}")] * 2

        self.assertEqual(read_decision(hook_failure.judge_checks(checks)), ("ask", "asks"))
        self.assertIsNone(
            hook_failure.judge_checks([hook_failure.Check(list, hook_failure.Decision.DENY, "x {error}")])
        )


class RegistrationTest(unittest.TestCase):
    def test_every_pre_tool_use_entry_runs_a_script_this_test_knows(self) -> None:
        for hook in list_pre_tool_use_hooks():
            with self.subTest(command=hook["command"]):
                self.assertTrue((SCRIPTS_DIRECTORY / read_script_name(hook)).is_file())

    def test_every_entry_states_its_failure_handling_and_matches_its_hook(self) -> None:
        for hook in list_pre_tool_use_hooks():
            name = read_script_name(hook)

            with self.subTest(script=name):
                if name in GIT_ROLE_LATER:
                    self.assertIn(hook.get("onFailure", CONTINUE), (BLOCK, CONTINUE))
                else:
                    policy = load_script(name).FAILURE_POLICY

                    self.assertEqual(hook.get("onFailure"), BLOCK if policy.is_blocking() else CONTINUE)

    def test_the_shell_guard_blocks_on_failure_for_its_production_check(self) -> None:
        hooks = [hook for hook in list_pre_tool_use_hooks() if read_script_name(hook) == "shell-guard-hook.py"]

        self.assertEqual([hook.get("onFailure") for hook in hooks], [BLOCK])

    def test_blocking_entries_do_not_run_in_the_background_where_on_failure_has_no_effect(self) -> None:
        for hook in list_pre_tool_use_hooks():
            with self.subTest(command=hook["command"]):
                self.assertFalse(hook.get("onFailure") == BLOCK and any(hook.get(key) for key in BACKGROUND_KEYS))

    def test_entries_for_other_events_never_block_on_failure(self) -> None:
        for event, entries in load_settings()["hooks"].items():
            for hook in (hook for entry in entries for hook in entry["hooks"]):
                with self.subTest(event=event, command=hook["command"]):
                    self.assertTrue(event == PRE_TOOL_USE or hook.get("onFailure") != BLOCK)


class HookFaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._home = Path(self._directory.name).resolve()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_each_hook_hands_its_own_policy_to_run_hook(self) -> None:
        for name in list_adopted_scripts():
            with self.subTest(script=name):
                module = load_script(name)
                recorder = mock.Mock(return_value=0)

                with mock.patch.object(module, "run_hook", recorder), mock.patch("sys.argv", [name]):
                    module.main()
                self.assertIs(recorder.call_args.args[1], module.FAILURE_POLICY)

    def test_each_hook_answers_an_error_inside_its_judgment_through_its_own_main(self) -> None:
        self.assertEqual(sorted(JUDGMENT_PARTS), list_adopted_scripts())
        for name, part in JUDGMENT_PARTS.items():
            with self.subTest(script=name):
                module = load_script(name)
                policy = module.FAILURE_POLICY
                code, stdout, stderr = self._run_main_with_broken_part(module, part, name)

                if policy.is_blocking():
                    self.assertEqual((code, read_decision(json.loads(stdout))[0]), (0, policy.on_error.value))
                else:
                    self.assertEqual((code, stdout), (1, ""))
                self.assertIn(f"{policy.hook_name}: RuntimeError", stderr)

    def test_each_hook_script_exits_1_on_input_it_cannot_read(self) -> None:
        for name in list_adopted_scripts():
            for stdin in UNREADABLE_INPUTS:
                with self.subTest(script=name, stdin=stdin):
                    result = self._run_script(SCRIPTS_DIRECTORY / name, stdin)

                    self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)

    def test_each_hook_script_exits_1_when_its_helper_is_missing(self) -> None:
        alone = self._home / "alone"

        alone.mkdir()
        for name in list_adopted_scripts():
            with self.subTest(script=name):
                shutil.copy(SCRIPTS_DIRECTORY / name, alone / name)
                result = self._run_script(alone / name, json.dumps(TOOL_CALL))

                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn("ModuleNotFoundError", result.stderr)

    def test_a_transcript_that_cannot_be_read_is_an_error_and_a_missing_one_is_not(self) -> None:
        unreadable = {"hook_event_name": "PostToolUse", "session_id": "s", "transcript_path": str(self._home)}
        missing = {**unreadable, "transcript_path": str(self._home / "not-written.jsonl")}
        script = SCRIPTS_DIRECTORY / "reply-language-hook.py"
        failed = self._run_script(script, json.dumps(unreadable))
        quiet = self._run_script(script, json.dumps(missing))

        self.assertEqual((failed.returncode, failed.stdout), (1, ""), failed.stderr)
        self.assertIn("IsADirectoryError", failed.stderr)
        self.assertEqual((quiet.returncode, quiet.stdout, quiet.stderr), (0, "", ""))

    def _run_main_with_broken_part(self, module: ModuleType, part: str, name: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        broken = mock.Mock(side_effect=RuntimeError("broken on purpose"))
        environment = {
            "REPLY_LANGUAGE": "ko",
            "HOME": str(self._home),
            "CLAUDE_CONFIG_DIR": str(self._home / ".claude"),
        }

        with (
            mock.patch.object(module, part, broken),
            mock.patch.dict(os.environ, environment),
            mock.patch("sys.argv", [name]),
            mock.patch("sys.stdin", io.StringIO(json.dumps(TOOL_CALL))),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = module.main()
        self.assertTrue(broken.called, f"{name} did not reach {part}")
        return code, stdout.getvalue(), stderr.getvalue()

    def _run_script(self, script: Path, stdin: str) -> subprocess.CompletedProcess[str]:
        removed = ("CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "SKILL_GATE")
        environment = {key: value for key, value in os.environ.items() if key not in removed}

        environment.update(
            {"HOME": str(self._home), "CLAUDE_CONFIG_DIR": str(self._home / ".claude"), "REPLY_LANGUAGE": "ko"}
        )
        return subprocess.run(
            [sys.executable, str(script)], input=stdin, capture_output=True, text=True, env=environment, check=False
        )


def raise_error_now() -> list[Any]:
    raise RuntimeError("broken on purpose")


def deny(reason: str) -> Any:
    return hook_failure.Finding(hook_failure.Decision.DENY, reason)


def read_decision(output: dict[str, Any] | None) -> tuple[str, str]:
    decision = (output or {}).get("hookSpecificOutput", {})

    return decision.get("permissionDecision", ""), decision.get("permissionDecisionReason", "")


if __name__ == "__main__":
    unittest.main()
