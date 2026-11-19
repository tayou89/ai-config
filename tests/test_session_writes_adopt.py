"""Tests for taking over handed-over files, the --no-stage dialog contents, and recording a commit's hooks.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_shell_guard import (
    ASK,
    COMMIT_WRAPPER,
    FOREIGN_LINES_EXIT,
    HEAD_COMMIT_MESSAGE,
    HEAD_COMMIT_SUBJECT,
    LOG_FILE,
    MINE_1,
    MINE_2,
    OTHER_SESSION,
    OWN_SESSION,
    PENDING_SUFFIX,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    RECORD_DIRECTORY_NAME,
    SCRIPTS_DIRECTORY,
    SessionWritesFixture,
)

SESSION_WRITES = SCRIPTS_DIRECTORY / "session_writes.py"
FOLDER_OWNER = SCRIPTS_DIRECTORY / "folder_owner.py"
THIRD_SESSION = "session-third"
PLUGIN_FILE = "plugin.ts"
HANDED_TEXT = "".join(f"export const step{number} = {number}\n" for number in range(1, 21))
THIRD_LINE = "export const third = 'not handed over'\n"
LATER_LINE = "export const later = 'written after the handover'\n"
UNRECORDED_LINE = "export const typed = 'by a person'\n"
APP_SESSION = "local_0f3a"
SESSION_TITLE = "login handover"
SESSIONS_FOLDER = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions" / "account" / "org"
ADOPTIONS_FILE = "adopted.jsonl"
ADOPT_FAILED_EXIT = 1
OPEN_STATE_TEXT = "아직 닫히지 않았습니다"
UNKNOWN_STATE_TEXT = "닫혔는지 알 수 없습니다"
HOOK_NOTES_FILE = "notes.md"
HOOK_LISTING_FILE = "pending-listing.txt"
COMMIT_HOOK_TOOL = "commit hook"
COMMIT_HOOK_PENDING_PREFIX = "commit-hooks-"


def rewrite(text: str) -> str:
    """Read a file and write it back whole, most lines unchanged, as a session that took it over did."""
    changed = text.replace("= 5\n", "= 'five'\n").replace("= 12\n", "= 'twelve'\n")
    lines = changed.splitlines(keepends=True)

    return "".join([*lines[:8], "export const extra = true\n", *lines[8:]])


class TakeoverFixture(SessionWritesFixture):
    def adopt(self, session: str, sender: str, *paths: str) -> subprocess.CompletedProcess[str]:
        """Run the takeover as a recorded Bash call, whose start gives the session its record folder."""
        arguments = ["adopt", "--from", sender, *paths]
        command = {"command": " ".join(["python3", str(SESSION_WRITES), *arguments])}

        self.tool_uses += 1
        self.run_record_hook(session, PRE_TOOL_USE, f"call-{self.tool_uses}", command)
        result = subprocess.run(
            [sys.executable, str(SESSION_WRITES), *arguments],
            env={**self.environment(session), "HOME": str(self.root)},
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )
        self.run_record_hook(session, POST_TOOL_USE, f"call-{self.tool_uses}", command, {})
        return result

    def run_folder_owner(self, session: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(FOLDER_OWNER), *arguments],
            env={**self.environment(session), "HOME": str(self.root)},
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )

    def open_record(self, session: str) -> None:
        """Give a session its record folder, as the record hook does at its first call."""
        self.tool_uses += 1
        self.run_record_hook(session, PRE_TOOL_USE, f"call-{self.tool_uses}", {"command": "ls"})
        self.run_record_hook(session, POST_TOOL_USE, f"call-{self.tool_uses}", {"command": "ls"}, {})

    def add_app_record(self, cli_session: str, is_archived: bool) -> None:
        """An app record of a session, as the desktop app keeps one, under the fixture's home folder."""
        folder = self.root / SESSIONS_FOLDER
        record = {
            "sessionId": APP_SESSION,
            "cliSessionId": cli_session,
            "title": SESSION_TITLE,
            "isArchived": is_archived,
        }

        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{APP_SESSION}.json").write_text(json.dumps(record))

    def run_home_guard(self, session: str, command: str) -> subprocess.CompletedProcess[str]:
        payload = {
            "session_id": session,
            "hook_event_name": PRE_TOOL_USE,
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.repository),
        }

        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIRECTORY / "shell-guard-hook.py")],
            input=json.dumps(payload),
            env={**self.environment(None), "HOME": str(self.root)},
            capture_output=True,
            text=True,
            check=False,
        )

    def read_working(self, path: str) -> str:
        return (self.repository / path).read_text()

    def read_committed(self, path: str) -> str:
        return self.read_committed_bytes(path).decode()


