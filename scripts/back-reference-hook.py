#!/usr/bin/env python3
"""Catch questions to the user that point elsewhere in the chat instead of showing it.

Chat messages cannot be linked, so a question such as "apply the draft shown
earlier?" makes the user scroll back or ask what it was. The next-step box is
drawn before the closing message is written and must make sense on its own, so
a pointer there gets a notice that the closing message must carry the text. A
question dialog may be all the user sees before answering, so a pointer there
is sent back. A closing message whose question points back gets one more turn
to show the text. A list alone does not count as showing the text, since the
questions that pointed back most often sat in a list of open items.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_PRE_TOOL_USE = "PreToolUse"
_STOP = "Stop"
_WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
_QUESTION_TOOL = "AskUserQuestion"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_HOOK_EVENT_KEY = "hookEventName"
_NOTICE_PREFIX = "[BACK-REFERENCE]"
_POINTER_PATTERN = re.compile(
    r"(?:앞서|앞에서|아까|방금|이전에|지난번에)\s?(?:제가\s?)?"
    r"(?:보여|보인|보내|말씀|드린|적은|적어|쓴|썼|정리|제시|설명|올린|올려|소개|제안|안내|공유|언급)"
    r"|(?:앞|앞선|이전|지난)(?:의)?\s?(?:답변|메시지|대화|문안|초안)"
    r"|\b(?:shown|showed|mentioned|described|listed|given|proposed)\s(?:earlier|before|previously)\b"
    r"|\bprevious (?:message|reply|answer)\b",
    re.IGNORECASE,
)
_BOX_POINTER_PATTERN = re.compile(
    r"(?:(?<![가-힣])(?:아래|위)(?:의|에|에서)?\s?(?:적은\s|보인\s)?(?:내용|문안|문장|표|목록|항목|안)|\d+(?:·\d+)*(?:절|장))"
    r"(?:이|가|을|를|은|는|과|와|의|에|으로|로|대로)?(?![가-힣])"
    r"|이 메시지"
    r"|\b(?:below|above)\b(?!-)|\bin (?:my|this) message\b",
    re.IGNORECASE,
)
_QUESTION_PATTERN = re.compile(r"\?|까요|니까|주십시오|주세요|알려 주|말씀해 주")
_SHOWN_PATTERN = re.compile(
    r"\]\((?!#)|^\s*>|```|https?://|(?:[~.\w@+-]*/)+[\w@+-]+\.[A-Za-z][A-Za-z0-9]{0,4}\b",
    re.MULTILINE,
)
_INTRODUCES_TEXT_PATTERN = re.compile(r"(?:아래|다음)(?:와|과)?\s?같|\bas follows\b", re.IGNORECASE)
_QUOTED_PATTERN = re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|`[^`\n]*`')
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。])\s+|\n")


def main() -> None:
    try:
        output = _make_output(json.load(sys.stdin))

        if output:
            print(json.dumps(output, ensure_ascii=False))
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(f"back-reference-hook: {error}", file=sys.stderr)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    event = hook_input.get("hook_event_name")
    tool_name = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input") or {}

    if event == _PRE_TOOL_USE and tool_name == _WRAP_UP_TOOL:
        return _make_wrap_up_notice(tool_input)
    elif event == _PRE_TOOL_USE and tool_name == _QUESTION_TOOL:
        return _make_question_decision(tool_input)
    elif event == _STOP and not hook_input.get("stop_hook_active"):
        return _make_closing_decision(_to_text(hook_input.get("last_assistant_message")))
    else:
        return None


def _make_wrap_up_notice(tool_input: dict[str, Any]) -> dict[str, Any] | None:
    labels = [_to_text(action.get("label")) for action in tool_input.get("actions") or [] if not action.get("action")]
    fields = "\n".join([_to_text(tool_input.get("ask")), *labels])
    pointer = _search_unquoted(_POINTER_PATTERN, fields) or _search_unquoted(_BOX_POINTER_PATTERN, fields)

    if pointer:
        notice = f'{_NOTICE_PREFIX} The next-step question points elsewhere ("{pointer}"), so it does not say on its own what is being reviewed. The closing message written after this call is the place that can show that text or link the file that holds it.'

        return {_HOOK_OUTPUT_KEY: {_HOOK_EVENT_KEY: _PRE_TOOL_USE, "additionalContext": notice}}
    else:
        return None


def _make_question_decision(tool_input: dict[str, Any]) -> dict[str, Any] | None:
    questions = tool_input.get("questions") or []
    options = [option for question in questions for option in question.get("options") or []]
    texts = [_to_text(question.get("question")) for question in questions] + [
        f"{_to_text(option.get('label'))}\n{_to_text(option.get('description'))}" for option in options
    ]
    combined = "\n".join(texts)
    pointer = _search_unquoted(_POINTER_PATTERN, combined)
    has_preview = any(option.get("preview") for option in options)

    if pointer and not has_preview and not _SHOWN_PATTERN.search(combined):
        reason = f'{_NOTICE_PREFIX} The question points to earlier text ("{pointer}"), and the dialog may be all the user sees while answering. Put that text in the question, an option description, or a preview, or link the file that holds it, then ask again.'

        return {
            _HOOK_OUTPUT_KEY: {
                _HOOK_EVENT_KEY: _PRE_TOOL_USE,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
    else:
        return None


def _make_closing_decision(message: str) -> dict[str, Any] | None:
    paragraphs = _PARAGRAPH_BREAK.split(message)
    pointers = [_find_unshown_pointer(paragraphs, index) for index in range(len(paragraphs))]
    pointer = next((found for found in pointers if found), "")

    if pointer:
        reason = f'{_NOTICE_PREFIX} The closing message asks about earlier text ("{pointer}") without showing it. Chat messages cannot be linked, so add a short follow-up that shows that text or links the file that holds it.'

        return {"decision": "block", "reason": reason}
    else:
        return None


def _find_unshown_pointer(paragraphs: list[str], index: int) -> str:
    nearby = "\n\n".join(paragraphs[max(0, index - 1) : index + 2])

    if _SHOWN_PATTERN.search(nearby):
        return ""
    else:
        sentences = _SENTENCE_BREAK.split(paragraphs[index])
        pointers = [_search_unquoted(_POINTER_PATTERN, sentence) for sentence in sentences if _is_asking(sentence)]

        return next((found for found in pointers if found), "")


def _search_unquoted(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(_QUOTED_PATTERN.sub(" ", text))

    return match.group(0) if match else ""


def _to_text(value: object) -> str:
    return "" if value is None else str(value)


def _is_asking(sentence: str) -> bool:
    return bool(_search_unquoted(_QUESTION_PATTERN, sentence)) and not _INTRODUCES_TEXT_PATTERN.search(sentence)


if __name__ == "__main__":
    main()
