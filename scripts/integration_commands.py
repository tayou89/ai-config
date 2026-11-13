"""What the merge, integration, and cleanup tools share: running commands, reading Git state, and exit codes."""

from __future__ import annotations

import json
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
