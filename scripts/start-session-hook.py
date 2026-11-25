#!/usr/bin/env python3
"""PreToolUse hook for starting another session.

It denies a new session or proposal that names no model or effort, since a
session started without them inherits the starting session's level instead
of the work-type table's. When a session would start now while the computer
is overloaded, it lets the start through without asking the user and adds the
load to the session's context, using the load threshold the batch(1) command
uses: a 1-minute load average above 1.5 times the CPU count. When memory is
also short (the system's memory pressure level is critical, or it warns while
the load is above that threshold), it asks the user before the start instead,
since starting more sessions then can freeze the computer (user decision,
2026-11-23). A critical level asks even when the load cannot be read; an
unreadable pressure level keeps the load notice alone. When the hook cannot
finish its own judgment it refuses the start, and its settings entry blocks
the start when the hook cannot answer at all (`hook_failure.py`): the user
can still start the session from the app's sidebar.

A session started to share a folder (`use_worktree: false`, or a `cwd`
without its own worktree) works where that folder is. When the folder is a
task worktree, the folder guard (worktree-guard-hook.py) lets only its owner
write there, so the starter is told that the new session can only read
there, and, when the folder is the starter's own, how to hand it over
(folder_owner.py, user decision, 2026-11-18). A reading session may share
any folder, so the start itself is never refused for it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from folder_owner import (
    AccessKind,
    FolderAccess,
    OwnerFileError,
    find_linked_worktree,
    judge_access,
    read_current_identity,
)
from hook_failure import Decision, FailurePolicy, run_hook

FAILURE_POLICY = FailurePolicy.blocking(
    "start-session-hook",
    Decision.DENY,
    'The start-session hook could not check this start ({error}), so it refuses it. Tell the user, who can start the session from the app\'s sidebar; README "Hooks" (failure handling) says how to repair the hook from a terminal.',
)

_START_TOOL = "mcp__ccd_session__start_session"
_PRE_TOOL_USE = "PreToolUse"
_EVENT_KEY = "hook_event_name"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_DECISION_KEY = "permissionDecision"
_DECISION_REASON_KEY = "permissionDecisionReason"
_CONTEXT_KEY = "additionalContext"
_DECISION_DENY = "deny"
_DECISION_ASK = "ask"
_PRESSURE_COMMAND = ("sysctl", "-n", "kern.memorystatus_vm_pressure_level")
_PRESSURE_TIMEOUT_SECONDS = 2
_PRESSURE_WARN = 2
_PRESSURE_CRITICAL = 4
_PROPOSAL_KEY = "proposal_id"
_TITLE_KEY = "title"
_INITIATION_KEY = "initiation"
_LEVEL_KEYS = ("model", "effort")
_CARD_ONLY_INITIATION = "own_initiative"
_LOAD_FACTOR = 1.5
_LEVEL_REASON = (
    "Set {missing} from the work-type table in the decisions-and-collaboration skill "
    "(\"Model and effort for runs you start\"), using the highest row any part of the work needs. "
    "A session started without them inherits this session's level."
)
_USE_WORKTREE_KEY = "use_worktree"
_CWD_KEY = "cwd"
_SESSION_KEY = "session_id"
_OTHERS_FOLDER_NOTICE = (
    "The new session shares {root}, the task worktree of {owner}, which may still be open, so it can only read there: "
    "the folder guard refuses its writes. If it should write, start it with `use_worktree: true` instead."
)
_OWN_FOLDER_NOTICE = (
    "The new session shares your task worktree {root}, where only you may write: the folder guard refuses its writes. "
    "If it should write, start it with `use_worktree: true` instead, or, once it has started, hand the folder over with "
    "`python3 ~/.claude/scripts/folder_owner.py hand-over --to <its local_ id>` and stop writing there yourself."
)
_LOAD_NOTICE = (
    "The 1-minute load average is {load:.0f}, above {limit:.0f} (1.5 times {cpus} CPUs). "
    "The session starts without asking the user."
)
_MEMORY_REASON = (
    "Memory is short: the system reports memory pressure level {pressure} and the 1-minute load average is {load:.0f} "
    "(limit {limit:.0f}). Another session now may freeze this computer, so the user decides. "
    "If the user declines, keep the start queued and try again after a running session finishes."
)
_MEMORY_REASON_WITHOUT_LOAD = "Memory is short: the system reports memory pressure level {pressure}, and the load average could not be read. Another session now may freeze this computer, so the user decides. If the user declines, keep the start queued and try again after a running session finishes."


def main() -> int:
    return run_hook(_make_current_output, FAILURE_POLICY)


def _make_current_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    return make_output(hook_input, _read_load(), _read_pressure())


def _read_load() -> tuple[float, int] | None:
    try:
        return os.getloadavg()[0], os.cpu_count() or 1
    except (OSError, AttributeError):
        return None


def _read_pressure() -> int | None:
    try:
        completed = subprocess.run(
            _PRESSURE_COMMAND,
            capture_output=True,
            text=True,
            timeout=_PRESSURE_TIMEOUT_SECONDS,
            check=True,
        )

        return int(completed.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def make_output(
    hook_input: dict[str, Any],
    load: tuple[float, int] | None,
    pressure: int | None = None,
) -> dict[str, Any] | None:
    tool_input = hook_input.get(_TOOL_INPUT_KEY) or {}
    specific_output = None

    if hook_input.get(_EVENT_KEY) == _PRE_TOOL_USE and hook_input.get(_TOOL_NAME_KEY) == _START_TOOL:
        cwd = hook_input.get(_CWD_KEY)
        session_id = hook_input.get(_SESSION_KEY)
        specific_output = decide(
            tool_input,
            load,
            cwd if isinstance(cwd, str) else None,
            session_id if isinstance(session_id, str) else None,
            pressure,
        )
    if specific_output:
        return {_HOOK_OUTPUT_KEY: {"hookEventName": _PRE_TOOL_USE, **specific_output}}
    else:
        return None


def decide(
    tool_input: dict[str, Any],
    load: tuple[float, int] | None,
    cwd: str | None = None,
    session_id: str | None = None,
    pressure: int | None = None,
) -> dict[str, str] | None:
    missing = _list_missing_levels(tool_input)

    if missing:
        return {
            _DECISION_KEY: _DECISION_DENY,
            _DECISION_REASON_KEY: _LEVEL_REASON.format(missing=" and ".join(f"`{key}`" for key in missing)),
        }
    else:
        return _decide_folder_and_load(tool_input, load, pressure, _judge_shared_folder(tool_input, cwd, session_id))


def _decide_folder_and_load(
    tool_input: dict[str, Any],
    load: tuple[float, int] | None,
    pressure: int | None,
    access: FolderAccess | None,
) -> dict[str, str] | None:
    """Ask the user before a start while memory is short; otherwise pass on the folder and load notices."""
    folder_notices = _list_folder_notices(access)
    notices = [*folder_notices, *_list_load_notices(tool_input, load)]

    if pressure is not None and _is_starting_now(tool_input) and _is_memory_short(pressure, load):
        return {
            _DECISION_KEY: _DECISION_ASK,
            _DECISION_REASON_KEY: _describe_short_memory(pressure, load),
            _CONTEXT_KEY: "\n".join(folder_notices),
        }
    elif notices:
        return {_CONTEXT_KEY: "\n".join(notices)}
    else:
        return None


def _describe_short_memory(pressure: int, load: tuple[float, int] | None) -> str:
    if load is not None:
        current, cpus = load

        return _MEMORY_REASON.format(pressure=pressure, load=current, limit=_LOAD_FACTOR * cpus)
    else:
        return _MEMORY_REASON_WITHOUT_LOAD.format(pressure=pressure)


def _list_folder_notices(access: FolderAccess | None) -> list[str]:
    if access is not None and access.kind == AccessKind.OTHERS and access.owner is not None:
        return [_OTHERS_FOLDER_NOTICE.format(root=access.worktree.root, owner=access.owner.describe())]
    elif access is not None and access.kind == AccessKind.OWN:
        return [_OWN_FOLDER_NOTICE.format(root=access.worktree.root)]
    else:
        return []


def _list_load_notices(tool_input: dict[str, Any], load: tuple[float, int] | None) -> list[str]:
    if load is not None and _is_starting_now(tool_input) and _is_overloaded(*load):
        current, cpus = load

        return [_LOAD_NOTICE.format(load=current, cpus=cpus, limit=_LOAD_FACTOR * cpus)]
    else:
        return []


def _judge_shared_folder(tool_input: dict[str, Any], cwd: str | None, session_id: str | None) -> FolderAccess | None:
    """What this session may do in the task worktree the new session would share, or None when it shares none.

    An unreadable owner record checks nothing here; the folder guard records it when the session writes.
    """
    folder = _read_shared_folder(tool_input, cwd)
    worktree = find_linked_worktree(folder) if folder is not None else None
    identity = read_current_identity(session_id) if worktree is not None else None

    try:
        return judge_access(worktree, identity) if worktree is not None and identity is not None else None
    except OwnerFileError:
        return None


def _read_shared_folder(tool_input: dict[str, Any], cwd: str | None) -> Path | None:
    """The folder the new session works in when it gets no worktree of its own: its `cwd`, or this session's."""
    named = tool_input.get(_CWD_KEY)
    use_worktree = tool_input.get(_USE_WORKTREE_KEY)

    if isinstance(named, str) and named and use_worktree is not True:
        return Path(os.path.expanduser(named)).resolve()
    elif use_worktree is False and cwd:
        return Path(cwd).resolve()
    else:
        return None


def _list_missing_levels(tool_input: dict[str, Any]) -> list[str]:
    if _is_proposal_start(tool_input):
        return []
    else:
        return [key for key in _LEVEL_KEYS if not tool_input.get(key)]


def _is_proposal_start(tool_input: dict[str, Any]) -> bool:
    return bool(tool_input.get(_PROPOSAL_KEY)) and not tool_input.get(_TITLE_KEY)


def _is_starting_now(tool_input: dict[str, Any]) -> bool:
    return _is_proposal_start(tool_input) or tool_input.get(_INITIATION_KEY) != _CARD_ONLY_INITIATION


def _is_overloaded(load: float, cpus: int) -> bool:
    return load > _LOAD_FACTOR * cpus


def _is_memory_short(pressure: int, load: tuple[float, int] | None) -> bool:
    """A critical level is short on its own; a warning counts only with a load that was read and is above the limit."""
    return pressure >= _PRESSURE_CRITICAL or (pressure >= _PRESSURE_WARN and load is not None and _is_overloaded(*load))


if __name__ == "__main__":
    sys.exit(main())
