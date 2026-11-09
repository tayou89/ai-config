#!/usr/bin/env python3
"""PreToolUse, PostToolUse and PostToolUseFailure hook: record the files this session writes.

It never blocks a tool call and prints nothing. A failure only leaves a write
unrecorded, which the commit wrapper then treats as someone else's, so the
safe direction is kept.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from session_writes import (
    RepositorySnapshot,
    WriteEvent,
    append_events,
    find_command_roots,
    find_repository_root,
    get_record_directory,
    list_changes,
    remove_expired_records,
    save_pending,
    store_text,
    take_file_snapshot,
    take_pending,
    take_snapshot,
    take_snapshot_again,
)

_PRE_EVENT = "PreToolUse"
_FILE_PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
_ORIGINAL_FILE_KEY = "originalFile"


def main() -> None:
    try:
        _record_tool_call(json.load(sys.stdin))
    except Exception as error:
        print(f"session-writes-hook: {type(error).__name__}: {error}", file=sys.stderr)


def _record_tool_call(payload: dict[str, Any]) -> None:
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")

    if session_id and tool_use_id and payload.get("hook_event_name") == _PRE_EVENT:
        _remove_expired_session_records(session_id)
        save_pending(session_id, tool_use_id, _take_snapshots(session_id, payload))
    elif session_id and tool_use_id:
        _record_changes(session_id, tool_use_id, payload)


def _remove_expired_session_records(session_id: str) -> None:
    if not (get_record_directory() / session_id).exists():
        remove_expired_records()


def _take_snapshots(session_id: str, payload: dict[str, Any]) -> list[RepositorySnapshot]:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
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
    pending = take_pending(session_id, tool_use_id)

    if pending:
        start, befores = pending
        end = time.time()
        tool_name = payload.get("tool_name", "")
        events = [
            WriteEvent(before.root, path, old, new, start, end, tool_name)
            for before in _correct_before_snapshots(befores, payload)
            for path, old, new in list_changes(before, take_snapshot_again(before))
        ]
        append_events(session_id, events)


def _correct_before_snapshots(befores: list[RepositorySnapshot], payload: dict[str, Any]) -> list[RepositorySnapshot]:
    """Use the edit tool's own copy of the old file, taken as it ran, over the earlier snapshot."""
    response = payload.get("tool_response")
    original = response.get(_ORIGINAL_FILE_KEY) if isinstance(response, dict) else None

    if isinstance(original, str) and len(befores) == 1 and not befores[0].covers_repository:
        before = befores[0]
        path = next(iter(before.blobs))
        return [RepositorySnapshot(before.root, before.head, {path: store_text(before.root, original)}, False)]
    else:
        return befores


if __name__ == "__main__":
    main()
