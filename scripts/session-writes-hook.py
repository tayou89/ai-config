#!/usr/bin/env python3
"""PreToolUse, PostToolUse and PostToolUseFailure hook: record the files this session writes.

It never blocks a tool call and prints nothing. A failure only leaves a write
unrecorded, which the commit wrapper then treats as someone else's.

For Edit and Write it also records whether the tool's own reported change
turns the before snapshot into the after snapshot exactly: only then is the
change surely the session's, since the snapshots also catch whatever other
sessions write meanwhile. An Edit is judged first by the strings it reports
replacing and then by its structured patch, which shows tabs as spaces.

A snapshot that cannot be taken, before or after the call, such as one of a
folder the call moved away, turns what it would have covered into an unknown
write over the call's time. Each other snapshot is still recorded, and the
call stops counting as running once it has ended.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from session_writes import (
    RECORD_FORMAT,
    PendingCall,
    RepositorySnapshot,
    SessionWritesError,
    SnapshotScope,
    UnknownWrite,
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
    take_snapshot,
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
_OLD_STRING_KEY = "oldString"
_NEW_STRING_KEY = "newString"
_REPLACE_ALL_KEY = "replaceAll"
_FIRST_OCCURRENCE = 1
_EVERY_OCCURRENCE = -1
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
        _record_call_start(session_id, tool_use_id, payload)
    elif session_id and tool_use_id:
        _record_changes(session_id, tool_use_id, payload)


def _record_call_start(session_id: str, tool_use_id: str, payload: dict[str, Any]) -> None:
    """Save the call's pending file, then, on the session's first call, drop expired records.

    The cleanup comes last because it can fail while other sessions remove
    their own files, and failing before the pending file is saved would leave
    the call unseen by them.
    """
    is_first_call = not (get_record_directory() / session_id).exists()
    start = time.time()
    snapshots, unknown = _take_snapshots(_list_scopes(session_id, payload))

    save_pending(session_id, tool_use_id, PendingCall(start, snapshots, unknown))
    if is_first_call:
        remove_expired_records()


def _list_scopes(session_id: str, payload: dict[str, Any]) -> list[SnapshotScope]:
    tool_name = payload.get(_TOOL_NAME_KEY, "")
    tool_input = payload.get(_INPUT_KEY) or {}
    file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))

    if file_path:
        return _list_file_scopes(file_path)
    else:
        roots = find_command_roots(session_id, tool_input.get("command") or "", payload.get("cwd"))

        return [SnapshotScope(root, None) for root in roots]


def _list_file_scopes(file_path: str) -> list[SnapshotScope]:
    root = find_repository_root(file_path)

    if root:
        return [SnapshotScope(root, os.path.relpath(os.path.realpath(file_path), os.path.realpath(root)))]
    else:
        return []


def _take_snapshots(scopes: Sequence[SnapshotScope]) -> tuple[list[RepositorySnapshot], list[SnapshotScope]]:
    """The snapshots that could be taken, and the scopes of those that could not."""
    taken = [(scope, _try_snapshot(scope)) for scope in scopes]
    snapshots = [snapshot for _, snapshot in taken if snapshot is not None]
    unknown = [scope for scope, snapshot in taken if snapshot is None]

    return snapshots, unknown


def _try_snapshot(scope: SnapshotScope) -> RepositorySnapshot | None:
    try:
        snapshot = take_snapshot(scope)
    except SessionWritesError:
        return None
    else:
        return snapshot


def _record_changes(session_id: str, tool_use_id: str, payload: dict[str, Any]) -> None:
    """Record the call's changes and its unknown writes, then drop its pending file, so it is never in neither."""
    pending = read_pending(session_id, tool_use_id)

    if pending:
        pairs = [(before, _try_snapshot(before.get_scope())) for before in pending.snapshots]
        end = time.time()
        tool_name = payload.get(_TOOL_NAME_KEY, "")
        changes = [
            (before.root, change)
            for before, after in pairs
            if after is not None
            for change in list_changes(before, after)
        ]
        events = [
            WriteEvent(
                root,
                path,
                old,
                new,
                pending.start,
                end,
                tool_name,
                _judge_exactness(payload, root, old, new),
                RECORD_FORMAT,
            )
            for root, (path, old, new) in changes
        ]
        unknown = [*pending.unknown, *(before.get_scope() for before, after in pairs if after is None)]
        unknown_writes = [UnknownWrite(scope.root, scope.path, pending.start, end, tool_name) for scope in unknown]

        append_events(session_id, [*events, *unknown_writes])
        remove_pending(session_id, tool_use_id)


