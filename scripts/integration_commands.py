"""What the merge, integration, cleanup, and commit tools share: running commands, reading Git state, the
processes working in a worktree, and exit codes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

EXIT_DONE = 0
EXIT_NOT_READY = 2
EXIT_NO_POLICY = 3
EXIT_COMMAND_FAILED = 4
EXIT_LOCK_TIMEOUT = 5
COMMAND_TIMEOUT_SECONDS = 120
ENCODING = "utf-8"
REMOTE = "origin"
REMOTE_REF_PREFIX = "refs/remotes/origin/"
BRANCH_REF_PREFIX = "refs/heads/"
_PROCESS_FIELD = "p"
_NAME_FIELD = "n"


class CommandError(Exception):
    """A gh or git command failed, could not start, or answered something the tool cannot use."""


class NotReadyError(Exception):
    """The step cannot go ahead yet, for the reasons listed; nothing was changed."""

    def __init__(self, problems: Sequence[str]):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def run_command(arguments: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return its result whatever its exit code; a command that cannot start or hangs is an error."""
    try:
        return subprocess.run(
            list(arguments),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding=ENCODING,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CommandError(f"`{' '.join(arguments)}` could not run: {error}") from error


def run_required(arguments: Sequence[str], cwd: Path | None = None) -> str:
    result = run_command(arguments, cwd)

    if result.returncode == 0:
        return result.stdout.strip()
    else:
        raise CommandError(f"`{' '.join(arguments)}` failed: {result.stderr.strip() or result.stdout.strip()}")


def read_json(arguments: Sequence[str], cwd: Path | None = None) -> Any:
    """A command's JSON output; output that is not JSON is the command's failure, not a crash of the tool."""
    output = run_required(arguments, cwd)

    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise CommandError(f"`{' '.join(arguments)}` did not answer JSON: {error}") from error


def git(root: Path, *arguments: str) -> str:
    return run_required(["git", "-C", str(root), *arguments])


def read_worktree_root() -> Path:
    return Path(run_required(["git", "rev-parse", "--show-toplevel"]))


def read_current_branch(root: Path) -> str:
    """The checked-out branch, or "" when HEAD is detached."""
    return run_command(["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"]).stdout.strip()


def print_problems(heading: str, problems: Sequence[str]) -> None:
    print(f"{heading}:\n" + "\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)


def announce_wait() -> None:
    print("Another integration in this repository is running; waiting for it to finish.", flush=True)


def has_tracked_changes(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain", "--untracked-files=no"))


def is_ancestor(root: Path, commit: str, ref: str) -> bool:
    return run_command(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, ref]).returncode == 0


def list_worktree_users(worktree: Path) -> list[int]:
    """Processes whose current folder is inside the worktree, other than the calling session and what it started.

    The calling session's process, and the helpers it started there, may sit
    in the worktree it works in; another session's processes there mean the
    worktree is in use by someone else. A process that ended between the two
    listings, such as lsof itself, no longer uses it. A program that only
    holds files open in the worktree, without working in it, is not seen.
    Raises CommandError when the processes cannot be listed.
    """
    listed = run_required(["lsof", "-a", "-d", "cwd", "-F", "pn"])
    parents = _parse_parent_processes(run_required(["ps", "-axo", "pid=,ppid="]))
    running = set(parents)
    inside = {pid for pid, folder in _parse_process_folders(listed) if pid in running and _is_inside(folder, worktree)}

    return sorted(inside - _list_own_processes(parents, inside))


def _parse_process_folders(output: str) -> list[tuple[int, Path]]:
    """Pairs of process ID and current folder from `lsof -F pn`, whose lines start with their field letter."""
    pairs = []
    pid = 0

    for line in output.splitlines():
        if line.startswith(_PROCESS_FIELD):
            pid = _parse_process_id(line[1:])
        elif line.startswith(_NAME_FIELD) and pid:
            pairs.append((pid, Path(line[1:])))
    return pairs


def _parse_parent_processes(output: str) -> dict[int, int]:
    return dict(_parse_parent_line(line) for line in output.splitlines())


def _parse_parent_line(line: str) -> tuple[int, int]:
    """A `ps -o pid=,ppid=` line as the process and its parent."""
    fields = line.split()

    if len(fields) == 2:
        return _parse_process_id(fields[0]), _parse_process_id(fields[1])
    else:
        raise CommandError(f"a process listing holds {line!r} where a process and its parent belong")


def _parse_process_id(text: str) -> int:
    try:
        return int(text)
    except ValueError as error:
        raise CommandError(f"a process listing holds {text!r} where a process ID belongs") from error


def _list_own_processes(parents: dict[int, int], inside: set[int]) -> set[int]:
    """This process's ancestors, plus everything the highest of them working in the worktree started."""
    ancestors = _list_ancestors(parents, os.getpid())
    owners = [pid for pid in ancestors if pid in inside]
    session = owners[-1] if owners else os.getpid()
    descendants = {pid for pid in parents if session in _list_ancestors(parents, pid)}

    return set(ancestors) | descendants


def _list_ancestors(parents: dict[int, int], pid: int) -> list[int]:
    """The process and its ancestors, nearest first."""
    chain = []

    while pid > 1 and pid not in chain:
        chain.append(pid)
        pid = parents.get(pid, 0)
    return chain


def _is_inside(folder: Path, worktree: Path) -> bool:
    resolved = folder.resolve()

    return resolved == worktree or worktree in resolved.parents
