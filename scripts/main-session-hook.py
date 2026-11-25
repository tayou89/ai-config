#!/usr/bin/env python3
"""Tell a main or role session what it is, at each user turn and before it changes files or starts a long command.

A session is the main session once it has started another session while no
other session started it, or after it was moved to the top level
(`session-management` "Working alongside other sessions", user
decisions, 2026-11-08 and 2026-11-13). The rule lives in a skill, and a long
session can hold a copy loaded before the rule changed, since a compaction
restores each skill as it was loaded; a main session that worked on its own
and passed the replacement threshold without proposing it did so with that
rule missing from its context (2026-11-13). This hook puts the facts back:

- UserPromptSubmit, which also runs on turns another session's message
  starts: the role, the context's size against the 80% replacement
  threshold, and whether the installed `session-management` skill
  changed after this session last loaded it.
- PreToolUse on file edits and long commands (Edit, Write, NotebookEdit,
  Monitor, a terminal-tab command, a Bash command run in the background): one
  sentence that such work goes to a session of its own.

Hook input carries no session lineage, so the hook reads the desktop app's
session records through `session_lineage`; when they or the app's session
variable are missing, as in a terminal session, the hook adds nothing. A
top-level session whose title its project's role list marks `(not main)`,
read through `project_rules` from the hook input's folder, is not main, since
the user set that role apart (user decision, 2026-11-16). A role session
gets a role sentence of its own, the same size and skill sentences, and a
tool sentence like a main session's: it is a session whose title is in that
role list and that is either directly under a top-level session, as under a
project master, or top-level and marked `(not main)`. A
task session under a role session is two levels down, so it gets nothing
even when its title matches, and so does a top-level session that has not
started one yet, such as a new master. A role session under a master ran
on a skill copy loaded hours before the rule changed when it prepared
another session's estimate itself (`session-management` "Project master and
role sessions", user decision, 2026-11-19); the notice keeps the role and the
installed skill's state in view and blocks nothing. The
context's size is the last request's input tokens in the transcript, which
matched the app's context reading within 1% on 2026-11-17. Hook input carries
no window size, so the window is the 1,000,000 tokens the app reported for
the main sessions' model that day; a model with another window reads wrong.
A subagent's tool calls get nothing, since it cannot start sessions, and an
unreadable transcript leaves out the size and skill sentences.
MAIN_SESSION_NOTICE=off in the settings env turns the notices off. The hook
never blocks; a failure adds no notice and shows a hook error notice instead
(`hook_failure.py`).
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hook_failure import FailurePolicy, run_hook
from project_rules import list_not_main_role_titles, list_role_titles
from session_lineage import (
    count_started_sessions,
    is_under_another_session,
    read_host_session_record,
    read_parent_record,
    read_session_title,
)

_HOOK_NAME = "main-session-hook"
FAILURE_POLICY = FailurePolicy.notice(_HOOK_NAME)
_SWITCH_VARIABLE = "MAIN_SESSION_NOTICE"
_SWITCH_OFF = "off"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_LEAD_SKILL = "session-management"
_SKILL_FILE = Path("skills") / _LEAD_SKILL / "SKILL.md"
_SKILL_TOOL = "Skill"
_SKILL_INPUT_KEY = "skill"
_EVENT_KEY = "hook_event_name"
_AGENT_ID_KEY = "agent_id"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"
_TRANSCRIPT_PATH_KEY = "transcript_path"
_CWD_KEY = "cwd"
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
_CONTEXT_WINDOW = 1_000_000
_REPLACEMENT_PERCENT = 80
_PERCENT_SCALE = 100
_THOUSAND = 1_000
_NOTICE_TAG = "[MAIN SESSION]"
_ROLE_NOTICE = (
    f"{_NOTICE_TAG} This session started {{count}} other {{noun}} and is not under another session, so it is "
    f'a main session, which {_LEAD_SKILL} "Working alongside other sessions" limits to '
    "conversation and managing sessions."
)
_SIZE_NOTICE = "Its context is about {size}k of the {window}k window ({percent}%), {position} the {threshold}% {threshold_name}."
_MAIN_THRESHOLD_NAME = "replacement threshold"
_ROLE_THRESHOLD_NAME = "context threshold"
_PAST_POSITION = "past"
_BELOW_POSITION = "below"
_SKILL_CHANGED_NOTICE = (
    f"The installed {_LEAD_SKILL} skill file was modified after this session last called the Skill tool for it, "
    "and a compaction restores each skill as it was loaded."
)
_SKILL_NOT_LOADED_NOTICE = f"This session has not loaded the {_LEAD_SKILL} skill with the Skill tool."
_TOOL_NOTICE = (
    f"{_NOTICE_TAG} This is a main session, where file changes and long commands go to a session of their own "
    "unless the tool allows neither starting nor proposing one."
)
_ROLE_NOTICE_TAG = "[ROLE SESSION]"
_ROLE_SESSION_NOTICE = f"{_ROLE_NOTICE_TAG} Its project's role list and its place in the session tree mark this session `{{title}}` as a role session; under {_LEAD_SKILL}, file changes, runs, and preparing a new estimate, analysis, comparison, or review belong to a task session under it, other sessions' requests included."
_ROLE_TOOL_NOTICE = f"{_ROLE_NOTICE_TAG} This is a role session, where file changes and long commands go to a task session under it unless the tool allows neither starting nor proposing one."


@dataclass(frozen=True)
class _Lead:
    """What a session that leads others is told: its role sentence, its tool notice, and its context threshold."""

    role_notice: str
    tool_notice: str
    threshold_name: str


def main() -> int:
    return run_hook(_make_hook_output, FAILURE_POLICY)


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
        lead = _read_lead(hook_input)

        return lead.tool_notice if lead is not None else None
    else:
        return None


def _make_turn_notice(hook_input: dict[str, Any]) -> str | None:
    lead = _read_lead(hook_input)

    if lead is not None:
        transcript = _read_transcript_records(hook_input.get(_TRANSCRIPT_PATH_KEY))
        lines = [lead.role_notice]

        if transcript is not None:
            lines.extend(_list_transcript_notices(transcript, lead.threshold_name))
        return " ".join(lines)
    else:
        return None


def _read_lead(hook_input: dict[str, Any]) -> _Lead | None:
    """How this session leads others: as a main session, as a role session, or not at all.

    A title its project's rules mark `(not main)` is never main, and a main session is told only that role.
    """
    record = read_host_session_record()
    title = read_session_title(record) if record is not None else ""
    folder = _get_session_folder(hook_input)
    is_set_apart = bool(title) and title in list_not_main_role_titles(folder)
    count = count_started_sessions(record) if record is not None and not is_set_apart else 0

    if count:
        return _Lead(
            role_notice=_ROLE_NOTICE.format(count=count, noun="session" if count == 1 else "sessions"),
            tool_notice=_TOOL_NOTICE,
            threshold_name=_MAIN_THRESHOLD_NAME,
        )
    elif _is_role_session(record, title, folder, is_set_apart):
        return _Lead(
            role_notice=_ROLE_SESSION_NOTICE.format(title=title),
            tool_notice=_ROLE_TOOL_NOTICE,
            threshold_name=_ROLE_THRESHOLD_NAME,
        )
    else:
        return None


def _is_role_session(record: dict[str, Any] | None, title: str, folder: Path, is_set_apart: bool) -> bool:
    return record is not None and title in list_role_titles(folder) and _is_placed_as_role(record, is_set_apart)


def _is_placed_as_role(record: dict[str, Any], is_set_apart: bool) -> bool:
    """Whether the session tree puts a listed title at a role session's place, not at a task session's.

    A role session is directly under a top-level session such as its project master, or top-level and marked
    `(not main)`; a task session is under a role session, and a session whose parent record is missing is not judged.
    """
    parent = read_parent_record(record)

    if parent is not None:
        return not is_under_another_session(parent)
    else:
        return is_set_apart and not is_under_another_session(record)


def _get_session_folder(hook_input: dict[str, Any]) -> Path:
    folder = hook_input.get(_CWD_KEY)

    return Path(folder).resolve() if isinstance(folder, str) and folder else Path.cwd()


def _is_writing_call(hook_input: dict[str, Any]) -> bool:
    tool_name = hook_input.get(_TOOL_NAME_KEY)
    tool_input = hook_input.get(_TOOL_INPUT_KEY) or {}

    if tool_name == _BASH_TOOL:
        return isinstance(tool_input, dict) and tool_input.get(_BACKGROUND_KEY) is True
    else:
        return tool_name in _WRITING_TOOLS


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


def _list_transcript_notices(transcript: list[dict[str, Any]], threshold_name: str) -> list[str]:
    size = _read_context_size(transcript)
    notices = [_make_size_notice(size, threshold_name)] if size is not None else []

    return notices + _list_skill_notices(transcript)


def _parse_record(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)

        return record if isinstance(record, dict) else None
    except (ValueError, RecursionError):
        return None


def _read_context_size(transcript: list[dict[str, Any]]) -> int | None:
    """The last request's input tokens, the whole context the model read.

    Records with no input tokens, such as messages the tool writes itself, are not requests and are skipped.
    """
    totals = [total for total in map(_sum_input_tokens, transcript) if total]

    if totals:
        return totals[-1]
    else:
        return None


def _sum_input_tokens(record: dict[str, Any]) -> int | None:
    message = _get_assistant_message(record)
    usage = message.get(_USAGE_KEY) if message is not None else None

    if isinstance(usage, dict):
        return sum(value for value in (usage.get(key) for key in _INPUT_TOKEN_KEYS) if isinstance(value, int))
    else:
        return None


def _make_size_notice(size: int, threshold_name: str) -> str:
    is_past = size * _PERCENT_SCALE > _CONTEXT_WINDOW * _REPLACEMENT_PERCENT

    return _SIZE_NOTICE.format(
        size=round(size / _THOUSAND),
        window=f"{_CONTEXT_WINDOW // _THOUSAND:,}",
        percent=round(size * _PERCENT_SCALE / _CONTEXT_WINDOW),
        position=_PAST_POSITION if is_past else _BELOW_POSITION,
        threshold=_REPLACEMENT_PERCENT,
        threshold_name=threshold_name,
    )


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