def _judge_exactness(
    payload: dict[str, Any], root: str, before_blob: str | None, after_blob: str | None
) -> bool | None:
    """Whether an Edit or Write call's own reported change turns its before snapshot into its after snapshot.

    None for other tools. A failed call, a response this hook cannot read, or
    contents it cannot read back is not exact: a failure does not promise that
    nothing changed, and what cannot be read cannot account for the change.
    """
    tool_name = payload.get(_TOOL_NAME_KEY)
    response = payload.get(_RESPONSE_KEY)

    if tool_name not in (_EDIT_TOOL, _WRITE_TOOL):
        return None
    elif payload.get(_EVENT_NAME_KEY) == _FAILURE_EVENT or not isinstance(response, dict):
        return False
    else:
        return _is_change_explained(payload, response, root, before_blob, after_blob)


def _is_change_explained(
    payload: dict[str, Any], response: dict[str, Any], root: str, before_blob: str | None, after_blob: str | None
) -> bool:
    try:
        before = read_blob_text(root, before_blob)
        after = read_blob_text(root, after_blob)
    except SessionWritesError:
        return False
    else:
        is_edit = payload.get(_TOOL_NAME_KEY) == _EDIT_TOOL
        expected = _list_expected_files(response, before) if is_edit else [_expect_write(payload, response)]

        return after is not None and after in expected


def _list_expected_files(response: dict[str, Any], before: str | None) -> list[str | None]:
    """The files an Edit may have left by what it reports: its replaced strings, then its structured patch.

    Claude Code reports the strings as the file has them, but its structured
    patch shows each tab as two spaces (seen in 2.1.317), so for a file
    indented with tabs only the strings account for the change. Either one
    must give the whole after snapshot, so a write by anyone else meanwhile
    still fails both.
    """
    return [_expect_replacement(response, before), _expect_patch(response, before)]


def _expect_replacement(response: dict[str, Any], before: str | None) -> str | None:
    """The file an Edit should leave by its reported strings."""
    return _change_lf_text(before, functools.partial(_replace_reported_strings, response=response))


def _replace_reported_strings(text: str, response: dict[str, Any]) -> str | None:
    """LF text with an Edit's oldString replaced as Claude Code does, or None when the strings do not fit it.

    It replaces the first oldString, or every one with replaceAll, and when
    newString is empty it takes along the LF right after oldString if one
    follows, as 36 recorded deletions show.
    """
    old = response.get(_OLD_STRING_KEY)
    new = response.get(_NEW_STRING_KEY)
    count = _EVERY_OCCURRENCE if response.get(_REPLACE_ALL_KEY) is True else _FIRST_OCCURRENCE

    if isinstance(old, str) and isinstance(new, str) and old and old in text:
        is_line_removed = not new and not old.endswith(_LF) and f"{old}{_LF}" in text
        target = f"{old}{_LF}" if is_line_removed else old

        return text.replace(target, new, count)
    else:
        return None


def _expect_patch(response: dict[str, Any], before: str | None) -> str | None:
    """The file an Edit should leave by its structured patch."""
    hunks = response.get(_PATCH_KEY)

    if isinstance(hunks, list):
        return _change_lf_text(before, functools.partial(_apply_hunks, hunks=hunks))
    else:
        return None


def _change_lf_text(before: str | None, change: Callable[[str], str | None]) -> str | None:
    """A change made to the file's text with LF endings, back in the file's own endings, or None when it does not fit.

    Claude Code reports an Edit with line endings turned into LF and writes
    the result back in the file's own endings.
    """
    changed = change(before.replace(_CRLF, _LF)) if before is not None else None

    if changed is not None and before is not None:
        return _restore_line_endings(changed, before)
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
