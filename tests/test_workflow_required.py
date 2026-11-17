"""Tests for the commit wrapper's refusal without a recorded workflow, and for the direct and team modes."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from workflow_world import BASE, ENCODING, POLICY_FILE, SCRIPTS_DIRECTORY, WorkflowWorld, load_script

COMMIT_WRAPPER = SCRIPTS_DIRECTORY / "claude-commit.py"
GUARD_HOOK = SCRIPTS_DIRECTORY / "worktree-guard-hook.py"
EXIT_GIT_ERROR = 1
EXIT_NO_WORKFLOW = 11
DIRECT_POLICY = {"mode": "direct", "base": BASE}
TEAM_POLICY = {"mode": "team", "base": "dev"}
PULL_REQUEST_POLICY = {"mode": "pull-request", "base": BASE, "requiredChecks": ["checks"]}
MESSAGE = "Fix: record a note for the workflow test\n\n- first reason\n- second reason\n- third reason"
SUBJECT = MESSAGE.splitlines()[0]
NOTE_FILE = "note.txt"
SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
EXCLUDE_FILE = Path(".git") / "info" / "exclude"
DENY = "deny"
PASS = "pass"


class WorkflowRequiredTest(unittest.TestCase):
    def test_commits_nothing_without_a_workflow(self) -> None:
        world = self._make_world(None)
        head = world.git(world.original, "rev-parse", "HEAD")
        result = self._run_wrapper(world, world.original)

        self.assertEqual(result.returncode, EXIT_NO_WORKFLOW, result.stdout + result.stderr)
        self.assertIn("records no Git workflow", result.stdout + result.stderr)
        self.assertEqual(world.git(world.original, "rev-parse", "HEAD"), head)
        self.assertEqual(world.git(world.original, "diff", "--cached", "--name-only"), "")

    def test_commits_nothing_when_the_workflow_cannot_be_read(self) -> None:
        cases = [(b"{not json", "not valid JSON"), (b"\xff\xfe", "cannot be read")]

        for content, reason in cases:
            with self.subTest(reason=reason):
                world = self._make_world(None)

                (world.original / POLICY_FILE).write_bytes(content)
                result = self._run_wrapper(world, world.original)
                self.assertEqual(result.returncode, EXIT_NO_WORKFLOW, result.stdout + result.stderr)
                self.assertIn(reason, result.stdout + result.stderr)

    def test_direct_and_team_workflows_commit(self) -> None:
        worlds = {"direct": self._make_world(DIRECT_POLICY), "team": self._make_team_world()}

        for name, world in worlds.items():
            with self.subTest(workflow=name):
                result = self._run_wrapper(world, world.original)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(world.git(world.original, "log", "-1", "--format=%s"), SUBJECT)

    def test_worktree_of_a_team_repository_reads_the_original_checkout_copy(self) -> None:
        world = self._make_team_world()
        worktree = world.add_worktree("task-a")
        result = self._run_wrapper(world, worktree)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(world.git(worktree, "log", "-1", "--format=%s"), SUBJECT)

    def test_folder_outside_a_repository_is_reported_as_such(self) -> None:
        world = self._make_world(None)
        folder = world.root / "plain"

        folder.mkdir()
        result = self._run_wrapper(world, folder)
        self.assertEqual(result.returncode, EXIT_GIT_ERROR, result.stdout + result.stderr)
        self.assertIn("not inside a Git repository", result.stdout + result.stderr)

    def test_worktree_guard_leaves_checkouts_without_worktrees_writable(self) -> None:
        cases = [
            ("direct", self._make_world(DIRECT_POLICY), PASS),
            ("team", self._make_team_world(), PASS),
            ("pull-request", self._make_world(PULL_REQUEST_POLICY), DENY),
        ]

        for name, world, expected in cases:
            with self.subTest(workflow=name):
                self.assertEqual(self._run_guard_edit(world, world.original / "README.md"), expected)

    def test_cleanup_refuses_workflows_without_worktrees(self) -> None:
        world = self._make_world(DIRECT_POLICY)
        cleanup = load_script("worktree-cleanup")

        with self.assertRaisesRegex(cleanup.PolicyError, "no worktrees to clean up"):
            cleanup.read_cleanup_policy(world.original)

    def test_only_pull_request_and_local_integration_are_worktree_modes(self) -> None:
        policy = load_script("workflow_policy")
        expected = {"pull-request": True, "local-integration": True, "direct": False, "team": False}

        self.assertEqual({mode.value: mode.is_worktree_mode for mode in policy.WorkflowMode}, expected)

    def _make_world(self, policy: dict[str, Any] | None) -> WorkflowWorld:
        world = WorkflowWorld(policy)

        self.addCleanup(world.close)
        return world

    def _make_team_world(self) -> WorkflowWorld:
        """A team's repository keeps the file out of Git, so only this checkout sees it."""
        world = self._make_world(None)
        exclude = world.original / EXCLUDE_FILE

        world.write(world.original, POLICY_FILE, json.dumps(TEAM_POLICY))
        exclude.write_text(f"{exclude.read_text(encoding=ENCODING)}{POLICY_FILE}\n", encoding=ENCODING)
        return world

    def _make_environment(self, world: WorkflowWorld) -> dict[str, str]:
        config_directory = world.root / "claude"
        environment = {**world.environment, CONFIG_DIRECTORY_VARIABLE: str(config_directory)}

        config_directory.mkdir(exist_ok=True)
        environment.pop(SESSION_VARIABLE, None)
        return environment

    def _run_wrapper(self, world: WorkflowWorld, checkout: Path) -> subprocess.CompletedProcess[str]:
        world.write(checkout, NOTE_FILE, "note\n")
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), MESSAGE, NOTE_FILE],
            env=self._make_environment(world),
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_guard_edit(self, world: WorkflowWorld, path: Path) -> str:
        tool_input = {"file_path": str(path)}
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": tool_input,
            "cwd": str(path.parent),
        }
        result = subprocess.run(
            [sys.executable, str(GUARD_HOOK)],
            input=json.dumps(payload),
            env=self._make_environment(world),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.stderr, "")
        if result.stdout.strip():
            return str(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"])
        else:
            return PASS


if __name__ == "__main__":
    unittest.main()
