#!/usr/bin/env python3
"""Remind the model what evidence allows a changed recommendation.

Recommendations kept moving toward the user's latest remark, each time with a
reason renamed as new evidence, although the rule that forbids it was loaded.
The next-step box is drawn before the closing message is written, so a change
mentioned there gets a notice in time for the closing message. A closing
message that announces a change without saying when its evidence appeared
gets one more turn to say it or keep the earlier recommendation. The hook
cannot judge whether the evidence is real; it only makes the timing explicit.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from hook_failure import FailurePolicy, run_hook

FAILURE_POLICY = FailurePolicy.notice("recommendation-change-hook")
_PRE_TOOL_USE = "PreToolUse"
_STOP = "Stop"
_WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_HOOK_EVENT_KEY = "hookEventName"
_NOTICE_PREFIX = "[RECOMMENDATION CHANGE]"
_TIMING_NOTE = "When it appeared is its place in order against the earlier recommendation, or a time read from a clock or a record, not an estimate."
_RULE_SUMMARY = "`work-principles` 7 allows a change only on a fact, a requirement the user says must be met, or a result that first appeared after the earlier recommendation; an option the user asked to see or their reaction to a result does not count."
_CHANGE_PATTERN = re.compile(
    r"추천(?:을|은|이|도|으로|안을|\s?표시(?:를|도|는)?|\s?(?=바꿈))"
    r"(?:(?!(?<!에)서\s|고\s|며\s|지만\s)[^.\n。!?]){0,40}?"
    r"(?:바꿉|바꿨|바꾸겠|바꾸었|바꿀게|바꿔|바꿈|옮깁|옮겼|옮기겠|고칩니다|고쳤|고치겠|"
    r"바뀌었|바뀝니다|바뀌어요|변경했|변경합니다|변경하겠)(?!다고|다는|던|야|도)"
    r"|(?<!not )(?<!n't )(?<!never )(?<!to )\b(?:chang|switch|revis|mov)(?:e|ed|es|ing)?\s(?:my|our)\srecommendation\b"
    r"|\b(?:my|our|the)\srecommendation\s(?:changed|moved|switched|is now|now goes)\b"
    r"|\bI now recommend\b",
    re.IGNORECASE,
)
_DATED_PATTERN = re.compile(
    r"처음\s?(?:나온|나왔|얻은|얻었|생긴|생겼|드러난|드러났|보인|보였|확인|알게)"
    r"|(?:뒤|후|다음|이후)에?\s?(?:나온|나왔|얻은|얻었|생긴|생겼|드러난|드러났|확인한|확인했|알게\s?(?:된|되어|됐)|본|봤)"
    r"|새로\s?(?:나온|나왔|얻은|얻었|생긴|생겼|드러난|드러났|확인한|확인했|알게\s?(?:된|되어|됐))"
    r"|(?<![\w-])\d{4}-\d{2}-\d{2}(?![\w.-])|\d+회차"
    r"|\bfirst (?:appeared|came up|showed up|found)\b|\b(?:appeared|came up|found) after\b",
    re.IGNORECASE,
)
_RELAY_PATTERN = re.compile(
    r"세션(?:이|은|께서)|리뷰어|검토자|\b(?:session|reviewer)\s(?:changed|revised|moved|switched)\b",
    re.IGNORECASE,
)
_QUESTION_PATTERN = re.compile(r"\?|까요|나요|습니까")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。])\s+|\n")
_QUOTED_PATTERN = re.compile(r'```.*?```|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|`[^`\n]*`', re.DOTALL)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def main() -> int:
    return run_hook(_make_output, FAILURE_POLICY)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    event = hook_input.get("hook_event_name")

    if event == _PRE_TOOL_USE and hook_input.get("tool_name") == _WRAP_UP_TOOL:
        return _make_wrap_up_notice(hook_input.get("tool_input") or {})
    elif event == _STOP and not hook_input.get("stop_hook_active"):
        return _make_closing_decision(_to_text(hook_input.get("last_assistant_message")))
    else:
        return None


def _make_wrap_up_notice(tool_input: dict[str, Any]) -> dict[str, Any] | None:
    labels = [_to_text(action.get("label")) for action in tool_input.get("actions") or [] if not action.get("action")]
    fields = "\n".join([_to_text(tool_input.get("recap")), _to_text(tool_input.get("ask")), *labels])
    change = _search_change(fields)

    if change:
        notice = f'{_NOTICE_PREFIX} The next-step box says a recommendation changed ("{change}"). {_RULE_SUMMARY} The closing message is where that evidence and when it appeared can be named. {_TIMING_NOTE}'

        return {_HOOK_OUTPUT_KEY: {_HOOK_EVENT_KEY: _PRE_TOOL_USE, "additionalContext": notice}}
    else:
        return None


def _make_closing_decision(message: str) -> dict[str, Any] | None:
    paragraphs = _PARAGRAPH_BREAK.split(_QUOTED_PATTERN.sub(" ", message))
    changes = [_find_undated_change(paragraphs, index) for index in range(len(paragraphs))]
    change = next((found for found in changes if found), "")

    if change:
        reason = f'{_NOTICE_PREFIX} The closing message says a recommendation changed ("{change}") without saying when its evidence appeared. {_RULE_SUMMARY} A short follow-up can name that evidence and when it appeared, or say the earlier recommendation stands when there is none. {_TIMING_NOTE}'

        return {"decision": "block", "reason": reason}
    else:
        return None


def _find_undated_change(paragraphs: list[str], index: int) -> str:
    change = _search_change(paragraphs[index])
    nearby = "\n\n".join(paragraphs[max(0, index - 1) : index + 2])

    return "" if _DATED_PATTERN.search(nearby) else change


def _search_change(text: str) -> str:
    sentences = _SENTENCE_BREAK.split(_QUOTED_PATTERN.sub(" ", text))
    changes = [_search_own_change(sentence) for sentence in sentences]

    return next((found for found in changes if found), "")


def _search_own_change(sentence: str) -> str:
    match = _CHANGE_PATTERN.search(sentence)

    if match and not _RELAY_PATTERN.search(sentence[: match.start()]) and not _QUESTION_PATTERN.search(sentence):
        return match.group(0)
    else:
        return ""


def _to_text(value: object) -> str:
    return "" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())
