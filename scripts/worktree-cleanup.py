#!/usr/bin/env python3
"""worktree-cleanup.py — remove a task's worktree and branch only after its work is preserved.

Usage:
    worktree-cleanup.py <worktree> [--abandoned]

Cleanup is its own step after the merge, so a cleanup that fails never
repeats a merge, and it removes nothing it cannot show is kept elsewhere
(user decision, 2026-11-13). It refuses while the worktree holds
uncommitted or untracked files, ignored files that .agent-workflow.json's
cleanupIgnored does not list as regenerable, or a process whose working
folder is inside it other than the calling session and what that session
started, and until the branch's last commit is integrated:

  pull-request       a merged pull request's head is the branch's last commit.
                     A squash merge leaves the branch's commits out of the
                     base's history, so ancestry cannot show this.
  local-integration  the branch's last commit is an ancestor of the base,
                     which local-integrate.py's merge commits make it.

Then it removes the worktree without --force, deletes the local branch only
while it still points at that commit, and deletes the remote branch with a
lease on the same commit. With --abandoned, for a task the user gave up, it
skips the integration proof and keeps both branches, so the work stays
recoverable. Closing a pull request is not abandoning it. A program that
only holds files open in the worktree, without working in it, is not seen.

Exit codes:
    0  removed
    2  not removed: something in it is not preserved; the reasons are printed
    3  the repository records no worktree workflow, or its file is wrong
    4  a git, gh, or lsof command failed
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from integration_commands import (
    BRANCH_REF_PREFIX,
    EXIT_COMMAND_FAILED,
    EXIT_DONE,
    EXIT_NO_POLICY,
    EXIT_NOT_READY,
    REMOTE,
    CommandError,
    NotReadyError,
    git,
    is_ancestor,
    print_problems,
    read_current_branch,
    read_json,
    run_command,
    run_required,
)
from workflow_policy import (
    PolicyError,
    WorkflowMode,
    WorkflowPolicy,
    find_repository_root,
    is_original_checkout,
    read_original_checkout,
    read_original_policy,
)

IGNORED_STATUS = "!!"
STATUS_PATH_START = 3
STATUS_ENTRY_SEPARATOR = "\0"
RENAMED_STATUSES = ("R", "C")
MAX_SHOWN_PATHS = 5
PROCESS_FIELD = "p"
NAME_FIELD = "n"
PULL_REQUEST_FIELDS = "number,url,headRefOid"


def main(arguments: Sequence[str]) -> int:
    options = parse_options(arguments)

    try:
        worktree = Path(options.worktree).resolve()
        original = find_original_checkout(worktree)
        policy = read_cleanup_policy(original)
        branch = read_current_branch(worktree)
        tip = git(worktree, "rev-parse", "HEAD")
        problems = [
            *list_branch_problems(branch),
            *list_file_problems(worktree, policy.cleanup_ignored),
            *list_process_problems(worktree),
            *([] if options.abandoned else list_integration_problems(worktree, original, policy, branch, tip)),
        ]

        if problems:
            raise NotReadyError(problems)
        else:
            remove(worktree, original, branch, tip, options.abandoned)
            return EXIT_DONE
    except NotReadyError as error:
        print_problems("Not removed", error.problems)
        return EXIT_NOT_READY
    except PolicyError as error:
        print(f"Not removed: {error}", file=sys.stderr)
        return EXIT_NO_POLICY
    except CommandError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return EXIT_COMMAND_FAILED


def parse_options(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove a task's worktree and branch only after its work is preserved."
    )

    parser.add_argument("worktree", help="the task's worktree folder")
    parser.add_argument("--abandoned", action="store_true", help="the user gave the task up: keep its branches")
    return parser.parse_args(arguments)


def find_original_checkout(worktree: Path) -> Path:
    """The original checkout of a linked worktree; the original checkout itself is never removed."""
    check_linked_worktree(worktree)
    original = read_original_checkout(worktree)

    if original is None:
        raise CommandError(f"Git cannot find the original checkout of {worktree}")
    else:
        return original


def read_cleanup_policy(original: Path) -> WorkflowPolicy:
    policy = read_original_policy(original)

    if policy is None:
        raise PolicyError(
            f"{original} records no worktree workflow, so this tool cannot tell how its work is integrated"
        )
    else:
        return policy


def list_branch_problems(branch: str) -> list[str]:
    if branch:
        return []
    else:
        return ["the worktree has no branch checked out, so nothing shows where its commits are kept"]


def list_file_problems(worktree: Path, regenerable: Sequence[str]) -> list[str]:
    entries = parse_status_entries(read_status(worktree))
    unsaved = [path for status, path in entries if status != IGNORED_STATUS]
    kept = [path for status, path in entries if status == IGNORED_STATUS and not is_regenerable(path, regenerable)]
    problems = []

    if unsaved:
        problems.append(f"uncommitted or untracked files: {show_paths(unsaved)}")
    if kept:
        problems.append(f"ignored files that cleanupIgnored does not list as regenerable: {show_paths(kept)}")
    return problems


def list_process_problems(worktree: Path) -> list[str]:
    """Processes whose current folder is inside the worktree, other than the calling session and what it started.

    The calling session's process, and the helpers it started there, may sit
    in the worktree it is cleaning up; another session's processes there mean
    the worktree is still in use. A process that ended between the two
    listings, such as lsof itself, no longer uses it.
    """
    try:
        listed = run_required(["lsof", "-a", "-d", "cwd", "-F", "pn"])
        parents = parse_parent_processes(run_required(["ps", "-axo", "pid=,ppid="]))
    except (CommandError, ValueError) as error:
        return [f"could not list the processes working in it ({error}), so it may still be in use"]
    else:
        running = set(parents)
        inside = {
            pid for pid, folder in parse_process_folders(listed) if pid in running and is_inside(folder, worktree)
        }
        users = sorted(inside - list_own_processes(parents, inside))

        if users:
            return [f"processes still work in it: {', '.join(str(pid) for pid in users)}"]
        else:
            return []


def list_integration_problems(
    worktree: Path,
    original: Path,
    policy: WorkflowPolicy,
    branch: str,
    tip: str,
) -> list[str]:
    if policy.mode == WorkflowMode.PULL_REQUEST:
        pull_list = ["gh", "pr", "list", "--head", branch, "--state", "merged", "--json", PULL_REQUEST_FIELDS]
        merged = read_json(pull_list, worktree)
        heads = {str(entry.get("headRefOid")) for entry in merged if isinstance(entry, dict)}

        if tip in heads:
            return []
        else:
            return [
                f"no merged pull request has {branch}'s last commit {tip[:12]}; merge it first, or pass --abandoned"
            ]
    elif is_ancestor(original, tip, BRANCH_REF_PREFIX + policy.base):
        return []
    else:
        return [f"{branch}'s last commit {tip[:12]} is not in {policy.base}; integrate it first, or pass --abandoned"]


def remove(worktree: Path, original: Path, branch: str, tip: str, abandoned: bool) -> None:
    git(original, "worktree", "remove", str(worktree))
    print(f"Removed the worktree {worktree}.")
    if abandoned:
        print(f"Kept the branch {branch} at {tip[:12]}, since the task was abandoned.")
    else:
        git(original, "update-ref", "-d", BRANCH_REF_PREFIX + branch, tip)
        print(f"Deleted the local branch {branch}, whose last commit {tip[:12]} is integrated.")
        remove_remote_branch(original, branch, tip)


def remove_remote_branch(original: Path, branch: str, tip: str) -> None:
    """Delete the remote branch only while it points at the integrated commit; GitHub may have deleted it already."""
    ref = BRANCH_REF_PREFIX + branch

    try:
        listed = git(original, "ls-remote", REMOTE, ref)
        remote_tip = next((line.split()[0] for line in listed.splitlines() if line.split()[-1] == ref), "")

        if remote_tip == tip:
            git(original, "push", "--quiet", f"--force-with-lease={ref}:{tip}", REMOTE, f":{ref}")
            print(f"Deleted {branch} on {REMOTE}.")
        elif remote_tip:
            print(f"Kept {branch} on {REMOTE}: it points at {remote_tip[:12]}, not the integrated commit.")
        else:
            print(f"{branch} is not on {REMOTE}.")
    except CommandError as error:
        print(f"The worktree and the local branch are removed, but {branch} on {REMOTE} is not ({error}).")


def check_linked_worktree(worktree: Path) -> None:
    if find_repository_root(worktree) != worktree:
        raise NotReadyError([f"{worktree} is not the top folder of a Git worktree"])
    elif is_original_checkout(worktree):
        raise NotReadyError([f"{worktree} is the original checkout, not a task's worktree"])


def read_status(worktree: Path) -> str:
    """The raw status output; its first entry starts with a space, which a stripped answer would lose."""
    arguments = ["git", "-C", str(worktree), "status", "--porcelain", "--ignored", "--untracked-files=all", "-z"]
    result = run_command(arguments)

    if result.returncode == 0:
        return result.stdout
    else:
        raise CommandError(f"`git status` failed in {worktree}: {result.stderr.strip()}")


def parse_status_entries(output: str) -> list[tuple[str, str]]:
    """Status and path pairs from `git status --porcelain -z`, skipping the source path a rename adds."""
    fields = output.split(STATUS_ENTRY_SEPARATOR)
    entries = []
    index = 0

    while index < len(fields):
        field = fields[index]
        status = field[: STATUS_PATH_START - 1]

        if field:
            entries.append((status, field[STATUS_PATH_START:]))
        index += 2 if status.startswith(RENAMED_STATUSES) else 1
    return entries


def parse_process_folders(output: str) -> list[tuple[int, Path]]:
    """Pairs of process ID and current folder from `lsof -F pn`, whose lines start with their field letter."""
    pairs = []
    pid = 0

    for line in output.splitlines():
        if line.startswith(PROCESS_FIELD):
            pid = int(line[1:])
        elif line.startswith(NAME_FIELD) and pid:
            pairs.append((pid, Path(line[1:])))
    return pairs


def parse_parent_processes(output: str) -> dict[int, int]:
    pairs = (line.split() for line in output.splitlines())

    return {int(pid): int(parent) for pid, parent in pairs}


def list_own_processes(parents: dict[int, int], inside: set[int]) -> set[int]:
    """This script's ancestors, plus everything the highest of them working in the worktree started."""
    ancestors = list_ancestors(parents, os.getpid())
    owners = [pid for pid in ancestors if pid in inside]
    session = owners[-1] if owners else os.getpid()
    descendants = {pid for pid in parents if session in list_ancestors(parents, pid)}

    return set(ancestors) | descendants


def list_ancestors(parents: dict[int, int], pid: int) -> list[int]:
    """The process and its ancestors, nearest first."""
    chain = []

    while pid > 1 and pid not in chain:
        chain.append(pid)
        pid = parents.get(pid, 0)
    return chain


def show_paths(paths: Sequence[str]) -> str:
    shown = ", ".join(paths[:MAX_SHOWN_PATHS])

    return f"{shown} and {len(paths) - MAX_SHOWN_PATHS} more" if len(paths) > MAX_SHOWN_PATHS else shown


def is_regenerable(path: str, patterns: Sequence[str]) -> bool:
    parts = PurePosixPath(path.rstrip("/")).parts

    return any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in patterns)


def is_inside(folder: Path, worktree: Path) -> bool:
    resolved = folder.resolve()

    return resolved == worktree or worktree in resolved.parents


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
