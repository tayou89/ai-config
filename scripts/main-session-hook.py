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
  starts: the role, the context's size against the context threshold, and
  whether the installed `session-management` skill changed after this
  session last loaded it. Any other session, a terminal session with no
  app records included, gets one size sentence once its context is
  past the threshold, since `collaboration-core` has every session record
  its state there (user decision, 2026-11-25).
- PreToolUse on file edits and long commands (Edit, Write, NotebookEdit,
  Monitor, a terminal-tab command, a Bash command run in the background): one
  sentence that such work goes to a session of its own.
- SessionStart after a compaction (matcher `compact`): a main or role
  session is told that it was just compacted, since `session-management`
  has it reload that skill, read its state record, and check it against the
  live session list before coordinating again (user decision, 2026-11-25).

Hook input carries no session lineage, so the hook reads the desktop app's
session records through `session_lineage`; when they or the app's session
variable are missing, as in a terminal session, the hook gives no role
sentence. A
top-level session whose title its project's role list marks `(not main)`,
read through `project_rules` from the hook input's folder, is not main, since
the user set that role apart (user decision, 2026-11-16). A role session
gets a role sentence of its own, the same size and skill sentences, and a
tool sentence like a main session's: it is a session whose title is in that
role list and that is either directly under a top-level session, as under a
project master, or top-level and marked `(not main)`. A
task session under a role session is two levels down, so it gets no role
sentence even when its title matches, and so does a top-level session that
has not started one yet, such as a new master. A role session under a master ran
on a skill copy loaded hours before the rule changed when it prepared
another session's estimate itself (`session-management` "Project master and
role sessions", user decision, 2026-11-19); the notice keeps the role and the
installed skill's state in view and blocks nothing.

