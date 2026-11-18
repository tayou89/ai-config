#!/usr/bin/env python3
"""Tests for scripts/worktree-cleanup.py: it removes a task's worktree only once its work is preserved."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from workflow_world import BASE, WorkflowWorld

PULL_REQUEST_POLICY = {
    "mode": "pull-request",
    "base": BASE,
    "requiredChecks": ["checks"],
    "cleanupIgnored": ["node_modules"],
}
LOCAL_POLICY = {"mode": "local-integration", "base": BASE, "checkCommands": [[sys.executable, "-c", "pass"]]}
EXIT_DONE = 0
EXIT_NOT_READY = 2


class CleanupWorld:
    def __init__(self, policy: dict[str, Any]):
        self.world = WorkflowWorld(policy)
        exclude = self.world.original / ".git" / "info" / "exclude"

        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text("node_modules/\nsecret.local\n", encoding="utf-8")

    def make_task(self, number: int, branch: str) -> tuple[Path, str]:
        worktree = self.world.add_worktree(branch)
        tip = self.world.commit_file(worktree, f"{branch}.txt", "work\n", f"Feat: add {branch}")

        if number:
            self.world.open_pull_request(number, branch, worktree)
        return worktree, tip

    def cleanup(self, worktree: Path, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.world.run_script("worktree-cleanup", [str(worktree), *arguments], cwd or self.world.root)

    def has_branch(self, branch: str) -> bool:
        return bool(self.world.git(self.world.original, "branch", "--list", branch))

    def has_remote_branch(self, branch: str) -> bool:
        return bool(self.world.git(self.world.remote, "branch", "--list", branch))


class PullRequestCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cleanup_world = CleanupWorld(PULL_REQUEST_POLICY)
        self.world = self.cleanup_world.world
        self.addCleanup(self.world.close)

    def merge(self, worktree: Path) -> None:
        result = self.world.run_script("pr-merge", [], worktree)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_squash_merged_branch_is_removed_on_the_pull_request_record_not_on_ancestry(self) -> None:
        worktree, tip = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        is_ancestor = subprocess.run(
            ["git", "-C", str(self.world.original), "merge-base", "--is-ancestor", tip, BASE],
            env=self.world.environment,
            check=False,
        )
        result = self.cleanup_world.cleanup(worktree)

        self.assertNotEqual(is_ancestor.returncode, 0)
        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertFalse(self.cleanup_world.has_branch("task-a"))
        self.assertFalse(self.cleanup_world.has_remote_branch("task-a"))

    def test_remote_branch_that_moved_after_the_merge_is_kept(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        helper = self.world.root / "mover"
        self.world.git(self.world.root, "clone", "-q", "--branch", "task-a", str(self.world.remote), str(helper))
        moved = self.world.commit_file(helper, "later.txt", "later\n", "Feat: add work after the merge")
        self.world.git(helper, "push", "-q", "origin", "task-a")
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertIn("Kept task-a on origin", result.stdout)
        self.assertEqual(self.world.git(self.world.remote, "rev-parse", "refs/heads/task-a"), moved)

    def test_a_branch_whose_name_ends_like_another_keeps_the_other(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        self.world.git(self.world.original, "push", "-q", "origin", f"{BASE}:refs/heads/team/task-a")
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertTrue(self.cleanup_world.has_remote_branch("team/task-a"))

    def test_commit_after_the_merged_head_keeps_the_worktree(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        self.world.commit_file(worktree, "later.txt", "later\n", "Feat: add a later change")
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("no merged pull request", result.stderr)
        self.assertTrue(worktree.exists())
        self.assertTrue(self.cleanup_world.has_branch("task-a"))

    def test_open_pull_request_keeps_the_worktree(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertTrue(worktree.exists())

    def test_untracked_and_unlisted_ignored_files_keep_the_worktree(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)

        for name, words in (("draft.txt", "untracked"), ("secret.local", "cleanupIgnored")):
            with self.subTest(name):
                self.world.write(worktree, name, "keep me\n")
                result = self.cleanup_world.cleanup(worktree)
                (worktree / name).unlink()

                self.assertEqual(result.returncode, EXIT_NOT_READY)
                self.assertIn(words, result.stderr)
                self.assertTrue(worktree.exists())

    def test_regenerable_ignored_files_do_not_block(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        self.world.write(worktree, "node_modules/package/index.js", "module.exports = 1\n")
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertFalse(worktree.exists())

    def test_a_process_working_in_the_worktree_keeps_it(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        sleeper = subprocess.Popen(["sleep", "30"], cwd=worktree)
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn(str(sleeper.pid), result.stderr)
        self.assertTrue(worktree.exists())

    def test_the_calling_session_inside_the_worktree_does_not_block_itself(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.merge(worktree)
        result = self.cleanup_world.cleanup(worktree, cwd=worktree)

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)

    def test_abandoned_task_removes_the_worktree_and_keeps_its_branches(self) -> None:
        worktree, tip = self.cleanup_world.make_task(1, "task-a")
        result = self.cleanup_world.cleanup(worktree, "--abandoned")

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertEqual(self.world.git(self.world.original, "rev-parse", "task-a"), tip)
        self.assertTrue(self.cleanup_world.has_remote_branch("task-a"))

    def test_abandoned_task_with_uncommitted_work_is_kept(self) -> None:
        worktree, _ = self.cleanup_world.make_task(1, "task-a")
        self.world.write(worktree, "task-a.txt", "changed but not committed\n")
        result = self.cleanup_world.cleanup(worktree, "--abandoned")

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertTrue(worktree.exists())

    def test_the_original_checkout_is_never_removed(self) -> None:
        result = self.cleanup_world.cleanup(self.world.original, "--abandoned")

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("original checkout", result.stderr)
        self.assertTrue(self.world.original.exists())


class LocalIntegrationCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cleanup_world = CleanupWorld(LOCAL_POLICY)
        self.world = self.cleanup_world.world
        self.addCleanup(self.world.close)

    def test_integrated_branch_is_removed_on_ancestry(self) -> None:
        worktree, _ = self.cleanup_world.make_task(0, "task-a")
        integrated = self.world.run_script("local-integrate", [], worktree)
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(integrated.returncode, EXIT_DONE, integrated.stderr)
        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertFalse(worktree.exists())
        self.assertFalse(self.cleanup_world.has_branch("task-a"))

    def test_branch_not_yet_integrated_is_kept(self) -> None:
        worktree, _ = self.cleanup_world.make_task(0, "task-a")
        result = self.cleanup_world.cleanup(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("is not in main", result.stderr)
        self.assertTrue(worktree.exists())


if __name__ == "__main__":
    unittest.main()
