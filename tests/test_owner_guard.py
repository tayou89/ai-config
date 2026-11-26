#!/usr/bin/env python3
"""Tests for the folder-owner part of scripts/worktree-guard-hook.py.

Only a task worktree's owner session writes there; other sessions may read.
Owners are recorded when the worktree is made, and an unowned or abandoned
worktree is taken by the next session that writes there, asking first when
it holds uncommitted changes.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import unittest

from owner_world import (
    ASK,
    COMMIT_MESSAGE,
    DENY,
    FOLDER_OWNER_COMMAND,
    FOURTH,
    GAPS_EXIT,
    GAPS_FILE_NAME,
    OTHER,
    OWNER,
    PASS,
    POST_TOOL_USE,
    SESSION_START,
    SESSION_WRITES_COMMAND,
    TERMINAL,
    THIRD,
    OwnerWorld,
    Session,
)
from workflow_world import BASE, ENCODING

NESTED_SHELLS = 14
PENDING_TAKE_NOTE = "was asked to take the folder"
LEFT_FILE = "left.txt"
ACCEPT_GAPS_COMMAND = f"python3 ~/.claude/scripts/claude-commit.py --accept-gaps 'Fix: x' {LEFT_FILE}"
LONG_NAME_PROGRAM = "import json\nprint(" + "1" * 300 + " / 60)\n"
LONG_PATH_PROGRAM = "".join(f"print({'2' * 150} / 3)\n" for _ in range(8))


def nest(command: str) -> str:
    """A command inside more shells than the guard follows, so it cannot be read."""
    for _ in range(NESTED_SHELLS):
        command = f"sh -c {shlex.quote(command)}"
    return command


class OwnerGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.world.write(self.world.original, ".gitignore", ".claude/worktrees/\nbuild/\n")
        self.world.git(self.world.original, "commit", "-q", "-am", "Chore: ignore build output")
        self.world.git(self.world.original, "push", "-q", "origin", BASE)
        self.worktree = self.world.add_worktree_as(OWNER, "task-a")

    def test_worktree_add_records_its_owner(self) -> None:
        owner = self.world.read_owner(self.worktree)

        self.assertEqual(owner["owner"]["appSession"], OWNER.app)
        self.assertEqual(owner["method"], "created")
        self.assertEqual(owner["title"], OWNER.title)

    def test_file_tools_write_only_for_the_owner(self) -> None:
        cases = [
            ("Edit", {"file_path": str(self.worktree / "README.md")}),
            ("Write", {"file_path": str(self.worktree / "new" / "file.txt")}),
            ("NotebookEdit", {"notebook_path": str(self.worktree / "book.ipynb")}),
            ("Write", {"file_path": str(self.worktree / "build" / "ignored.txt")}),
        ]

        for tool, tool_input in cases:
            with self.subTest(tool=tool, path=next(iter(tool_input.values()))):
                self.assertEqual(self.world.decide(OWNER, tool, tool_input, self.world.root), PASS)
                self.assertEqual(self.world.decide(OTHER, tool, tool_input, self.world.root), DENY)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OWNER.cli)

    def test_other_sessions_shell_writes_are_refused(self) -> None:
        commands = [
            "echo x > README.md",
            "sed -i '' s/seed/x/ README.md",
            "touch notes.txt",
            "rm README.md",
            "git add README.md",
            "git commit -m x",
            "git restore README.md",
            "git stash",
            "npm test",
            "make",
            "uv run pytest",
            "python3 tools/format.py",
            "./run.sh",
            "codex exec --full-auto 'fix the tests'",
            "codex exec 'fix the tests'",
            "python3 ~/.claude/scripts/claude-commit.py 'Fix: x' README.md",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.worktree), DENY)
                self.assertEqual(self.world.bash(OWNER, command, self.worktree), PASS)

    def test_commands_from_elsewhere_that_touch_the_folder_are_refused(self) -> None:
        folder = shlex.quote(str(self.worktree))
        commands = [
            f"echo x > {folder}/README.md",
            f"cp /etc/hosts {folder}/hosts",
            f"git -C {folder} add README.md",
            f"codex exec -C {folder} --full-auto 'fix it'",
            f"python3 -c \"open('{self.worktree}/x.txt', 'w').write('x')\"",
            f"cd {folder} && npm run build",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.world.root), DENY)

    def test_other_sessions_may_read_the_folder(self) -> None:
        folder = shlex.quote(str(self.worktree))
        commands = [
            (f"cat {folder}/README.md", self.world.root),
            (f"git -C {folder} status", self.world.root),
            (f"git -C {folder} diff --stat", self.world.root),
            (f"git -C {folder} log --oneline -3", self.world.root),
            (f"grep -rn seed {folder}", self.world.root),
            (f"sed -n 1,5p {folder}/README.md", self.world.root),
            (f"head -3 {folder}/README.md | wc -l", self.world.root),
            (f"cp {folder}/README.md {self.world.root}/copy.txt", self.world.root),
            (f"codex exec -s read-only -C {folder} 'review the diff'", self.world.root),
            ("ls && git status && git log -1", self.worktree),
            ("gh pr view 3", self.worktree),
            ("git worktree list", self.worktree),
        ]

        for command, cwd in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, cwd), PASS)

    def test_terminal_tab_commands_are_judged_by_their_paths(self) -> None:
        tool_input = {"command": f"echo x > {self.worktree}/notes.txt", "tab_id": "2"}
        reading = {"command": f"cat {self.worktree}/README.md", "tab_id": "2"}

        self.assertEqual(self.world.decide(OTHER, "mcp__terminal__run_in_terminal", tool_input, self.world.root), DENY)
        self.assertEqual(self.world.decide(OTHER, "mcp__terminal__run_in_terminal", reading, self.world.root), PASS)
        self.assertEqual(self.world.decide(OWNER, "mcp__terminal__run_in_terminal", tool_input, self.world.root), PASS)

    def test_a_command_the_guard_cannot_follow_is_refused(self) -> None:
        cases = [
            (f"echo x > {self.worktree}/notes.txt", self.world.root),
            (f"echo x > {self.world.root}/notes.txt", self.world.root),
            ("ls", self.worktree),
        ]

        for command, cwd in cases:
            with self.subTest(command=command):
                result = self.world.run_guard(OTHER, "Bash", {"command": nest(command)}, cwd)
                output = json.loads(result.stdout)["hookSpecificOutput"]

                self.assertEqual(output["permissionDecision"], DENY)
                self.assertIn("ExpansionLimitError", output["permissionDecisionReason"])

    def test_symbolic_links_into_the_folder_are_followed(self) -> None:
        link = self.world.root / "shortcut"

        link.symlink_to(self.worktree, target_is_directory=True)
        self.assertEqual(self.world.decide(OTHER, "Write", {"file_path": str(link / "x.txt")}, self.world.root), DENY)
        self.assertEqual(self.world.bash(OTHER, "echo x > shortcut/x.txt", self.world.root), DENY)

    def test_owner_files_are_never_written_by_tools(self) -> None:
        owner_file = self.world.owner_file(self.worktree)
        gaps_file = owner_file.with_name("claude-owner-gaps.jsonl")
        cases = [
            ("Write", {"file_path": str(owner_file), "content": "{}"}),
            ("Edit", {"file_path": str(owner_file)}),
            ("Write", {"file_path": str(gaps_file), "content": ""}),
            ("Bash", {"command": f"rm {shlex.quote(str(owner_file))}"}),
            ("Bash", {"command": f"echo '{{}}' > {shlex.quote(str(owner_file))}"}),
            ("Bash", {"command": f"mv {shlex.quote(str(owner_file))} /tmp/x"}),
        ]

        for tool, tool_input in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.world.decide(OWNER, tool, tool_input, self.world.root), DENY)
                self.assertEqual(self.world.decide(OTHER, tool, tool_input, self.world.root), DENY)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OWNER.cli)

    def test_refusal_names_the_owner_and_the_ways_on(self) -> None:
        reason = self.world.reason(OTHER, "Write", {"file_path": str(self.worktree / "x.txt")}, self.world.root)

        self.assertIn(OWNER.title, reason)
        self.assertIn(OWNER.app, reason)
        self.assertIn("hand-over --to", reason)
        self.assertIn("take --from", reason)

    def test_subagents_and_a_resumed_session_count_as_the_owner(self) -> None:
        resumed = type(OWNER)(OWNER.app, "cli-owner-resumed", OWNER.title)
        tool_input = {"file_path": str(self.worktree / "x.txt")}

        self.assertEqual(self.world.decide(resumed, "Write", tool_input, self.world.root), PASS)

    def test_original_checkout_stays_read_only_for_everyone(self) -> None:
        tool_input = {"file_path": str(self.world.original / "README.md")}

        self.assertEqual(self.world.decide(OWNER, "Edit", tool_input, self.world.root), DENY)
        self.assertEqual(self.world.decide(OTHER, "Edit", tool_input, self.world.root), DENY)


class OwnerClaimTest(unittest.TestCase):
    """Worktrees made before owners were recorded, worktrees the app made, and abandoned ones."""

    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree = self.world.add_worktree("task-b")
        self.write_input = {"file_path": str(self.worktree / "x.txt")}

    def test_first_write_to_a_clean_unowned_folder_claims_it_without_asking(self) -> None:
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), PASS)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)
        self.assertEqual(self.world.read_owner(self.worktree)["method"], "first-write")
        self.assertEqual(self.world.decide(OWNER, "Write", self.write_input, self.world.root), DENY)

    def test_reading_does_not_claim(self) -> None:
        self.assertEqual(self.world.bash(OTHER, f"cat {self.worktree}/README.md", self.world.root), PASS)
        self.assertIsNone(self.world.read_owner(self.worktree))

    def test_first_write_to_a_folder_with_uncommitted_changes_asks_once(self) -> None:
        self.world.write(self.worktree, "left.txt", "left behind\n")
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), ASK)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), PASS)

    def test_an_asked_take_stays_noted_until_its_step_after_the_call_takes_the_folder(self) -> None:
        gaps = self.world.git_directory(self.worktree) / GAPS_FILE_NAME

        self.world.write(self.worktree, "left.txt", "left behind\n")
        before = self.world.run_guard(OTHER, "Write", self.write_input, self.world.root)
        noted = gaps.read_text(encoding=ENCODING)

        self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, POST_TOOL_USE, {})
        remaining = gaps.read_text(encoding=ENCODING) if gaps.exists() else ""

        self.assertEqual(json.loads(before.stdout)["hookSpecificOutput"]["permissionDecision"], ASK)
        self.assertIn(PENDING_TAKE_NOTE, noted)
        self.assertNotIn(PENDING_TAKE_NOTE, remaining)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)

    def test_an_asked_take_stays_noted_when_its_step_after_the_call_fails(self) -> None:
        gaps = self.world.git_directory(self.worktree) / GAPS_FILE_NAME

        self.world.write(self.worktree, "left.txt", "left behind\n")
        self.world.run_guard(OTHER, "Write", self.write_input, self.world.root)
        self.world.owner_file(self.worktree).write_text("{broken", encoding=ENCODING)
        after = self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, POST_TOOL_USE, {})

        self.assertEqual(after.returncode, 1)
        self.assertIn(PENDING_TAKE_NOTE, gaps.read_text(encoding=ENCODING))

    def test_an_asked_call_is_noted_in_every_folder_it_takes_not_only_the_one_it_asks_about(self) -> None:
        clean = self.world.add_worktree("task-clean")
        command = {"command": f"touch {clean / 'x.txt'} {self.worktree / 'x.txt'}"}

        self.world.write(self.worktree, "left.txt", "left behind\n")
        before = self.world.run_guard(OTHER, "Bash", command, self.world.root)
        noted = [self.world.git_directory(folder) / GAPS_FILE_NAME for folder in (clean, self.worktree)]

        self.assertEqual(json.loads(before.stdout)["hookSpecificOutput"]["permissionDecision"], ASK)
        for gaps in noted:
            with self.subTest(folder=gaps.parent.name):
                self.assertIn(PENDING_TAKE_NOTE, gaps.read_text(encoding=ENCODING))

    def test_a_take_whose_loss_cannot_be_recorded_after_the_call_stays_noted(self) -> None:
        """The owner record stays sound; only recording that another session took the folder first fails."""
        gaps = self.world.git_directory(self.worktree) / GAPS_FILE_NAME

        self.world.write(self.worktree, "left.txt", "left behind\n")
        self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, tool_use_id="call-other")
        self.world.run_guard(THIRD, "Write", self.write_input, self.world.root, tool_use_id="call-third")
        self.world.run_guard(THIRD, "Write", self.write_input, self.world.root, POST_TOOL_USE, {}, "call-third")
        gaps.chmod(0o444)
        self.addCleanup(gaps.chmod, 0o644)
        after = self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, POST_TOOL_USE, {}, "call-other")

        self.assertEqual(after.returncode, 1)
        self.assertEqual(self.world.read_owner_cli(self.worktree), THIRD.cli)
        self.assertIn(PENDING_TAKE_NOTE, gaps.read_text(encoding=ENCODING))
        self.assertIn(OTHER.app, gaps.read_text(encoding=ENCODING))

    def test_a_take_left_noted_after_another_session_won_stops_the_owners_commit_until_shown_once(self) -> None:
        """The note a failed step after the call leaves reaches the owner's real commit, which is refused for the gap
        while its owner record stays sound, and goes ahead once the guard's dialog has shown it."""
        gaps = self.world.git_directory(self.worktree) / GAPS_FILE_NAME

        self.world.write(self.worktree, LEFT_FILE, "left behind\n")
        self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, tool_use_id="call-other")
        self.world.run_guard(THIRD, "Write", self.write_input, self.world.root, tool_use_id="call-third")
        self.world.run_guard(THIRD, "Write", self.write_input, self.world.root, POST_TOOL_USE, {}, "call-third")
        gaps.chmod(0o444)
        self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, POST_TOOL_USE, {}, "call-other")
        gaps.chmod(0o644)
        refused, decision, accepted = self.commit_through_gap_dialog(THIRD)

        self.assertEqual(refused.returncode, GAPS_EXIT, refused.stdout + refused.stderr)
        self.assertIn(PENDING_TAKE_NOTE, refused.stdout + refused.stderr)
        self.assertEqual(decision, ASK)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_a_declined_take_stays_noted_and_the_next_owners_commit_asks_about_it_once(self) -> None:
        """A call the user declined never ran its step after the call, so its note says it may not have run."""
        self.world.write(self.worktree, LEFT_FILE, "left behind\n")
        declined = self.world.run_guard(OTHER, "Write", self.write_input, self.world.root, tool_use_id="call-other")
        taken = self.world.decide(THIRD, "Write", self.write_input, self.world.root)
        refused, decision, accepted = self.commit_through_gap_dialog(THIRD)

        self.assertEqual(json.loads(declined.stdout)["hookSpecificOutput"]["permissionDecision"], ASK)
        self.assertEqual(taken, ASK)
        self.assertEqual(refused.returncode, GAPS_EXIT, refused.stdout + refused.stderr)
        self.assertIn("one may not have run", refused.stdout + refused.stderr)
        self.assertIn(PENDING_TAKE_NOTE, refused.stdout + refused.stderr)
        self.assertEqual(decision, ASK)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def commit_through_gap_dialog(
        self, session: Session
    ) -> tuple[subprocess.CompletedProcess[str], str, subprocess.CompletedProcess[str]]:
        """The owner's commit, the guard's dialog for --accept-gaps, and the commit the dialog lets through."""
        refused = self.world.run_commit(session, self.worktree, COMMIT_MESSAGE, LEFT_FILE)
        decision = self.world.bash(session, ACCEPT_GAPS_COMMAND, self.worktree)
        accepted = self.world.run_commit(session, self.worktree, "--accept-gaps", COMMIT_MESSAGE, LEFT_FILE)

        return refused, decision, accepted

    def test_a_folder_whose_changes_git_cannot_list_is_refused_rather_than_asked_about(self) -> None:
        (self.world.git_directory(self.worktree) / "index").write_text("garbage", encoding=ENCODING)
        result = self.world.run_guard(OTHER, "Write", self.write_input, self.world.root)
        output = json.loads(result.stdout)["hookSpecificOutput"]

        self.assertEqual(output["permissionDecision"], DENY)
        self.assertIn("GitReadError", output["permissionDecisionReason"])
        self.assertIsNone(self.world.read_owner(self.worktree))

    def test_declined_claim_leaves_the_folder_unowned(self) -> None:
        self.world.write(self.worktree, "left.txt", "left behind\n")
        result = self.world.run_guard(OTHER, "Write", self.write_input, self.world.root)

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], ASK)
        self.assertIsNone(self.world.read_owner(self.worktree))

    def test_the_session_the_app_made_the_folder_for_claims_it_without_asking(self) -> None:
        self.world.write(self.worktree, "left.txt", "written before the record\n")
        self.world.write_app_record(OTHER, worktreePath=str(self.worktree), cwd=str(self.worktree))
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), PASS)
        self.assertEqual(self.world.read_owner(self.worktree)["method"], "app-worktree")

    def test_session_start_in_the_folder_the_app_made_claims_it(self) -> None:
        self.world.write_app_record(OTHER, worktreePath=str(self.worktree), cwd=str(self.worktree))
        self.world.run_guard(OTHER, "", {}, self.worktree, SESSION_START)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)

    def test_session_start_elsewhere_claims_nothing(self) -> None:
        self.world.run_guard(OTHER, "", {}, self.worktree, SESSION_START)
        self.assertIsNone(self.world.read_owner(self.worktree))

    def test_enter_worktree_records_its_owner(self) -> None:
        response = {"worktreePath": str(self.worktree), "message": f"Switched to {self.worktree}"}

        self.world.run_guard(OTHER, "EnterWorktree", {"name": "task-b"}, self.worktree, POST_TOOL_USE, response)
        self.assertEqual(self.world.read_owner(self.worktree)["method"], "created")
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)

    def test_a_clean_folder_whose_owner_left_is_taken_without_asking(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        self.world.archive(OWNER)
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), PASS)
        owner = self.world.read_owner(self.worktree)

        self.assertEqual(owner["owner"]["cliSession"], OTHER.cli)
        self.assertEqual(owner["previous"]["appSession"], OWNER.app)
        self.assertEqual(owner["method"], "taken-over")

    def test_a_folder_whose_owner_left_changes_behind_asks(self) -> None:
        self.world.write_owner(self.worktree, OWNER)
        self.world.archive(OWNER)
        self.world.write(self.worktree, "left.txt", "left behind\n")
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), ASK)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)

    def test_an_owner_whose_state_is_unknown_keeps_the_folder(self) -> None:
        self.world.write_owner(self.worktree, TERMINAL)
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), DENY)

    def test_a_terminal_owner_whose_process_ended_has_left(self) -> None:
        self.world.write_owner(self.worktree, TERMINAL, {"pid": 999_999, "started": "Thu Jan  1 00:00:00 1970"})
        self.assertEqual(self.world.decide(OTHER, "Write", self.write_input, self.world.root), PASS)
        self.assertEqual(self.world.read_owner_cli(self.worktree), OTHER.cli)

    def test_four_sessions_in_turn_as_in_the_personal_website_worktree(self) -> None:
        """One worktree, four sessions one after another: each writes only while it is the owner."""
        worktree = self.world.add_worktree_as(OWNER, "pr-workflow")
        write = {"file_path": str(worktree / "page.html")}

        self.assertEqual(self.world.decide(OWNER, "Write", write, self.world.root), PASS)
        self.world.write(worktree, "page.html", "first page\n")
        self.world.git(worktree, "add", "-A")
        self.world.git(worktree, "commit", "-q", "-m", "Feat: first page")
        self.world.archive(OWNER)
        self.assertEqual(self.world.decide(OTHER, "Write", write, self.world.root), PASS)
        self.assertEqual(self.world.decide(THIRD, "Write", write, self.world.root), DENY)
        self.world.write(worktree, "page.html", "a draft the second session left\n")
        self.world.archive(OTHER)
        self.assertEqual(self.world.decide(THIRD, "Write", write, self.world.root), ASK)
        self.assertEqual(self.world.decide(FOURTH, "Write", write, self.world.root), DENY)
        self.assertEqual(self.world.read_owner_cli(worktree), THIRD.cli)

    def test_owner_commands_ask_only_where_the_plan_says(self) -> None:
        folder_owner = FOLDER_OWNER_COMMAND

        self.world.write_owner(self.worktree, OWNER)
        self.assertEqual(self.world.bash(OTHER, f"{folder_owner} take --from {OWNER.app}", self.worktree), ASK)
        self.assertEqual(self.world.bash(OWNER, f"{folder_owner} hand-over --to {OTHER.app}", self.worktree), PASS)
        self.assertEqual(self.world.bash(OTHER, f"{folder_owner} show", self.worktree), PASS)

    def test_adopt_in_a_task_worktree_asks_while_the_owner_is_open(self) -> None:
        adopt = f"{SESSION_WRITES_COMMAND} adopt --from {OWNER.app} README.md"

        self.world.write_owner(self.worktree, OWNER)
        self.assertEqual(self.world.bash(OTHER, adopt, self.worktree), ASK)
        self.world.archive(OWNER)
        self.assertEqual(self.world.bash(OTHER, adopt, self.worktree), PASS)

    def test_a_guard_failure_refuses_the_call_and_leaves_no_gap(self) -> None:
        relative = {"file_path": str(self.worktree.relative_to(self.world.original) / "x.txt")}

        self.world.owner_file(self.worktree).write_text("{broken", encoding=ENCODING)
        for tool_input, cwd in [(self.write_input, self.world.root), (relative, self.world.original)]:
            with self.subTest(cwd=cwd):
                result = self.world.run_guard(OTHER, "Write", tool_input, cwd)
                output = json.loads(result.stdout)["hookSpecificOutput"]

                self.assertEqual(output["permissionDecision"], DENY)
                self.assertIn("OwnerFileError", output["permissionDecisionReason"])
                self.assertIn("worktree-guard-hook", result.stderr)
        self.assertFalse((self.world.git_directory(self.worktree) / GAPS_FILE_NAME).exists())

    def test_worktree_with_the_same_name_after_cleanup_starts_unowned(self) -> None:
        worktree = self.world.add_worktree_as(OWNER, "again")

        self.world.git(self.world.original, "worktree", "remove", str(worktree))
        self.world.git(self.world.original, "branch", "-D", "again")
        self.world.git(self.world.original, "worktree", "add", "-q", "-b", "again", str(worktree), f"origin/{BASE}")
        self.assertIsNone(self.world.read_owner(worktree))



