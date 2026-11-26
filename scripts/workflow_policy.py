"""The Git workflow a repository records in .agent-workflow.json.

Repositories that give every writing task its own worktree name their mode
there, with the base branch and the checks that guard it. The merge and
integration tools read the file from the base branch as fetched, so a task
branch cannot loosen its own gate; the hooks read the original checkout's
copy, which needs no Git command.

A repository that commits straight to its base branch records the direct
mode, and a team's repository records the team mode in a copy Git does not
track; neither uses worktrees, so the hooks leave them alone and the shell
guard asks before a merge there. A repository without the file records no
workflow at all: the commit wrapper commits nothing there until one is
recorded (user decision, 2026-11-17).
"""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from integration_commands import CommandError

POLICY_FILE = ".agent-workflow.json"
GIT_DIRECTORY_NAME = ".git"
GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE")
_MODE_KEY = "mode"
_BASE_KEY = "base"
_REQUIRED_CHECKS_KEY = "requiredChecks"
_CHECK_COMMANDS_KEY = "checkCommands"
_CLEANUP_IGNORED_KEY = "cleanupIgnored"
_ENCODING = "utf-8"
_GIT_TIMEOUT_SECONDS = 10
_CLEANUP_EXCEPTION_PREFIX = "!"
_CLEANUP_COMMENT_PREFIX = "#"
_NULL_CHARACTER = "\0"
_ESCAPE = "\\"
_TRAILING_BLANKS = " \t"
_PATTERN_SYNTAX = "*?/"
_ESCAPED_CHARACTER = re.compile(r"\\(.)", re.DOTALL)
_CHARACTER_RANGE = re.compile(r"\[!?\]?[^\]]*\]")
_LITERAL_STAND_IN = "x"


class WorkflowMode(str, Enum):
    PULL_REQUEST = "pull-request"
    LOCAL_INTEGRATION = "local-integration"
    DIRECT = "direct"
    TEAM = "team"

    @property
    def is_worktree_mode(self) -> bool:
        """Whether every writing task gets its own worktree and the original checkout stays read-only."""
        return self in (WorkflowMode.PULL_REQUEST, WorkflowMode.LOCAL_INTEGRATION)


class PolicyError(Exception):
    """The workflow file exists but does not say what the tools need."""


class GitReadError(CommandError):
    """Git, or a file Git keeps for a worktree, could not answer what a guard or tool needs.

    This is not the same as there being nothing to find, so a guard refuses the call instead of reading it as safe
    (user decision, 2026-11-24), and the tools, which already stop on a CommandError, stop as before.
    """


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
    """The policy in a checkout's working tree, or None when the repository records none; something other than a
    file in its place raises PolicyError, since the repository may record one there that cannot be read."""
    path = root / POLICY_FILE

    if path.is_file():
        return parse_policy(_read_policy_text(path), str(path))
    elif os.path.lexists(path):
        raise PolicyError(f"{path} is not a file")
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
    """The nearest folder at or above a path holding `.git`: a folder in an original checkout, a file in a worktree.

    Callers pass any word of a command that may be a path, so a name no file can have, such as one longer than the
    system allows, counts as missing (os.path.exists) instead of raising as pathlib does. The `.git` entry of an
    existing folder is read strictly: one that is there but cannot be examined, such as a link that loops, counts as
    there, and one the system cannot examine at all raises GitReadError, so a damaged repository never reads as a
    folder outside any repository (user decision, 2026-11-24).
    """
    existing = next((folder for folder in [path, *path.parents] if os.path.exists(folder)), None)
    folders = [existing, *existing.parents] if existing else []

    return next((folder for folder in folders if _has_git_entry(folder)), None)


def has_git_location_variable() -> bool:
    """Whether the environment names a repository for Git through its location variables, as Git reads them."""
    return any(name in os.environ for name in GIT_LOCATION_VARIABLES)


def _has_git_entry(folder: Path) -> bool:
    try:
        os.lstat(folder / GIT_DIRECTORY_NAME)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as error:
        if error.errno == errno.ENAMETOOLONG:
            return False
        else:
            raise GitReadError(f"{folder / GIT_DIRECTORY_NAME} cannot be examined: {error}") from error
    else:
        return True


