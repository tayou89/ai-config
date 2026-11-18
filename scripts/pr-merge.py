#!/usr/bin/env python3
"""pr-merge.py — merge a task's pull request one at a time, on fresh evidence only.

Usage:
    pr-merge.py [--wait [--wait-minutes N] [--poll-seconds N]] [<pull request number, URL, or branch>]

Run it in the task's worktree once its pull request is ready. It works only
in a repository whose .agent-workflow.json, as committed on the base branch,
names the pull-request mode, and the shell guard sends a plain
`gh pr merge` there to this script (user decision, 2026-11-13).

Without branch protection, which private repositories get only with a paid
plan, GitHub merges a pull request whatever its checks say. So this script
holds the repository's integration lock and, inside it, reads the policy
from the freshly fetched base branch and asks GitHub again whether the pull
request's head contains the current base branch and whether every required
check passed on that head, then squash-merges that exact head with
--match-head-commit. A second session merging at the same time waits for
the lock and then finds the base moved, so two changes never land untested
together. A missing, pending, cancelled, skipped, or neutral check is not a
pass.

The lock covers the sessions that share this clone. A merge on GitHub's
website or from another computer between the check and the merge is not
stopped; the script then warns that the merge landed on a base it did not
check. The checks run the pull request's own workflow and scripts, so a
pull request that changes them gets a warning to review that change.

After the merge it waits until the fetched base branch contains the merge
commit and fast-forwards the original checkout when that checkout is clean
and on the base branch, and it fast-forwards the task branch to the merged
head, since worktree-cleanup.py finds a squash-merged branch by that last
commit. Run again after an interruption: a pull request already merged is
only finished, never merged twice. It does not remove the worktree or the
branch; worktree-cleanup.py does that as its own step.

With --wait, run as one background command once the pull request is open
(user decision, 2026-11-17), it first waits, outside the lock, until every
required check on the head has finished. Inside the lock it then waits for
GitHub to work out whether the pull request merges cleanly, and when the
base moved meanwhile, the merge is clean, and every required check passed
on the current head, it asks GitHub's update-branch endpoint to merge the
base into the head (refused if the head moved), fetches that new head,
waits for its checks, and merges. Holding the lock through that second wait
queues this computer's merges, so a pull request brings in the base at most
once. A conflict, a failed check, or the end of the wait leaves the pull
request unmerged with the reasons printed; when the checks fail on the head
update-branch made, that head stays on GitHub and the task branch is behind
it until the session pulls it.

Exit codes:
    0  merged, or already merged and now finished
    2  not ready to merge; the reasons are printed
    3  the repository records no guarded pull-request workflow, or its file is wrong
    4  a gh or git command failed
    5  another integration held the lock longer than this one waits
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from integration_commands import (
    EXIT_COMMAND_FAILED,
    EXIT_DONE,
    EXIT_LOCK_TIMEOUT,
    EXIT_NO_POLICY,
    EXIT_NOT_READY,
    REMOTE,
    REMOTE_REF_PREFIX,
    CommandError,
    NotReadyError,
    announce_wait,
    git,
    has_tracked_changes,
    is_ancestor,
    print_problems,
    read_current_branch,
    read_json,
    read_worktree_root,
    run_required,
)
from integration_lock import DEFAULT_WAIT_SECONDS, LockTimeoutError, hold_integration_lock
from workflow_policy import (
    POLICY_FILE,
    PolicyError,
    WorkflowMode,
    WorkflowPolicy,
    read_committed_policy,
    read_common_git_directory,
    read_original_checkout,
)

PULL_REQUEST_FIELDS = (
    "number,url,state,isDraft,mergeable,baseRefName,headRefName,headRefOid,statusCheckRollup,mergeCommit"
)
OPEN_STATE = "OPEN"
MERGED_STATE = "MERGED"
CONFLICTING = "CONFLICTING"
UNKNOWN_MERGEABLE = "UNKNOWN"
COMPLETED_STATUS = "COMPLETED"
SUCCESS = "SUCCESS"
CHECK_RUN_TYPE = "CheckRun"
GATE_PATHS = (POLICY_FILE, ".github/workflows/", ".githooks/", "scripts/check-commit.py")
CONFIRM_ATTEMPTS = 6
CONFIRM_DELAY_SECONDS = 2.0
MERGEABLE = "MERGEABLE"
PENDING_STATES = ("PENDING", "EXPECTED")
UPDATE_BRANCH_PATH = "repos/{{owner}}/{{repo}}/pulls/{number}/update-branch"
DEFAULT_WAIT_MINUTES = 60.0
DEFAULT_POLL_SECONDS = 30.0
SECONDS_PER_MINUTE = 60.0


@dataclass(frozen=True)
class MergeOptions:
    selector: str | None
    wait: bool
    wait_seconds: float
    poll_seconds: float


@dataclass(frozen=True)
class WaitLimit:
    """When a waiting run gives up, and how often it asks GitHub again until then."""

    deadline: float
    poll_seconds: float

    def has_time_left(self) -> bool:
        return time.monotonic() < self.deadline

    def read_seconds_left(self) -> float:
        return max(self.deadline - time.monotonic(), 0.0)


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str
    is_draft: bool
    mergeable: str
    base: str
    head_branch: str
    head_commit: str
    checks: tuple[dict[str, Any], ...]
    merge_commit: str | None


@dataclass(frozen=True)
class MergeResult:
    pull_request: PullRequest
    merge_commit: str


def main(arguments: Sequence[str]) -> int:
    options = parse_options(arguments)
    limit = WaitLimit(time.monotonic() + options.wait_seconds, options.poll_seconds)

    try:
        root = read_worktree_root()
        number = view_pull_request(options.selector).number

        if options.wait:
            wait_for_checks(root, number, limit)
        lock_wait_seconds = read_lock_wait_seconds(options, limit)

        with hold_integration_lock(find_common_git_directory(root), lock_wait_seconds, announce_wait):
            pull_request = view_pull_request(str(number))
            ready = bring_in_base(root, pull_request, limit) if options.wait else pull_request
            result = merge_when_ready(root, ready)

            update_original_checkout(root, result.pull_request.base, result.merge_commit)
            update_task_branch(root, result.pull_request)
        print(f"Merged {result.pull_request.url} into {result.pull_request.base} as {result.merge_commit[:12]}.")
        print("Remove the worktree with worktree-cleanup.py once nothing in it is still needed.")
        return EXIT_DONE
    except NotReadyError as error:
        print_problems("Not merged", error.problems)
        return EXIT_NOT_READY
    except PolicyError as error:
        print(f"Not merged: {error}", file=sys.stderr)
        return EXIT_NO_POLICY
    except CommandError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return EXIT_COMMAND_FAILED
    except LockTimeoutError as error:
        print(f"Not merged: {error}", file=sys.stderr)
        return EXIT_LOCK_TIMEOUT


def parse_options(arguments: Sequence[str]) -> MergeOptions:
    parser = argparse.ArgumentParser(description="Merge a task's pull request one at a time, on fresh evidence only.")

    parser.add_argument("pull_request", nargs="?", help="number, URL, or branch; the current branch's when omitted")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="wait for the required checks, bring in a moved base on GitHub when it merges cleanly, then merge",
    )
    parser.add_argument(
        "--wait-minutes",
        type=parse_positive_number,
        default=DEFAULT_WAIT_MINUTES,
        help="how long --wait waits",
    )
    parser.add_argument(
        "--poll-seconds",
        type=parse_positive_number,
        default=DEFAULT_POLL_SECONDS,
        help="how often --wait asks",
    )
    parsed = parser.parse_args(arguments)

    return MergeOptions(
        selector=parsed.pull_request,
        wait=parsed.wait,
        wait_seconds=parsed.wait_minutes * SECONDS_PER_MINUTE,
        poll_seconds=parsed.poll_seconds,
    )


def parse_positive_number(text: str) -> float:
    """A wait or poll time; zero would ask GitHub without pause and a negative one cannot be slept."""
    value = float(text)

    if value > 0:
        return value
    else:
        raise argparse.ArgumentTypeError(f"{text} is not a positive number")


def read_lock_wait_seconds(options: MergeOptions, limit: WaitLimit) -> float:
    """A waiting run waits for the lock as long as its wait has left, read once the wait outside the lock is over."""
    if options.wait:
        return max(limit.read_seconds_left(), DEFAULT_WAIT_SECONDS)
    else:
        return DEFAULT_WAIT_SECONDS


def find_common_git_directory(root: Path) -> Path:
    common = read_common_git_directory(root)

    if common is None:
        raise CommandError(f"Git cannot find the repository folder of {root}")
    else:
        return common


def view_pull_request(selector: str | None) -> PullRequest:
    selection = [selector] if selector else []
    data = read_json(["gh", "pr", "view", *selection, "--json", PULL_REQUEST_FIELDS])

    try:
        merge_commit = data.get("mergeCommit") or {}

        return PullRequest(
            number=int(data["number"]),
            url=str(data["url"]),
            state=str(data["state"]),
            is_draft=bool(data["isDraft"]),
            mergeable=str(data["mergeable"]),
            base=str(data["baseRefName"]),
            head_branch=str(data["headRefName"]),
            head_commit=str(data["headRefOid"]),
            checks=tuple(data.get("statusCheckRollup") or ()),
            merge_commit=merge_commit.get("oid"),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise CommandError(f"`gh pr view` answered without the fields this script reads: {error}") from error


def read_base_policy(root: Path, base: str) -> WorkflowPolicy:
    """The workflow as committed on the freshly fetched base, so the task branch's own copy cannot change the gate."""
    git(root, "fetch", "--quiet", REMOTE, base)
    policy = read_committed_policy(root, REMOTE_REF_PREFIX + base)

    if policy is None:
        raise PolicyError(f"{base} records no guarded pull-request workflow; merge it as the project's rules say")
    elif policy.mode != WorkflowMode.PULL_REQUEST:
        raise PolicyError(f"{base} records the {policy.mode.value} workflow, not pull requests")
    elif policy.base != base:
        raise PolicyError(f"the pull request targets {base}, but the recorded workflow merges into {policy.base}")
    else:
        return policy


