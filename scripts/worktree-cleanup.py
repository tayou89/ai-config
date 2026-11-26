#!/usr/bin/env python3
"""worktree-cleanup.py — remove a task's worktree and branch only after its work is preserved.

Usage:
    worktree-cleanup.py <worktree> [--abandoned]

Cleanup is its own step after the merge, so a cleanup that fails never
repeats a merge, and it removes nothing it cannot show is kept elsewhere
(user decision, 2026-11-13). It refuses while the worktree holds
uncommitted or untracked files, ignored files that .agent-workflow.json's
cleanupIgnored does not list as regenerable, a folder holding its own Git
repository, whose commits may exist nowhere else, or a process whose working
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

cleanupIgnored holds positive Git path patterns read from the repository
root, which Git itself matches with letter case kept: `build` is only the
top entry of that name, `app/build/` that folder, `**/node_modules` the name
at any depth. They reach Git's command line, not a .gitignore file, so a
leading `!` or `#`, an unescaped trailing space or tab, a lone trailing
backslash, a null character, and patterns that name no file (only
wildcards, character ranges, and slashes) are refused.

Exit codes:
    0  removed
    2  not removed: something in it is not preserved; the reasons are printed
    3  the repository records no worktree workflow, or its file is wrong
    4  a git, gh, or lsof command failed
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from integration_commands import (
    BRANCH_REF_PREFIX,
    ENCODING,
    EXIT_COMMAND_FAILED,
    EXIT_DONE,
    EXIT_NO_POLICY,
    EXIT_NOT_READY,
    REMOTE,
    CommandError,
    NotReadyError,
    git,
    is_ancestor,
    list_worktree_users,
    print_problems,
    read_current_branch,
    read_json,
    run_binary_command,
)
from workflow_policy import (
    NO_ORIGINAL_CHECKOUT_MESSAGE,
    POLICY_FILE,
    PolicyError,
    WorkflowMode,
    WorkflowPolicy,
    check_cleanup_patterns,
    find_repository_root,
    is_original_checkout,
    read_original_checkout,
    read_original_policy,
)

IGNORED_STATUS = b"!!"
STATUS_PATH_START = 3
ENTRY_SEPARATOR = b"\0"
FOLDER_ENTRY_SUFFIX = b"/"
RENAMED_STATUS_LETTERS = frozenset(b"RC")
REPOSITORY_ENTRY = b".git"
BARE_REPOSITORY_ENTRIES = frozenset((b"HEAD", b"objects", b"refs"))
ROOT_ANCHOR = "/"
DISPLAY_ERRORS = "backslashreplace"
STATUS_ARGUMENTS = ("status", "--porcelain", "--ignored", "--untracked-files=all", "-z")
VOUCHED_ARGUMENTS = ("-c", "core.ignorecase=false", "ls-files", "--others", "--ignored", "-z")
REPOSITORY_PROBLEM = "folders holding their own Git repository, whose commits may exist nowhere else"
KEPT_PROBLEM = "ignored files that cleanupIgnored does not list as regenerable (its patterns are read from the repository root; write **/<name> for any depth)"
MAX_SHOWN_PATHS = 5
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
        raise CommandError(NO_ORIGINAL_CHECKOUT_MESSAGE.format(folder=worktree))
    else:
        return original


def read_cleanup_policy(original: Path) -> WorkflowPolicy:
    policy = read_original_policy(original)

    if policy is None:
        raise PolicyError(
            f"{original} records no worktree workflow, so this tool cannot tell how its work is integrated"
        )
    elif not policy.mode.is_worktree_mode:
        raise PolicyError(
            f"{original} records the {policy.mode.value} workflow, which gives tasks no worktrees to clean up"
        )
    else:
        check_cleanup_patterns(policy.cleanup_ignored, f"{POLICY_FILE} in {original}")
        return policy


def list_branch_problems(branch: str) -> list[str]:
    if branch:
        return []
    else:
        return ["the worktree has no branch checked out, so nothing shows where its commits are kept"]


def list_file_problems(worktree: Path, patterns: Sequence[str]) -> list[str]:
    entries = parse_status_entries(_read_git_output(worktree, *STATUS_ARGUMENTS))
    ignored = [path for status, path in entries if status == IGNORED_STATUS]
    unsaved = [path for status, path in entries if status != IGNORED_STATUS]
    repositories = _list_nested_repositories(worktree)
    vouched = _list_vouched_paths(worktree, patterns)
    kept = [path for path in ignored if not _is_vouched(path, vouched) and not _is_inside_any(path, repositories)]
    problems = []

    if unsaved:
        problems.append(f"uncommitted or untracked files: {show_paths(unsaved)}")
    if repositories:
        problems.append(f"{REPOSITORY_PROBLEM}: {show_paths(repositories)}")
    if kept:
        problems.append(f"{KEPT_PROBLEM}: {show_paths(kept)}")
    return problems


def list_process_problems(worktree: Path) -> list[str]:
    """Another session's processes working in the worktree mean it is still in use."""
    try:
        users = list_worktree_users(worktree)
    except CommandError as error:
        return [f"could not list the processes working in it ({error}), so it may still be in use"]
    else:
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


