#!/usr/bin/env python3
"""Tests for scripts/worktree-cleanup.py: it removes a task's worktree only once its work is preserved."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

from workflow_world import BASE, WorkflowWorld, load_script

PULL_REQUEST_POLICY = {
    "mode": "pull-request",
    "base": BASE,
    "requiredChecks": ["checks"],
    "cleanupIgnored": ["node_modules"],
}
LOCAL_POLICY = {"mode": "local-integration", "base": BASE, "checkCommands": [[sys.executable, "-c", "pass"]]}
EXIT_DONE = 0
EXIT_NOT_READY = 2
DEFAULT_EXCLUDES = "node_modules/\nsecret.local\n"
MATCHING_PATTERNS = (
    "build",
    "**/node_modules",
    "app/dist/",
    "**/.DS_Store",
    "/cache/",
    "app/*.timestamp-*",
    "link",
    "\\#hash/",
)
MATCHING_POLICY = {**PULL_REQUEST_POLICY, "cleanupIgnored": MATCHING_PATTERNS}
MATCHING_EXCLUDES = "build\nBuild\nnode_modules\ndist\n.DS_Store\ncache\n*.timestamp-*\n*.local\nlink\n\\#hash\n"
REGENERABLE_FILES = (
    "build/out.txt",
    "node_modules/package/index.txt",
    "app/node_modules/package/index.txt",
    "app/dist/bundle.txt",
    ".DS_Store",
    "app/.DS_Store",
    "cache/data.txt",
    "app/vite.config.ts.timestamp-1.txt",
    "#hash/notes.txt",
)
KEPT_FILES = (
    "app/src/build/notes.local",
    "Build/out.txt",
    "dist/bundle.txt",
    "app/dist",
    "app/sub/vite.config.ts.timestamp-1.txt",
)
OUTSIDE_FILE = "outside/keep.txt"
LINKED_NAMES = ("link", "node_modules/outside", "tools/node_modules")
LINE_BREAK_NAMES = ("\n", "\r\n")
INNER_REPOSITORIES = ("build/dependency", "build/backup.git", "lib", "vendor/linked", "vendor/symlinked")
REFUSED_PATTERNS = ("!x", "#x", "x ", "x\t", "x\\\\ ", "x\\", "x\0", "**", "/", "*/**", "[a-z]*/**", "**/[!a]*")
ACCEPTED_PATTERNS = ("\\#x", "\\!x", "x\\ ", "x\\\\", "\\*", "app/build/", "app/[a-z]*", "**/node_modules")
EXIT_COMMAND_FAILED = 4
LS_FILES = "ls-files"
FAILED_GIT = 128


class _UnreadableEntry:
    """A folder entry whose kind the file system cannot report, as a failing disk would."""

    name = b"build"
    path = b"build"

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        raise OSError(errno.EIO, "input/output error")


class _UnreadableKindScan:
    """Stands in for the iterator os.scandir returns, listing one entry whose kind cannot be read."""

    def __init__(self, folder: bytes):
        self._entries = iter([_UnreadableEntry()])

    def __enter__(self) -> _UnreadableKindScan:
        return self

    def __exit__(self, *details: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[_UnreadableEntry]:
        return self

    def __next__(self) -> _UnreadableEntry:
        return next(self._entries)

    def close(self) -> None:
        self._entries = iter([])


def _scan_unopenable(folder: bytes) -> _UnreadableKindScan:
    raise PermissionError(errno.EACCES, "permission denied", folder)


class CleanupWorld:
    def __init__(self, policy: dict[str, Any], excludes: str = DEFAULT_EXCLUDES):
        self.world = WorkflowWorld(policy)
        exclude = self.world.original / ".git" / "info" / "exclude"

        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text(excludes, encoding="utf-8")

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


class RegenerableMatchingTest(unittest.TestCase):
    """cleanupIgnored holds Git path patterns read from the repository root, matched by Git with letter case kept."""

    def setUp(self) -> None:
        self.cleanup_world = CleanupWorld(MATCHING_POLICY, MATCHING_EXCLUDES)
        self.world = self.cleanup_world.world
        self.addCleanup(self.world.close)
        self.world.git(self.world.original, "config", "core.ignorecase", "true")
        self.worktree, _ = self.cleanup_world.make_task(0, "task-a")
        self.cleanup = load_script("worktree-cleanup")
        self._run_binary_command = self.cleanup.run_binary_command
        self.outside = self.world.root / OUTSIDE_FILE
        self.world.write(self.world.root, OUTSIDE_FILE, "keep me\n")

    def test_every_listed_form_is_removed_and_links_keep_their_targets(self) -> None:
        for name in REGENERABLE_FILES:
            self.world.write(self.worktree, name, "rebuilt\n")
        for name in LINKED_NAMES:
            (self.worktree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.worktree / name).symlink_to(self.outside.parent, target_is_directory=True)
        (self.worktree / "node_modules" / "broken").symlink_to(self.world.root / "missing")
        result = self.cleanup_world.cleanup(self.worktree, "--abandoned")

        self.assertEqual(result.returncode, EXIT_DONE, result.stderr)
        self.assertFalse(self.worktree.exists())
        self.assertTrue(self.outside.exists())

    def test_ignored_files_outside_the_listed_paths_keep_the_worktree(self) -> None:
        for name in KEPT_FILES:
            with self.subTest(name):
                self.world.write(self.worktree, name, "keep me\n")
                kept = self._find_problem(self.cleanup.KEPT_PROBLEM)
                self._remove_top_folder(name)

                self.assertIn(name, kept)

    def test_the_refusal_says_how_patterns_are_read(self) -> None:
        self.world.write(self.worktree, "dist/bundle.txt", "keep me\n")
        kept = self._find_problem(self.cleanup.KEPT_PROBLEM)

        self.assertIn("read from the repository root", kept)

    def test_a_link_is_not_a_folder_for_a_folder_pattern(self) -> None:
        (self.worktree / "app").mkdir()
        (self.worktree / "app" / "dist").symlink_to(self.outside.parent, target_is_directory=True)
        kept = self._find_problem(self.cleanup.KEPT_PROBLEM)

        self.assertIn("app/dist", kept)

    def test_names_differing_only_in_line_breaks_stay_apart(self) -> None:
        (self.worktree / "cache").mkdir()
        for name in LINE_BREAK_NAMES:
            (self.worktree / "cache" / name).write_text("rebuilt\n", encoding="utf-8")
        kept = self._find_problem(self.cleanup.KEPT_PROBLEM, ("cache/?",))

        self.assertIn(repr("cache/\r\n"), kept)
        self.assertNotIn(repr("cache/\n"), kept)

    def test_shown_names_keep_backslashes_line_breaks_and_unreadable_bytes_apart(self) -> None:
        shown = self.cleanup.show_paths([b"cache/\xff", b"cache/\\xff", b"cache/a\nb"])

        self.assertEqual(shown, ", ".join(repr(name) for name in ("cache/\udcff", "cache/\\xff", "cache/a\nb")))

    def test_every_kind_of_inner_repository_keeps_the_worktree(self) -> None:
        self.world.commit_file(self.worktree, "lib/tracked.txt", "tracked\n", "Feat: add a tracked folder")
        self.world.git(self.worktree, "init", "-q", "build/dependency")
        self.world.git(self.worktree, "init", "-q", "--bare", "build/backup.git")
        self.world.git(self.worktree, "init", "-q", "lib")
        self.world.write(self.worktree, "vendor/linked/.git", "gitdir: /nowhere\n")
        (self.worktree / "vendor" / "symlinked").mkdir()
        (self.worktree / "vendor" / "symlinked" / ".git").symlink_to(self.outside.parent, target_is_directory=True)
        repositories = self._find_problem(self.cleanup.REPOSITORY_PROBLEM)

        for folder in INNER_REPOSITORIES:
            with self.subTest(folder):
                self.assertIn(folder, repositories)

    def test_the_cleanup_command_keeps_inner_repositories_and_their_commits(self) -> None:
        self.world.git(self.worktree, "init", "-q", "--bare", "build/backup.git")
        self.world.git(self.worktree, "init", "-q", "build/dependency")
        dependency = self.worktree / "build" / "dependency"
        commit = self.world.commit_file(dependency, "work.txt", "not pushed anywhere\n", "Feat: add unpushed work")
        result = self.cleanup_world.cleanup(self.worktree, "--abandoned")

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn(self.cleanup.REPOSITORY_PROBLEM, result.stderr)
        self.assertTrue((self.worktree / "build" / "backup.git" / "HEAD").exists())
        self.assertEqual(self.world.git(dependency, "rev-parse", "HEAD"), commit)

    def test_without_a_list_every_ignored_file_keeps_the_worktree(self) -> None:
        self.world.write(self.worktree, "cache/data.txt", "rebuilt\n")
        kept = self._find_problem(self.cleanup.KEPT_PROBLEM, ())

        self.assertIn("cache/data.txt", kept)

    def test_a_search_or_git_failure_stops_the_cleanup_before_anything_is_removed(self) -> None:
        failures = {
            "a folder that cannot be opened": mock.patch("os.scandir", _scan_unopenable),
            "an entry whose kind cannot be read": mock.patch("os.scandir", _UnreadableKindScan),
            "git ls-files failing": mock.patch.object(self.cleanup, "run_binary_command", self._fail_ls_files),
        }

        self.world.write(self.worktree, "build/out.txt", "rebuilt\n")
        with contextlib.redirect_stderr(io.StringIO()):
            for name, failure in failures.items():
                with self.subTest(name), failure, mock.patch.object(self.cleanup, "remove") as remove:
                    exit_code = self.cleanup.main([str(self.worktree), "--abandoned"])

                    self.assertEqual(exit_code, EXIT_COMMAND_FAILED)
                    remove.assert_not_called()

    def _fail_ls_files(self, arguments: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
        if LS_FILES in arguments:
            return subprocess.CompletedProcess(list(arguments), FAILED_GIT, b"", b"fatal: test failure")
        else:
            return self._run_binary_command(arguments, cwd)

    def _find_problem(self, words: str, patterns: Sequence[str] = MATCHING_PATTERNS) -> str:
        problems = self.cleanup.list_file_problems(self.worktree, patterns)

        return next((problem for problem in problems if problem.startswith(words)), "")

    def _remove_top_folder(self, name: str) -> None:
        top = self.worktree / Path(name).parts[0]

        if top.is_dir():
            shutil.rmtree(top)
        else:
            top.unlink()


class CleanupPatternTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_script("workflow_policy")

    def test_patterns_the_gitignore_grammar_reads_otherwise_are_refused(self) -> None:
        for pattern in REFUSED_PATTERNS:
            with self.subTest(pattern), self.assertRaises(self.policy.PolicyError):
                self.policy.check_cleanup_patterns([pattern], "the test policy")

    def test_escaped_and_located_patterns_are_accepted(self) -> None:
        self.policy.check_cleanup_patterns(ACCEPTED_PATTERNS, "the test policy")

    def test_other_tools_still_read_a_policy_with_a_refused_pattern(self) -> None:
        text = json.dumps({**PULL_REQUEST_POLICY, "cleanupIgnored": ["!app/build/keep/"]})
        parsed = self.policy.parse_policy(text, "the test policy")

        self.assertEqual(parsed.cleanup_ignored, ("!app/build/keep/",))

    def test_a_rename_in_either_column_is_one_entry(self) -> None:
        cleanup = load_script("worktree-cleanup")
        entries = cleanup.parse_status_entries(b" R new\0old\0R  moved\0source\0C  copy\0origin\nal\0?? other\0")

        self.assertEqual(entries, [(b" R", b"new"), (b"R ", b"moved"), (b"C ", b"copy"), (b"??", b"other")])


if __name__ == "__main__":
    unittest.main()