def merge_when_ready(root: Path, pull_request: PullRequest) -> MergeResult:
    """Merge the exact head that passed, or finish a merge an earlier run made."""
    if pull_request.state == MERGED_STATE:
        print(f"{pull_request.url} is already merged; finishing what an earlier run started.")
        return MergeResult(pull_request, confirm_merge(root, pull_request))
    else:
        policy = read_base_policy(root, pull_request.base)
        base_tip = read_remote_branch_tip(pull_request.base)
        problems = list_merge_problems(pull_request, policy, base_tip)

        if problems:
            raise NotReadyError(problems)
        else:
            warn_about_gate_changes(root, pull_request)
            run_required(
                [
                    "gh",
                    "pr",
                    "merge",
                    str(pull_request.number),
                    "--squash",
                    "--match-head-commit",
                    pull_request.head_commit,
                ],
            )
            merge_commit = confirm_merge(root, view_pull_request(str(pull_request.number)))
            warn_if_base_moved(root, merge_commit, base_tip)
            return MergeResult(pull_request, merge_commit)


def list_merge_problems(pull_request: PullRequest, policy: WorkflowPolicy, base_tip: str) -> list[str]:
    state_problems = list_state_problems(pull_request)

    if state_problems:
        return state_problems
    else:
        return [
            *list_base_problems(pull_request, base_tip),
            *list_check_problems(pull_request.checks, policy.required_checks),
        ]


