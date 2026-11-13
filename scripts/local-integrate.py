#!/usr/bin/env python3
"""local-integrate.py — integrate a task branch into the base of a repository that has no remote.

Usage:
    local-integrate.py [<branch>]

Run it in the task's worktree once every change is committed. It works only
in a repository whose .agent-workflow.json, as committed on the original
checkout's branch, names the local-integration mode, and it stands in for
the pull request and its checks there (user decision, 2026-11-13).

Holding the repository's integration lock, it checks that the task's work
is committed and the original checkout is clean and on the base, merges the
branch onto the base as it is now in a temporary worktree of its own (merge
--no-ff, so cleanup can later prove the branch was integrated by ancestry),
runs the recorded check commands on that exact result, each for at most an
hour, and only when every check passes fast-forwards the original
checkout's base branch to it. A conflict, a failed or hung check, or a base
that moved meanwhile leaves the base untouched. An integration stopped
halfway, even by a crash, leaves only its temporary worktree, which the
next run removes before it starts.

Exit codes:
    0  integrated
    2  not integrated: a conflict, a failed check, or uncommitted work; the reasons are printed
    3  the repository records no local-integration workflow, or its file is wrong
    4  a git command failed
    5  another integration held the lock longer than this one waits
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from integration_commands import (
    BRANCH_REF_PREFIX,
    EXIT_COMMAND_FAILED,
    EXIT_DONE,
    EXIT_LOCK_TIMEOUT,
    EXIT_NO_POLICY,
    EXIT_NOT_READY,
    CommandError,
    NotReadyError,
    announce_wait,
    git,
    has_tracked_changes,
    print_problems,
    read_current_branch,
    read_worktree_root,
    run_command,
)
from integration_lock import DEFAULT_WAIT_SECONDS, LockTimeoutError, hold_integration_lock
from workflow_policy import PolicyError, WorkflowMode, WorkflowPolicy, read_original_checkout, read_original_policy

INTEGRATION_FOLDER = "agent-integration"
BRANCH_NAME_SEPARATOR = "/"
FOLDER_NAME_SEPARATOR = "-"
CHECK_TIMEOUT_SECONDS = 3600


def main(arguments: Sequence[str]) -> int:
    branch = parse_options(arguments)

    try:
        root = read_worktree_root()
        original = find_original_checkout(root)
        policy = read_integration_policy(original)
        task_branch = branch or read_current_branch(root)

        with hold_integration_lock(original / ".git", DEFAULT_WAIT_SECONDS, announce_wait):
            check_ready(root, original, policy, task_branch)
            merge_commit = integrate(original, policy, task_branch)
        print(f"Integrated {task_branch} into {policy.base} as {merge_commit[:12]}.")
        print("Remove the worktree with worktree-cleanup.py once nothing in it is still needed.")
        return EXIT_DONE
    except NotReadyError as error:
        print_problems("Not integrated", error.problems)
        return EXIT_NOT_READY
    except PolicyError as error:
        print(f"Not integrated: {error}", file=sys.stderr)
        return EXIT_NO_POLICY
    except CommandError as error:
        print(f"Not integrated: {error}", file=sys.stderr)
        return EXIT_COMMAND_FAILED
    except LockTimeoutError as error:
        print(f"Not integrated: {error}", file=sys.stderr)
        return EXIT_LOCK_TIMEOUT


def parse_options(arguments: Sequence[str]) -> str | None:
    parser = argparse.ArgumentParser(
        description="Integrate a task branch into the base of a repository without a remote."
    )

    parser.add_argument("branch", nargs="?", help="the task branch; the current branch when omitted")
    branch: str | None = parser.parse_args(arguments).branch

    return branch


def find_original_checkout(root: Path) -> Path:
    original = read_original_checkout(root)

    if original is None:
        raise CommandError(f"Git cannot find the original checkout of {root}")
    else:
        return original


def read_integration_policy(original: Path) -> WorkflowPolicy:
    """The workflow committed on the original checkout's branch, which a task branch cannot change."""
    policy = read_original_policy(original)

    if policy is None:
        raise PolicyError(f"{original} records no local-integration workflow; integrate as the project's rules say")
    elif policy.mode != WorkflowMode.LOCAL_INTEGRATION:
        raise PolicyError(f"{original} records the {policy.mode.value} workflow, not local integration")
    else:
        return policy


def check_ready(root: Path, original: Path, policy: WorkflowPolicy, branch: str) -> None:
    """Checked inside the lock, so an original checkout changed while this run waited is seen."""
    problems = [
        *list_task_problems(root, branch, policy.base),
        *list_original_problems(original, policy.base),
    ]

    if problems:
        raise NotReadyError(problems)


