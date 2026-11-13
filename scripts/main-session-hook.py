#!/usr/bin/env python3
"""Tell a main session that it is one, at each user turn and before it changes files or starts a long command.

A session is the main session once it has started another session while no
other session started it, or after it was moved to the top level
(`decisions-and-collaboration` "Working alongside other sessions", user
decisions, 2026-11-08 and 2026-11-13). The rule lives in a skill, and a long
session can hold a copy loaded before the rule changed, since a compaction
restores each skill as it was loaded; a main session that worked on its own
and passed the replacement threshold without proposing it did so with that
rule missing from its context (2026-11-13). This hook puts the facts back:

- UserPromptSubmit, which also runs on turns another session's message
  starts: the role, the conversation part's size against the replacement
  threshold, and whether the installed `decisions-and-collaboration` skill
  changed after this session last loaded it.
- PreToolUse on file edits and long commands (Edit, Write, NotebookEdit,
  Monitor, a terminal-tab command, a Bash command run in the background): one
  sentence that such work goes to a session of its own.

Hook input carries no session lineage, so the hook reads the desktop app's
session records (`claude-code-sessions/*/*/local_*.json` in the app's support
folder): `spawnedFrom` names the session that started a session and
`lineageDetached` marks one moved to the top level. These records are not a
documented interface; when they or the app's session variable are missing,
as in a terminal session, the hook adds nothing. The conversation part is
estimated from the transcript as the last request's input tokens minus the
first request's, which matched the app's Messages category within 2.6% on
2026-11-13, reading high as the fixed part grows after the first request.
A subagent's tool calls get nothing, since it cannot start sessions, and an
unreadable transcript leaves out the size and skill sentences.
MAIN_SESSION_NOTICE=off in the settings env turns the notices off. The hook
never blocks, and any failure adds nothing.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

_HOOK_NAME = "main-session-hook"
_SWITCH_VARIABLE = "MAIN_SESSION_NOTICE"
_SWITCH_OFF = "off"
_HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_SESSION_RECORDS_PATH = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions"
_SESSION_RECORD_PATTERN = "*/*/local_*.json"
_SESSION_RECORD_SUFFIX = ".json"
_SPAWNED_FROM_KEY = "spawnedFrom"
_SESSION_ID_KEY = "sessionId"
_DETACHED_KEY = "lineageDetached"
_LEAD_SKILL = "decisions-and-collaboration"
_SKILL_FILE = Path("skills") / _LEAD_SKILL / "SKILL.md"
_SKILL_TOOL = "Skill"
_SKILL_INPUT_KEY = "skill"
_EVENT_KEY = "hook_event_name"
_AGENT_ID_KEY = "agent_id"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"
_TRANSCRIPT_PATH_KEY = "transcript_path"
_BACKGROUND_KEY = "run_in_background"
_USER_PROMPT_SUBMIT = "UserPromptSubmit"
_PRE_TOOL_USE = "PreToolUse"
_BASH_TOOL = "Bash"
_WRITING_TOOLS = ("Edit", "Write", "NotebookEdit", "Monitor", "mcp__terminal__run_in_terminal")
_RECORD_TYPE_KEY = "type"
_ASSISTANT_TYPE = "assistant"
_ASSISTANT_MARKER = b'"type":"assistant"'
_SIDECHAIN_KEY = "isSidechain"
_MESSAGE_KEY = "message"
_USAGE_KEY = "usage"
_CONTENT_KEY = "content"
_TIMESTAMP_KEY = "timestamp"
_TOOL_USE_TYPE = "tool_use"
_NAME_KEY = "name"
_INPUT_KEY = "input"
_INPUT_TOKEN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
_UTC_SUFFIX = "Z"
_UTC_OFFSET = "+00:00"
_ENCODING = "utf-8"
_REPLACEMENT_THRESHOLD = 200_000
_THOUSAND = 1_000
_NOTICE_TAG = "[MAIN SESSION]"
_ROLE_NOTICE = (
    f"{_NOTICE_TAG} This session started {{count}} other {{noun}} and is not under another session, so it is "
    f'a main session, which {_LEAD_SKILL} "Working alongside other sessions" limits to '
    "conversation and managing sessions."
)
_SIZE_PAST_NOTICE = "Its conversation part is about {size}k tokens, past the {threshold}k replacement threshold."
_SIZE_BELOW_NOTICE = "Its conversation part is about {size}k of the {threshold}k replacement threshold."
_SKILL_CHANGED_NOTICE = (
    f"The installed {_LEAD_SKILL} skill changed after this session last loaded it, "
    "and a compaction restores each skill as it was loaded."
)
_SKILL_NOT_LOADED_NOTICE = f"This session has not loaded the {_LEAD_SKILL} skill with the Skill tool."
_TOOL_NOTICE = (
    f"{_NOTICE_TAG} This is a main session, where file changes and long commands go to a session of their own "
    "unless the tool allows neither starting nor proposing one."
)


def main() -> int:
    try:
        output = _make_hook_output(_read_hook_input())

        if output is not None:
            print(json.dumps(output))
    except Exception as error:
        # The notice is advisory: a failure must not delay or break the turn or the tool call.
        print(f"{_HOOK_NAME}: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


def _make_hook_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    notice = _make_notice(hook_input)

    if notice is not None:
        return {"hookSpecificOutput": {"hookEventName": hook_input.get(_EVENT_KEY), "additionalContext": notice}}
    else:
        return None


def _make_notice(hook_input: dict[str, Any]) -> str | None:
    event = hook_input.get(_EVENT_KEY)

    if os.environ.get(_SWITCH_VARIABLE) == _SWITCH_OFF:
        return None
    elif hook_input.get(_AGENT_ID_KEY):
        # A subagent shares the session's variables but cannot start sessions, so the role is not its own.
        return None
    elif event == _USER_PROMPT_SUBMIT:
        return _make_turn_notice(hook_input)
    elif event == _PRE_TOOL_USE and _is_writing_call(hook_input):
        return _TOOL_NOTICE if _count_started_sessions() else None
    else:
        return None


def _read_hook_input() -> dict[str, Any]:
    text = sys.stdin.read()
    hook_input = json.loads(text) if text.strip() else {}

    return hook_input if isinstance(hook_input, dict) else {}


def _make_turn_notice(hook_input: dict[str, Any]) -> str | None:
    count = _count_started_sessions()

    if count:
        transcript = _read_transcript_records(hook_input.get(_TRANSCRIPT_PATH_KEY))
        lines = [_ROLE_NOTICE.format(count=count, noun="session" if count == 1 else "sessions")]

        if transcript is not None:
            lines.extend(_list_transcript_notices(transcript))
        return " ".join(lines)
    else:
        return None


def _count_started_sessions() -> int:
    """How many sessions this session started, or 0 when it is under another session or not an app session."""
    host_session = os.environ.get(_HOST_SESSION_VARIABLE, "")
    records = Path.home() / _SESSION_RECORDS_PATH

    if host_session and records.is_dir() and _is_top_level(records, host_session):
        return sum(1 for path in records.glob(_SESSION_RECORD_PATTERN) if _is_started_by(path, host_session))
    else:
        return 0


def _is_writing_call(hook_input: dict[str, Any]) -> bool:
    tool_name = hook_input.get(_TOOL_NAME_KEY)
    tool_input = hook_input.get(_TOOL_INPUT_KEY) or {}

    if tool_name == _BASH_TOOL:
        return isinstance(tool_input, dict) and tool_input.get(_BACKGROUND_KEY) is True
    else:
        return tool_name in _WRITING_TOOLS


def _is_top_level(records: Path, host_session: str) -> bool:
    """A session whose own record is missing counts as not top level, so an unknown layout adds nothing."""
    own_records = list(records.glob(f"*/*/{host_session}{_SESSION_RECORD_SUFFIX}"))
    record = _read_json(own_records[0]) if own_records else None

    if isinstance(record, dict):
        return not record.get(_SPAWNED_FROM_KEY) or record.get(_DETACHED_KEY) is True
    else:
        return False


def _is_started_by(path: Path, host_session: str) -> bool:
    """Search the bytes first, so only the few records that name this session are parsed."""
    try:
        if host_session.encode(_ENCODING) in path.read_bytes():
            record = _read_json(path)
            spawned_from = record.get(_SPAWNED_FROM_KEY) if isinstance(record, dict) else None

            return isinstance(spawned_from, dict) and spawned_from.get(_SESSION_ID_KEY) == host_session
        else:
            return False
    except OSError:
        return False


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding=_ENCODING))
    except (OSError, ValueError, RecursionError):
        return None


def _read_transcript_records(transcript_path: Any) -> list[dict[str, Any]] | None:
    """The transcript's main-chain assistant records in order, or None when there is no readable transcript.

    Only lines holding the assistant marker are parsed, about a quarter of a long transcript.
    """
    try:
        if isinstance(transcript_path, str) and transcript_path:
            lines = Path(transcript_path).read_bytes().splitlines()
            records = [_parse_record(line) for line in lines if _ASSISTANT_MARKER in line]

            return [record for record in records if record is not None and not record.get(_SIDECHAIN_KEY)]
        else:
            return None
    except OSError:
        return None


def _list_transcript_notices(transcript: list[dict[str, Any]]) -> list[str]:
    size = _estimate_conversation_size(transcript)
    notices = [_make_size_notice(size)] if size is not None else []

    return notices + _list_skill_notices(transcript)


def _parse_record(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)

        return record if isinstance(record, dict) else None
    except (ValueError, RecursionError):
        return None


def _estimate_conversation_size(transcript: list[dict[str, Any]]) -> int | None:
    """The last request's input tokens minus the first's: the first holds only the fixed part and the brief.

    Records with no input tokens, such as messages the tool writes itself, are not requests and are skipped.
    """
    totals = [total for total in map(_sum_input_tokens, transcript) if total]

    if totals:
        return max(totals[-1] - totals[0], 0)
    else:
        return None


def _sum_input_tokens(record: dict[str, Any]) -> int | None:
    message = _get_assistant_message(record)
    usage = message.get(_USAGE_KEY) if message is not None else None

    if isinstance(usage, dict):
        return sum(value for value in (usage.get(key) for key in _INPUT_TOKEN_KEYS) if isinstance(value, int))
    else:
        return None


def _make_size_notice(size: int) -> str:
    template = _SIZE_PAST_NOTICE if size > _REPLACEMENT_THRESHOLD else _SIZE_BELOW_NOTICE

    return template.format(size=round(size / _THOUSAND), threshold=_REPLACEMENT_THRESHOLD // _THOUSAND)


def _list_skill_notices(transcript: list[dict[str, Any]]) -> list[str]:
    loaded_at = _find_last_skill_load(transcript)
    installed_at = _get_modified_time(_get_config_directory() / _SKILL_FILE)

    if loaded_at is None:
        return [_SKILL_NOT_LOADED_NOTICE]
    elif installed_at is not None and installed_at > loaded_at:
        return [_SKILL_CHANGED_NOTICE]
    else:
        return []


def _get_modified_time(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _find_last_skill_load(transcript: list[dict[str, Any]]) -> float | None:
    """When this session last called the Skill tool for the lead skill, as a POSIX timestamp."""
    load_times = [_parse_timestamp(record) for record in transcript if _is_lead_skill_load(record)]
    known_times = [time for time in load_times if time is not None]

    return known_times[-1] if known_times else None


def _parse_timestamp(record: dict[str, Any]) -> float | None:
    try:
        stamp = record.get(_TIMESTAMP_KEY)

        if isinstance(stamp, str):
            return datetime.datetime.fromisoformat(stamp.replace(_UTC_SUFFIX, _UTC_OFFSET)).timestamp()
        else:
            return None
    except ValueError:
        return None


def _get_config_directory() -> Path:
    return Path(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY).expanduser()


def _get_assistant_message(record: dict[str, Any]) -> dict[str, Any] | None:
    message = record.get(_MESSAGE_KEY) if record.get(_RECORD_TYPE_KEY) == _ASSISTANT_TYPE else None

    return message if isinstance(message, dict) else None


def _is_lead_skill_load(record: dict[str, Any]) -> bool:
    message = _get_assistant_message(record)
    content = message.get(_CONTENT_KEY) if message is not None else None

    return isinstance(content, list) and any(_is_lead_skill_call(block) for block in content)


def _is_lead_skill_call(block: Any) -> bool:
    tool_input = block.get(_INPUT_KEY) if isinstance(block, dict) else None

    return (
        isinstance(tool_input, dict)
        and block.get(_RECORD_TYPE_KEY) == _TOOL_USE_TYPE
        and block.get(_NAME_KEY) == _SKILL_TOOL
        and tool_input.get(_SKILL_INPUT_KEY) == _LEAD_SKILL
    )


if __name__ == "__main__":
    sys.exit(main())