def read_common_git_directory(root: Path) -> Path:
    """The Git folder a checkout shares with its worktrees. Raises GitReadError when Git cannot tell."""
    result = _run_git(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])

    if result is not None and result.returncode == 0:
        return Path(result.stdout.strip())
    else:
        raise GitReadError(f"Git could not find the repository folder of {root}: {_describe_git_failure(result)}")


def read_original_checkout(root: Path) -> Path | None:
    """The original checkout that a worktree, or the original checkout itself, belongs to; None for a repository whose
    Git folder lies elsewhere, such as a submodule's. Raises GitReadError when Git cannot tell."""
    common = read_common_git_directory(root)

    if is_original_checkout(common.parent):
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


def check_cleanup_patterns(patterns: Sequence[str], source: str) -> None:
    """Git takes these patterns on its command line, which skips the .gitignore file grammar, so the parts of it a
    reader expects are refused rather than read as names. Only the cleanup tool calls this: the hooks read the same
    file and refuse the calls they cannot judge when reading it fails.
    """
    findings = [(pattern, _find_cleanup_pattern_problem(pattern)) for pattern in patterns]
    refused = [f"{pattern!r} {problem}" for pattern, problem in findings if problem]

    if refused:
        raise PolicyError(
            f"{source} lists {_CLEANUP_IGNORED_KEY!r} patterns this tool cannot use: {'; '.join(refused)}"
        )


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


def _describe_git_failure(result: subprocess.CompletedProcess[str] | None) -> str:
    if result is None:
        return "git did not run or did not finish in time"
    else:
        return result.stderr.strip() or f"exit code {result.returncode}"


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


def _read_policy_text(path: Path) -> str:
    """A file that cannot be read records no usable workflow, which the callers report like a malformed one."""
    try:
        return path.read_text(encoding=_ENCODING)
    except (OSError, UnicodeDecodeError) as error:
        raise PolicyError(f"{path} cannot be read: {error}") from error


def _find_cleanup_pattern_problem(pattern: str) -> str | None:
    if pattern.startswith(_CLEANUP_EXCEPTION_PREFIX):
        return f"starts with {_CLEANUP_EXCEPTION_PREFIX!r}, an exception the list does not support"
    elif pattern.startswith(_CLEANUP_COMMENT_PREFIX):
        return f"starts with {_CLEANUP_COMMENT_PREFIX!r}, a comment in .gitignore; escape it with a backslash"
    elif _NULL_CHARACTER in pattern:
        return "holds a null character"
    elif _has_unescaped_trailing_blank(pattern):
        return "ends in an unescaped space or tab, which this list refuses; escape a kept space with a backslash"
    elif _has_lone_trailing_escape(pattern):
        return "ends in a backslash that escapes nothing, so it matches no file"
    elif _is_wildcard_only(pattern):
        return "names no file or folder, only wildcards, character ranges, or slashes"
    else:
        return None


def _count_trailing_escapes(text: str) -> int:
    return len(text) - len(text.rstrip(_ESCAPE))


def _has_unescaped_trailing_blank(pattern: str) -> bool:
    """A single trailing blank is escaped by an odd run of backslashes before it; any other trailing blank is not."""
    kept = pattern.rstrip(_TRAILING_BLANKS)
    blanks = len(pattern) - len(kept)
    is_escaped = blanks == 1 and _count_trailing_escapes(kept) % 2 == 1

    return blanks > 0 and not is_escaped


def _has_lone_trailing_escape(pattern: str) -> bool:
    return _count_trailing_escapes(pattern) % 2 == 1


def _is_wildcard_only(pattern: str) -> bool:
    """An escaped character is part of a name, and a character range such as `[a-z]` stands for any letter in it."""
    literal = _ESCAPED_CHARACTER.sub(_LITERAL_STAND_IN, pattern)
    unranged = _CHARACTER_RANGE.sub("", literal)

    return all(character in _PATTERN_SYNTAX for character in unranged)


def _is_command(command: Any) -> bool:
    return isinstance(command, list) and bool(command) and all(isinstance(word, str) and word for word in command)
