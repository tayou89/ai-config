#!/usr/bin/env python3
"""Tests that the Git guards and the commit wrapper refuse what they cannot finish judging.

A guard that could not read what it needs, or failed while judging, must not read that as safe (user decision,
2026-11-24): the folder guard and the shell guard refuse the call, and the commit wrapper commits nothing.
"""

from __future__ import annotations

import functools
import importlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

from workflow_world import BASE, SCRIPTS_DIRECTORY, WorkflowWorld, load_script

workflow_policy = load_script("workflow_policy")
folder_owner = load_script("folder_owner")
shell_guard = load_script("shell-guard-hook")
claude_commit = load_script("claude-commit")
pr_merge = load_script("pr-merge")
local_integrate = load_script("local-integrate")
path_listing = importlib.import_module("path_listing")
session_writes = importlib.import_module("session_writes")

WORKTREE_GUARD = SCRIPTS_DIRECTORY / "worktree-guard-hook.py"
SHELL_GUARD = SCRIPTS_DIRECTORY / "shell-guard-hook.py"
POLICY = {"mode": "pull-request", "base": BASE, "requiredChecks": ["checks"]}
SESSION_ID = "guard-failure-test"
DENY = "deny"
ASK = "ask"
PASS = "pass"
FAILED = "the hook failed"
ENCODING = "utf-8"
NOTES = "notes.md"
GONE = "gone-from-the-working-tree.md"
HEAD_OBJECT = "0" * 40
SUBMODULE_COMMIT = "1" * 40
REGULAR_FILE_MODE = "100644"
BROKEN_POLICIES = ["{", '{"mode": "pull-requests", "base": "main", "requiredChecks": ["checks"]}']
FOLDER_GUARD_REFUSAL = "The folder guard could not finish judging this call"
READ_ONLY_REFUSAL = "so it stays read-only"
INCLUDE_COMMAND = "python3 ~/.claude/scripts/claude-commit.py --include-unrecorded README.md 'Fix: x' README.md"
GIT_SETTINGS = ("-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "protocol.file.allow=always")
GIT_FAILURE = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal")
BLOB_FAILURE = subprocess.CompletedProcess([], 128, stdout=b"", stderr=b"fatal")
RESTORE_FROM_HEAD = ("--source=HEAD", "--staged", "--worktree")


def run_git(folder: Path, *arguments: str) -> None:
    """Git with the test's own identity and none of this computer's settings."""
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment = {**inherited, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    command = ["git", *GIT_SETTINGS, "-C", str(folder), *arguments]

    subprocess.run(command, env=environment, capture_output=True, check=True)


class GuardRun:
    """One guard run: its decision, reason, and error output."""

    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        output = json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}

        self.returncode = result.returncode
        self.decision = output.get("permissionDecision", PASS) if result.returncode == 0 else FAILED
        self.reason = output.get("permissionDecisionReason", "")
        self.stderr = result.stderr


class GuardWorld:
    def __init__(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.worktree = self.world.add_worktree("task-a")
        self.config_directory = self.world.root / "claude"
        self.config_directory.mkdir()

    def close(self) -> None:
        self.world.close()

    def run(self, script: Path, payload: dict[str, Any], environment: dict[str, str] | None = None) -> GuardRun:
        base = {**self.world.environment, "CLAUDE_CONFIG_DIR": str(self.config_directory)}
        result = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload),
            env={**base, **(environment or {})},
            capture_output=True,
            text=True,
            check=False,
        )

        return GuardRun(result)

    def make_payload(self, tool_name: str, tool_input: dict[str, str], cwd: Path) -> dict[str, Any]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": SESSION_ID,
            "tool_use_id": "call-1",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "cwd": str(cwd),
        }


class WorktreeGuardFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = GuardWorld()
        self.addCleanup(self.guard.close)
        self.original = self.guard.world.original
        self.policy = self.original / workflow_policy.POLICY_FILE

    def edit(self, path: Path, environment: dict[str, str] | None = None) -> GuardRun:
        payload = self.guard.make_payload("Edit", {"file_path": str(path)}, self.original)

        return self.guard.run(WORKTREE_GUARD, payload, environment)

    def test_a_broken_workflow_file_refuses_writes_in_the_original_checkout(self) -> None:
        for text in BROKEN_POLICIES:
            with self.subTest(text=text):
                self.policy.write_text(text, encoding=ENCODING)
                run = self.edit(self.original / "README.md")

                self.assertEqual(run.decision, DENY)
                self.assertIn("PolicyError", run.reason)

    def test_a_workflow_path_that_is_a_folder_refuses_writes_in_the_original_checkout(self) -> None:
        self.policy.unlink()
        self.policy.mkdir()
        run = self.edit(self.original / "README.md")

        self.assertEqual(run.decision, DENY)
        self.assertIn("is not a file", run.reason)

    def test_a_broken_workflow_file_restored_from_a_terminal_brings_the_protection_back(self) -> None:
        """The repair runs outside the hooks, so it does not depend on the guard it repairs, and it takes the file from
        the last commit, so a staged break is undone too."""
        restore = ["git", "-C", str(self.original), "restore", *RESTORE_FROM_HEAD, self.policy.name]
        restore_call = self.guard.make_payload("Bash", {"command": shlex.join(restore)}, self.original)
        readme = self.original / "README.md"

        self.policy.write_text("{", encoding=ENCODING)
        self.guard.world.git(self.original, "add", self.policy.name)
        broken = self.edit(readme)
        restore_through_the_guard = self.guard.run(WORKTREE_GUARD, restore_call)

        subprocess.run(restore, env=self.guard.world.environment, capture_output=True, check=True)
        restored = self.edit(readme)
        reading = self.guard.run(
            WORKTREE_GUARD, self.guard.make_payload("Bash", {"command": "cat README.md"}, self.original)
        )

        self.assertEqual((broken.decision, restore_through_the_guard.decision), (DENY, DENY))
        self.assertIn("PolicyError", broken.reason)
        self.assertEqual(restored.decision, DENY)
        self.assertIn(READ_ONLY_REFUSAL, restored.reason)
        self.assertEqual(reading.decision, PASS)
        self.assertEqual(self.guard.world.git(self.original, "status", "--porcelain"), "")

    def test_a_broken_workflow_file_in_the_last_commit_is_restored_from_an_earlier_one(self) -> None:
        """When a broken file was committed, the last commit is no good source; the one before it is."""
        self.policy.write_text("{", encoding=ENCODING)
        self.guard.world.git(self.original, "commit", "-q", "-am", "Chore: break the workflow file")
        broken = self.edit(self.original / "README.md")
        restore = ["git", "-C", str(self.original), "restore", "--source=HEAD~1", "--staged", "--worktree", "--"]

        subprocess.run([*restore, self.policy.name], env=self.guard.world.environment, capture_output=True, check=True)
        restored = self.edit(self.original / "README.md")
        staged = self.guard.world.git(
            self.original, "diff", "--cached", "--name-only", "HEAD~1", "--", self.policy.name
        )

        self.assertIn("PolicyError", broken.reason)
        self.assertEqual(restored.decision, DENY)
        self.assertIn(READ_ONLY_REFUSAL, restored.reason)
        self.assertEqual(staged, "")

    def test_a_staged_copy_unlike_the_file_is_kept_aside_before_the_restore(self) -> None:
        """The two documented looks before a restore show and keep both copies, which the restore then drops."""
        staged_text = '{"mode": "staged only"}'
        working_text = '{"mode": "working only"}'

        self.policy.write_text(staged_text, encoding=ENCODING)
        self.guard.world.git(self.original, "add", self.policy.name)
        self.policy.write_text(working_text, encoding=ENCODING)
        kept_staged = self.guard.world.git(self.original, "show", f":{self.policy.name}")
        kept_working = self.policy.read_text(encoding=ENCODING)
        restore = ["git", "-C", str(self.original), "restore", *RESTORE_FROM_HEAD, self.policy.name]

        subprocess.run(restore, env=self.guard.world.environment, capture_output=True, check=True)
        self.assertEqual((kept_staged, kept_working), (staged_text, working_text))
        self.assertEqual(self.guard.world.git(self.original, "status", "--porcelain"), "")

    def test_a_git_entry_that_cannot_be_read_refuses_writes_there(self) -> None:
        folder = self.guard.world.root / "looping"

        folder.mkdir()
        (folder / ".git").symlink_to(folder / ".git")
        run = self.guard.run(
            WORKTREE_GUARD, self.guard.make_payload("Edit", {"file_path": str(folder / "x.md")}, folder)
        )

        self.assertEqual(run.decision, DENY)
        self.assertIn(FOLDER_GUARD_REFUSAL, run.reason)

    def test_a_gap_record_that_cannot_be_read_refuses_accepting_its_gaps(self) -> None:
        worktree = folder_owner.find_linked_worktree(self.guard.worktree)
        command = f"python3 ~/.claude/scripts/claude-commit.py --accept-gaps 'Fix: x' {NOTES}"

        self.assertIsNotNone(worktree)
        worktree.gaps_file.write_text("{}\n", encoding=ENCODING)
        worktree.gaps_file.chmod(0o000)
        self.addCleanup(worktree.gaps_file.chmod, 0o644)
        run = self.guard.run(WORKTREE_GUARD, self.guard.make_payload("Bash", {"command": command}, self.guard.worktree))

        self.assertEqual(run.decision, DENY)
        self.assertIn("OwnerFileError", run.reason)

    def test_worktree_records_that_cannot_be_listed_refuse_removing_a_folder_that_may_hold_one(self) -> None:
        records = self.original / ".git" / "worktrees"
        command = {"command": f"rm -rf {self.original / '.claude'}"}

        records.chmod(0o000)
        self.addCleanup(records.chmod, 0o755)
        run = self.guard.run(WORKTREE_GUARD, self.guard.make_payload("Bash", command, self.guard.world.root))

        self.assertEqual(run.decision, DENY)
        self.assertIn("PermissionError", run.reason)

    def test_a_call_from_an_unknown_session_is_refused_only_where_a_worktree_is_touched(self) -> None:
        inside_file = {"file_path": str(self.guard.worktree / "README.md")}
        outside_file = {"file_path": str(self.guard.world.root / "notes.txt")}
        inside = self.guard.make_payload("Edit", inside_file, self.original)
        outside = self.guard.make_payload("Edit", outside_file, self.original)

        for payload in (inside, outside):
            payload.pop("session_id")
        self.assertEqual(self.guard.run(WORKTREE_GUARD, inside).decision, DENY)
        self.assertEqual(self.guard.run(WORKTREE_GUARD, outside).decision, PASS)

    def test_git_that_cannot_run_refuses_a_write_it_must_check(self) -> None:
        empty_path = self.guard.world.root / "no-programs"

        empty_path.mkdir()
        run = self.edit(self.original / "claude-plans" / "plan-x.md", {"PATH": str(empty_path)})

        self.assertEqual(run.decision, DENY)
        self.assertIn("FileNotFoundError", run.reason)

    def test_a_garbled_worktree_link_refuses_writes_there(self) -> None:
        (self.guard.worktree / ".git").write_text("nonsense\n", encoding=ENCODING)
        run = self.edit(self.guard.worktree / "README.md")

        self.assertEqual(run.decision, DENY)
        self.assertIn("names no Git folder", run.reason)

    def test_a_new_worktree_whose_note_cannot_be_kept_is_refused(self) -> None:
        notes = self.guard.config_directory / "folder-owner"
        command = {"command": f"git worktree add -q -b task-b {self.original / '.claude' / 'worktrees' / 'task-b'}"}
        payload = self.guard.make_payload("Bash", command, self.original)

        notes.write_text("", encoding=ENCODING)
        refused = self.guard.run(WORKTREE_GUARD, payload)
        notes.unlink()
        kept = self.guard.run(WORKTREE_GUARD, payload)

        self.assertEqual(refused.decision, DENY)
        self.assertIn(FOLDER_GUARD_REFUSAL, refused.reason)
        self.assertEqual(kept.decision, PASS)

    def test_a_new_worktree_note_that_cannot_be_read_after_the_call_shows_a_hook_error(self) -> None:
        note = self.guard.config_directory / "folder-owner" / "pending-adds" / "call-1.json"
        command = {"command": f"git worktree add -q -b task-b {self.original / '.claude' / 'worktrees' / 'task-b'}"}
        payload = {
            **self.guard.make_payload("Bash", command, self.original),
            "hook_event_name": "PostToolUse",
            "tool_response": {},
        }

        note.parent.mkdir(parents=True)
        note.write_text("{", encoding=ENCODING)
        run = self.guard.run(WORKTREE_GUARD, payload)

        self.assertEqual(run.returncode, 1)
        self.assertIn("worktree-guard-hook", run.stderr)


class ShellGuardFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = GuardWorld()
        self.addCleanup(self.guard.close)
        self.original = self.guard.world.original

    def bash(self, command: str, cwd: Path) -> GuardRun:
        return self.guard.run(SHELL_GUARD, self.guard.make_payload("Bash", {"command": command}, cwd))

    def decide_with(self, failing: str, command: str) -> dict[str, str]:
        """The shell guard's decision, judged here, while one of its checks fails."""
        environment = {"CLAUDE_CONFIG_DIR": str(self.guard.config_directory)}

        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(shell_guard, failing, side_effect=RuntimeError("boom")),
        ):
            output = shell_guard.decide(command, str(self.original), SESSION_ID)
        self.assertIsNotNone(output)
        return output["hookSpecificOutput"]

    def test_input_that_is_not_json_exits_one_for_the_settings_entry_to_block(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SHELL_GUARD)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual((result.returncode, result.stdout), (1, ""))

    def test_a_failing_check_keeps_the_refusal_another_check_found(self) -> None:
        output = self.decide_with("find_production_reasons", "git commit -m x")

        self.assertEqual(output["permissionDecision"], DENY)
        self.assertIn(shell_guard.COMMIT_REASON, output["permissionDecisionReason"])

    def test_a_failing_production_check_asks(self) -> None:
        output = self.decide_with("find_production_reasons", "ls")

        self.assertEqual(output["permissionDecision"], ASK)
        self.assertIn("production-mode check", output["permissionDecisionReason"])

    def test_a_failing_git_check_refuses_even_where_it_would_ask(self) -> None:
        output = self.decide_with("find_push_reasons", "git push")

        self.assertEqual(output["permissionDecision"], DENY)
        self.assertIn("push check", output["permissionDecisionReason"])

    def test_a_repository_whose_config_git_cannot_read_refuses_pushes_and_staging(self) -> None:
        with (self.guard.worktree / ".git").open(encoding=ENCODING) as git_file:
            git_directory = Path(git_file.read().split(":", 1)[1].strip())
        config = git_directory.parent.parent / "config"

        with config.open("a", encoding=ENCODING) as config_file:
            config_file.write("[broken\n")
        for command in ("git push", "git add -A"):
            with self.subTest(command=command):
                run = self.bash(command, self.guard.worktree)

                self.assertEqual(run.decision, DENY)
                self.assertIn("GitReadError", run.reason)

    def test_staging_a_file_whose_content_cannot_be_read_is_refused(self) -> None:
        key = self.guard.worktree / "server.pem"

        key.write_text("not a key\n", encoding=ENCODING)
        key.chmod(0o000)
        self.addCleanup(key.chmod, 0o644)
        run = self.bash("git add server.pem", self.guard.worktree)

        self.assertEqual(run.decision, DENY)
        self.assertIn("credential check", run.reason)

    def test_a_git_location_from_the_environment_or_an_option_keeps_the_credential_check(self) -> None:
        """A folder outside any repository skips the staging check only when nothing else names a repository."""
        elsewhere = self.guard.world.root / "elsewhere"
        location = {"GIT_DIR": str(self.original / ".git"), "GIT_WORK_TREE": str(self.original)}
        payload = self.guard.make_payload("Bash", {"command": "git add -A"}, elsewhere)
        option_command = f"git --git-dir={self.original / '.git'} --work-tree={self.original} add -A"

        elsewhere.mkdir()
        (self.original / ".env").write_text("TOKEN=fake\n", encoding=ENCODING)
        from_environment = self.guard.run(SHELL_GUARD, payload, location)
        from_option = self.bash(option_command, elsewhere)
        outside_any_repository = self.guard.run(SHELL_GUARD, payload)

        self.assertEqual((from_environment.decision, from_option.decision), (DENY, DENY))
        self.assertIn(".env", from_environment.reason)
        self.assertIn(".env", from_option.reason)
        self.assertEqual(outside_any_repository.decision, PASS)

    def test_unrecorded_lines_whose_dialog_cannot_be_kept_are_refused(self) -> None:
        output = self.decide_with("add_include_offer", INCLUDE_COMMAND)

        self.assertEqual(output["permissionDecision"], DENY)
        self.assertIn("unrecorded lines check", output["permissionDecisionReason"])

    def test_unrecorded_lines_of_a_call_from_an_unknown_session_are_refused(self) -> None:
        payload = self.guard.make_payload("Bash", {"command": INCLUDE_COMMAND}, self.original)

        payload.pop("session_id")
        run = self.guard.run(SHELL_GUARD, payload)

        self.assertEqual(run.decision, DENY)
        self.assertIn("README.md", run.reason)

    def test_a_worktree_link_that_cannot_be_read_stops_only_the_calls_that_need_it(self) -> None:
        adopt = "python3 ~/.claude/scripts/session_writes.py adopt --from other-session README.md"

        (self.guard.worktree / ".git").write_text("nonsense\n", encoding=ENCODING)
        listing = self.bash("ls", self.guard.worktree)
        adopting = self.bash(adopt, self.guard.worktree)

        self.assertEqual(listing.decision, PASS)
        self.assertEqual(adopting.decision, DENY)
        self.assertIn("names no Git folder", adopting.reason)

    def test_a_repository_git_cannot_read_refuses_a_pull_request_merge(self) -> None:
        folder = self.guard.world.root / "not-really-a-repository"

        (folder / ".git").mkdir(parents=True)
        run = self.bash("gh pr merge 1 --squash", folder)

        self.assertEqual(run.decision, DENY)
        self.assertIn("merge check", run.reason)


