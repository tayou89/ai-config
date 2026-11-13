#!/usr/bin/env python3
"""Tests for scripts/local-integrate.py in disposable repositories without a remote."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from workflow_world import BASE, WorkflowWorld

PASSING_CHECK = [sys.executable, "-c", "import pathlib; assert not pathlib.Path('broken.txt').exists()"]
SLOW_CHECK = [sys.executable, "-c", "import time; time.sleep(30)"]
POLICY = {"mode": "local-integration", "base": BASE, "checkCommands": [PASSING_CHECK]}
EXIT_DONE = 0
EXIT_NOT_READY = 2
EXIT_NO_POLICY = 3


class LocalIntegrateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.addCleanup(self.world.close)
        self.original = self.world.original

    def make_task(self, branch: str, name: str, text: str = "") -> Path:
        worktree = self.world.add_worktree(branch)

        self.world.commit_file(worktree, name, text or f"{name}\n", f"Feat: add {name}")
        return worktree

    def integrate(self, worktree: Path) -> subprocess.CompletedProcess[str]:
        return self.world.run_script("local-integrate", [], worktree)

    def base_tip(self) -> str:
        return self.world.git(self.original, "rev-parse", BASE)

    def set_checks(self, commands: list[list[str]]) -> None:
        self.world.write(self.original, ".agent-workflow.json", json.dumps({**POLICY, "checkCommands": commands}))
        self.world.git(self.original, "commit", "-q", "-am", "Chore: change the checks")

    def test_integrates_a_passing_branch_with_a_merge_commit(self) -> None:
        worktree = self.make_task("task-a", "a.txt")
        task_tip = self.world.git(worktree, "rev-parse", "HEAD")
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertTrue((self.original / "a.txt").exists())
        self.assertEqual(self.world.git(self.original, "rev-parse", "HEAD^2"), task_tip)
        self.assertEqual(self.world.git(self.original, "status", "--porcelain", "--untracked-files=no"), "")

    def test_failed_check_leaves_the_base_untouched(self) -> None:
        worktree = self.make_task("task-a", "broken.txt")
        before = self.base_tip()
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("failed with exit code", result.stderr)
        self.assertEqual(self.base_tip(), before)
        self.assertEqual(self.list_integration_folders(), [])

    def test_conflict_leaves_the_base_untouched_and_names_the_file(self) -> None:
        worktree = self.make_task("task-a", "README.md", "task version\n")
        self.world.commit_file(self.original, "README.md", "base version\n", "Fix: change the readme")
        before = self.base_tip()
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("conflicts with main in README.md", result.stderr)
        self.assertEqual(self.base_tip(), before)
        self.assertEqual(self.list_integration_folders(), [])

    def test_checks_run_on_the_merged_result_not_on_the_branch_alone(self) -> None:
        worktree = self.make_task("task-a", "a.txt")
        self.world.commit_file(self.original, "broken.txt", "x\n", "Feat: add a file the check rejects")
        before = self.base_tip()
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertEqual(self.base_tip(), before)

    def test_concurrent_integrations_both_land_the_second_merged_onto_the_first(self) -> None:
        first = self.make_task("task-a", "a.txt")
        second = self.make_task("task-b", "b.txt")
        processes = [self.world.start_script("local-integrate", [], worktree) for worktree in (first, second)]
        outputs = [process.communicate(timeout=120) for process in processes]
        merges = self.world.git(self.original, "rev-list", "--merges", BASE).splitlines()

        self.assertEqual([process.returncode for process in processes], [EXIT_DONE, EXIT_DONE], outputs)
        self.assertTrue((self.original / "a.txt").exists())
        self.assertTrue((self.original / "b.txt").exists())
        self.assertEqual(len(merges), 2)
        self.assertEqual(self.world.git(self.original, "rev-parse", f"{merges[0]}^1"), merges[1])

    def test_base_that_moves_while_the_checks_run_is_not_overwritten(self) -> None:
        move_base = ["git", "-C", str(self.original), "commit", "-q", "--allow-empty", "-m", "Chore: move"]
        mover = f"import subprocess; subprocess.run({move_base!r}, check=True)"
        self.set_checks([[sys.executable, "-c", mover]])
        worktree = self.make_task("task-a", "a.txt")
        result = self.integrate(worktree)
        tip_message = self.world.git(self.original, "log", "-1", "--format=%s")

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("changed while the checks ran", result.stderr)
        self.assertEqual(tip_message, "Chore: move")
        self.assertEqual(self.list_integration_folders(), [])

    def test_missing_branch_and_missing_check_program_are_reported_not_crashed(self) -> None:
        worktree = self.make_task("task-a", "a.txt")
        missing_branch = self.world.run_script("local-integrate", ["no-such-branch"], worktree)
        self.set_checks([["no-such-program-for-checks"]])
        missing_program = self.integrate(worktree)

        self.assertEqual(missing_branch.returncode, EXIT_NOT_READY)
        self.assertIn("no branch 'no-such-branch'", missing_branch.stderr)
        self.assertEqual(missing_program.returncode, EXIT_NOT_READY)
        self.assertIn("could not start", missing_program.stderr)

    def test_interrupted_integration_leaves_the_base_and_the_next_run_cleans_up(self) -> None:
        self.set_checks([SLOW_CHECK])
        worktree = self.make_task("task-a", "a.txt")
        before = self.base_tip()
        process = self.world.start_script("local-integrate", [], worktree)
        self.wait_for_integration_folder()
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=30)
        after_kill = self.base_tip()
        leftovers = self.list_integration_folders()
        self.set_checks([PASSING_CHECK])
        result = self.integrate(worktree)

        self.assertEqual(after_kill, before)
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertIn("stopped before it finished", result.stdout)
        self.assertEqual(self.list_integration_folders(), [])

    def test_refuses_uncommitted_work_in_the_task_worktree(self) -> None:
        worktree = self.make_task("task-a", "a.txt")
        self.world.write(worktree, "draft.txt", "unsaved\n")
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("uncommitted or untracked", result.stderr)

    def test_refuses_a_dirty_original_checkout(self) -> None:
        worktree = self.make_task("task-a", "a.txt")
        self.world.write(self.original, "README.md", "someone's edit\n")
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("must stay clean", result.stderr)

    def test_refuses_a_repository_without_the_local_integration_workflow(self) -> None:
        self.world.write(
            self.original, ".agent-workflow.json", '{"mode": "pull-request", "base": "main", "requiredChecks": ["c"]}'
        )
        self.world.git(self.original, "commit", "-q", "-am", "Chore: switch to pull requests")
        worktree = self.make_task("task-a", "a.txt")
        result = self.integrate(worktree)

        self.assertEqual(result.returncode, EXIT_NO_POLICY)

    def list_integration_folders(self) -> list[str]:
        folder = self.original / ".git" / "agent-integration"

        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    def wait_for_integration_folder(self) -> None:
        deadline = time.monotonic() + 30

        while not self.list_integration_folders() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
