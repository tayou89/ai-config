#!/usr/bin/env python3
"""Keep the text the user reads in the language set by REPLY_LANGUAGE.

The app draws some tool inputs for the user (task list, next-step box,
question dialog, file caption, session title), so PreToolUse can stop a
field in another language before the user sees it. Prose between tool calls
reaches the user before any hook runs, so PostToolUse can only remind the
model afterwards, from the transcript. Some text never reaches the
transcript and the newest message may not be written yet when the hook
runs, so the reminder is best effort.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class _Language(NamedTuple):
    name: str
    letter_pattern: re.Pattern[str]
    letter_weight: float


class _Field(NamedTuple):
    path: str
    text: str


_LANGUAGE_VARIABLE = "REPLY_LANGUAGE"
_LANGUAGES = {
    "ko": _Language(name="Korean 존댓말", letter_pattern=re.compile(r"[가-힣]"), letter_weight=2.5),
}
_PRE_TOOL_USE = "PreToolUse"
_POST_TOOL_USE = "PostToolUse"
_DECISION_DENY = "deny"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_HOOK_EVENT_KEY = "hookEventName"
_MESSAGE_KEY = "message"
_TYPE_KEY = "type"
_TEXT_KEY = "text"
_ID_KEY = "id"
_UUID_KEY = "uuid"
_ASSISTANT_TYPE = "assistant"
_ASSISTANT_MARKER = f'"{_ASSISTANT_TYPE}"'
_ENCODING = "utf-8"
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_IGNORED_SPANS = (
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"`[^`\n]*`"),
    re.compile(r"\]\([^)]*\)"),
    re.compile(r"https?://\S+"),
    re.compile(r"(?:~|\.{1,2})?/[\w./@+-]+"),
    re.compile(r"\b[\w-]+\.[A-Za-z]{1,5}\b"),
    re.compile(r"\blocal_[0-9a-f-]+"),
    re.compile(r"\b[0-9a-f]{7,40}\b"),
    re.compile(r"<[^>\n]{0,80}>"),
)
_MINIMUM_RATIO = 0.3
_FIELD_MINIMUM_LATIN = 4
_PROSE_MINIMUM_LATIN = 10
_TRANSCRIPT_TAIL_BYTES = 131072
_STATE_DIRECTORY = "claude-reply-language"
_TEMPORARY_DIRECTORY_VARIABLES = ("TMPDIR", "TEMP", "TMP")
_DEFAULT_TEMPORARY_DIRECTORY = "/tmp"


def _read_plan_fields(tool_input: dict[str, Any]) -> Iterator[_Field]:
    for index, item in enumerate(tool_input.get("items") or []):
        yield _Field(f"items[{index}].content", _to_text(item.get("content")))


def _read_wrap_up_fields(tool_input: dict[str, Any]) -> Iterator[_Field]:
    yield _Field("recap", _to_text(tool_input.get("recap")))
    yield _Field("ask", _to_text(tool_input.get("ask")))
    for index, action in enumerate(tool_input.get("actions") or []):
        if not action.get("action"):
            yield _Field(f"actions[{index}].label", _to_text(action.get("label")))


def _read_question_fields(tool_input: dict[str, Any]) -> Iterator[_Field]:
    for question_index, question in enumerate(tool_input.get("questions") or []):
        prefix = f"questions[{question_index}]"

        yield _Field(f"{prefix}.question", _to_text(question.get("question")))
        yield _Field(f"{prefix}.header", _to_text(question.get("header")))
        for option_index, option in enumerate(question.get("options") or []):
            yield _Field(f"{prefix}.options[{option_index}].label", _to_text(option.get("label")))
            yield _Field(f"{prefix}.options[{option_index}].description", _to_text(option.get("description")))


def _read_caption_fields(tool_input: dict[str, Any]) -> Iterator[_Field]:
    yield _Field("caption", _to_text(tool_input.get("caption")))


def _read_title_fields(tool_input: dict[str, Any]) -> Iterator[_Field]:
    yield _Field("title", _to_text(tool_input.get("title")))


_FIELD_READERS: dict[str, Callable[[dict[str, Any]], Iterator[_Field]]] = {
    "mcp__ccd_turn__update_plan": _read_plan_fields,
    "mcp__ccd_turn__wrap_up": _read_wrap_up_fields,
    "AskUserQuestion": _read_question_fields,
    "SendUserFile": _read_caption_fields,
    "mcp__ccd_session__start_session": _read_title_fields,
}


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
        language = _LANGUAGES.get(os.environ.get(_LANGUAGE_VARIABLE, ""))
        output = _make_output(hook_input, language) if language else None

        if output:
            print(json.dumps(output))
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        print(f"reply-language-hook: {error}", file=sys.stderr)


def _make_output(hook_input: dict[str, Any], language: _Language) -> dict[str, Any] | None:
    event = hook_input.get("hook_event_name")

    if event == _PRE_TOOL_USE:
        return _make_field_decision(hook_input, language)
    elif event == _POST_TOOL_USE:
        return _make_prose_reminder(hook_input, language)
    else:
        return None


def _make_field_decision(hook_input: dict[str, Any], language: _Language) -> dict[str, Any] | None:
    reader = _FIELD_READERS.get(_to_text(hook_input.get("tool_name")))
    tool_input = hook_input.get("tool_input") or {}
    fields = list(reader(tool_input)) if reader else []
    foreign = [field for field in fields if _is_foreign(field.text, language, _FIELD_MINIMUM_LATIN)]

    if foreign:
        listed = "\n".join(f"- {field.path}: {field.text[:120]}" for field in foreign)
        reason = f"The user reads these fields, so write them in {language.name} (CLAUDE.md Language Policy), then call the tool again. Keep code, paths, and names as they are; a field that is only a name, such as a product or tool name, passes once it has a word in {language.name} beside the name.\n{listed}"

        return {
            _HOOK_OUTPUT_KEY: {
                _HOOK_EVENT_KEY: _PRE_TOOL_USE,
                "permissionDecision": _DECISION_DENY,
                "permissionDecisionReason": reason,
            },
        }
    else:
        return None


def _make_prose_reminder(hook_input: dict[str, Any], language: _Language) -> dict[str, Any] | None:
    transcript_path = _to_text(hook_input.get("transcript_path"))
    session_id = _to_text(hook_input.get("session_id"))

    if transcript_path and session_id:
        texts = _read_unchecked_texts(transcript_path, _get_state_path(session_id))
        foreign = [text for text in texts if _is_foreign(text, language, _PROSE_MINIMUM_LATIN)]

        if foreign:
            reminder = f'[LANGUAGE REMINDER] Your last message to the user was not in {language.name}: "{foreign[-1][:100]}". Write every line the user sees in {language.name} from now on, including progress lines between tool calls.'

            return {_HOOK_OUTPUT_KEY: {_HOOK_EVENT_KEY: _POST_TOOL_USE, "additionalContext": reminder}}
        else:
            return None
    else:
        return None


def _read_unchecked_texts(transcript_path: str, state_path: str) -> list[str]:
    checked = _read_state(state_path)
    entries = [entry for entry in _read_transcript_tail(transcript_path) if _is_main_assistant_entry(entry)]
    uuids = [_to_text(entry.get(_UUID_KEY)) for entry in entries]
    start = uuids.index(checked) + 1 if checked in uuids else _find_last_message_start(entries)
    unchecked = entries[start:]

    if unchecked:
        _write_state(state_path, uuids[-1])
    return [text for entry in unchecked for text in _read_entry_texts(entry)]


def _find_last_message_start(entries: list[dict[str, Any]]) -> int:
    if entries:
        last_id = _get_message_id(entries[-1])

        return next(index for index, entry in enumerate(entries) if _get_message_id(entry) == last_id)
    else:
        return 0


def _read_state(state_path: str) -> str:
    if os.path.exists(state_path):
        with open(state_path, encoding=_ENCODING, errors="replace") as state:
            return state.read().strip()
    else:
        return ""


def _write_state(state_path: str, uuid: str) -> None:
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding=_ENCODING) as state:
            state.write(uuid)
    except OSError as error:
        print(f"reply-language-hook: could not save the check position: {error}", file=sys.stderr)


def _read_transcript_tail(transcript_path: str) -> list[dict[str, Any]]:
    with open(transcript_path, "rb") as transcript:
        size = transcript.seek(0, os.SEEK_END)

        transcript.seek(max(0, size - _TRANSCRIPT_TAIL_BYTES))
        lines = transcript.read().decode(_ENCODING, errors="replace").split("\n")
    entries = []

    for line in lines[1:] if size > _TRANSCRIPT_TAIL_BYTES else lines:
        entry = _parse_entry(line) if _ASSISTANT_MARKER in line else None

        if entry:
            entries.append(entry)
    return entries


def _parse_entry(line: str) -> dict[str, Any] | None:
    try:
        entry = json.loads(line)

        return entry if isinstance(entry, dict) else None
    except json.JSONDecodeError:
        return None


def _read_entry_texts(entry: dict[str, Any]) -> list[str]:
    content = entry.get(_MESSAGE_KEY, {}).get("content") or []

    return [
        _to_text(block.get(_TEXT_KEY))
        for block in content
        if isinstance(block, dict) and block.get(_TYPE_KEY) == _TEXT_KEY
    ]


def _get_message_id(entry: dict[str, Any]) -> object:
    return entry.get(_MESSAGE_KEY, {}).get(_ID_KEY)


def _get_state_path(session_id: str) -> str:
    safe_id = re.sub(r"[^\w-]", "_", session_id)
    configured = [os.environ.get(variable) for variable in _TEMPORARY_DIRECTORY_VARIABLES]
    temporary_directory = next((directory for directory in configured if directory), _DEFAULT_TEMPORARY_DIRECTORY)

    return os.path.join(temporary_directory, _STATE_DIRECTORY, safe_id)


def _to_text(value: object) -> str:
    return "" if value is None else str(value)


def _measure(text: str, language: _Language) -> tuple[int, int]:
    body = text

    for pattern in _IGNORED_SPANS:
        body = pattern.sub(" ", body)
    native = len(language.letter_pattern.findall(body))
    latin = sum(len(word) for word in _LATIN_WORD.findall(body))

    return native, latin


def _is_main_assistant_entry(entry: dict[str, Any]) -> bool:
    return entry.get(_TYPE_KEY) == _ASSISTANT_TYPE and not entry.get("isSidechain")


def _is_foreign(text: str, language: _Language, minimum_latin: int) -> bool:
    native, latin = _measure(text, language)
    weighted = native * language.letter_weight

    return latin >= minimum_latin and weighted / (weighted + latin) < _MINIMUM_RATIO


if __name__ == "__main__":
    main()
