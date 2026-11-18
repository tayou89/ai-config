"""Tests for how the commit wrapper counts a merge commit toward its large-change review."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest

from workflow_world import BASE, ENCODING, SCRIPTS_DIRECTORY, WorkflowWorld

COMMIT_WRAPPER = SCRIPTS_DIRECTORY / "claude-commit.py"
POLICY = {"mode": "pull-request", "base": BASE, "requiredChecks": ["checks"]}
MERGE_MESSAGE = "Merge: bring the base into the task branch\n\n- first reason\n- second reason\n- third reason"
PICK_MESSAGE = "Feat: take one change from another branch\n\n- first reason\n- second reason\n- third reason"
SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
EXIT_REVIEW_MISSING = 7
BASE_LINES = 300
RESOLUTION_LINES = 250
COUNT_PATTERN = re.compile(r"Large change reviewed\s*\|\s*OK \((\d+) code lines\)")


def make_lines(prefix: str, count: int) -> str:
    return "".join(f"{prefix} {number}\n" for number in range(count))


class MergeCommitCountTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.addCleanup(self.world.close)
        self.world.write(self.world.original, "shared.py", "first\nsecond\nthird\n")
        self.world.git(self.world.original, "add", "shared.py")
        self.world.git(self.world.original, "commit", "-q", "-m", "Chore: add the shared file")
        self.world.git(self.world.original, "push", "-q", "origin", BASE)
        self.worktree = self.world.add_worktree("task-a")
        self.world.commit_file(self.worktree, "task.py", make_lines("task", 30), "Feat: add the task file")

    def test_a_clean_merge_of_a_large_base_needs_no_review(self) -> None:
        self.world.push_base_commit("base.py", make_lines("base", BASE_LINES))
        self.merge_base()
        result = self.run_wrapper("--no-stage", MERGE_MESSAGE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read_counted_lines(result), 0)
        self.assertEqual(len(self.world.git(self.worktree, "log", "-1", "--format=%P").split()), 2)

    def test_a_large_conflict_resolution_still_needs_review(self) -> None:
        self.world.push_base_commit("shared.py", "first\nthe base's second\nthird\n")
        self.world.commit_file(self.worktree, "shared.py", "first\nthe task's second\nthird\n", "Feat: edit shared")
        self.merge_base()
        self.world.write(self.worktree, "shared.py", make_lines("resolved", RESOLUTION_LINES))
        self.world.git(self.worktree, "add", "shared.py")
        refused = self.run_wrapper("--no-stage", MERGE_MESSAGE)
        reviewed = self.run_wrapper("--no-stage", "--reviewed", MERGE_MESSAGE)
        remerge = self.world.git(self.worktree, "show", "--remerge-diff", "--numstat", "--format=", "HEAD")

        self.assertEqual(refused.returncode, EXIT_REVIEW_MISSING, refused.stdout + refused.stderr)
        self.assertEqual(reviewed.returncode, 0, reviewed.stdout + reviewed.stderr)
        self.assertEqual(self.read_counted_lines(reviewed), sum_numstat(remerge))

    def test_a_merge_of_several_branches_counts_against_head(self) -> None:
        self.world.push_base_commit("base.py", make_lines("base", BASE_LINES))
        self.world.git(self.world.original, "checkout", "-q", "-b", "side")
        self.world.commit_file(self.world.original, "side.py", "side\n", "Feat: add the side file")
        self.world.git(self.world.original, "push", "-q", "origin", "side")
        self.world.git(self.worktree, "fetch", "-q", "origin")
        self.world.git(self.worktree, "merge", "--no-commit", "--no-ff", f"origin/{BASE}", "origin/side")
        result = self.run_wrapper("--no-stage", MERGE_MESSAGE)

        self.assertEqual(result.returncode, EXIT_REVIEW_MISSING, result.stdout + result.stderr)

    def test_a_cherry_pick_counts_against_head(self) -> None:
        base_commit = self.world.push_base_commit("base.py", make_lines("base", BASE_LINES))
        self.world.git(self.worktree, "fetch", "-q", "origin")
        self.world.git(self.worktree, "cherry-pick", "--no-commit", base_commit)
        result = self.run_wrapper("--no-stage", PICK_MESSAGE)

        self.assertEqual(result.returncode, EXIT_REVIEW_MISSING, result.stdout + result.stderr)

    def merge_base(self) -> None:
        self.world.git(self.worktree, "fetch", "-q", "origin")
        subprocess.run(
            ["git", "-C", str(self.worktree), "merge", "--no-commit", "--no-ff", f"origin/{BASE}"],
            env=self.world.environment,
            capture_output=True,
            check=False,
        )

    def run_wrapper(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        config_directory = self.world.root / "claude"
        environment = {**self.world.environment, CONFIG_DIRECTORY_VARIABLE: str(config_directory)}

        config_directory.mkdir(exist_ok=True)
        environment.pop(SESSION_VARIABLE, None)
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            env=environment,
            cwd=self.worktree,
            capture_output=True,
            text=True,
            encoding=ENCODING,
            check=False,
        )

    def read_counted_lines(self, result: subprocess.CompletedProcess[str]) -> int:
        match = COUNT_PATTERN.search(result.stdout)

        self.assertIsNotNone(match, result.stdout)
        return int(match.group(1)) if match else -1


def sum_numstat(numstat: str) -> int:
    return sum(int(added) + int(deleted) for added, deleted, _ in (line.split("\t") for line in numstat.splitlines()))


if __name__ == "__main__":
    unittest.main()