class TakeoverTest(unittest.TestCase):
    """A session that took over another's uncommitted file commits that session's recorded lines as its own."""

    fixture: TakeoverFixture

    def setUp(self) -> None:
        self.fixture = TakeoverFixture()
        self.fixture.create()
        self.fixture.create_file(OTHER_SESSION, PLUGIN_FILE, HANDED_TEXT)

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_without_takeover_a_whole_rewrite_is_tangled(self) -> None:
        self.fixture.create_file(OWN_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT))
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("tangled", result.stderr)

    def test_takeover_commits_the_handed_over_lines(self) -> None:
        adopted = self.fixture.adopt(OWN_SESSION, OTHER_SESSION, PLUGIN_FILE)
        self.fixture.create_file(OWN_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT))
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE)

        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed(PLUGIN_FILE), rewrite(HANDED_TEXT))

    def test_third_session_lines_stay_out(self) -> None:
        self.fixture.create_file(THIRD_SESSION, PLUGIN_FILE, HANDED_TEXT + THIRD_LINE)
        self.fixture.adopt(OWN_SESSION, OTHER_SESSION, PLUGIN_FILE)
        self.fixture.create_file(OWN_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT + THIRD_LINE))
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(THIRD_LINE, self.fixture.read_committed(PLUGIN_FILE))
        self.assertIn(THIRD_LINE, self.fixture.read_working(PLUGIN_FILE))

    def test_writes_after_the_takeover_stay_the_senders(self) -> None:
        self.fixture.adopt(OWN_SESSION, OTHER_SESSION, PLUGIN_FILE)
        self.fixture.create_file(OWN_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT))
        self.fixture.write(OTHER_SESSION, PLUGIN_FILE, LATER_LINE)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(LATER_LINE, self.fixture.read_committed(PLUGIN_FILE))

    def test_a_chain_of_takeovers_counts_every_handover(self) -> None:
        self.fixture.adopt(THIRD_SESSION, OTHER_SESSION, PLUGIN_FILE)
        self.fixture.create_file(THIRD_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT))
        self.fixture.adopt(OWN_SESSION, THIRD_SESSION, PLUGIN_FILE)
        self.fixture.create_file(OWN_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT) + LATER_LINE)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed(PLUGIN_FILE), rewrite(HANDED_TEXT) + LATER_LINE)

    def test_adopt_refuses_a_file_the_sender_never_wrote(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.adopt(OWN_SESSION, OTHER_SESSION, LOG_FILE)
        adoptions = self.fixture.config_directory / RECORD_DIRECTORY_NAME / OWN_SESSION / ADOPTIONS_FILE

        self.assertEqual(result.returncode, ADOPT_FAILED_EXIT, result.stdout + result.stderr)
        self.assertFalse(adoptions.exists())

    def test_adopt_takes_an_app_session_id(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.add_app_record(OTHER_SESSION, is_archived=True)
        result = self.fixture.adopt(OWN_SESSION, APP_SESSION, PLUGIN_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(OTHER_SESSION, result.stdout)

    def test_adopt_in_a_task_worktree_takes_the_folder_and_commits_its_lines(self) -> None:
        """In a task worktree the owner commits whole files, so taking over takes the folder, not lines."""
        worktree = self.fixture.create_worktree("task")
        self.fixture.repository = worktree
        claim = self.fixture.run_folder_owner(OTHER_SESSION, "claim")
        self.fixture.create_file(OTHER_SESSION, PLUGIN_FILE, HANDED_TEXT)
        adopted = self.fixture.adopt(OWN_SESSION, OTHER_SESSION, PLUGIN_FILE)
        self.fixture.append(PLUGIN_FILE, UNRECORDED_LINE)
        result = self.fixture.run_wrapper("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE, cwd=worktree)

        self.assertEqual(claim.returncode, 0, claim.stderr)
        self.assertEqual(adopted.returncode, 0, adopted.stdout + adopted.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(UNRECORDED_LINE, self.fixture.read_committed(PLUGIN_FILE))

    def test_adopt_takes_over_nothing_when_one_file_fails(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.adopt(OWN_SESSION, OTHER_SESSION, PLUGIN_FILE, LOG_FILE)
        adoptions = self.fixture.config_directory / RECORD_DIRECTORY_NAME / OWN_SESSION / ADOPTIONS_FILE

        self.assertEqual(result.returncode, ADOPT_FAILED_EXIT, result.stdout + result.stderr)
        self.assertFalse(adoptions.exists())

    def test_takeovers_in_a_circle_end(self) -> None:
        self.fixture.adopt(OWN_SESSION, OTHER_SESSION, PLUGIN_FILE)
        self.fixture.create_file(OWN_SESSION, PLUGIN_FILE, rewrite(HANDED_TEXT))
        self.fixture.adopt(OTHER_SESSION, OWN_SESSION, PLUGIN_FILE)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, PLUGIN_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed(PLUGIN_FILE), rewrite(HANDED_TEXT))


class TakeoverQuestionTest(unittest.TestCase):
    """The shell guard asks before a takeover from a session that may still commit those lines itself."""

    fixture: TakeoverFixture

    def setUp(self) -> None:
        self.fixture = TakeoverFixture()
        self.fixture.create()
        command_parts = [
            f"cd {self.fixture.repository} &&",
            "python3 ~/.claude/scripts/session_writes.py",
            f"adopt --from {APP_SESSION} {PLUGIN_FILE}",
        ]
        self.command = " ".join(command_parts)

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_takeover_from_an_archived_session_runs_without_asking(self) -> None:
        self.fixture.add_app_record(OTHER_SESSION, is_archived=True)
        result = self.fixture.run_home_guard(OWN_SESSION, self.command)

        self.assertEqual(result.stdout, "", result.stderr)

    def test_takeover_from_an_open_session_is_asked(self) -> None:
        self.fixture.add_app_record(OTHER_SESSION, is_archived=False)
        result = self.fixture.run_home_guard(OWN_SESSION, self.command)
        decision = json.loads(result.stdout)["hookSpecificOutput"]

        self.assertEqual(decision["permissionDecision"], ASK, result.stderr)
        self.assertIn(SESSION_TITLE, decision["permissionDecisionReason"])
        self.assertIn(OPEN_STATE_TEXT, decision["permissionDecisionReason"])

    def test_takeover_from_a_session_with_no_app_record_is_asked(self) -> None:
        result = self.fixture.run_home_guard(OWN_SESSION, self.command)
        decision = json.loads(result.stdout)["hookSpecificOutput"]

        self.assertEqual(decision["permissionDecision"], ASK, result.stderr)
        self.assertIn(UNKNOWN_STATE_TEXT, decision["permissionDecisionReason"])


class NoStageDialogTest(unittest.TestCase):
    """With --no-stage the dialog shows, and the approval covers, the prepared index the commit takes."""

    fixture: TakeoverFixture

    def setUp(self) -> None:
        self.fixture = TakeoverFixture()
        self.fixture.create()
        self.fixture.open_record(OWN_SESSION)
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.head = self.fixture.run_git("rev-parse", "HEAD")
        self.fixture.run_git("read-tree", self.head, index=self.fixture.private_index)
        self.fixture.run_git("add", LOG_FILE, index=self.fixture.private_index)
        command_parts = [
            f"cd {self.fixture.repository} &&",
            f"GIT_INDEX_FILE={self.fixture.private_index}",
            str(COMMIT_WRAPPER),
            "--no-stage",
            f"--expect-head {self.head}",
            f"--include-unrecorded {LOG_FILE}",
            f'"{HEAD_COMMIT_SUBJECT}"',
            LOG_FILE,
        ]
        self.command = " ".join(command_parts)

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_dialog_lists_the_index_contents(self) -> None:
        self.fixture.append(LOG_FILE, MINE_2)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

        self.assertIn("+ ## mine 1", reason)
        self.assertNotIn("+ ## mine 2", reason)

    def test_retry_after_an_approved_run_is_not_asked_again(self) -> None:
        self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)
        self.run_no_stage_wrapper("--expect-head", "abc")
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(result.stdout, "", result.stderr)

    def test_index_unlike_the_dialog_is_refused(self) -> None:
        self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)
        self.fixture.append(LOG_FILE, MINE_2)
        self.fixture.run_git("add", LOG_FILE, index=self.fixture.private_index)
        result = self.run_no_stage_wrapper("--expect-head", self.head)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("changed after the shell guard's dialog", result.stderr)

    def run_no_stage_wrapper(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.fixture.run_wrapper(
            "--no-stage",
            *arguments,
            "--include-unrecorded",
            LOG_FILE,
            HEAD_COMMIT_MESSAGE,
            LOG_FILE,
            index=self.fixture.private_index,
        )


class CommitHookRecordTest(unittest.TestCase):
    """The wrapper records what commit hooks write while git commit runs, and only then counts as running."""

    fixture: TakeoverFixture

    def setUp(self) -> None:
        self.fixture = TakeoverFixture()
        self.fixture.create()
        (self.fixture.repository / HOOK_NOTES_FILE).write_text("notes\n")
        self.fixture.run_git("add", HOOK_NOTES_FILE)
        self.fixture.run_git("commit", "-q", "-m", "Chore: notes")
        self.listing = self.fixture.root / HOOK_LISTING_FILE
        record_folder = self.fixture.config_directory / RECORD_DIRECTORY_NAME / OWN_SESSION
        hook = self.fixture.repository / ".git" / "hooks" / "pre-commit"

        hook.write_text(f'#!/bin/sh\nls "{record_folder}" > "{self.listing}"\necho hooked >> {HOOK_NOTES_FILE}\n')
        hook.chmod(0o755)

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_hook_rewrite_is_recorded_as_the_sessions_write(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        events = [event for event in self.fixture.read_events(OWN_SESSION) if event.get("tool") == COMMIT_HOOK_TOOL]

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([event["path"] for event in events], [HOOK_NOTES_FILE])

    def test_commit_counts_as_running_only_while_its_hooks_run(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        listed = self.listing.read_text().split()

        self.assertTrue(any(name.startswith(COMMIT_HOOK_PENDING_PREFIX) for name in listed), listed)
        self.assertEqual([name for name in self.fixture.list_pending_files() if name.endswith(PENDING_SUFFIX)], [])


if __name__ == "__main__":
    unittest.main()
