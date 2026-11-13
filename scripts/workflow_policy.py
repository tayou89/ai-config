"""The Git workflow a repository records in .agent-workflow.json.

Repositories that give every writing task its own worktree name their mode
there, with the base branch and the checks that guard it. The merge and
integration tools read the file from the base branch as fetched, so a task
branch cannot loosen its own gate; the hooks read the original checkout's
copy, which needs no Git command.

A repository without the file records no worktree workflow: the hooks leave
it alone, and the shell guard asks before a merge there.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

POLICY_FILE = ".agent-workflow.json"
GIT_DIRECTORY_NAME = ".git"
_MODE_KEY = "mode"
_BASE_KEY = "base"
_REQUIRED_CHECKS_KEY = "requiredChecks"
_CHECK_COMMANDS_KEY = "checkCommands"
_CLEANUP_IGNORED_KEY = "cleanupIgnored"
_ENCODING = "utf-8"
_GIT_TIMEOUT_SECONDS = 10


class WorkflowMode(str, Enum):
    PULL_REQUEST = "pull-request"
    LOCAL_INTEGRATION = "local-integration"


class PolicyError(Exception):
    """The workflow file exists but does not say what the tools need."""


@dataclass(frozen=True)
class WorkflowPolicy:
    mode: WorkflowMode
    base: str
    required_checks: tuple[str, ...]
    check_commands: tuple[tuple[str, ...], ...]
    cleanup_ignored: tuple[str, ...]


def parse_policy(text: str, source: str) -> WorkflowPolicy:
    """Read the file's JSON; a pull-request policy without required checks is an error, since no check is no gate."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise PolicyError(f"{source} is not valid JSON: {error}") from error
    else:
        policy = WorkflowPolicy(
            mode=_read_mode(data, source),
            base=_read_text(data, _BASE_KEY, source),
            required_checks=_read_texts(data, _REQUIRED_CHECKS_KEY, source),
            check_commands=_read_commands(data, source),
            cleanup_ignored=_read_texts(data, _CLEANUP_IGNORED_KEY, source),
        )

        _check_mode_fields(policy, source)
        return policy


def read_checkout_policy(root: Path) -> WorkflowPolicy | None:
    """The policy in a checkout's working tree, or None when the repository records none."""
    path = root / POLICY_FILE

    if path.is_file():
        return parse_policy(path.read_text(encoding=_ENCODING), str(path))
    else:
        return None


def read_committed_policy(root: Path, ref: str) -> WorkflowPolicy | None:
    """The policy as committed at a ref such as refs/remotes/origin/main, or None when that commit has no file."""
    result = _run_git(root, ["show", f"{ref}:{POLICY_FILE}"])

    if result is not None and result.returncode == 0:
        return parse_policy(result.stdout, f"{POLICY_FILE} at {ref}")
    else:
        return None


def find_repository_root(path: Path) -> Path | None:
    """The nearest folder at or above a path holding `.git`: a folder in an original checkout, a file in a worktree."""
    existing = next((folder for folder in [path, *path.parents] if folder.exists()), None)
    folders = [existing, *existing.parents] if existing else []

    return next((folder for folder in folders if (folder / GIT_DIRECTORY_NAME).exists()), None)


def read_common_git_directory(root: Path) -> Path | None:
    """The Git folder a checkout shares with its worktrees, or None when Git cannot tell."""
    result = _run_git(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])

    if result is not None and result.returncode == 0:
        return Path(result.stdout.strip())
    else:
        return None


def read_original_checkout(root: Path) -> Path | None:
    """The original checkout that a worktree, or the original checkout itself, belongs to."""
    common = read_common_git_directory(root)

    if common is not None and is_original_checkout(common.parent):
        return common.parent
    else:
        return None


def read_original_policy(original: Path) -> WorkflowPolicy | None:
    """The policy committed on the original checkout's branch, which no task branch can change."""
    if is_original_checkout(original):
        return read_committed_policy(original, "HEAD")
    else:
        return None


def is_original_checkout(root: Path) -> bool:
    """Whether a repository root is the checkout the repository was cloned into, not a linked worktree."""
    return (root / GIT_DIRECTORY_NAME).is_dir()


def _run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding=_ENCODING,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _read_mode(data: Any, source: str) -> WorkflowMode:
    text = _read_text(data, _MODE_KEY, source)

    try:
        return WorkflowMode(text)
    except ValueError as error:
        modes = ", ".join(mode.value for mode in WorkflowMode)
        raise PolicyError(f"{source} names the mode {text!r}; use one of {modes}") from error


def _read_text(data: Any, key: str, source: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None

    if isinstance(value, str) and value:
        return value
    else:
        raise PolicyError(f"{source} needs a text value for {key!r}")


def _read_texts(data: Any, key: str, source: str) -> tuple[str, ...]:
    value = data.get(key, []) if isinstance(data, dict) else []

    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    else:
        raise PolicyError(f"{source} needs a list of names for {key!r}")


def _read_commands(data: Any, source: str) -> tuple[tuple[str, ...], ...]:
    value = data.get(_CHECK_COMMANDS_KEY, []) if isinstance(data, dict) else []

    if isinstance(value, list) and all(_is_command(command) for command in value):
        return tuple(tuple(command) for command in value)
    else:
        raise PolicyError(f"{source} needs {_CHECK_COMMANDS_KEY!r} as a list of commands, each a list of words")


def _check_mode_fields(policy: WorkflowPolicy, source: str) -> None:
    if policy.mode == WorkflowMode.PULL_REQUEST and not policy.required_checks:
        raise PolicyError(f"{source} lists no {_REQUIRED_CHECKS_KEY!r}, so no merge could be checked")
    elif policy.mode == WorkflowMode.LOCAL_INTEGRATION and not policy.check_commands:
        raise PolicyError(f"{source} lists no {_CHECK_COMMANDS_KEY!r}, so no integration could be checked")


def _is_command(command: Any) -> bool:
    return isinstance(command, list) and bool(command) and all(isinstance(word, str) and word for word in command)