def list_state_problems(pull_request: PullRequest) -> list[str]:
    if pull_request.state != OPEN_STATE:
        return [f"the pull request is {pull_request.state.lower()}"]
    elif pull_request.is_draft:
        return ["the pull request is a draft"]
    elif pull_request.mergeable == CONFLICTING:
        return [f"it conflicts with {pull_request.base}; merge {pull_request.base} into the task branch and resolve it"]
    elif pull_request.mergeable == UNKNOWN_MERGEABLE:
        return ["GitHub has not finished checking it for conflicts; run this again in a minute"]
    else:
        return []


def list_base_problems(pull_request: PullRequest, base_tip: str) -> list[str]:
    """Whether the head contains the base branch as GitHub has it now, not as it was when the checks ran."""
    missing = count_missing_base_commits(pull_request, base_tip)

    if missing > 0:
        return [
            f"its head lacks {missing} commits now on {pull_request.base}; merge {pull_request.base} into the task "
            "branch, push it, and wait for its checks",
        ]
    else:
        return []


def count_missing_base_commits(pull_request: PullRequest, base_tip: str) -> int:
    compare_path = f"repos/{{owner}}/{{repo}}/compare/{base_tip}...{pull_request.head_commit}"
    answer = run_required(["gh", "api", compare_path, "--jq", ".behind_by"])

    if answer.isdigit():
        return int(answer)
    else:
        raise CommandError(f"GitHub's compare answer is not a count: {answer!r}")


def wait_for_checks(root: Path, number: int, limit: WaitLimit) -> PullRequest:
    """Wait until every required check on the head has finished, passed or not; the merge step then judges them."""
    pull_request = view_pull_request(str(number))
    required = read_base_policy(root, pull_request.base).required_checks

    while is_waiting_for_checks(pull_request, required) and limit.has_time_left():
        time.sleep(limit.poll_seconds)
        pull_request = view_pull_request(str(number))
    return pull_request


