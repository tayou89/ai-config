#!/usr/bin/env python3
"""PreToolUse hook: keep the original checkout of a worktree repository read-only.

A repository whose .agent-workflow.json names a worktree mode gives every
writing task its own worktree and branch, because sessions that wrote in
one shared folder mixed each other's changes (user decision, 2026-11-13).
Rules alone left sessions writing in the original folder, so this hook
refuses there:

  - Edit, Write, and NotebookEdit of a file Git does not ignore
  - shell redirections, `tee`, in-place `sed` or `perl`, and file commands
    (`rm`, `mv`, `touch`, `mkdir`, the destination of `cp` or `ln`) on such a file
  - Git commands that change the working tree, the index, or HEAD there,
    and the commit wrapper run there

Reads, `git fetch`, and `git worktree add` pass, so resident sessions can
read there and start worktrees from it. Files Git ignores, such as
claude-plans/, stay writable, since plans must outlive a task's worktree.
In any folder of such a repository it also refuses `git worktree remove`
and `git worktree move`, which skip the preservation checks of
worktree-cleanup.py.

What a script file or a program does inside itself is not seen, as in the
skill gate. Any failure lets the call through, since a wrong refusal stops
every session's work.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from shell_command import CommandCall, ExpansionLimitError, expand_shell_command, parse_git_command
from workflow_policy import (
    GIT_DIRECTORY_NAME,
    find_repository_root,
    is_original_checkout,
    read_checkout_policy,
    read_original_checkout,
)

_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_FILE_PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
_SHELL_TOOLS = frozenset({"Bash", "Monitor", "mcp__terminal__run_in_terminal"})
_TERMINAL_TOOL = "mcp__terminal__run_in_terminal"
_COMMAND_KEY = "command"
_WRITING_GIT_SUBCOMMANDS = frozenset(
    {
        "add",
        "am",
        "apply",
        "checkout",
        "checkout-index",
        "cherry-pick",
        "clean",
        "commit",
        "merge",
        "mv",
        "pull",
        "read-tree",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stage",
        "stash",
        "switch",
        "update-index",
    },
)
_WORKTREE_SUBCOMMAND = "worktree"
_UNGUARDED_WORKTREE_ACTIONS = frozenset({"remove", "move"})
_COMMIT_WRAPPER_NAME = "claude-commit.py"
_EVERY_OPERAND_WRITERS = frozenset({"rm", "rmdir", "unlink", "mv", "touch", "mkdir", "truncate"})
_DESTINATION_WRITERS = frozenset({"cp", "ln", "install", "rsync"})
_OPTION_PREFIX = "-"
_GIT_TIMEOUT_SECONDS = 5
_DENY = "deny"
_HOOK_EVENT_NAME = "PreToolUse"
_READ_ONLY_REASON = (
    "{root} is the original checkout of a repository whose .agent-workflow.json gives every writing task "
    "its own worktree, so it stays read-only ({what}). Enter a worktree first (EnterWorktree, or a session "
    "started with use_worktree) and write there; files Git ignores, such as claude-plans/, stay writable here."
)
_WORKTREE_REMOVAL_REASON = (
    "Remove a task's worktree with ~/.claude/scripts/worktree-cleanup.py, which first checks that its work "
    "is committed, integrated, and not in use ({what})."
)


def main() -> None:
    try:
        decision = decide(json.load(sys.stdin))

        if decision:
            print(json.dumps(decision))
    except Exception as error:
        print(f"worktree-guard-hook: {type(error).__name__}: {error}", file=sys.stderr)


def decide(payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    cwd = None if tool_name == _TERMINAL_TOOL and tool_input.get("tab_id") else payload.get("cwd")
    file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))

    if isinstance(file_path, str) and file_path:
        reasons = _list_file_reasons(_join(cwd, file_path), f"{tool_name} {os.path.basename(file_path)}")
    elif tool_name in _SHELL_TOOLS:
        reasons = _list_command_reasons(str(tool_input.get(_COMMAND_KEY) or ""), cwd)
    else:
        reasons = []
    if reasons:
        return _make_decision(reasons)
    else:
        return None


def _list_command_reasons(command: str, cwd: str | None) -> list[str]:
    try:
        calls = expand_shell_command(command, cwd, _get_config_directory())
    except ExpansionLimitError:
        return []
    else:
        return [reason for call in calls for reason in _list_call_reasons(call)]


def _list_call_reasons(call: CommandCall) -> list[str]:
    written = [path for path in [*call.list_written_files(), *_list_file_command_targets(call)] if path and call.cwd]
    file_reasons = [
        reason for path in written for reason in _list_file_reasons(_join(call.cwd, path), f"writes {path}")
    ]
    git_command = parse_git_command(call.argv, call.cwd)
    work_dir = git_command.work_dir if git_command else call.cwd

    if git_command and work_dir and git_command.subcommand in _WRITING_GIT_SUBCOMMANDS:
        return [*file_reasons, *_list_folder_reasons(work_dir, f"git {git_command.subcommand}")]
    elif git_command and work_dir and _is_unguarded_worktree_change(git_command.subcommand, git_command.arguments):
        return [*file_reasons, *_list_worktree_removal_reasons(work_dir, f"git worktree {git_command.arguments[0]}")]
    elif call.cwd and any(os.path.basename(argument) == _COMMIT_WRAPPER_NAME for argument in call.argv):
        return [*file_reasons, *_list_folder_reasons(call.cwd, "the commit wrapper")]
    else:
        return file_reasons


def _list_file_reasons(path: Path, what: str) -> list[str]:
    root = _find_guarded_original(path)

    if root and not _is_inside(path, root / GIT_DIRECTORY_NAME) and not _is_ignored(root, path):
        return [_READ_ONLY_REASON.format(root=root, what=what)]
    else:
        return []


def _list_folder_reasons(folder: str, what: str) -> list[str]:
    root = _find_guarded_original(Path(folder))

    if root:
        return [_READ_ONLY_REASON.format(root=root, what=what)]
    else:
        return []


def _list_file_command_targets(call: CommandCall) -> list[str]:
    """What file commands change: every operand of `rm` or `touch`, the destination of `cp` or `ln`."""
    program = os.path.basename(call.argv[0]) if call.argv else ""
    operands = [argument for argument in call.argv[1:] if not argument.startswith(_OPTION_PREFIX)]

    if program in _EVERY_OPERAND_WRITERS:
        return operands
    elif program in _DESTINATION_WRITERS and operands:
        return [operands[-1]]
    else:
        return []


def _list_worktree_removal_reasons(folder: str, what: str) -> list[str]:
    root = find_repository_root(Path(folder))
    original = read_original_checkout(root) if root else None

    if original and read_checkout_policy(original) is not None:
        return [_WORKTREE_REMOVAL_REASON.format(what=what)]
    else:
        return []


def _find_guarded_original(path: Path) -> Path | None:
    """The repository root when the path is in an original checkout whose workflow uses worktrees, else None."""
    root = find_repository_root(path)

    if root and is_original_checkout(root) and read_checkout_policy(root) is not None:
        return root
    else:
        return None


def _make_decision(reasons: list[str]) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": _HOOK_EVENT_NAME,
            "permissionDecision": _DENY,
            "permissionDecisionReason": "\n".join(dict.fromkeys(reasons)),
        },
    }


def _join(cwd: str | None, path: str) -> Path:
    """The absolute path with symbolic links resolved, so a linked folder cannot hide the original checkout."""
    expanded = os.path.expanduser(path)

    return Path(os.path.join(cwd or os.getcwd(), expanded)).resolve()


def _run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _get_config_directory() -> Path:
    return Path(os.path.expanduser(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY))


def _is_ignored(root: Path, path: Path) -> bool:
    return _run_git(root, ["check-ignore", "--quiet", str(path)]).returncode == 0


def _is_inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _is_unguarded_worktree_change(subcommand: str, arguments: list[str]) -> bool:
    return subcommand == _WORKTREE_SUBCOMMAND and bool(arguments) and arguments[0] in _UNGUARDED_WORKTREE_ACTIONS


if __name__ == "__main__":
    main()