def parse_status_entries(output: bytes) -> list[tuple[bytes, bytes]]:
    """Status and path pairs from `git status --porcelain -z`, skipping the source path a rename or copy adds."""
    fields = output.split(ENTRY_SEPARATOR)
    entries = []
    index = 0

    while index < len(fields):
        field = fields[index]
        status = field[: STATUS_PATH_START - 1]

        if field:
            entries.append((status, field[STATUS_PATH_START:]))
        index += 2 if RENAMED_STATUS_LETTERS & set(status) else 1
    return entries


def show_paths(paths: Sequence[bytes]) -> str:
    """Each name quoted with its escapes, so a backslash, a line break, and an unreadable byte stay distinguishable."""
    shown = ", ".join(repr(os.fsdecode(path)) for path in paths[:MAX_SHOWN_PATHS])

    return f"{shown} and {len(paths) - MAX_SHOWN_PATHS} more" if len(paths) > MAX_SHOWN_PATHS else shown


def _read_git_output(worktree: Path, *arguments: str) -> bytes:
    """Raw bytes, so a file name reaches the comparison unchanged and a first entry keeps its leading space."""
    result = run_binary_command(["git", "-C", str(worktree), *arguments])

    if result.returncode == 0:
        return result.stdout
    else:
        error = result.stderr.decode(ENCODING, DISPLAY_ERRORS).strip()

        raise CommandError(f"`git {' '.join(arguments)}` failed in {worktree}: {error}")


def _list_vouched_paths(worktree: Path, patterns: Sequence[str]) -> set[bytes]:
    """The untracked paths Git matches to cleanupIgnored; without a pattern Git would refuse to list any."""
    if patterns:
        excludes = [f"--exclude={_anchor_pattern(pattern)}" for pattern in patterns]
        output = _read_git_output(worktree, *VOUCHED_ARGUMENTS, *excludes)

        return set(output.split(ENTRY_SEPARATOR)) - {b""}
    else:
        return set()


def _list_nested_repositories(worktree: Path) -> list[bytes]:
    """Folders below the top holding a `.git` folder, file, or link, or entries named like a bare repository's.

    Git lists a bare repository as plain files and skips a `.git` inside a folder it tracks, so its status cannot
    show them all. The names alone decide, so a folder that only looks like a repository also keeps the worktree.
    Links are not followed, since removing a link leaves its target. Every error stops the search: os.walk would
    treat a folder whose kind cannot be read as a file and skip what is inside it.
    """
    root = os.fsencode(worktree)
    repositories = []
    pending = [root]

    try:
        while pending:
            folder = pending.pop()
            entries = _list_folder_entries(folder)
            names = {entry.name for entry in entries}

            if folder != root and (REPOSITORY_ENTRY in names or names.issuperset(BARE_REPOSITORY_ENTRIES)):
                repositories.append(os.path.relpath(folder, root))
            else:
                pending.extend(entry.path for entry in entries if entry.is_dir(follow_symlinks=False))
    except OSError as error:
        raise CommandError(f"could not look through {worktree} for repositories inside it: {error}") from error
    else:
        return repositories


def _list_folder_entries(folder: bytes) -> list[os.DirEntry[bytes]]:
    with os.scandir(folder) as entries:
        return list(entries)


def _anchor_pattern(pattern: str) -> str:
    """Git matches a pattern whose only slash is a trailing one at any depth; this list reads each from the root."""
    return pattern if pattern.startswith(ROOT_ANCHOR) else ROOT_ANCHOR + pattern


def _is_vouched(path: bytes, vouched: set[bytes]) -> bool:
    """A folder entry is one Git did not look inside, so no pattern vouches for it."""
    return not path.endswith(FOLDER_ENTRY_SUFFIX) and path in vouched


def _is_inside_any(path: bytes, folders: Sequence[bytes]) -> bool:
    return any(path.startswith(folder + FOLDER_ENTRY_SUFFIX) for folder in folders)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