class CommitWrapperFailureTest(unittest.TestCase):
    def run_failing(self, action: Callable[[], object]) -> tuple[int, str]:
        errors = io.StringIO()

        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            action()
        return int(raised.exception.code), errors.getvalue()

    def test_an_approval_that_cannot_be_kept_commits_nothing(self) -> None:
        with mock.patch.object(claude_commit, "approve_include", side_effect=OSError("disk full")):
            code, errors = self.run_failing(
                lambda: claude_commit.approve_dialog_contents("session", "/tmp", ["a.txt"], from_index=False),
            )
        self.assertEqual(code, claude_commit.EXIT_FOREIGN_LINES)
        self.assertIn("could not be kept", errors)

    def test_an_approval_no_dialog_showed_commits_nothing(self) -> None:
        unshown = claude_commit.IncludeUnshownError(NOTES)

        with mock.patch.object(claude_commit, "approve_include", side_effect=unshown):
            code, errors = self.run_failing(
                lambda: claude_commit.approve_dialog_contents("session", "/tmp", [NOTES], from_index=False),
            )
        self.assertEqual(code, claude_commit.EXIT_FOREIGN_LINES)
        self.assertIn(f"no dialog of the shell guard showed the lines of {NOTES}", errors)

    def test_staged_content_that_cannot_be_read_stops_the_credential_check(self) -> None:
        listing = subprocess.CompletedProcess([], 0, stdout="server.pem\0", stderr="")

        with (
            mock.patch.object(claude_commit, "run_git", return_value=listing),
            mock.patch.object(claude_commit, "read_staged_mode", return_value=REGULAR_FILE_MODE),
            mock.patch.object(claude_commit.subprocess, "run", return_value=BLOB_FAILURE),
        ):
            code, errors = self.run_failing(claude_commit.check_credential_files)
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)
        self.assertIn("could not read the staged content of server.pem", errors)

    def test_staged_content_of_a_plain_file_that_cannot_be_read_stops_the_line_check(self) -> None:
        """The check never falls back to the working file, which may differ from what is staged."""
        whole_file_check = mock.Mock(return_value=True)

        with (
            mock.patch.object(claude_commit, "read_staged_mode", return_value=REGULAR_FILE_MODE),
            mock.patch.object(claude_commit.subprocess, "run", return_value=BLOB_FAILURE),
            mock.patch.object(claude_commit, "is_whole_file_own", whole_file_check),
        ):
            code, errors = self.run_failing(
                lambda: claude_commit.describe_foreign_lines(SESSION_ID, "/tmp", NOTES, None)
            )
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)
        self.assertIn(f"could not read the staged content of {NOTES}", errors)
        whole_file_check.assert_not_called()

    def test_staged_content_is_read_by_its_repository_path_from_a_subfolder(self) -> None:
        """The index is asked by the path from the repository's top, so running in a subfolder never makes a staged
        file read as removed and checked as the working file instead."""
        directory = tempfile.TemporaryDirectory()
        repository = Path(os.path.realpath(directory.name))
        notes = repository / "docs" / NOTES

        self.addCleanup(directory.cleanup)
        run_git(repository, "init", "-q")
        notes.parent.mkdir()
        notes.write_text("staged\n", encoding=ENCODING)
        run_git(repository, "add", f"docs/{NOTES}")
        notes.write_text("working\n", encoding=ENCODING)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(notes.parent)
        self.assertEqual(claude_commit.read_staged_content(f"docs/{NOTES}"), b"staged\n")

    def test_a_staged_submodule_and_a_folder_in_the_base_hold_no_file_to_read(self) -> None:
        """A submodule's entry names a commit of another repository and a folder has no content, so both read as no
        file rather than as content Git could not show, which would stop every such commit."""
        directory = tempfile.TemporaryDirectory()
        repository = Path(os.path.realpath(directory.name))
        notes = repository / "docs" / NOTES

        self.addCleanup(directory.cleanup)
        run_git(repository, "init", "-q")
        notes.parent.mkdir()
        notes.write_text("base\n", encoding=ENCODING)
        run_git(repository, "add", f"docs/{NOTES}")
        run_git(repository, "update-index", "--add", "--cacheinfo", f"160000,{SUBMODULE_COMMIT},part")
        run_git(repository, "update-index", "--add", "--cacheinfo", f"160000,{SUBMODULE_COMMIT},keys.json")
        run_git(repository, "commit", "-q", "-m", "base")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(notes.parent)
        base = claude_commit.read_revision("HEAD")
        self.assertIsNone(claude_commit.read_staged_content("part"))
        self.assertEqual(claude_commit.read_base_text(base, "docs"), "")
        self.assertEqual(claude_commit.read_base_text(base, "part"), "")
        self.assertEqual(claude_commit.read_base_text(base, f"docs/{NOTES}"), "base\n")
        self.assertIsNone(claude_commit.read_staged_content("docs"))
        read_keys = functools.partial(claude_commit.read_staged_content, "keys.json")

        self.assertIsNone(claude_commit.get_credential_reason("keys.json", read_keys))

    def test_the_base_of_a_submodule_is_the_commit_the_write_record_starts_from(self) -> None:
        """The record names a submodule's base by its old commit, so the wrapper passes the same object, and a change
        the session recorded is not refused as unproven."""
        directory = tempfile.TemporaryDirectory()
        repository = Path(os.path.realpath(directory.name))

        self.addCleanup(directory.cleanup)
        run_git(repository, "init", "-q")
        run_git(repository, "update-index", "--add", "--cacheinfo", f"160000,{SUBMODULE_COMMIT},part")
        run_git(repository, "commit", "-q", "-m", "base")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(repository)
        base = claude_commit.read_revision("HEAD")
        recorded = session_writes._read_committed_blob(str(repository), base, "part")
        self.assertEqual(claude_commit.read_base_blob(base, "part"), recorded)
        self.assertEqual(recorded, SUBMODULE_COMMIT)

    def test_an_approval_covers_the_repository_paths_it_was_given_for(self) -> None:
        """A file named from a subfolder is approved and left out of the line check by its path from the top, so a
        same-named file at the top is still checked."""
        arguments = claude_commit.parse_commit_arguments(["--include-unrecorded", NOTES, "Fix: x", NOTES])
        scope = ("/repository", [f"docs/{NOTES}"])

        with (
            mock.patch.object(claude_commit, "read_session_id", return_value=SESSION_ID),
            mock.patch.object(claude_commit, "find_repository_paths", return_value=scope),
            mock.patch.object(claude_commit, "approve_dialog_contents", return_value={f"docs/{NOTES}": "0123abc"}),
            mock.patch.object(claude_commit, "is_content_stageable", return_value=True),
        ):
            approval = claude_commit.approve_included_files(arguments)
        self.assertEqual(approval.paths, (f"docs/{NOTES}",))
        self.assertEqual(list(approval.contents), [NOTES])

    def test_an_index_git_cannot_list_stops_the_commit_rather_than_read_a_removal(self) -> None:
        """HEAD holds the file and the working tree no longer does, so an index Git cannot list would read as a
        removal already staged and leave the file out of the commit unseen."""
        unmatched = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        answers = {
            "ls-files": GIT_FAILURE,
            "rev-parse": subprocess.CompletedProcess([], 0, stdout=f"{HEAD_OBJECT}\n", stderr=""),
            "ls-tree": subprocess.CompletedProcess([], 0, stdout=f"100644 blob {HEAD_OBJECT}\t{GONE}\n", stderr=""),
        }

        with mock.patch.object(claude_commit, "run_git", return_value=unmatched):
            absent = claude_commit.is_in_index(NOTES)
        with mock.patch.object(claude_commit, "run_git", side_effect=lambda arguments, **_: answers[arguments[0]]):
            code, errors = self.run_failing(lambda: claude_commit.stage_file(GONE))
        self.assertFalse(absent)
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)
        self.assertNotIn("removal already staged", errors)

    def test_a_head_tree_git_cannot_read_stops_the_commit_but_an_unborn_head_holds_nothing(self) -> None:
        with mock.patch.object(claude_commit, "read_revision", return_value=None):
            unborn = claude_commit.is_in_head(NOTES)
        with (
            mock.patch.object(claude_commit, "read_revision", return_value="0123abc"),
            mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE),
        ):
            code, _ = self.run_failing(lambda: claude_commit.is_in_head(NOTES))
        self.assertFalse(unborn)
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)

    def test_a_branch_git_cannot_read_stops_the_commit_but_a_detached_head_is_on_none(self) -> None:
        detached = subprocess.CompletedProcess([], 1, stdout="", stderr="")

        with mock.patch.object(claude_commit, "run_git", return_value=detached):
            branch = claude_commit.read_head_branch()
        with mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE):
            code, _ = self.run_failing(claude_commit.read_head_branch)
        self.assertIsNone(branch)
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)

    def test_a_merge_git_cannot_redo_needs_a_reviewed_commit(self) -> None:
        output = io.StringIO()

        with mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE):
            code, errors = self.run_failing(lambda: claude_commit.read_automatic_merge("0123abc", reviewed=False))
            with redirect_stdout(output):
                reviewed_base = claude_commit.read_automatic_merge("0123abc", reviewed=True)
            state_code, _ = self.run_failing(claude_commit.read_merge_heads)
        self.assertEqual(code, claude_commit.EXIT_REVIEW_MISSING)
        self.assertIn("--reviewed", errors)
        self.assertEqual(reviewed_base, [])
        self.assertEqual(state_code, claude_commit.EXIT_GIT_ERROR)

    def test_included_files_git_cannot_place_in_a_repository_commit_nothing(self) -> None:
        arguments = claude_commit.parse_commit_arguments(["--include-unrecorded", NOTES, "Fix: x", NOTES])

        with (
            mock.patch.object(claude_commit, "read_session_id", return_value=SESSION_ID),
            mock.patch.object(claude_commit, "find_repository_paths", return_value=None),
        ):
            code, errors = self.run_failing(lambda: claude_commit.approve_included_files(arguments))
        self.assertEqual(code, claude_commit.EXIT_FOREIGN_LINES)
        self.assertIn(NOTES, errors)

    def test_the_line_check_leaves_out_only_the_files_an_approval_covers(self) -> None:
        listing = subprocess.CompletedProcess([], 0, stdout=f"{NOTES}\0", stderr="")
        problem = f"FAIL: {NOTES} has lines this session did not write"

        with (
            mock.patch.object(claude_commit, "read_repository_root", return_value="/tmp"),
            mock.patch.object(claude_commit, "run_git", return_value=listing),
            mock.patch.object(claude_commit, "describe_staged_problem", return_value=problem),
        ):
            claude_commit.check_staged_lines((NOTES,), None)
            code, errors = self.run_failing(lambda: claude_commit.check_staged_lines((), None))
        self.assertEqual(code, claude_commit.EXIT_FOREIGN_LINES)
        self.assertIn(problem, errors)

    def test_a_dialog_record_that_cannot_be_read_commits_nothing(self) -> None:
        damaged = claude_commit.IncludeRecordError("offered-includes.jsonl holds a line that cannot be read")

        with mock.patch.object(claude_commit, "approve_include", side_effect=damaged):
            code, errors = self.run_failing(
                lambda: claude_commit.approve_dialog_contents(SESSION_ID, "/tmp", [NOTES], from_index=False),
            )
        self.assertEqual(code, claude_commit.EXIT_FOREIGN_LINES)
        self.assertIn("dialog record cannot be read", errors)

    def test_a_staged_list_git_cannot_give_stops_the_line_check(self) -> None:
        with (
            mock.patch.object(claude_commit, "read_repository_root", return_value="/tmp"),
            mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE),
        ):
            code, _ = self.run_failing(lambda: claude_commit.check_staged_lines([], None))
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)

    def test_a_revision_git_cannot_read_stops_the_commit_but_an_unknown_name_does_not(self) -> None:
        unknown = subprocess.CompletedProcess([], 1, stdout="", stderr="")

        with mock.patch.object(claude_commit, "run_git", return_value=unknown):
            missing = claude_commit.read_revision("HEAD")
        with mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE):
            code, errors = self.run_failing(lambda: claude_commit.read_revision("HEAD"))
        self.assertIsNone(missing)
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)
        self.assertIn("could not read HEAD", errors)

    def test_a_base_git_cannot_read_stops_the_commit(self) -> None:
        with (
            mock.patch.object(claude_commit, "read_tree_file_blob", return_value="0123abc"),
            mock.patch.object(claude_commit.subprocess, "run", return_value=BLOB_FAILURE),
        ):
            code, errors = self.run_failing(lambda: claude_commit.read_base_text("0123abc", NOTES))
        with mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE):
            listed_code, listed_errors = self.run_failing(lambda: claude_commit.read_base_text("0123abc", NOTES))
        self.assertEqual((code, listed_code), (claude_commit.EXIT_GIT_ERROR, claude_commit.EXIT_GIT_ERROR))
        self.assertIn(f"could not read {NOTES} as it is in", errors)
        self.assertIn(f"could not read {NOTES} as it is in", listed_errors)

    def test_an_index_git_cannot_read_or_change_stops_the_commit(self) -> None:
        actions = {
            "mode": lambda: claude_commit.read_index_mode("/tmp", NOTES),
            "removal": lambda: claude_commit.remove_index_entry("/tmp", NOTES, "deleted"),
        }

        for name, action in actions.items():
            with self.subTest(name=name), mock.patch.object(claude_commit, "run_git", return_value=GIT_FAILURE):
                code, errors = self.run_failing(action)

                self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)
                self.assertIn(NOTES, errors)

    def test_a_git_read_error_that_reaches_the_top_stops_the_commit(self) -> None:
        with mock.patch.object(claude_commit, "run_commit", side_effect=claude_commit.GitReadError("a .git file")):
            code, errors = self.run_failing(lambda: claude_commit.main([]))
        self.assertEqual(code, claude_commit.EXIT_GIT_ERROR)
        self.assertIn("a .git file", errors)

    def test_a_write_record_error_that_reaches_the_top_stops_the_commit(self) -> None:
        unreadable = claude_commit.SessionWritesError("the session transcripts cannot be listed")

        with mock.patch.object(claude_commit, "run_commit", side_effect=unreadable):
            code, errors = self.run_failing(lambda: claude_commit.main([]))
        self.assertEqual(code, claude_commit.EXIT_FOREIGN_LINES)
        self.assertIn("transcripts cannot be listed", errors)


