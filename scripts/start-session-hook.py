#!/usr/bin/env python3
"""PreToolUse hook for starting another session.

It denies a new session or proposal that names no model or effort, since a
session started without them inherits the starting session's level instead
of the work-type table's. When a session would start now while the computer
is overloaded, it asks the user first, using the load threshold the batch(1)
command uses: a 1-minute load average above 1.5 times the CPU count.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_START_TOOL = "mcp__ccd_session__start_session"
_PRE_TOOL_USE = "PreToolUse"
_EVENT_KEY = "hook_event_name"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_DECISION_DENY = "deny"
_DECISION_ASK = "ask"
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
_LOAD_REASON = (
    "이 컴퓨터가 지금 과부하입니다(1분 부하 평균 {load:.0f}, CPU {cpus}개, 기준 {limit:.0f}). "
    "지금 세션을 더 열면 모든 세션이 느려집니다. 그래도 열려면 허용하고, 기다리려면 거부해 주세요."
)


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
        output = make_output(hook_input, _read_load())

        if output:
            print(json.dumps(output))
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(f"start-session-hook: {error}", file=sys.stderr)


def _read_load() -> tuple[float, int] | None:
    try:
        return os.getloadavg()[0], os.cpu_count() or 1
    except (OSError, AttributeError):
        return None


def make_output(hook_input: dict[str, Any], load: tuple[float, int] | None) -> dict[str, Any] | None:
    tool_input = hook_input.get(_TOOL_INPUT_KEY) or {}
    decision = None

    if hook_input.get(_EVENT_KEY) == _PRE_TOOL_USE and hook_input.get(_TOOL_NAME_KEY) == _START_TOOL:
        decision = decide(tool_input, load)
    if decision:
        verdict, reason = decision

        return {
            _HOOK_OUTPUT_KEY: {
                "hookEventName": _PRE_TOOL_USE,
                "permissionDecision": verdict,
                "permissionDecisionReason": reason,
            }
        }
    else:
        return None


def decide(tool_input: dict[str, Any], load: tuple[float, int] | None) -> tuple[str, str] | None:
    missing = _list_missing_levels(tool_input)

    if missing:
        return _DECISION_DENY, _LEVEL_REASON.format(missing=" and ".join(f"`{key}`" for key in missing))
    elif load is not None and _is_starting_now(tool_input) and _is_overloaded(*load):
        current, cpus = load

        return _DECISION_ASK, _LOAD_REASON.format(load=current, cpus=cpus, limit=_LOAD_FACTOR * cpus)
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


if __name__ == "__main__":
    main()
