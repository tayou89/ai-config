"""Which session started this one, read from the desktop app's session records.

Hook input carries no session lineage, so hooks read the app's records
(`claude-code-sessions/*/*/local_*.json` in the app's support folder):
`spawnedFrom` names the session that started a session and `lineageDetached`
marks one moved to the top level. These records are not a documented
interface; when they or the app's session variable are missing, as in a
terminal session, every function here reports nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
_SESSION_RECORDS_PATH = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions"
_SESSION_RECORD_PATTERN = "*/*/local_*.json"
_SESSION_RECORD_SUFFIX = ".json"
_SPAWNED_FROM_KEY = "spawnedFrom"
_SESSION_ID_KEY = "sessionId"
_DETACHED_KEY = "lineageDetached"
_ENCODING = "utf-8"


def read_host_session_record() -> dict[str, Any] | None:
    """This session's own record, or None outside the desktop app or when the record is missing."""
    return _read_session_record(_get_host_session(), _get_records_directory())


def count_started_sessions() -> int:
    """How many sessions this session started, or 0 when it is under another session or not an app session."""
    host_session = _get_host_session()
    records = _get_records_directory()
    record = _read_session_record(host_session, records)

    if record is not None and not is_under_another_session(record):
        paths = records.glob(_SESSION_RECORD_PATTERN)

        return sum(1 for path in paths if _is_started_by(path, host_session))
    else:
        return 0


def is_under_another_session(record: dict[str, Any]) -> bool:
    return bool(record.get(_SPAWNED_FROM_KEY)) and record.get(_DETACHED_KEY) is not True


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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding=_ENCODING))
    except (OSError, ValueError, RecursionError):
        return None
