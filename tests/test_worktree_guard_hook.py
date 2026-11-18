#!/usr/bin/env python3
"""Tests for scripts/worktree-guard-hook.py: the original checkout of a worktree repository stays read-only."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from workflow_world import BASE, SCRIPTS_DIRECTORY, WorkflowWorld

HOOK_SCRIPT = SCRIPTS_DIRECTORY / "worktree-guard-hook.py"
SHELL_GUARD_SCRIPT = SCRIPTS_DIRECTORY / "shell-guard-hook.py"
POLICY = {"mode": "pull-request", "base": BASE, "requiredChecks": ["checks"]}
DENY = "deny"
PASS = "pass"
WRAPPER = "python3 ~/.claude/scripts/claude-commit.py 'Fix: x' a.txt"


class WorktreeGuardHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.addCleanup(self.world.close)
        self.original = self.world.original
        self.worktree = self.world.add_worktree("task-a")
        self.config_directory = self.world.root / "claude"
        self.config_directory.mkdir()
        (self.original / ".git" / "info").mkdir(exist_ok=True)
        (self.original / ".git" / "info" / "exclude").write_text("claude-plans/\n", encoding="utf-8")

    def run_hook(self, tool_name: str, tool_input: dict[str, str], cwd: Path) -> str:
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input, "cwd": str(cwd)}
        environment = {**self.world.environment, "CLAUDE_CONFIG_DIR": str(self.config_directory)}
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.stderr, "")
        if result.stdout.strip():
            return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        else:
            return PASS

    def bash(self, command: str, cwd: Path) -> str:
        return self.run_hook("Bash", {"command": command}, cwd)

    def test_file_tools_are_refused_in_the_original_checkout_only(self) -> None:
        cases = [
            ("Edit", self.original / "README.md", DENY),
            ("Write", self.original / "new" / "file.txt", DENY),
            ("NotebookEdit", self.original / "book.ipynb", DENY),
            ("Write", self.original / "claude-plans" / "plan-x.md", PASS),
            ("Edit", self.worktree / "README.md", PASS),
            ("Write", self.worktree / "new.txt", PASS),
        ]

        for tool, path, expected in cases:
            with self.subTest(tool=tool, path=str(path)):
                key = "notebook_path" if tool == "NotebookEdit" else "file_path"
                self.assertEqual(self.run_hook(tool, {key: str(path)}, self.original), expected)

    def test_shell_writes_and_writing_git_commands_are_refused_in_the_original_checkout(self) -> None:
        cases = [
            ("echo x > README.md", DENY),
            ("sed -i '' s/seed/x/ README.md", DENY),
            ("echo x | tee new.txt", DENY),
            ("git commit -m x", DENY),
            ("git pull", DENY),
            ("git checkout -b other", DENY),
            ("git stash", DENY),
            (WRAPPER, DENY),
            (f"git -C {self.original} reset --hard", DENY),
            ("cat README.md", PASS),
            ("git status", PASS),
            ("git fetch origin", PASS),
            ("git log --oneline -3", PASS),
            ("git worktree add -b task-b .claude/worktrees/task-b origin/main", PASS),
            ("echo note > claude-plans/notes.md", PASS),
            ("rm README.md", DENY),
            ("mv README.md OTHER.md", DENY),
            ("touch new.txt", DENY),
            ("cp /etc/hosts hosts.txt", DENY),
            ("cp README.md /tmp/readme-copy.txt", PASS),
        ]

        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, self.original), expected)

    def test_the_same_commands_pass_in_a_task_worktree(self) -> None:
        for command in ("echo x > a.txt", "git commit -m x", "git pull", WRAPPER):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, self.worktree), PASS)

    def test_worktree_removal_goes_through_the_cleanup_tool_from_any_folder(self) -> None:
        for cwd in (self.original, self.worktree):
            with self.subTest(cwd=str(cwd)):
                self.assertEqual(self.bash(f"git worktree remove --force {self.worktree}", cwd), DENY)
        self.assertEqual(self.bash("git worktree list", self.original), PASS)

    def test_a_repository_without_a_worktree_workflow_is_left_alone(self) -> None:
        plain = WorkflowWorld(None)
        self.addCleanup(plain.close)

        self.assertEqual(self.run_hook("Edit", {"file_path": str(plain.original / "README.md")}, plain.original), PASS)
        self.assertEqual(self.bash("git commit -m x", plain.original), PASS)
        self.assertEqual(self.bash("git worktree remove x", plain.original), PASS)

    def test_a_malformed_command_lets_the_call_through(self) -> None:
        self.assertEqual(self.bash("echo 'unclosed", self.original), PASS)

    def test_the_shell_guard_reads_the_original_checkouts_policy_not_the_task_branchs(self) -> None:
        (self.worktree / ".agent-workflow.json").unlink()
        payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 1 --squash"}, "cwd": str(self.worktree)}
        environment = {**self.world.environment, "CLAUDE_CONFIG_DIR": str(self.config_directory)}
        result = subprocess.run(
            [sys.executable, str(SHELL_GUARD_SCRIPT)],
            input=json.dumps(payload),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], DENY)


if __name__ == "__main__":
    unittest.main()