The context's size is the input tokens of the last request after the
transcript's last compaction boundary, read from the transcript's end; it
matched the app's context reading within 1% on 2026-11-17, and right after
a compaction, before the next request, it is unknown and left out. The
threshold is 85% of the automatic compaction point the session's settings
give, read the way Claude Code documents it (model-config "Set the
auto-compact window", read 2026-11-25): `CLAUDE_CODE_AUTO_COMPACT_WINDOW` as a
literal token count, then the managed, project-local, project, and user
settings files, the project ones at the original checkout's root as the
desktop app reads them for a worktree, each file's `modelSettings` value for
the transcript's last model before its top-level `autoCompactWindow`; at least
100,000 tokens and at most the 1,000,000-token window of the models this
setup runs, lowered by `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, and about 967,000
when nothing sets it or a model value is "auto". `autoCompactEnabled` false
or `DISABLE_AUTO_COMPACT` turns compaction off, and the notice says so. A
`--autocompact` launch option is not visible to a hook, so the sentence names
the point as what the settings give.
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
from workflow_policy import find_repository_root, read_original_checkout

_HOOK_NAME = "main-session-hook"
FAILURE_POLICY = FailurePolicy.notice(_HOOK_NAME)
_SWITCH_VARIABLE = "MAIN_SESSION_NOTICE"
_SWITCH_OFF = "off"
_ENCODING = "utf-8"
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
_SOURCE_KEY = "source"
_USER_PROMPT_SUBMIT = "UserPromptSubmit"
_PRE_TOOL_USE = "PreToolUse"
_SESSION_START = "SessionStart"
_COMPACT_SOURCE = "compact"
_BASH_TOOL = "Bash"
_WRITING_TOOLS = ("Edit", "Write", "NotebookEdit", "Monitor", "mcp__terminal__run_in_terminal")
_RECORD_TYPE_KEY = "type"
_ASSISTANT_TYPE = "assistant"
_ASSISTANT_MARKER = b'"type":"assistant"'
_COMPACT_BOUNDARY = "compact_boundary"
_COMPACT_BOUNDARY_MARKER = f'"subtype":"{_COMPACT_BOUNDARY}"'.encode()
_SUBTYPE_KEY = "subtype"
_TAIL_START_BYTES = 1 << 20
_SIDECHAIN_KEY = "isSidechain"
_MODEL_KEY = "model"
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
_DEFAULT_COMPACTION_POINT = 967_000
_MINIMUM_COMPACTION_POINT = 100_000
_THRESHOLD_PERCENT = 85
_PERCENT_SCALE = 100
_THOUSAND = 1_000
_WINDOW_VARIABLE = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
_PERCENT_OVERRIDE_VARIABLE = "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"
_DISABLE_VARIABLE = "DISABLE_AUTO_COMPACT"
_TRUE_VALUES = ("1", "true", "yes", "on")
_TOKEN_SUFFIXES = {"k": 1_000, "m": 1_000_000}
_WINDOW_KEY = "autoCompactWindow"
_ENABLED_KEY = "autoCompactEnabled"
_MODEL_SETTINGS_KEY = "modelSettings"
_AUTO_VALUE = "auto"
_LONG_CONTEXT_SUFFIX = "[1m]"
_MANAGED_SETTINGS = Path("/Library/Application Support/ClaudeCode/managed-settings.json")
_PROJECT_SETTINGS_FOLDER = ".claude"
_SETTINGS_FILE = "settings.json"
_PROJECT_SETTINGS_FILES = ("settings.local.json", _SETTINGS_FILE)
_NOTICE_TAG = "[MAIN SESSION]"
_LEAD_SUBJECT = "Its"
_OTHER_SUBJECT = "This session's"
_ROLE_NOTICE = (
    f"{_NOTICE_TAG} This session started {{count}} other {{noun}} and is not under another session, so it is "
    f'a main session, which {_LEAD_SKILL} "Working alongside other sessions" limits to '
    "conversation and managing sessions, apart from an exception its project's rules record with the user's "
    "approval, which holds within its stated scope."
)
_SIZE_NOTICE = (
    "{subject} context is about {size}k, {percent}% of the {point}k automatic compaction point its settings give, "
    "{position} the {threshold}% context threshold."
)
_COMPACTION_OFF_NOTICE = "Its context is about {size}k, and its settings turn automatic compaction off."
_COMPACTED_NOTICE = (
    f"This session was just compacted, and {_LEAD_SKILL} has a coordinating session reload that skill, read its "
    "state record, and check it against the live session list and waiting items before coordinating again."
)
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
    """What a session that leads others is told: its role sentence and its tool notice."""

    role_notice: str
    tool_notice: str


@dataclass(frozen=True)
class _ContextReading:
    """The last request after the transcript's last compaction: its input tokens and model, or None for each."""

    size: int | None
    model: str | None


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
    elif event == _SESSION_START and hook_input.get(_SOURCE_KEY) == _COMPACT_SOURCE:
        lead = _read_lead(hook_input)

        return f"{lead.role_notice} {_COMPACTED_NOTICE}" if lead is not None else None
    else:
        return None


def _make_turn_notice(hook_input: dict[str, Any]) -> str | None:
    lead = _read_lead(hook_input)
    reading = _read_context_tail(hook_input.get(_TRANSCRIPT_PATH_KEY))

    if lead is not None:
        transcript = _read_transcript_records(hook_input.get(_TRANSCRIPT_PATH_KEY))
        lines = [lead.role_notice]

        if transcript is not None and reading is not None:
            lines.extend(_list_size_notices(hook_input, reading))
            lines.extend(_list_skill_notices(transcript))
        return " ".join(lines)
    elif reading is not None and _is_possibly_past_threshold(reading.size):
        return _make_context_notice(hook_input, reading)
    else:
        return None


def _make_context_notice(hook_input: dict[str, Any], reading: _ContextReading) -> str | None:
    """The one sentence a session that leads none gets, only once its context is past the threshold."""
    point = _read_compaction_point(_get_session_folder(hook_input), reading.model)

    if reading.size is not None and point is not None and _is_past_threshold(reading.size, point):
        return _make_size_notice(reading.size, point, _OTHER_SUBJECT)
    else:
        return None


def _list_size_notices(hook_input: dict[str, Any], reading: _ContextReading) -> list[str]:
    if reading.size is None:
        return []
    else:
        point = _read_compaction_point(_get_session_folder(hook_input), reading.model)

        if point is None:
            return [_COMPACTION_OFF_NOTICE.format(size=round(reading.size / _THOUSAND))]
        else:
            return [_make_size_notice(reading.size, point, _LEAD_SUBJECT)]


def _is_possibly_past_threshold(size: int | None) -> bool:
    """Whether a size could pass the threshold of any compaction point, so smaller ones skip reading settings."""
    return size is not None and _is_past_threshold(size, _apply_percent_override(_MINIMUM_COMPACTION_POINT))


def _is_past_threshold(size: int, point: int) -> bool:
    return size * _PERCENT_SCALE > point * _THRESHOLD_PERCENT


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
        )
    elif _is_role_session(record, title, folder, is_set_apart):
        return _Lead(
            role_notice=_ROLE_SESSION_NOTICE.format(title=title),
            tool_notice=_ROLE_TOOL_NOTICE,
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


def _parse_record(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)

        return record if isinstance(record, dict) else None
    except (ValueError, RecursionError):
        return None


def _sum_input_tokens(record: dict[str, Any]) -> int | None:
    message = _get_assistant_message(record)
    usage = message.get(_USAGE_KEY) if message is not None else None

    if isinstance(usage, dict):
        return sum(value for value in (usage.get(key) for key in _INPUT_TOKEN_KEYS) if isinstance(value, int))
    else:
        return None


def _make_size_notice(size: int, point: int, subject: str) -> str:
    return _SIZE_NOTICE.format(
        subject=subject,
        size=round(size / _THOUSAND),
        percent=round(size * _PERCENT_SCALE / point),
        point=f"{point // _THOUSAND:,}",
        position=_PAST_POSITION if _is_past_threshold(size, point) else _BELOW_POSITION,
        threshold=_THRESHOLD_PERCENT,
    )


def _read_context_tail(transcript_path: Any) -> _ContextReading | None:
    """The last main-chain request after the last compaction boundary, read backward from the transcript's end.

    The read doubles from 1 MB until it finds that request, a boundary, or the file's start, so a long transcript
    is not read whole on every turn. None means there is no readable transcript.
    """
    try:
        if isinstance(transcript_path, str) and transcript_path:
            return _scan_transcript_tail(Path(transcript_path))
        else:
            return None
    except OSError:
        return None


def _scan_transcript_tail(path: Path) -> _ContextReading:
    file_size = path.stat().st_size
    length = _TAIL_START_BYTES
    reading = None

    with path.open("rb") as transcript:
        while reading is None:
            start = max(file_size - length, 0)

            transcript.seek(start)
            lines = transcript.read().splitlines()
            reading = _find_last_request(lines[1:] if start else lines, is_whole=start == 0)
            length *= 2
    return reading


def _find_last_request(lines: list[bytes], is_whole: bool) -> _ContextReading | None:
    """The reading from the last lines, or None when they hold neither a request nor a boundary and more lie before.

    Lines are picked by a byte marker and judged by their parsed fields, since a message can quote either marker.
    """
    candidates = (line for line in reversed(lines) if _ASSISTANT_MARKER in line or _COMPACT_BOUNDARY_MARKER in line)
    parsed = (record for record in map(_parse_record, candidates) if record is not None)
    records = (record for record in parsed if not record.get(_SIDECHAIN_KEY))
    found = next((record for record in records if _is_boundary(record) or _sum_input_tokens(record)), None)

    if found is not None and not _is_boundary(found):
        return _ContextReading(size=_sum_input_tokens(found), model=_read_model(found))
    elif found is not None or is_whole:
        return _ContextReading(size=None, model=None)
    else:
        return None


def _is_boundary(record: dict[str, Any]) -> bool:
    return record.get(_SUBTYPE_KEY) == _COMPACT_BOUNDARY


def _read_model(record: dict[str, Any]) -> str | None:
    message = _get_assistant_message(record)
    model = message.get(_MODEL_KEY) if message is not None else None

    return model if isinstance(model, str) else None


def _read_compaction_point(folder: Path, model: str | None) -> int | None:
    """The token count at which the session's settings compact it, or None when they turn compaction off."""
    settings = _read_settings_files(folder)

    if _is_compaction_off(settings):
        return None
    else:
        window = _read_window_variable() or _find_settings_window(settings, model) or _DEFAULT_COMPACTION_POINT
        point = min(max(window, _MINIMUM_COMPACTION_POINT), _CONTEXT_WINDOW)

        return _apply_percent_override(point)


def _read_settings_files(folder: Path) -> list[dict[str, Any]]:
    """The settings files from the highest scope down: managed, project-local, project, then user."""
    root = _find_settings_root(folder)
    project_files = [root / _PROJECT_SETTINGS_FOLDER / name for name in _PROJECT_SETTINGS_FILES]
    paths = [_MANAGED_SETTINGS, *project_files, _get_config_directory() / _SETTINGS_FILE]

    return [_read_json_object(path) for path in paths]


def _find_settings_root(folder: Path) -> Path:
    """The folder whose `.claude` settings apply: the original checkout's root for a worktree, as the app reads it."""
    root = find_repository_root(folder)
    original = read_original_checkout(root) if root is not None else None

    return original or root or folder


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding=_ENCODING))

        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_compaction_off(settings: list[dict[str, Any]]) -> bool:
    if os.environ.get(_DISABLE_VARIABLE, "").strip().lower() in _TRUE_VALUES:
        return True
    else:
        switches = [value[_ENABLED_KEY] for value in settings if isinstance(value.get(_ENABLED_KEY), bool)]

        return bool(switches) and switches[0] is False


def _read_window_variable() -> int | None:
    """The variable's token count: a bare number is literal tokens, a k or M suffix multiplies; unusable is skipped."""
    text = os.environ.get(_WINDOW_VARIABLE, "").strip().lower()
    multiplier = _TOKEN_SUFFIXES.get(text[-1:], 1)
    number = text[:-1] if multiplier != 1 else text

    try:
        tokens = int(float(number) * multiplier)

        return tokens if tokens > 0 else None
    except (ValueError, OverflowError):
        return None


def _find_settings_window(settings: list[dict[str, Any]], model: str | None) -> int | None:
    """The first window the settings files set, each file's value for the model before its top-level value."""
    windows = (_read_model_window(value, model) or _read_token_count(value.get(_WINDOW_KEY)) for value in settings)

    return next((window for window in windows if window is not None), None)


def _read_model_window(settings: dict[str, Any], model: str | None) -> int | None:
    """A file's `modelSettings` window for the model, matched on the transcript's model id with or without `[1m]`.

    "auto" gives the tuned default, which also outranks the file's top-level value; a key written as an alias such
    as `opus` does not match, so the top-level value then applies.
    """
    model_settings = settings.get(_MODEL_SETTINGS_KEY)

    if model is None or not isinstance(model_settings, dict):
        return None
    else:
        entries = [entry for key, entry in model_settings.items() if _is_same_model(key, model)]
        windows = [entry.get(_WINDOW_KEY) for entry in entries if isinstance(entry, dict)]

        if _AUTO_VALUE in windows:
            return _DEFAULT_COMPACTION_POINT
        else:
            return next((count for count in map(_read_token_count, windows) if count is not None), None)


def _is_same_model(key: Any, model: str) -> bool:
    return isinstance(key, str) and key.removesuffix(_LONG_CONTEXT_SUFFIX) == model.removesuffix(_LONG_CONTEXT_SUFFIX)


def _read_token_count(value: Any) -> int | None:
    """A settings window, which the settings reference gives as a number of tokens; anything else is skipped."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    else:
        return None


def _apply_percent_override(point: int) -> int:
    text = os.environ.get(_PERCENT_OVERRIDE_VARIABLE, "").strip()

    if text.isdecimal() and text.isascii() and 1 <= int(text) <= _PERCENT_SCALE:
        return point * int(text) // _PERCENT_SCALE
    else:
        return point


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