class IncludeRecordTest(unittest.TestCase):
    """What a dialog showed is what the commit wrapper may commit, in a session the record hook never saw too."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(directory.name))
        configuration = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "claude")})

        self.addCleanup(directory.cleanup)
        configuration.start()
        self.addCleanup(configuration.stop)
        self.repository = root / "repository"
        run_git(root, "init", "-q", str(self.repository))
        (self.repository / NOTES).write_text("typed by a person\n", encoding=ENCODING)

    def test_a_session_without_a_record_keeps_its_dialog_and_its_approval(self) -> None:
        offered = session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        approved = session_writes.approve_include(SESSION_ID, str(self.repository), [NOTES])

        self.assertEqual(approved, offered)
        self.assertTrue(session_writes.is_include_approved(SESSION_ID, str(self.repository), approved))

    def test_an_approval_no_dialog_showed_is_refused(self) -> None:
        with self.assertRaises(session_writes.IncludeUnshownError):
            session_writes.approve_include(SESSION_ID, str(self.repository), [NOTES])

    def test_a_file_changed_after_its_dialog_is_refused(self) -> None:
        session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        (self.repository / NOTES).write_text("typed by a person\nand more\n", encoding=ENCODING)
        with self.assertRaises(session_writes.IncludeChangedError):
            session_writes.approve_include(SESSION_ID, str(self.repository), [NOTES])

    def test_a_newest_dialog_line_that_cannot_be_read_does_not_fall_back_to_an_older_dialog(self) -> None:
        offers = session_writes.get_record_directory() / SESSION_ID / "offered-includes.jsonl"

        session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        with offers.open("a", encoding=ENCODING) as file:
            file.write("{broken\n")
        with self.assertRaises(session_writes.IncludeRecordError):
            session_writes.approve_include(SESSION_ID, str(self.repository), [NOTES])
        session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        self.assertTrue(session_writes.approve_include(SESSION_ID, str(self.repository), [NOTES]))

    def test_a_newest_dialog_line_with_a_damaged_field_does_not_fall_back_to_an_older_dialog(self) -> None:
        """A root that is not text, or not a string, would read as another repository's dialog and let the older one
        stand in, so such a line counts as one that cannot be read."""
        offers = session_writes.get_record_directory() / SESSION_ID / "offered-includes.jsonl"
        root = str(self.repository)
        entry = json.dumps({"root": root, "blobs": {NOTES: None}, "time": 1.0}).encode(ENCODING)
        damaged = {
            "bytes that are not text": entry.replace(root.encode(ENCODING), root.encode(ENCODING) + b"\xff"),
            "a root that is not a string": entry.replace(json.dumps(root).encode(ENCODING), b"null"),
        }

        for name, line in damaged.items():
            with self.subTest(name=name):
                session_writes.add_include_offer(SESSION_ID, root, [NOTES])
                with offers.open("ab") as file:
                    file.write(line + b"\n")
                with self.assertRaises(session_writes.IncludeRecordError):
                    session_writes.approve_include(SESSION_ID, root, [NOTES])
                session_writes.add_include_offer(SESSION_ID, root, [NOTES])
                self.assertTrue(session_writes.approve_include(SESSION_ID, root, [NOTES]))

    def test_a_dialog_line_cut_off_before_its_end_lets_the_next_dialog_read(self) -> None:
        offers = session_writes.get_record_directory() / SESSION_ID / "offered-includes.jsonl"

        session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        with offers.open("a", encoding=ENCODING) as file:
            file.write('{"root": "cut off')
        session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        self.assertTrue(session_writes.approve_include(SESSION_ID, str(self.repository), [NOTES]))

    def test_a_record_whose_repositories_cannot_be_read_raises(self) -> None:
        record = session_writes.get_record_directory() / f"{SESSION_ID}.jsonl"

        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{}\n", encoding=ENCODING)
        record.chmod(0o000)
        self.addCleanup(record.chmod, 0o644)
        with self.assertRaises(session_writes.SessionWritesError):
            session_writes._read_recorded_roots(SESSION_ID)

    def test_transcripts_that_cannot_be_listed_raise_rather_than_make_a_late_record_complete(self) -> None:
        projects = session_writes._get_config_directory() / "projects"

        session_writes.add_include_offer(SESSION_ID, str(self.repository), [NOTES])
        projects.mkdir()
        projects.chmod(0o000)
        self.addCleanup(projects.chmod, 0o755)
        with self.assertRaises(session_writes.SessionWritesError):
            session_writes.is_record_complete(SESSION_ID)


class GitReadTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()

        self.addCleanup(directory.cleanup)
        self.root = Path(os.path.realpath(directory.name))

    def test_git_that_cannot_find_the_repository_folder_raises_an_error_the_tools_stop_on(self) -> None:
        (self.root / ".git").mkdir()
        with self.assertRaises(workflow_policy.GitReadError) as raised:
            workflow_policy.read_common_git_directory(self.root)
        self.assertIsInstance(raised.exception, workflow_policy.CommandError)

    def test_a_workflow_path_that_is_a_folder_raises(self) -> None:
        (self.root / workflow_policy.POLICY_FILE).mkdir()
        with self.assertRaises(workflow_policy.PolicyError):
            workflow_policy.read_checkout_policy(self.root)

    def test_a_worktree_link_that_cannot_be_read_raises(self) -> None:
        git_file = self.root / ".git"
        links = [("nonsense\n", 0o644), ("gitdir: elsewhere\n", 0o000), (f"gitdir: {self.root / 'gone'}\n", 0o644)]

        for text, mode in links:
            with self.subTest(text=text, mode=mode):
                git_file.write_text(text, encoding=ENCODING)
                git_file.chmod(mode)
                with self.assertRaises(folder_owner.GitReadError):
                    folder_owner.find_linked_worktree(self.root)
                git_file.chmod(0o644)

    def test_a_link_to_a_folder_with_neither_a_worktree_record_nor_a_repository_raises(self) -> None:
        """An empty folder, or a worktree record that lost its link to the shared Git folder, is no submodule."""
        target = self.root / "modules" / "part"
        cases = {
            "empty": [],
            "a folder with only a HEAD file, such as a log folder": ["HEAD"],
            "worktree record without its shared folder": ["HEAD", "gitdir"],
        }

        target.mkdir(parents=True)
        (self.root / ".git").write_text(f"gitdir: {target}\n", encoding=ENCODING)
        for name, files in cases.items():
            with self.subTest(name=name):
                for file in files:
                    (target / file).write_text("", encoding=ENCODING)
                with self.assertRaises(folder_owner.GitReadError):
                    folder_owner.find_linked_worktree(self.root)

    def test_a_link_to_a_repository_of_its_own_is_no_worktree(self) -> None:
        """A checkout whose Git folder lies elsewhere (`git init --separate-git-dir`) is a repository Git confirms."""
        work = self.root / "work"

        run_git(self.root, "init", "-q", "--separate-git-dir", str(self.root / "elsewhere"), str(work))
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "missing")}):
            self.assertIsNone(folder_owner.find_linked_worktree(work))

    def test_a_git_entry_that_loops_still_marks_a_repository_and_raises_on_reading(self) -> None:
        inside = self.root / "inside"

        inside.mkdir()
        (self.root / ".git").symlink_to(self.root / ".git")
        self.assertEqual(workflow_policy.find_repository_root(inside), self.root)
        with self.assertRaises(folder_owner.GitReadError):
            folder_owner.find_linked_worktree(inside)

    def test_a_repository_git_cannot_name_raises_but_a_plain_folder_is_outside_any(self) -> None:
        damaged = self.root / "damaged"
        plain = self.root / "plain"

        (damaged / ".git").mkdir(parents=True)
        plain.mkdir()
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in workflow_policy.GIT_LOCATION_VARIABLES:
                os.environ.pop(name, None)
            self.assertIsNone(session_writes.find_repository_root(str(plain)))
            with self.assertRaises(session_writes.SessionWritesError):
                session_writes.find_repository_root(str(damaged))

    def test_a_folder_inside_a_git_folder_is_in_no_working_tree(self) -> None:
        """A write such as one to `.git/info/exclude` changes no working tree, so the record notes nothing there
        instead of showing a hook error for each such write."""
        run_git(self.root, "init", "-q")
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in workflow_policy.GIT_LOCATION_VARIABLES:
                os.environ.pop(name, None)
            self.assertIsNone(session_writes.find_repository_root(str(self.root / ".git" / "info" / "exclude")))
            self.assertEqual(session_writes.find_repository_root(str(self.root / NOTES)), str(self.root))

    def test_a_gap_record_that_cannot_be_read_raises(self) -> None:
        worktree = folder_owner.LinkedWorktree(self.root, self.root)
        gaps = worktree.gaps_file

        gaps.write_text("{}\n", encoding=ENCODING)
        gaps.chmod(0o000)
        self.addCleanup(gaps.chmod, 0o644)
        with self.assertRaises(folder_owner.OwnerFileError):
            folder_owner.read_gaps(worktree)

    def test_a_real_submodule_is_no_worktree(self) -> None:
        part = self.root / "part"
        top = self.root / "top"

        run_git(self.root, "init", "-q", str(part))
        run_git(part, "commit", "-q", "--allow-empty", "-m", "Chore: seed")
        run_git(self.root, "init", "-q", str(top))
        run_git(top, "submodule", "add", "-q", str(part), "part")
        self.assertIsNone(folder_owner.find_linked_worktree(top / "part"))

    def test_a_head_git_cannot_read_raises_but_an_unborn_one_is_none(self) -> None:
        unborn = self.root / "unborn"
        plain = self.root / "plain"

        run_git(self.root, "init", "-q", str(unborn))
        plain.mkdir()
        self.assertIsNone(session_writes._read_head(str(unborn)))
        with self.assertRaises(session_writes.SessionWritesError):
            session_writes._read_head(str(plain))

    def test_changes_git_cannot_list_raise(self) -> None:
        worktree = folder_owner.LinkedWorktree(self.root, self.root / "gone")

        with self.assertRaises(folder_owner.GitReadError):
            folder_owner.has_uncommitted_changes(worktree)


class ListingTest(unittest.TestCase):
    """Records are listed level by level, so a folder that cannot be read raises instead of hiding what it holds."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()

        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_a_folder_that_is_missing_or_not_a_folder_matches_nothing(self) -> None:
        (self.root / "file").write_text("", encoding=ENCODING)
        for name in ("missing", "file"):
            with self.subTest(name=name):
                self.assertEqual(path_listing.list_matching_paths(self.root / name, "*/gitdir"), [])

    def test_matches_are_found_at_every_level(self) -> None:
        found = self.root / "worktrees" / "a" / "gitdir"

        found.parent.mkdir(parents=True)
        found.write_text("", encoding=ENCODING)
        (self.root / "worktrees" / "b").mkdir()
        self.assertEqual(path_listing.list_matching_paths(self.root, "worktrees/*/gitdir"), [found])

    def test_a_folder_that_cannot_be_read_raises(self) -> None:
        locked = self.root / "worktrees"

        locked.mkdir()
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)
        with self.assertRaises(PermissionError):
            path_listing.list_matching_paths(self.root, "worktrees/*/gitdir")


class IntegrationToolTest(unittest.TestCase):
    """After a merge or an integration is done, a Git step that fails only warns, and it does warn."""

    def test_an_original_checkout_git_cannot_find_after_the_merge_only_warns(self) -> None:
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output):
            pr_merge.update_original_checkout(Path(folder), BASE, "0123abc")
        self.assertIn("the merge itself is done", output.getvalue())

    def test_a_temporary_worktree_git_cannot_remove_warns_and_its_folder_still_goes(self) -> None:
        """A leftover Git no longer lists as a worktree would otherwise stay for every later run."""
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output):
            leftover = Path(folder) / "integration"

            leftover.mkdir()
            (leftover / NOTES).write_text("left\n", encoding=ENCODING)
            local_integrate.remove_worktree(Path(folder), leftover)
            is_left = leftover.exists()
        self.assertIn("could not remove the temporary worktree", output.getvalue())
        self.assertFalse(is_left)


if __name__ == "__main__":
    unittest.main()