def bring_in_base(root: Path, pull_request: PullRequest, limit: WaitLimit) -> PullRequest:
    """Inside the lock, merge a moved base into the head on GitHub when it merges cleanly, then wait for its checks.

    GitHub's update-branch endpoint makes the merge commit itself, so no session commits
    lines the base branch already brought through its own checks; a conflict is left to the
    session, and the merge step then reports it.
    """
    settled = wait_for_mergeable(pull_request, limit)
    required = read_base_policy(root, settled.base).required_checks

    if is_ready_to_bring_in_base(settled, required, limit):
        update_head_on_github(settled)
        moved = wait_for_new_head(root, settled, limit)
        return wait_for_checks(root, moved.number, limit)
    else:
        return settled


def wait_for_mergeable(pull_request: PullRequest, limit: WaitLimit) -> PullRequest:
    """GitHub works out whether an open pull request merges cleanly after its base moves; wait for that answer.

    GitHub answers UNKNOWN for a merged pull request every time, so only an open one is waited for.
    """
    current = pull_request

    while current.state == OPEN_STATE and current.mergeable == UNKNOWN_MERGEABLE and limit.has_time_left():
        time.sleep(limit.poll_seconds)
        current = view_pull_request(str(current.number))
    return current


def update_head_on_github(pull_request: PullRequest) -> None:
    """Ask GitHub to merge the base into the head, refused if the head moved since it was read."""
    update_path = UPDATE_BRANCH_PATH.format(number=pull_request.number)

    print(f"Bringing {pull_request.base} into {pull_request.head_branch} on GitHub, then waiting for its checks.")
    run_required(
        ["gh", "api", update_path, "--method", "PUT", "-f", f"expected_head_sha={pull_request.head_commit}"],
    )


def wait_for_new_head(root: Path, pull_request: PullRequest, limit: WaitLimit) -> PullRequest:
    """GitHub answers update-branch before the merge commit exists; wait until the head shows it, then fetch it.

    The merge commit exists only on GitHub, and the gate-change warning and the
    task branch's fast-forward read it from this clone.
    """
    current = view_pull_request(str(pull_request.number))

    while current.head_commit == pull_request.head_commit and limit.has_time_left():
        time.sleep(limit.poll_seconds)
        current = view_pull_request(str(pull_request.number))
    git(root, "fetch", "--quiet", REMOTE, current.head_branch)
    return current


def list_check_problems(checks: Sequence[dict[str, Any]], required: Sequence[str]) -> list[str]:
    """One line per required check that did not pass on the head; one that never ran is not a pass."""
    problems: list[str] = []

    for name in required:
        entries = [entry for entry in checks if read_check_name(entry) == name]

        if entries:
            problems.extend(
                f"the required check {name!r} is {describe_check(entry)}" for entry in entries if not is_passed(entry)
            )
        else:
            problems.append(f"the required check {name!r} has not run on the pull request's latest commit")
    return problems


def read_remote_branch_tip(branch: str) -> str:
    return run_required(["gh", "api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{branch}", "--jq", ".object.sha"])


def read_check_name(entry: dict[str, Any]) -> str:
    if entry.get("__typename") == CHECK_RUN_TYPE:
        return str(entry.get("name") or "")
    else:
        return str(entry.get("context") or "")


def describe_check(entry: dict[str, Any]) -> str:
    if entry.get("__typename") == CHECK_RUN_TYPE and entry.get("status") == COMPLETED_STATUS:
        return str(entry.get("conclusion") or "finished without a result").lower()
    elif entry.get("__typename") == CHECK_RUN_TYPE:
        return str(entry.get("status") or "not started").lower()
    else:
        return str(entry.get("state") or "not reported").lower()


def confirm_merge(root: Path, pull_request: PullRequest) -> str:
    """Wait until the fetched base branch contains the merge commit, so the next integration sees it."""
    if pull_request.state != MERGED_STATE or not pull_request.merge_commit:
        raise CommandError(f"GitHub does not report {pull_request.url} as merged")
    else:
        merge_commit = pull_request.merge_commit

        for _ in range(CONFIRM_ATTEMPTS):
            git(root, "fetch", "--quiet", REMOTE, pull_request.base)
            if is_ancestor(root, merge_commit, REMOTE_REF_PREFIX + pull_request.base):
                return merge_commit
            time.sleep(CONFIRM_DELAY_SECONDS)
        raise CommandError(
            f"GitHub reports {pull_request.url} as merged, but {pull_request.base} on {REMOTE} does not contain "
            f"{merge_commit[:12]} yet; run this again to finish",
        )


