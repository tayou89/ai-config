#!/usr/bin/env python3
"""PreToolUse, PostToolUse and PostToolUseFailure hook: record the files this session writes.

It never blocks a tool call and prints nothing. A failure only leaves a write
unrecorded, which the commit wrapper then treats as someone else's.

For Edit and Write it also records whether the tool's own reported change
turns the before snapshot into the after snapshot exactly: only then is the
change surely the session's, since the snapshots also catch whatever other
sessions write meanwhile.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from session_writes import (
    RECORD_FORMAT,
    RepositorySnapshot,
    WriteEvent,
    append_events,
    find_command_roots,
    find_repository_root,
    get_record_directory,
    list_changes,
    read_blob_text,
    read_pending,
    remove_expired_records,
    remove_pending,
    save_pending,
    take_file_snapshot,
    take_snapshot,
    take_snapshot_again,
)

_PRE_EVENT = "PreToolUse"
_FAILURE_EVENT = "PostToolUseFailure"
_EVENT_NAME_KEY = "hook_event_name"
_TOOL_NAME_KEY = "tool_name"
_INPUT_KEY = "tool_input"
_RESPONSE_KEY = "tool_response"
_EDIT_TOOL = "Edit"
_WRITE_TOOL = "Write"
_FILE_PATH_KEYS = {_EDIT_TOOL: "file_path", _WRITE_TOOL: "file_path", "NotebookEdit": "notebook_path"}
_PATCH_KEY = "structuredPatch"
_CONTENT_KEY = "content"
_HUNK_LINES_KEY = "lines"
_HUNK_OLD_START_KEY = "oldStart"
_HUNK_OLD_LINES_KEY = "oldLines"
_CONTEXT_SIGN = " "
_REMOVED_SIGN = "-"
_ADDED_SIGN = "+"
_NO_NEWLINE_SIGN = "\\"
_LF = "\n"
_CRLF = "\r\n"


def main() -> None:
    try:
        _record_tool_call(json.load(sys.stdin))
    except Exception as error:
        print(f"session-writes-hook: {type(error).__name__}: {error}", file=sys.stderr)


def _record_tool_call(payload: dict[str, Any]) -> None:
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")

    if session_id and tool_use_id and payload.get(_EVENT_NAME_KEY) == _PRE_EVENT:
        _remove_expired_session_records(session_id)
        start = time.time()

        save_pending(session_id, tool_use_id, _take_snapshots(session_id, payload), start)
    elif session_id and tool_use_id:
        _record_changes(session_id, tool_use_id, payload)


def _remove_expired_session_records(session_id: str) -> None:
    if not (get_record_directory() / session_id).exists():
        remove_expired_records()


def _take_snapshots(session_id: str, payload: dict[str, Any]) -> list[RepositorySnapshot]:
    tool_name = payload.get(_TOOL_NAME_KEY, "")
    tool_input = payload.get(_INPUT_KEY) or {}
    file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))

    if file_path:
        return _take_file_snapshots(file_path)
    else:
        roots = find_command_roots(session_id, tool_input.get("command") or "", payload.get("cwd"))
        return [take_snapshot(root) for root in roots]


def _take_file_snapshots(file_path: str) -> list[RepositorySnapshot]:
    root = find_repository_root(file_path)

    if root:
        return [take_file_snapshot(root, os.path.relpath(os.path.realpath(file_path), os.path.realpath(root)))]
    else:
        return []


def _record_changes(session_id: str, tool_use_id: str, payload: dict[str, Any]) -> None:
    pending = read_pending(session_id, tool_use_id)

    if pending:
        start, befores = pending
        afters = [take_snapshot_again(before) for before in befores]
        end = time.time()
        tool_name = payload.get(_TOOL_NAME_KEY, "")
        changes = [
            (before.root, change) for before, after in zip(befores, afters) for change in list_changes(before, after)
        ]
        events = [
            WriteEvent(
                root, path, old, new, start, end, tool_name, _judge_exactness(payload, root, old, new), RECORD_FORMAT
            )
            for root, (path, old, new) in changes
        ]

        append_events(session_id, events)
        remove_pending(session_id, tool_use_id)


def _judge_exactness(
    payload: dict[str, Any], root: str, before_blob: str | None, after_blob: str | None
) -> bool | None:
    """Whether an Edit or Write call's own reported change turns its before snapshot into its after snapshot.

    None for other tools. A failed call or a response this hook cannot read is
    not exact: a failure does not promise that nothing changed, and an unknown
    response cannot account for the change.
    """
    tool_name = payload.get(_TOOL_NAME_KEY)
    response = payload.get(_RESPONSE_KEY)

    if tool_name not in (_EDIT_TOOL, _WRITE_TOOL):
        return None
    elif payload.get(_EVENT_NAME_KEY) == _FAILURE_EVENT or not isinstance(response, dict):
        return False
    else:
        before = read_blob_text(root, before_blob)
        after = read_blob_text(root, after_blob)
        expected = _expect_edit(response, before) if tool_name == _EDIT_TOOL else _expect_write(payload, response)

        return expected is not None and expected == after


def _expect_edit(response: dict[str, Any], before: str | None) -> str | None:
    """The file an Edit should leave: its structured patch applied to the file before it, in that file's line endings.

    Claude Code reports the patch with line endings turned into LF and writes
    the result back in the file's own endings.
    """
    hunks = response.get(_PATCH_KEY)
    applied = (
        _apply_hunks(before.replace(_CRLF, _LF), hunks) if before is not None and isinstance(hunks, list) else None
    )

    if applied is not None and before is not None:
        return _restore_line_endings(applied, before)
    else:
        return None


def _expect_write(payload: dict[str, Any], response: dict[str, Any]) -> str | None:
    """The file a Write should leave: the content it was given, written as is."""
    tool_input = payload.get(_INPUT_KEY)
    requested = tool_input.get(_CONTENT_KEY) if isinstance(tool_input, dict) else None
    reported = response.get(_CONTENT_KEY)

    if isinstance(requested, str):
        return requested
    elif isinstance(reported, str):
        return reported
    else:
        return None


def _restore_line_endings(text: str, before: str) -> str | None:
    """Text with LF endings in the endings of the file before it, or None for a file that mixes them."""
    if _CRLF not in before:
        return text
    elif before.count(_CRLF) == before.count(_LF):
        return text.replace(_LF, _CRLF)
    else:
        return None


def _apply_hunks(text: str, hunks: list[Any]) -> str | None:
    """Apply a jsdiff structured patch to LF text, or None when it does not fit.

    Every kept and removed line must match the text where the patch puts it,
    so a change someone else made inside the patch's reach is caught.
    """
    lines = _split_keeping_ends(text)
    result: list[str] = []
    position = 0

    for hunk in hunks:
        start = _find_hunk_start(hunk)
        changes = _parse_hunk_lines(hunk)
        applied = _apply_hunk(lines, position, start, changes) if start is not None and changes is not None else None

        if applied is None:
            return None
        else:
            result.extend(applied[0])
            position = applied[1]
    return "".join([*result, *lines[position:]])


def _apply_hunk(
    lines: list[str],
    position: int,
    start: int,
    changes: list[tuple[str, str]],
) -> tuple[list[str], int] | None:
    """The lines from position through one hunk, and where the hunk ends, or None when it does not fit there."""
    if start >= position:
        return _apply_hunk_changes(lines, lines[position:start], start, changes)
    else:
        return None


def _apply_hunk_changes(
    lines: list[str],
    result: list[str],
    cursor: int,
    changes: list[tuple[str, str]],
) -> tuple[list[str], int] | None:
    """Kept and removed lines must match the text at the cursor; added lines go to the result."""
    for sign, line in changes:
        is_matching = cursor < len(lines) and lines[cursor] == line

        if sign == _ADDED_SIGN:
            result.append(line)
        elif is_matching and sign == _REMOVED_SIGN:
            cursor += 1
        elif is_matching:
            result.append(line)
            cursor += 1
        else:
            return None
    return result, cursor


def _split_keeping_ends(text: str) -> list[str]:
    pieces = text.split(_LF)

    return [f"{piece}{_LF}" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def _find_hunk_start(hunk: Any) -> int | None:
    """The 0-based line where a hunk begins; jsdiff numbers an insertion-only hunk by the line it follows."""
    old_start = hunk.get(_HUNK_OLD_START_KEY) if isinstance(hunk, dict) else None
    old_lines = hunk.get(_HUNK_OLD_LINES_KEY) if isinstance(hunk, dict) else None

    if isinstance(old_start, int) and isinstance(old_lines, int):
        return old_start if old_lines == 0 else old_start - 1
    else:
        return None


def _parse_hunk_lines(hunk: Any) -> list[tuple[str, str]] | None:
    """A hunk's lines as (sign, line with its LF), with the LF dropped where a no-newline marker follows."""
    raw = hunk.get(_HUNK_LINES_KEY) if isinstance(hunk, dict) else None
    changes: list[tuple[str, str]] = []

    for line in raw if isinstance(raw, list) else [None]:
        if isinstance(line, str) and line.startswith(_NO_NEWLINE_SIGN) and changes:
            sign, previous = changes[-1]
            changes[-1] = (sign, previous[: -len(_LF)])
        elif isinstance(line, str) and line[:1] in (_CONTEXT_SIGN, _REMOVED_SIGN, _ADDED_SIGN):
            changes.append((line[:1], f"{line[1:]}{_LF}"))
        else:
            return None
    return changes


if __name__ == "__main__":
    main()
