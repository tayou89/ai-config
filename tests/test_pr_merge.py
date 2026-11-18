#!/usr/bin/env python3
"""Tests for scripts/pr-merge.py against a local remote and the gh stand-in in github_double.py."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from workflow_world import BASE, PASSED_CHECK, WorkflowWorld, load_script

pr_merge = load_script("pr-merge")
integration_lock = load_script("integration_lock")

POLICY = {"mode": "pull-request", "base": BASE, "requiredChecks": ["checks"]}
EXIT_MERGED = 0
EXIT_NOT_READY = 2
EXIT_NO_POLICY = 3
EXIT_COMMAND_FAILED = 4
RUNNING_CHECK = {**PASSED_CHECK, "status": "IN_PROGRESS", "conclusion": None}
PENDING_STATUS = {"__typename": "StatusContext", "context": "checks", "state": "PENDING"}
FAST_WAIT = ["--wait", "--poll-seconds", "0.05"]
UPDATE_BRANCH_CALL = ["api", "repos/{owner}/{repo}/pulls/1/update-branch", "--method", "PUT"]
UPDATE_BRANCH_SUFFIX = "/update-branch"


def make_check(**values: Any) -> dict[str, Any]:
    return {**PASSED_CHECK, **values}


def make_pull_request(checks: list[dict[str, Any]], state: str) -> Any:
    """A pr-merge.PullRequest; the script is loaded at run time, so its type is not known statically."""
    return pr_merge.PullRequest(1, "url", state, False, "MERGEABLE", BASE, "task-a", "head", tuple(checks), None)


def open_task(world: WorkflowWorld, number: int, branch: str, name: str) -> tuple[Path, str]:
    worktree = world.add_worktree(branch)
    head = world.commit_file(worktree, name, f"{name}\n", f"Feat: add {name}")

    world.open_pull_request(number, branch, worktree)
    return worktree, head


class CheckEvaluationTest(unittest.TestCase):
    def test_passing_check_has_no_problem(self) -> None:
        self.assertEqual(pr_merge.list_check_problems([PASSED_CHECK], ["checks"]), [])

    def test_check_that_never_ran_is_not_a_pass(self) -> None:
        problems = pr_merge.list_check_problems([], ["checks"])

        self.assertEqual(len(problems), 1)
        self.assertIn("has not run", problems[0])

    def test_cancelled_skipped_neutral_failed_and_pending_checks_are_not_passes(self) -> None:
        cases = {
            "cancelled": make_check(conclusion="CANCELLED"),
            "skipped": make_check(conclusion="SKIPPED"),
            "neutral": make_check(conclusion="NEUTRAL"),
            "failure": make_check(conclusion="FAILURE"),
            "in_progress": make_check(status="IN_PROGRESS", conclusion=None),
        }

        for word, entry in cases.items():
            with self.subTest(word):
                problems = pr_merge.list_check_problems([entry], ["checks"])

                self.assertEqual(len(problems), 1)
                self.assertIn(word, problems[0])

    def test_status_context_counts_by_state(self) -> None:
        passed = {"__typename": "StatusContext", "context": "checks", "state": "SUCCESS"}
        pending = {"__typename": "StatusContext", "context": "checks", "state": "PENDING"}

        self.assertEqual(pr_merge.list_check_problems([passed], ["checks"]), [])
        self.assertIn("pending", pr_merge.list_check_problems([pending], ["checks"])[0])

    def test_other_checks_do_not_stand_in_for_a_required_one(self) -> None:
        other = make_check(name="lint")

        self.assertIn("has not run", pr_merge.list_check_problems([other], ["checks"])[0])

    def test_waits_only_while_a_required_check_has_not_started_or_finished(self) -> None:
        cases: dict[str, tuple[list[dict[str, Any]], str, bool]] = {
            "not started": ([], "OPEN", True),
            "running": ([RUNNING_CHECK], "OPEN", True),
            "status pending": ([PENDING_STATUS], "OPEN", True),
            "passed": ([PASSED_CHECK], "OPEN", False),
            "failed": ([make_check(conclusion="FAILURE")], "OPEN", False),
            "only another check": ([make_check(name="lint")], "OPEN", True),
            "merged": ([RUNNING_CHECK], "MERGED", False),
        }

        for word, (checks, state, waits) in cases.items():
            with self.subTest(word):
                pull_request = make_pull_request(checks, state)

                self.assertEqual(pr_merge.is_waiting_for_checks(pull_request, ["checks"]), waits)


class MergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.addCleanup(self.world.close)

    def make_task(self, number: int, branch: str, name: str) -> tuple[Path, str]:
        return open_task(self.world, number, branch, name)

    def merge(self, worktree: Any, *arguments: str) -> Any:
        return self.world.run_script("pr-merge", list(arguments), worktree)

    def test_merges_a_ready_pull_request_with_its_exact_head_and_updates_the_original(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        result = self.merge(worktree)
        merge_calls = [call for call in self.world.list_gh_calls() if call[:2] == ["pr", "merge"]]

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertEqual(merge_calls, [["pr", "merge", "1", "--squash", "--match-head-commit", head]])
        self.assertEqual(self.world.git(self.world.original, "rev-parse", "HEAD"), self.world.remote_tip())
        self.assertTrue((self.world.original / "a.txt").exists())

    def test_refuses_a_head_behind_the_moved_base(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.push_base_commit("other.txt", "other\n")
        tip = self.world.remote_tip()
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("lacks 1 commits", result.stderr)
        self.assertEqual(self.world.remote_tip(), tip)

    def test_refuses_when_the_required_check_is_missing(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.world.set_checks(head, [])
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("has not run", result.stderr)

    def test_refuses_when_the_required_check_was_skipped(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.world.set_checks(head, [make_check(conclusion="SKIPPED")])
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("skipped", result.stderr)

    def test_refuses_a_draft_and_a_conflicting_pull_request(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")

        for field, value, words in (("isDraft", True, "draft"), ("mergeable", "CONFLICTING", "conflicts")):
            with self.subTest(field):
                state = self.world.load_state()
                state["pulls"]["1"] = {**state["pulls"]["1"], "isDraft": False, "mergeable": "MERGEABLE", field: value}
                self.world.save_state(state)
                result = self.merge(worktree)

                self.assertEqual(result.returncode, EXIT_NOT_READY)
                self.assertIn(words, result.stderr)

    def test_refuses_without_a_recorded_workflow_on_the_base_branch(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.git(worktree, "rm", "-q", ".agent-workflow.json")
        self.world.git(worktree, "commit", "-q", "-m", "Chore: drop the workflow on the task branch")
        self.world.git(self.world.original, "rm", "-q", ".agent-workflow.json")
        self.world.git(self.world.original, "commit", "-q", "-m", "Chore: drop the workflow")
        self.world.git(self.world.original, "push", "-q", "origin", BASE)
        result = self.merge(worktree, "1")

        self.assertEqual(result.returncode, EXIT_NO_POLICY)
        self.assertIn("records no guarded pull-request workflow", result.stderr)

    def test_a_task_branch_cannot_loosen_its_own_gate(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        loose = '{"mode": "pull-request", "base": "main", "requiredChecks": ["anything"]}'
        head = self.world.commit_file(worktree, ".agent-workflow.json", loose, "Chore: loosen the gate")
        self.world.git(worktree, "push", "-q", "origin", "task-a")
        self.world.set_checks(head, [make_check(name="anything")])
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("'checks' has not run", result.stderr)

    def test_policy_without_required_checks_is_refused(self) -> None:
        self.world.git(self.world.original, "rm", "-q", ".agent-workflow.json")
        self.world.write(self.world.original, ".agent-workflow.json", '{"mode": "pull-request", "base": "main"}')
        self.world.git(self.world.original, "add", ".agent-workflow.json")
        self.world.git(self.world.original, "commit", "-q", "-m", "Chore: forget the checks")
        self.world.git(self.world.original, "push", "-q", "origin", BASE)
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NO_POLICY)
        self.assertIn("lists no 'requiredChecks'", result.stderr)

    def test_concurrent_merges_land_one_and_refuse_the_other_on_the_moved_base(self) -> None:
        first, _ = self.make_task(1, "task-a", "a.txt")
        second, _ = self.make_task(2, "task-b", "b.txt")
        self.world.update_state(mergeDelaySeconds=1.5)
        processes = [self.world.start_script("pr-merge", [], worktree) for worktree in (first, second)]
        outcomes = [(process.wait(timeout=120), *process.communicate()) for process in processes]
        codes = sorted(code for code, _, _ in outcomes)
        refused = next(stderr for code, _, stderr in outcomes if code == EXIT_NOT_READY)

        self.assertEqual(codes, [EXIT_MERGED, EXIT_NOT_READY])
        self.assertIn("lacks 1 commits", refused)
        self.assertEqual(self.world.git(self.world.original, "rev-parse", "HEAD"), self.world.remote_tip())

    def test_second_run_after_an_interrupted_merge_finishes_without_merging_again(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        interrupted = subprocess.run(
            ["gh", "pr", "merge", "1", "--squash", "--match-head-commit", head],
            cwd=worktree,
            env=self.world.environment,
            capture_output=True,
            check=False,
        )
        calls_before = len(self.world.list_gh_calls())

        self.assertEqual(interrupted.returncode, 0)
        result = self.merge(worktree, "1")
        merge_calls = [call for call in self.world.list_gh_calls()[calls_before:] if call[:2] == ["pr", "merge"]]

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertIn("already merged", result.stdout)
        self.assertEqual(merge_calls, [])
        self.assertEqual(self.world.git(self.world.original, "rev-parse", "HEAD"), self.world.remote_tip())

    def test_leaves_a_dirty_original_checkout_alone(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.write(self.world.original, "README.md", "someone's edit\n")
        before = self.world.git(self.world.original, "rev-parse", "HEAD")
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertIn("Left", result.stdout)
        self.assertEqual(self.world.git(self.world.original, "rev-parse", "HEAD"), before)

    def test_refuses_while_github_has_not_checked_for_conflicts(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        state = self.world.load_state()
        state["pulls"]["1"]["mergeable"] = "UNKNOWN"
        self.world.save_state(state)
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("has not finished checking", result.stderr)

    def test_refuses_a_pull_request_into_another_branch_than_the_recorded_base(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.git(self.world.original, "push", "-q", "origin", f"{BASE}:release")
        state = self.world.load_state()
        state["pulls"]["1"]["base"] = "release"
        self.world.save_state(state)
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_NO_POLICY)
        self.assertIn("merges into main", result.stderr)

    def test_a_failing_gh_is_a_command_failure_not_a_crash(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        (self.world.root / "bin" / "gh").write_text("#!/bin/sh\necho 'not json'\n", encoding="utf-8")
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_COMMAND_FAILED)
        self.assertIn("did not answer JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_warns_when_a_merge_from_outside_lands_between_the_check_and_the_merge(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.update_state(mergeDelaySeconds=2)
        process = self.world.start_script("pr-merge", [], worktree)
        self.wait_for_gh_call(["pr", "merge"])
        self.world.push_base_commit("outside.txt", "outside\n")
        stdout, stderr = process.communicate(timeout=120)

        self.assertEqual(process.returncode, EXIT_MERGED, stderr)
        self.assertIn("not on the checked base", stdout)

    def test_warns_when_the_pull_request_changes_its_own_checks(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.commit_file(worktree, ".github/workflows/checks.yml", "name: checks\n", "Chore: change the checks")
        self.world.git(worktree, "push", "-q", "origin", "task-a")
        result = self.merge(worktree)

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertIn("changes what its own checks run (.github/workflows/checks.yml)", result.stdout)

    def wait_for_gh_call(self, prefix: list[str]) -> None:
        deadline = time.monotonic() + 60

        while not any(call[: len(prefix)] == prefix for call in self.world.list_gh_calls()):
            self.assertLess(time.monotonic(), deadline, "the gh call never came")
            time.sleep(0.1)


class WaitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.addCleanup(self.world.close)

    def make_task(self, number: int, branch: str, name: str) -> tuple[Path, str]:
        return open_task(self.world, number, branch, name)

    def set_check_sequence(self, head: str, sequence: list[list[dict[str, Any]]]) -> None:
        state = self.world.load_state()

        state.setdefault("checkSequences", {})[head] = sequence
        self.world.save_state(state)

    def wait_and_merge(self, worktree: Path, *arguments: str) -> Any:
        return self.world.run_script("pr-merge", [*FAST_WAIT, *arguments], worktree)

    def list_calls(self, prefix: list[str]) -> list[list[str]]:
        return [call for call in self.world.list_gh_calls() if call[: len(prefix)] == prefix]

    def test_waits_for_running_checks_and_then_merges(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.set_check_sequence(head, [[], [RUNNING_CHECK], [RUNNING_CHECK], [PASSED_CHECK]])
        result = self.wait_and_merge(worktree)

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertEqual(len(self.list_calls(["pr", "merge"])), 1)
        self.assertTrue((self.world.original / "a.txt").exists())

    def test_stops_waiting_on_a_failed_check_without_merging(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.set_check_sequence(head, [*[[RUNNING_CHECK]] * 4, [make_check(conclusion="FAILURE")]])
        result = self.wait_and_merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("failure", result.stderr)
        self.assertEqual(self.list_calls(["pr", "merge"]), [])

    def test_leaves_a_moved_base_out_of_a_head_whose_checks_failed(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.world.set_checks(head, [make_check(conclusion="FAILURE")])
        self.world.push_base_commit("other.txt", "other\n")
        result = self.wait_and_merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("failure", result.stderr)
        self.assertEqual(self.list_calls(UPDATE_BRANCH_CALL), [])

    def test_gives_up_at_the_end_of_the_wait(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.world.set_checks(head, [RUNNING_CHECK])
        result = self.wait_and_merge(worktree, "--wait-minutes", "0.005")

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("in_progress", result.stderr)
        self.assertEqual(self.list_calls(["pr", "merge"]), [])

    def test_brings_in_a_moved_base_on_github_then_merges_and_cleanup_finds_it(self) -> None:
        worktree, head = self.make_task(1, "task-a", "a.txt")
        self.world.push_base_commit("other.txt", "other\n")
        self.world.update_state(afterUpdateChecks=[*[[RUNNING_CHECK]] * 3, [PASSED_CHECK]])
        result = self.wait_and_merge(worktree)
        updates = self.list_calls(UPDATE_BRANCH_CALL)
        merged_head = self.world.load_state()["pulls"]["1"]["mergedHead"]
        merges = self.list_calls(["pr", "merge"])
        cleanup = self.world.run_script("worktree-cleanup", [str(worktree)], self.world.root)

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertNotIn("could not list", result.stdout)
        self.assertEqual(updates, [[*UPDATE_BRANCH_CALL, "-f", f"expected_head_sha={head}"]])
        self.assertNotEqual(merged_head, head)
        self.assertEqual(merges, [["pr", "merge", "1", "--squash", "--match-head-commit", merged_head]])
        self.assertTrue((self.world.original / "a.txt").exists())
        self.assertTrue((self.world.original / "other.txt").exists())
        self.assertEqual(cleanup.returncode, EXIT_MERGED, cleanup.stderr)
        self.assertFalse(worktree.exists())

    def test_leaves_a_conflicting_moved_base_to_the_session(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.push_base_commit("a.txt", "the base's own a\n")
        state = self.world.load_state()
        state["pulls"]["1"]["mergeable"] = "CONFLICTING"
        self.world.save_state(state)
        result = self.wait_and_merge(worktree)

        self.assertEqual(result.returncode, EXIT_NOT_READY)
        self.assertIn("conflicts", result.stderr)
        self.assertEqual(self.list_calls(UPDATE_BRANCH_CALL), [])
        self.assertEqual(self.list_calls(["pr", "merge"]), [])

    def test_waits_for_github_to_check_for_conflicts(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.world.update_state(mergeableSequences={"1": [*["UNKNOWN"] * 5, "MERGEABLE"]})
        result = self.wait_and_merge(worktree)

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)

    def test_a_merged_pull_request_is_finished_without_waiting_on_its_conflict_check(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")
        self.assertEqual(self.world.run_script("pr-merge", [], worktree).returncode, EXIT_MERGED)
        self.world.update_state(mergeableSequences={"1": ["UNKNOWN"]})
        started = time.monotonic()
        result = self.wait_and_merge(worktree, "--wait-minutes", "1")

        self.assertEqual(result.returncode, EXIT_MERGED, result.stderr)
        self.assertIn("already merged", result.stdout)
        self.assertLess(time.monotonic() - started, 30)

    def test_refuses_a_poll_time_that_is_not_positive(self) -> None:
        worktree, _ = self.make_task(1, "task-a", "a.txt")

        for value in ("0", "-1", "soon"):
            with self.subTest(value):
                result = self.world.run_script("pr-merge", ["--wait", "--poll-seconds", value], worktree)

                self.assertNotEqual(result.returncode, EXIT_MERGED)
                self.assertIn("--poll-seconds", result.stderr)
        self.assertEqual(self.list_calls(["pr", "merge"]), [])

    def test_two_waiting_merges_land_both_and_bring_in_the_base_once(self) -> None:
        first, _ = self.make_task(1, "task-a", "a.txt")
        second, _ = self.make_task(2, "task-b", "b.txt")
        self.world.update_state(mergeDelaySeconds=1.0)
        processes = [self.world.start_script("pr-merge", FAST_WAIT, worktree) for worktree in (first, second)]
        outcomes = [(process.wait(timeout=120), *process.communicate()) for process in processes]
        updates = [call for call in self.list_calls(["api"]) if call[1].endswith(UPDATE_BRANCH_SUFFIX)]

        self.assertEqual([code for code, _, _ in outcomes], [EXIT_MERGED, EXIT_MERGED], outcomes)
        self.assertEqual(len(updates), 1)
        self.assertTrue((self.world.original / "a.txt").exists())
        self.assertTrue((self.world.original / "b.txt").exists())


class LockTest(unittest.TestCase):
    def test_a_held_lock_times_out_with_one_announcement(self) -> None:
        announcements: list[str] = []

        with tempfile.TemporaryDirectory() as folder:
            with integration_lock.hold_integration_lock(Path(folder), 1, lambda: announcements.append("first")):
                with self.assertRaises(integration_lock.LockTimeoutError):
                    with integration_lock.hold_integration_lock(Path(folder), 1.5, lambda: announcements.append("x")):
                        self.fail("the second holder got the lock")
        self.assertEqual(announcements, ["x"])


if __name__ == "__main__":
    unittest.main()