def warn_about_gate_changes(root: Path, pull_request: PullRequest) -> None:
    """The checks ran the pull request's own workflow and scripts, so a change to them deserves a second look."""
    try:
        changed = git(
            root, "diff", "--name-only", f"{REMOTE_REF_PREFIX}{pull_request.base}...{pull_request.head_commit}"
        )
        touched = [path for path in changed.splitlines() if path.startswith(GATE_PATHS)]

        if touched:
            print(f"Warning: this pull request changes what its own checks run ({', '.join(touched)}).")
    except CommandError as error:
        print(f"Warning: could not list the files this pull request changes ({error}).")


def warn_if_base_moved(root: Path, merge_commit: str, checked_base: str) -> None:
    """A merge from outside this clone between the check and the merge lands this one on a base it did not check."""
    try:
        parent = git(root, "rev-parse", f"{merge_commit}^1")

        if parent != checked_base:
            print(
                f"Warning: the merge landed on {parent[:12]}, not on the checked base {checked_base[:12]}; "
                "another merge happened outside this computer's sessions, so run the checks on the base now.",
            )
    except CommandError as error:
        print(f"Warning: could not compare the merge with the checked base ({error}).")


def update_original_checkout(root: Path, base: str, merge_commit: str) -> None:
    """Fast-forward the original checkout, which sessions only read; leave it when it is unclean or off the base."""
    try:
        original = read_original_checkout(root)

        if original is None:
            print("Left the original checkout as it is: Git cannot find it.")
        elif read_current_branch(original) != base or has_tracked_changes(original):
            print(f"Left {original} as it is: it is not a clean checkout of {base}.")
        else:
            git(original, "merge", "--ff-only", "--quiet", merge_commit)
            print(f"Fast-forwarded {original} to {merge_commit[:12]}.")
    except CommandError as error:
        print(f"Could not update the original checkout ({error}); the merge itself is done.")


def update_task_branch(root: Path, pull_request: PullRequest) -> None:
    """Fast-forward the task branch to the head that was merged, which update-branch may have moved past it.

    worktree-cleanup.py finds a squash-merged branch by its last commit, so a
    branch left behind GitHub's merge commit would look unmerged.
    """
    try:
        if read_current_branch(root) == pull_request.head_branch and not has_tracked_changes(root):
            git(root, "fetch", "--quiet", REMOTE, pull_request.head_branch)
            git(root, "merge", "--ff-only", "--quiet", pull_request.head_commit)
        else:
            print(f"Left {root} as it is: it is not a clean checkout of {pull_request.head_branch}.")
    except CommandError as error:
        print(f"Could not fast-forward {pull_request.head_branch} to the merged head ({error}); the merge is done.")


def is_passed(entry: dict[str, Any]) -> bool:
    if entry.get("__typename") == CHECK_RUN_TYPE:
        return entry.get("status") == COMPLETED_STATUS and entry.get("conclusion") == SUCCESS
    else:
        return entry.get("state") == SUCCESS


def is_finished(entry: dict[str, Any]) -> bool:
    if entry.get("__typename") == CHECK_RUN_TYPE:
        return entry.get("status") == COMPLETED_STATUS
    else:
        return entry.get("state") not in PENDING_STATES


def is_waiting_for_checks(pull_request: PullRequest, required: Sequence[str]) -> bool:
    """An open pull request whose required checks have not all started and finished on its head."""
    entries = [entry for entry in pull_request.checks if read_check_name(entry) in required]
    started = {read_check_name(entry) for entry in entries}

    return pull_request.state == OPEN_STATE and (
        not set(required) <= started or not all(is_finished(entry) for entry in entries)
    )


def is_ready_to_bring_in_base(pull_request: PullRequest, required: Sequence[str], limit: WaitLimit) -> bool:
    """An open, cleanly merging pull request whose head passed its checks but lacks commits now on the base.

    A head whose checks failed, or a wait already over, is left as it is rather than
    given a merge commit nobody will wait for.
    """
    return (
        pull_request.state == OPEN_STATE
        and pull_request.mergeable == MERGEABLE
        and limit.has_time_left()
        and not list_check_problems(pull_request.checks, required)
        and count_missing_base_commits(pull_request, read_remote_branch_tip(pull_request.base)) > 0
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
