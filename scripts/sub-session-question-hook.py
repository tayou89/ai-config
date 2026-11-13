#!/usr/bin/env python3
"""Send a question dialog in a session another session started back to the next-step box.

An open question dialog keeps the turn running, so a message the starting
session sends meanwhile, such as the user's answer it relayed after asking the
same question, waits in the queue, and stopping the session is refused while
it waits for the user (seen 2026-11-13). The next-step box question and the
closing message end the turn and still let the user answer in this session
(`decisions-and-collaboration` "Working alongside other sessions", user
decision, 2026-11-13).

Lineage comes from the desktop app's session records through
`session_lineage`. A top-level session, one moved to the top level, a
subagent's call, and a session without a readable record pass.
SUB_SESSION_QUESTION=off in the settings env turns the hook off, and any
failure lets the dialog through.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from session_lineage import is_under_another_session, read_host_session_record

_HOOK_NAME = "sub-session-question-hook"
_SWITCH_VARIABLE = "SUB_SESSION_QUESTION"
_SWITCH_OFF = "off"
_EVENT_KEY = "hook_event_name"
_TOOL_NAME_KEY = "tool_name"
_AGENT_ID_KEY = "agent_id"
_PRE_TOOL_USE = "PreToolUse"
_QUESTION_TOOL = "AskUserQuestion"
_DENY_REASON = (
    "[SUB-SESSION QUESTION] Another session started this one, and an open question dialog keeps this turn "
    "running, so an answer that session relays waits in the queue and it cannot stop this session. "
    "Ask as the mcp__ccd_turn__wrap_up question, with the options in the closing message, and end the turn; "
    "the user can still answer in this session."
)


def main() -> int:
    try:
        output = _make_hook_output(_read_hook_input())

        if output is not None:
            print(json.dumps(output))
    except Exception as error:
        # A failure must not block the dialog, since the user may be waiting to answer it.
        print(f"{_HOOK_NAME}: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


def _make_hook_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    if _is_sub_session_question(hook_input):
        decision = {
            "hookEventName": _PRE_TOOL_USE,
            "permissionDecision": "deny",
            "permissionDecisionReason": _DENY_REASON,
        }

        return {"hookSpecificOutput": decision}
    else:
        return None


def _read_hook_input() -> dict[str, Any]:
    text = sys.stdin.read()
    hook_input = json.loads(text) if text.strip() else {}

    return hook_input if isinstance(hook_input, dict) else {}


def _is_sub_session_question(hook_input: dict[str, Any]) -> bool:
    if _is_checked_question(hook_input):
        record = read_host_session_record()

        return record is not None and is_under_another_session(record)
    else:
        return False


def _is_checked_question(hook_input: dict[str, Any]) -> bool:
    """A subagent shares the session's variables but its dialog is not the session's turn, so it is not checked."""
    return (
        os.environ.get(_SWITCH_VARIABLE) != _SWITCH_OFF
        and not hook_input.get(_AGENT_ID_KEY)
        and hook_input.get(_EVENT_KEY) == _PRE_TOOL_USE
        and hook_input.get(_TOOL_NAME_KEY) == _QUESTION_TOOL
    )


if __name__ == "__main__":
    sys.exit(main())
