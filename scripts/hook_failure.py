"""Answer a hook's own errors so that a check it could not finish never lets a call through unseen.

Claude Code lets an action go ahead when a hook fails to start, exits with a
code other than 0 or 2, prints JSON it cannot accept, or times out, unless
the hook's settings entry sets `"onFailure": "block"` (hooks documentation,
read 2026-11-24; Claude Code 2.1.295 or later). A hook that catches its own
error and exits 0 has not failed as far as Claude Code can tell, so its
error is hidden and the call goes ahead. A hook that runs before an action
therefore reads its input and judges inside `run_hook`, which answers any
error with the hook's `FailurePolicy`:

- a blocking policy, for a hook whose settings entry blocks on failure:
  on a PreToolUse call it denies the call or asks the user, with the error
  in the reason; on any other event, and when the input is not a hook
  input, it exits 1, which that entry turns into a block where it can.
- a notice policy, for a hook whose settings entry lets the call go ahead
  on failure: it exits 1, so the call goes ahead with a hook error notice
  instead of an error nobody sees.

A hook with several independent checks runs them through `judge_checks`, so
a check that fails gives its own answer and keeps the others' findings, and
a denial outranks a question; a step all checks rely on, such as reading the
command, runs before them, so its failure gets the hook's own answer. A write
a hook makes only when its checks find nothing, such as recording a folder's
owner, runs as its own `judge_checks` after the first one returns nothing.
README "Hooks" says which hook answers how, and tests/test_hook_failure.py
keeps each settings entry in line with its policy.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

TOOL_NAME_KEY = "tool_name"
TOOL_INPUT_KEY = "tool_input"
PRE_TOOL_USE = "PreToolUse"
OUTPUT_KEY = "hookSpecificOutput"
_EXIT_SUCCESS = 0
_EXIT_FAILED = 1
_EVENT_KEY = "hook_event_name"
_INPUT_ERROR = "the hook input is not an object naming its event, with the tool's name and input for a tool call"
_REASON_SEPARATOR = "\n"


class Decision(str, Enum):
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class FailurePolicy:
    """What a hook answers to its own error: `on_error` None lets the call go ahead with a hook error notice."""

    @classmethod
    def blocking(cls, hook_name: str, on_error: Decision, reason: str) -> FailurePolicy:
        return cls(hook_name, on_error, reason)

    @classmethod
    def notice(cls, hook_name: str) -> FailurePolicy:
        return cls(hook_name, None, "")

    hook_name: str
    on_error: Decision | None
    reason: str

    def is_blocking(self) -> bool:
        return self.on_error is not None


@dataclass(frozen=True)
class Finding:
    decision: Decision
    reason: str


@dataclass(frozen=True)
class Check:
    """One check run on its own: when `find` raises, the check answers `on_error` with `reason`, `{error}` filled in."""

    find: Callable[[], list[Finding]]
    on_error: Decision
    reason: str


def run_hook(make_output: Callable[[dict[str, Any]], dict[str, Any] | None], policy: FailurePolicy) -> int:
    """Print the hook's answer to the input on stdin and return its exit code; any error gets the policy's answer."""
    hook_input: dict[str, Any] | None = None

    try:
        hook_input = _read_hook_input()
        output = make_output(hook_input)

        if output:
            print(json.dumps(output))
        return _EXIT_SUCCESS
    except Exception as error:
        return _answer_error(policy, hook_input, error)


def judge_checks(checks: Sequence[Check]) -> dict[str, Any] | None:
    findings = [finding for check in checks for finding in _run_check(check)]
    denials = [finding.reason for finding in findings if finding.decision == Decision.DENY]
    questions = [finding.reason for finding in findings if finding.decision == Decision.ASK]

    if denials:
        return make_decision(Decision.DENY, denials)
    elif questions:
        return make_decision(Decision.ASK, questions)
    else:
        return None


def make_decision(decision: Decision, reasons: Sequence[str]) -> dict[str, Any]:
    payload = {
        "hookEventName": PRE_TOOL_USE,
        "permissionDecision": decision.value,
        "permissionDecisionReason": _REASON_SEPARATOR.join(dict.fromkeys(reasons)),
    }

    return {OUTPUT_KEY: payload}


def _read_hook_input() -> dict[str, Any]:
    hook_input = json.load(sys.stdin)

    if isinstance(hook_input, dict) and _is_hook_input(hook_input):
        return hook_input
    else:
        raise ValueError(_INPUT_ERROR)


def _run_check(check: Check) -> list[Finding]:
    try:
        return check.find()
    except Exception as error:
        return [Finding(check.on_error, check.reason.format(error=_describe_error(error)))]


def _answer_error(policy: FailurePolicy, hook_input: dict[str, Any] | None, error: Exception) -> int:
    description = _describe_error(error)

    print(f"{policy.hook_name}: {description}", file=sys.stderr)
    if policy.on_error is not None and _is_tool_call(hook_input):
        print(json.dumps(make_decision(policy.on_error, [policy.reason.format(error=description)])))
        return _EXIT_SUCCESS
    else:
        return _EXIT_FAILED


def _describe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _is_hook_input(hook_input: dict[str, Any]) -> bool:
    """Every event names itself; a tool call also names its tool and gives that tool's input as an object."""
    event = hook_input.get(_EVENT_KEY)
    tool_name = hook_input.get(TOOL_NAME_KEY)
    has_tool = isinstance(tool_name, str) and bool(tool_name) and isinstance(hook_input.get(TOOL_INPUT_KEY), dict)

    return isinstance(event, str) and bool(event) and (event != PRE_TOOL_USE or has_tool)


def _is_tool_call(hook_input: dict[str, Any] | None) -> bool:
    return hook_input is not None and hook_input.get(_EVENT_KEY) == PRE_TOOL_USE
