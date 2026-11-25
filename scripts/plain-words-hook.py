#!/usr/bin/env python3
"""Keep internal words out of what the user reads when a decision is asked.

A rule asks for everyday words, yet a session that had it loaded explained a
decision twice in labels from its brief and in names it had coined, and the
user could not answer. The next-step box is drawn before the closing message
is written, so a box or question dialog holding an identifier (a commit
number, a command option, an exit code, a file name or path, or code) is sent
back to be rewritten, and a next-step box that asks a decision carries a
notice to the closing message that follows it. The hook cannot tell a plain
word from an internal one, so the notice names the kinds of words that
confused the user instead of listing words: a replay showed that quoted
labels taken from briefs were mostly the user's own answers, screen copy, and
session names.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from hook_failure import FailurePolicy, run_hook

FAILURE_POLICY = FailurePolicy.notice("plain-words-hook")
_PRE_TOOL_USE = "PreToolUse"
_WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
_QUESTION_TOOL = "AskUserQuestion"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_HOOK_EVENT_KEY = "hookEventName"
_ASK_FIELD = "ask"
_LABEL_FIELD = "label"
_NOTICE_PREFIX = "[PLAIN WORDS]"
_RULE_NAME = '`collaboration-core` "Plain replies and labels"'
_MAXIMUM_LISTED = 8
_IDENTIFIER_PATTERNS = {
    "commit number": re.compile(
        r"(?<![0-9A-Za-z_/.#-])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z_/-])(?!\.[0-9A-Za-z])"
    ),
    "command option": re.compile(r"(?<![0-9A-Za-z_-])--[a-z][a-z0-9-]+"),
    "exit code": re.compile(r"(?:\bexit(?:\s?code)?|종료)\s?(?:코드\s?)?\d+(?!\s?(?:분|초|시|개|번|줄|%))"),
    "file name": re.compile(
        r"(?<![0-9A-Za-z_/.~-])(?![A-Z][A-Za-z0-9]*\.js(?![0-9A-Za-z_]))"
        r"(?:~/|\.{1,2}/|/)?(?:[0-9A-Za-z_.-]+/)*"
        r"\.?[0-9A-Za-z_-]+(?:\.[0-9A-Za-z_-]+)*"
        r"\.(?:py|md|jsonl?|tsx?|jsx?|mjs|toml|ya?ml|sh|env|lock|cfg|ini)(?![0-9A-Za-z_/-])"
        r"|(?<![0-9A-Za-z_/.-])\.env(?![0-9A-Za-z_.-])"
    ),
    "code": re.compile(r"`[^`\n]+`"),
}
_IGNORED_SPANS = (
    re.compile(r"\[[^\]\n]*\]\([^)\n]*\)"),
    re.compile(r"https?://\S+"),
    re.compile(r"\blocal_[0-9a-f-]{8,}"),
)
_DECISION_NOTICE = f"{_NOTICE_PREFIX} The closing message comes next and asks the user to decide. Describe each choice by what it does, in words the user has used or everyday ones: a developer word (main, branch, pull request, hook), a label from a brief or another session's message, and a name coined earlier in this conversation, Korean renamings included, each need a plain sentence in its place ({_RULE_NAME})."


def main() -> int:
    return run_hook(_make_output, FAILURE_POLICY)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = _to_text(hook_input.get("tool_name"))
    tool_input = hook_input.get("tool_input") or {}

    if hook_input.get("hook_event_name") == _PRE_TOOL_USE and tool_name in (_WRAP_UP_TOOL, _QUESTION_TOOL):
        return _make_box_output(tool_name, tool_input)
    else:
        return None


def _find_identifiers(text: str) -> list[str]:
    prose = text

    for pattern in _IGNORED_SPANS:
        prose = pattern.sub(" ", prose)
    found = [
        f"{match.group(0)} ({kind})"
        for kind, pattern in _IDENTIFIER_PATTERNS.items()
        for match in pattern.finditer(prose)
    ]

    return list(dict.fromkeys(found))


def _make_box_output(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    identifiers = _find_identifiers("\n".join(_read_box_fields(tool_name, tool_input)))

    if identifiers:
        listed = ", ".join(identifiers[:_MAXIMUM_LISTED])
        reason = f"{_NOTICE_PREFIX} The user reads these fields: {listed}. Say what each one is or does in everyday words, or leave it out, then call the tool again ({_RULE_NAME})."

        return {
            _HOOK_OUTPUT_KEY: {
                _HOOK_EVENT_KEY: _PRE_TOOL_USE,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
    elif _asks_decision(tool_name, tool_input):
        return {_HOOK_OUTPUT_KEY: {_HOOK_EVENT_KEY: _PRE_TOOL_USE, "additionalContext": _DECISION_NOTICE}}
    else:
        return None


def _read_box_fields(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    if tool_name == _WRAP_UP_TOOL:
        actions = tool_input.get("actions") or []
        labels = [_to_text(action.get(_LABEL_FIELD)) for action in actions if not action.get("action")]

        return [_to_text(tool_input.get("recap")), _to_text(tool_input.get(_ASK_FIELD)), *labels]
    else:
        fields = []

        for question in tool_input.get("questions") or []:
            fields.extend([_to_text(question.get("question")), _to_text(question.get("header"))])
            for option in question.get("options") or []:
                fields.extend([_to_text(option.get(_LABEL_FIELD)), _to_text(option.get("description"))])
        return fields


def _asks_decision(tool_name: str, tool_input: dict[str, Any]) -> bool:
    return tool_name == _WRAP_UP_TOOL and bool(_to_text(tool_input.get(_ASK_FIELD)).strip())


def _to_text(value: object) -> str:
    return "" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())
