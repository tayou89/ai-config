#!/usr/bin/env python3
"""Tests for commits in a task worktree: the commit wrapper reads the folder's owner record and nothing else.

The owner commits whole files whoever wrote them (Codex, a formatter, a
person at a terminal there), and every other session is refused. Only a
recorded guard gap asks, once.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import unittest

from owner_world import (
    ASK,
    COMMIT_MESSAGE,
    GAPS_EXIT,
    NOT_OWNER_EXIT,
    OTHER,
    OWNER,
    PASS,
    OwnerWorld,
    Session,
)
from workflow_world import BASE, ENCODING, SCRIPTS_DIRECTORY, load_script

SHELL_GUARD_SCRIPT = SCRIPTS_DIRECTORY / "shell-guard-hook.py"
NOTES = "notes.txt"
CODEX_TEXT = "written by Codex\n"
ACCEPT_COMMAND = f"python3 ~/.claude/scripts/claude-commit.py --accept-gaps 'Fix: x' {NOTES}"
GIT_ERROR_EXIT = 1
MESSAGE_EXIT = 2
STAGING_MISMATCH_EXIT = 3
CLOCK_STEP_SECONDS = 0.05
folder_owner = load_script("folder_owner")


def identity(session: Session) -> folder_owner.SessionIdentity:
    """The identity a gap is recorded under, from this module's own copy of the script, whose classes the calls
    below compare against."""
    return folder_owner.SessionIdentity(session.app, session.cli, None)


class OwnerCommitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree = self.world.add_worktree_as(OWNER, "task-a")

    def test_owner_commits_lines_no_record_explains(self) -> None:
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        result = self.commit(OWNER, "--own-lines", COMMIT_MESSAGE, NOTES)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("task worktree", result.stdout)
        self.assertEqual(self.world.git(self.worktree, "show", f"HEAD:{NOTES}") + "\n", CODEX_TEXT)

    def test_an_idle_terminal_in_the_folder_does_not_stop_the_owner(self) -> None:
        sleeper = subprocess.Popen(["sleep", "30"], cwd=self.worktree)

        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        result = self.commit(OWNER, COMMIT_MESSAGE, NOTES)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_another_session_is_refused_and_told_whose_folder_it_is(self) -> None:
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        head = self.world.git(self.worktree, "rev-parse", "HEAD")
        result = self.commit(OTHER, COMMIT_MESSAGE, NOTES)

        self.assertEqual(result.returncode, NOT_OWNER_EXIT, result.stdout + result.stderr)
        self.assertIn(OWNER.title, result.stdout + result.stderr)
        self.assertEqual(self.world.git(self.worktree, "rev-parse", "HEAD"), head)

    def test_a_folder_without_an_owner_is_refused_with_how_to_claim_it(self) -> None:
        worktree = self.world.add_worktree("unowned")

        self.world.write(worktree, NOTES, CODEX_TEXT)
        result = self.world.run_commit(OWNER, worktree, COMMIT_MESSAGE, NOTES)

        self.assertEqual(result.returncode, NOT_OWNER_EXIT, result.stdout + result.stderr)
        self.assertIn("folder_owner.py claim", result.stdout + result.stderr)

    def test_outside_a_claude_session_the_owner_is_not_checked(self) -> None:
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        result = self.commit(None, COMMIT_MESSAGE, NOTES)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_guard_gap_asks_once_and_is_settled_by_the_commit(self) -> None:
        worktree = folder_owner.find_linked_worktree(self.worktree)

        folder_owner.add_gap(worktree, None, "Bash: ValueError: could not read the command", None)
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        refused = self.commit(OWNER, COMMIT_MESSAGE, NOTES)
        decision = self.world.bash(OWNER, ACCEPT_COMMAND, self.worktree)
        accepted = self.commit(OWNER, "--accept-gaps", COMMIT_MESSAGE, NOTES)

        self.assertEqual(refused.returncode, GAPS_EXIT, refused.stdout + refused.stderr)
        self.assertIn("--accept-gaps", refused.stdout + refused.stderr)
        self.assertEqual(decision, ASK)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(folder_owner.read_gaps(worktree), [])
        self.assertEqual(self.world.bash(OWNER, ACCEPT_COMMAND, self.worktree), PASS)

    def test_accepting_gaps_no_dialog_showed_is_refused(self) -> None:
        worktree = folder_owner.find_linked_worktree(self.worktree)

        folder_owner.add_gap(worktree, None, "Bash: ValueError: seen in the dialog", None)
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        unseen = self.commit(OWNER, "--accept-gaps", COMMIT_MESSAGE, NOTES)

        self.world.bash(OWNER, ACCEPT_COMMAND, self.worktree)
        folder_owner.add_gap(worktree, None, "Bash: ValueError: added after the dialog", None)
        later = self.commit(OWNER, "--accept-gaps", COMMIT_MESSAGE, NOTES)

        self.assertEqual(unseen.returncode, GAPS_EXIT, unseen.stdout + unseen.stderr)
        self.assertEqual(later.returncode, GAPS_EXIT, later.stdout + later.stderr)
        self.assertIn("added after the dialog", later.stdout + later.stderr)
        self.assertNotIn("seen in the dialog", later.stdout + later.stderr)

    def test_include_unrecorded_in_a_task_worktree_needs_no_dialog(self) -> None:
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        command = f"python3 ~/.claude/scripts/claude-commit.py --include-unrecorded {NOTES} 'Fix: x' {NOTES}"
        result = self.commit(OWNER, "--include-unrecorded", NOTES, COMMIT_MESSAGE, NOTES)

        self.assertIsNone(self.run_shell_guard(command))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("task worktree", result.stdout)

    def test_the_shell_guard_resolves_a_folder_reached_through_links(self) -> None:
        linked = self.world.root / "linked-task"
        loop = self.world.root / "loop"
        wrapper = f"python3 ~/.claude/scripts/claude-commit.py --include-unrecorded {NOTES} 'Fix: x' {NOTES}"

        linked.symlink_to(self.worktree)
        (self.world.root / "loop-a").symlink_to(self.world.root / "loop-b")
        (self.world.root / "loop-b").symlink_to(self.world.root / "loop-a")
        loop.symlink_to(self.world.root / "loop-a")
        looped = json.dumps(self.run_shell_guard(f"cd {loop} && {wrapper}"), ensure_ascii=False)

        self.assertIsNone(self.run_shell_guard(f"cd {linked} && {wrapper}"))
        self.assertNotIn("could not analyze", looped)
        self.assertIn(NOTES, looped)

    def test_owner_commits_a_prepared_merge(self) -> None:
        self.world.world.push_base_commit("base.txt", "from the base\n")
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        self.world.git(self.worktree, "add", NOTES)
        self.world.git(self.worktree, "commit", "-q", "-m", "Feat: notes")
        self.world.git(self.worktree, "fetch", "-q", "origin")
        self.world.git(self.worktree, "merge", "--no-ff", "--no-commit", f"origin/{BASE}")
        message = "Merge: bring the base into the task\n\n- one\n- two\n- three"
        result = self.commit(OWNER, "--no-stage", message)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.world.git(self.worktree, "rev-list", "--parents", "-1", "HEAD").split()), 3)

    def test_owner_amends_its_own_commit(self) -> None:
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        first = self.commit(OWNER, COMMIT_MESSAGE, NOTES)

        self.world.write(self.worktree, NOTES, CODEX_TEXT + "and a fix\n")
        amended = self.commit(OWNER, "--amend", COMMIT_MESSAGE, NOTES)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(amended.returncode, 0, amended.stdout + amended.stderr)
        self.assertEqual(self.world.git(self.worktree, "rev-list", "--count", f"origin/{BASE}..HEAD"), "1")

    def test_codex_results_are_committed_by_the_session_that_made_the_folder_only(self) -> None:
        self.world.write(self.worktree, "feature.py", "print('made by Codex')\n")
        stranger = self.commit(OTHER, COMMIT_MESSAGE, "feature.py")
        owner = self.commit(OWNER, "--own-lines", COMMIT_MESSAGE, "feature.py")

        self.assertEqual(stranger.returncode, NOT_OWNER_EXIT, stranger.stdout + stranger.stderr)
        self.assertEqual(owner.returncode, 0, owner.stdout + owner.stderr)

    def test_the_owners_own_gaps_do_not_ask(self) -> None:
        worktree = folder_owner.find_linked_worktree(self.worktree)

        folder_owner.add_gap(worktree, identity(OWNER), "Bash: OSError: the owner's own call", "toolu_own")
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        decision = self.world.bash(OWNER, ACCEPT_COMMAND, self.worktree)
        result = self.commit(OWNER, COMMIT_MESSAGE, NOTES)

        self.assertEqual(decision, PASS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(folder_owner.read_gaps(worktree), [])

    def test_gaps_of_others_unknown_sessions_and_before_the_owner_took_the_folder_still_ask(self) -> None:
        worktree = folder_owner.find_linked_worktree(self.worktree)

        folder_owner.add_gap(worktree, identity(OWNER), "Bash: OSError: before the owner took it", "toolu_before")
        self.move_claim_to_now()
        folder_owner.add_gap(worktree, identity(OTHER), "Bash: OSError: another session's call", "toolu_other")
        folder_owner.add_gap(worktree, None, "Bash: OSError: no known session", None)
        folder_owner.add_gap(worktree, identity(OWNER), "Bash: OSError: the owner's own call", "toolu_own")
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        dialog = self.world.reason(OWNER, "Bash", {"command": ACCEPT_COMMAND}, self.worktree)
        result = self.commit(OWNER, COMMIT_MESSAGE, NOTES)

        self.assertEqual(result.returncode, GAPS_EXIT, result.stdout + result.stderr)
        for output in (result.stdout + result.stderr, dialog):
            for asked in ("before the owner took it", "another session's call", "no known session"):
                self.assertIn(asked, output)
            self.assertNotIn("the owner's own call", output)

    def test_an_approved_gap_dialog_is_not_asked_again_after_a_failed_commit(self) -> None:
        worktree = folder_owner.find_linked_worktree(self.worktree)

        folder_owner.add_gap(worktree, None, "Bash: OSError: seen and approved", None)
        self.world.write(self.worktree, NOTES, CODEX_TEXT)
        self.assertEqual(self.world.bash(OWNER, ACCEPT_COMMAND, self.worktree), ASK)
        refused_message = self.commit(OWNER, "--accept-gaps", "fix: lower-case type", NOTES)

        self.assertEqual(refused_message.returncode, MESSAGE_EXIT, refused_message.stdout + refused_message.stderr)
        self.assertEqual(self.world.bash(OWNER, ACCEPT_COMMAND, self.worktree), PASS)

    def test_a_renamed_files_old_name_commits_as_the_rename(self) -> None:
        self.world.git(self.worktree, "mv", "README.md", "GUIDE.md")
        result = self.commit(OWNER, COMMIT_MESSAGE, "README.md", "GUIDE.md")
        names = self.world.git(self.worktree, "ls-tree", "--name-only", "HEAD").splitlines()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GUIDE.md", names)
        self.assertNotIn("README.md", names)

    def test_files_of_a_folder_git_removed_commit_as_their_removal(self) -> None:
        self.world.write(self.worktree, "docs/a.txt", CODEX_TEXT)
        added = self.commit(OWNER, COMMIT_MESSAGE, "docs/a.txt")

        self.world.git(self.worktree, "rm", "-r", "-q", "docs")
        removed = self.commit(OWNER, COMMIT_MESSAGE, "docs/a.txt")

        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertNotIn("docs", self.world.git(self.worktree, "ls-tree", "--name-only", "HEAD"))

    def test_a_removed_folder_and_a_mistyped_path_stop_as_before(self) -> None:
        self.world.write(self.worktree, "docs/a.txt", CODEX_TEXT)
        added = self.commit(OWNER, COMMIT_MESSAGE, "docs/a.txt")

        shutil.rmtree(self.worktree / "docs")
        removed = self.commit(OWNER, COMMIT_MESSAGE, "docs")
        mistyped = self.commit(OWNER, COMMIT_MESSAGE, "missing.txt")

        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertEqual(removed.returncode, STAGING_MISMATCH_EXIT, removed.stdout + removed.stderr)
        self.assertEqual(mistyped.returncode, GIT_ERROR_EXIT, mistyped.stdout + mistyped.stderr)

    def move_claim_to_now(self) -> None:
        """Mark the folder as taken after the gaps written so far, pausing on both sides so the times differ."""
        owner_file = self.world.owner_file(self.worktree)
        record = json.loads(owner_file.read_text(encoding=ENCODING))

        time.sleep(CLOCK_STEP_SECONDS)
        record["claimedAt"] = time.time()
        owner_file.write_text(json.dumps(record), encoding=ENCODING)
        time.sleep(CLOCK_STEP_SECONDS)

    def commit(self, session: Session | None, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.world.run_commit(session, self.worktree, *arguments)

    def run_shell_guard(self, command: str) -> dict[str, object] | None:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": OWNER.cli,
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.worktree),
        }
        result = subprocess.run(
            [sys.executable, str(SHELL_GUARD_SCRIPT)],
            input=json.dumps(payload),
            env=self.world.environment(OWNER),
            capture_output=True,
            text=True,
            encoding=ENCODING,
            check=False,
        )

        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout.strip() else None


if __name__ == "__main__":
    unittest.main()
