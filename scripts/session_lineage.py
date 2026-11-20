"""Which session started this one, read from the desktop app's session records.

Hook input carries no session lineage, so hooks read the app's records
(`claude-code-sessions/*/*/local_*.json` in the app's support folder):
`spawnedFrom` names the session that started a session, `lineageDetached`
marks one moved to the top level, `cliSessionId` names the Claude Code
session the write record files go by, and `isArchived` marks one the app
archived. These records are not a documented
interface; when they or the app's session variable are missing, as in a
terminal session, every function here reports nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
CLI_SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
APP_SESSION_PREFIX = "local_"
_SESSION_RECORDS_PATH = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions"
_SESSION_RECORD_PATTERN = "*/*/local_*.json"
_SESSION_RECORD_SUFFIX = ".json"
_SPAWNED_FROM_KEY = "spawnedFrom"
_SESSION_ID_KEY = "sessionId"
_DETACHED_KEY = "lineageDetached"
_TITLE_KEY = "title"
_CLI_SESSION_ID_KEY = "cliSessionId"
_ARCHIVED_KEY = "isArchived"
_WORKTREE_PATH_KEY = "worktreePath"
_ENCODING = "utf-8"


def read_host_session_record() -> dict[str, Any] | None:
    """This session's own record, or None outside the desktop app or when the record is missing."""
    return _read_session_record(_get_host_session(), _get_records_directory())


def count_started_sessions(record: dict[str, Any]) -> int:
    """How many sessions the session with this record started, or 0 when it is under another session."""
    session_id = record.get(_SESSION_ID_KEY)

    if isinstance(session_id, str) and session_id and not is_under_another_session(record):
        paths = _get_records_directory().glob(_SESSION_RECORD_PATTERN)

        return sum(1 for path in paths if _is_started_by(path, session_id))
    else:
        return 0


def find_session_record(session_id: str) -> dict[str, Any] | None:
    """The app's record of a session named by its app id (`local_…`) or its Claude Code id, or None when there is none.

    A terminal session has no app record, so it reads as None too.
    """
    records = _get_records_directory()

    if session_id.startswith(APP_SESSION_PREFIX):
        return _read_session_record(session_id, records)
    elif session_id and records.is_dir():
        matching = (path for path in records.glob(_SESSION_RECORD_PATTERN) if _is_cli_session_record(path, session_id))
        found = next(matching, None)
        record = _read_json(found) if found is not None else None

        return record if isinstance(record, dict) else None
    else:
        return None


def read_cli_session_id(record: dict[str, Any]) -> str | None:
    """The Claude Code session id an app record names, which the write record files go by."""
    cli_session_id = record.get(_CLI_SESSION_ID_KEY)

    return cli_session_id if isinstance(cli_session_id, str) and cli_session_id else None


def read_app_session_id(record: dict[str, Any]) -> str | None:
    """The desktop-app id (`local_…`) an app record belongs to."""
    session_id = record.get(_SESSION_ID_KEY)

    return session_id if isinstance(session_id, str) and session_id else None


def read_worktree_path(record: dict[str, Any]) -> str | None:
    """The worktree the app made for the session, which its record names only for a session started in one."""
    worktree_path = record.get(_WORKTREE_PATH_KEY)

    return worktree_path if isinstance(worktree_path, str) and worktree_path else None


def is_archived(record: dict[str, Any] | None) -> bool:
    """Whether the app has archived the session, so it no longer runs; a session with no record does not count."""
    return record is not None and record.get(_ARCHIVED_KEY) is True


def read_session_title(record: dict[str, Any]) -> str:
    title = record.get(_TITLE_KEY)

    return title.strip() if isinstance(title, str) else ""


def is_under_another_session(record: dict[str, Any]) -> bool:
    return bool(record.get(_SPAWNED_FROM_KEY)) and record.get(_DETACHED_KEY) is not True


def read_parent_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """The record of the session this one is still under, or None for a top-level session or a missing record."""
    spawned_from = record.get(_SPAWNED_FROM_KEY)
    parent_id = spawned_from.get(_SESSION_ID_KEY) if isinstance(spawned_from, dict) else None

    if isinstance(parent_id, str) and parent_id and is_under_another_session(record):
        return find_session_record(parent_id)
    else:
        return None


def _get_host_session() -> str:
    return os.environ.get(HOST_SESSION_VARIABLE, "")


def _get_records_directory() -> Path:
    return Path.home() / _SESSION_RECORDS_PATH


def _read_session_record(host_session: str, records: Path) -> dict[str, Any] | None:
    if host_session and records.is_dir():
        own_records = list(records.glob(f"*/*/{host_session}{_SESSION_RECORD_SUFFIX}"))
        record = _read_json(own_records[0]) if own_records else None

        return record if isinstance(record, dict) else None
    else:
        return None


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


def _is_cli_session_record(path: Path, cli_session_id: str) -> bool:
    """Search the bytes first, so only the few records that name the id are parsed."""
    try:
        if cli_session_id.encode(_ENCODING) in path.read_bytes():
            record = _read_json(path)

            return isinstance(record, dict) and record.get(_CLI_SESSION_ID_KEY) == cli_session_id
        else:
            return False
    except OSError:
        return False


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding=_ENCODING))
    except (OSError, ValueError, RecursionError):
        return None