class ReviewedBypassTest(unittest.TestCase):
    """Ways the independent review found past the guard, each refused now, and reads it refused that now pass."""

    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree = self.world.add_worktree_as(OWNER, "task-a")
        self.folder = shlex.quote(str(self.worktree))

    def test_every_spelling_of_a_takeover_asks(self) -> None:
        commands = [
            (f"{FOLDER_OWNER_COMMAND} take --from={OWNER.app}", self.worktree),
            (f"{FOLDER_OWNER_COMMAND} take {self.folder} --from {OWNER.app}", self.world.root),
            (f"{FOLDER_OWNER_COMMAND} take --from {OWNER.app} {self.folder}", self.world.root),
        ]

        for command, cwd in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, cwd), ASK)

    def test_an_owner_command_still_has_its_writes_judged(self) -> None:
        commands = [
            f"{FOLDER_OWNER_COMMAND} show > {self.folder}/a",
            f"{FOLDER_OWNER_COMMAND} show > {shlex.quote(str(self.world.original))}/README.md",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.world.root), DENY)

    def test_a_script_named_like_an_owner_command_is_any_program(self) -> None:
        command = f"python3 {self.world.root}/folder_owner.py take --from {OWNER.app}"

        self.assertEqual(self.world.bash(OTHER, command, self.worktree), DENY)

    def test_readers_that_run_or_write_through_an_option_are_refused(self) -> None:
        commands = [
            f"rg --pre /bin/sh needle {self.folder}/a",
            f"git -C {self.folder} grep -O/bin/sh needle -- a",
            f"codex exec -s read-only -C {self.folder} -o {self.folder}/a review",
            f"rsync --remove-source-files {self.folder}/README.md {self.world.root}/copy/",
            f"GH_PAGER=/bin/sh gh pr view 3 -R {self.folder}",
            f"git -C {self.folder} -ccore.pager=/bin/sh log",
            f"GIT_CONFIG_COUNT=1 git -C {self.folder} log",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.world.root), DENY)

    def test_commands_that_hide_where_they_write_are_refused_when_they_name_the_folder(self) -> None:
        git_folder = shlex.quote(str(self.world.git_directory(self.worktree)))
        commands = [
            (f"out={self.folder}/a; echo x > \"$out\"", self.world.root),
            (f"cd \"$(printf {self.folder})\"; touch a", self.world.root),
            (f"GIT_WORK_TREE={self.folder} GIT_DIR={git_folder} git reset --hard", self.world.root),
            ("(cd /tmp); touch a", self.worktree),
        ]

        for command, cwd in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, cwd), DENY)

    def test_removing_a_folder_that_holds_the_worktree_or_its_record_is_refused(self) -> None:
        worktrees = shlex.quote(str(self.world.original / ".claude" / "worktrees"))
        git_folder = shlex.quote(str(self.world.git_directory(self.worktree)))
        commands = [
            f"rm -rf {worktrees}",
            f"rm {worktrees}/*/README.md",
            f"rm -rf {git_folder}",
            f"mv {worktrees} /tmp/elsewhere",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.world.root), DENY)

    def test_a_new_terminal_tab_is_judged_in_its_folder(self) -> None:
        tool_input = {"command": "echo x > a", "cwd": str(self.worktree)}

        self.assertEqual(self.world.decide(OTHER, "mcp__terminal__run_in_terminal", tool_input, self.world.root), DENY)

    def test_help_text_never_reaches_the_hook_output(self) -> None:
        command = f"{FOLDER_OWNER_COMMAND} --help; touch {self.folder}/a"

        self.assertEqual(self.world.bash(OTHER, command, self.world.root), DENY)

    def test_a_call_that_names_its_own_session_is_refused(self) -> None:
        commands = [
            f"CLAUDE_CODE_HOST_SESSION_ID={OWNER.app} {FOLDER_OWNER_COMMAND} hand-over --to {OTHER.app}",
            f"CLAUDE_CODE_SESSION_ID={OWNER.cli} python3 ~/.claude/scripts/claude-commit.py 'Fix: x' README.md",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.worktree), DENY)

    def test_reads_that_were_refused_now_pass(self) -> None:
        commands = [
            "/bin/cat README.md",
            "git branch --show-current",
            "codex exec -s read-only -c model_reasoning_effort=xhigh review",
            "git -c core.quotepath=false status",
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.world.bash(OTHER, command, self.worktree), PASS)

    def test_a_worktree_add_naming_an_existing_folder_is_no_creation(self) -> None:
        """Only a folder missing before the call counts as made by it; an existing one follows the claim rules."""
        worktree = self.world.add_worktree("unowned")
        tool_input = {"command": f"true || git worktree add {shlex.quote(str(worktree))}"}

        self.world.write(worktree, "left.txt", "left behind\n")
        before = self.world.run_guard(OTHER, "Bash", tool_input, self.world.original)

        self.world.run_guard(OTHER, "Bash", tool_input, self.world.original, POST_TOOL_USE, {})
        self.assertEqual(json.loads(before.stdout)["hookSpecificOutput"]["permissionDecision"], ASK)
        self.assertNotEqual(self.world.read_owner(worktree)["method"], "created")

    def test_a_write_approved_after_another_session_claimed_the_folder_leaves_a_gap(self) -> None:
        worktree = self.world.add_worktree("unowned")
        write = {"file_path": str(worktree / "x.txt")}

        self.world.write(worktree, "left.txt", "left behind\n")
        first = self.world.run_guard(OTHER, "Write", write, self.world.root)

        self.world.run_guard(THIRD, "Write", write, self.world.root)
        self.world.run_guard(THIRD, "Write", write, self.world.root, POST_TOOL_USE, {})
        self.world.run_guard(OTHER, "Write", write, self.world.root, POST_TOOL_USE, {})
        gaps = (self.world.git_directory(worktree) / "claude-owner-gaps.jsonl").read_text(encoding=ENCODING)

        self.assertEqual(json.loads(first.stdout)["hookSpecificOutput"]["permissionDecision"], ASK)
        self.assertEqual(self.world.read_owner_cli(worktree), THIRD.cli)
        self.assertIn(OTHER.app, gaps)