def list_task_problems(root: Path, branch: str, base: str) -> list[str]:
    exists = run_command(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", BRANCH_REF_PREFIX + branch])

    if not branch or exists.returncode != 0:
        return [f"there is no branch {branch!r} to integrate"]
    elif branch == base:
        return [f"{branch} is the base branch itself; run this for a task branch"]
    elif git(root, "status", "--porcelain"):
        return [f"{root} has uncommitted or untracked files; commit or remove them first"]
    else:
        return []


def list_original_problems(original: Path, base: str) -> list[str]:
    branch = read_current_branch(original)

    if branch != base:
        return [f"the original checkout {original} is on {branch or 'no branch'}, not {base}"]
    elif has_tracked_changes(original):
        return [f"the original checkout {original} has uncommitted changes; it must stay clean"]
    else:
        return []


def integrate(original: Path, policy: WorkflowPolicy, branch: str) -> str:
    """Merge onto the current base in a temporary worktree, check that exact result, then move the base to it."""
    integration_folder = original / ".git" / INTEGRATION_FOLDER
    folder_name = f"{branch.replace(BRANCH_NAME_SEPARATOR, FOLDER_NAME_SEPARATOR)}-{os.getpid()}"
    folder = integration_folder / folder_name

    remove_leftover_worktrees(original, integration_folder)
    git(original, "worktree", "add", "--quiet", "--detach", str(folder), BRANCH_REF_PREFIX + policy.base)
    try:
        merge_branch(folder, branch, policy.base)
        run_checks(folder, policy.check_commands)
        merge_commit = git(folder, "rev-parse", "HEAD")
        move_base(original, policy.base, merge_commit)
        return merge_commit
    finally:
        remove_worktree(original, folder)


def remove_leftover_worktrees(original: Path, integration_folder: Path) -> None:
    """Remove what an integration stopped halfway left; the folder holds only this tool's temporary worktrees."""
    leftovers = sorted(integration_folder.iterdir()) if integration_folder.is_dir() else []

    for folder in leftovers:
        print(f"Removing {folder.name}, left by an integration that stopped before it finished.")
        remove_worktree(original, folder)
    git(original, "worktree", "prune")


def merge_branch(folder: Path, branch: str, base: str) -> None:
    message = f"Merge: integrate {branch} into {base}"
    merge = ["git", "-C", str(folder), "merge", "--no-ff", "--quiet", "-m", message, BRANCH_REF_PREFIX + branch]
    result = run_command(merge)
    conflicts = git(folder, "diff", "--name-only", "--diff-filter=U").splitlines() if result.returncode != 0 else []

    if conflicts:
        run_command(["git", "-C", str(folder), "merge", "--abort"])
        raise NotReadyError(
            [
                f"{branch} conflicts with {base} in {', '.join(conflicts)}; merge {base} into the task branch, "
                "resolve the conflict there, and run this again",
            ],
        )
    elif result.returncode != 0:
        raise CommandError(f"merging {branch} failed: {result.stderr.strip() or result.stdout.strip()}")


def run_checks(folder: Path, commands: Sequence[Sequence[str]]) -> None:
    """Run each recorded check on the merged result; their output goes straight to the caller."""
    for command in commands:
        print(f"Running {' '.join(command)}", flush=True)
        returncode = run_check(folder, command)

        if returncode != 0:
            raise NotReadyError([f"the check `{' '.join(command)}` failed with exit code {returncode}"])


def run_check(folder: Path, command: Sequence[str]) -> int:
    try:
        return subprocess.run(list(command), cwd=folder, timeout=CHECK_TIMEOUT_SECONDS, check=False).returncode
    except FileNotFoundError as error:
        raise NotReadyError([f"the check `{' '.join(command)}` could not start: {error}"]) from error
    except subprocess.TimeoutExpired as error:
        problem = f"the check `{' '.join(command)}` ran longer than {CHECK_TIMEOUT_SECONDS} seconds"
        raise NotReadyError([problem]) from error


def move_base(original: Path, base: str, merge_commit: str) -> None:
    """Fast-forward only, so a base that moved since the merge began refuses instead of losing a change."""
    result = run_command(["git", "-C", str(original), "merge", "--ff-only", "--quiet", merge_commit])

    if result.returncode != 0:
        raise NotReadyError([f"{base} changed while the checks ran, or the original checkout changed; run this again"])


def remove_worktree(original: Path, folder: Path) -> None:
    """Auxiliary: a temporary folder left behind is removed by the next run, so a failure here only warns."""
    try:
        run_command(["git", "-C", str(original), "worktree", "remove", "--force", str(folder)])
        if folder.exists():
            shutil.rmtree(folder)
    except (CommandError, OSError) as error:
        print(f"Warning: could not remove the temporary worktree {folder} ({error}); the next run removes it.")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