class GuardFailureTest(unittest.TestCase):
    """Commands the guard once failed on, letting them through unjudged: words longer than a file name may be,
    link loops, and one call judged before and after it ran."""

    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree = self.world.add_worktree_as(OWNER, "task-c")

    def test_inline_programs_longer_than_a_file_name_neither_fail_nor_leave_gaps(self) -> None:
        programs = [shlex.quote(LONG_NAME_PROGRAM), shlex.quote(LONG_PATH_PROGRAM)]
        runners = ["python3 -c", "node -e", "docker run --rm image bash -e -c"]

        for runner in runners:
            for program in programs:
                with self.subTest(runner=runner, length=len(program)):
                    self.assertEqual(self.world.bash(OWNER, f"{runner} {program}", self.worktree), PASS)
        self.assertFalse((self.world.git_directory(self.worktree) / GAPS_FILE_NAME).exists())

    def test_another_sessions_long_inline_program_that_names_the_folder_is_refused(self) -> None:
        writer = f"open({str(self.worktree / 'notes.txt')!r}, 'w')\n"
        command = f"python3 -c {shlex.quote(LONG_NAME_PROGRAM + writer)}"

        self.assertEqual(self.world.bash(OTHER, command, self.world.root), DENY)

    def test_a_link_loop_neither_fails_nor_hides_a_write(self) -> None:
        loop = self.world.root / "loop"

        (self.world.root / "loop-a").symlink_to(self.world.root / "loop-b")
        (self.world.root / "loop-b").symlink_to(self.world.root / "loop-a")
        loop.symlink_to(self.world.root / "loop-a")
        self.assertEqual(self.world.bash(OWNER, f"touch {loop}/x", self.worktree), PASS)
        self.assertEqual(self.world.bash(OTHER, f"touch {loop}/x {self.worktree}/x", self.world.root), DENY)

    def test_removing_a_name_too_long_for_a_file_does_not_fail(self) -> None:
        self.assertEqual(self.world.bash(OWNER, f"rm -rf {'y' * 300}/z", self.worktree), PASS)

    def test_a_failure_after_a_call_shows_a_hook_error_and_leaves_no_gap(self) -> None:
        self.world.owner_file(self.worktree).write_text("{broken", encoding=ENCODING)
        command = {"command": f"echo x > {self.worktree / 'x.txt'}"}
        result = self.world.run_guard(OTHER, "Bash", command, self.world.root, POST_TOOL_USE, {})

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("OwnerFileError", result.stderr)
        self.assertFalse((self.world.git_directory(self.worktree) / GAPS_FILE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
