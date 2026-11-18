#!/usr/bin/env python3
"""Show each session the new lines of the shared session bulletin once.

A UserPromptSubmit hook. Sessions announce changes other sessions need as
one line in the bulletin, a Markdown list whose entries start with their
date (user decision, 2026-11-13). At each user turn the hook adds the
entries this session has not seen yet to its context, so every session
learns of them without being woken by a message. Only entries dated within
the last 7 days count, so a session's first turn shows that week and older
entries are never shown. A record per session in `bulletin-notice/` inside
the Claude folder holds the entries already seen; files no turn has touched
for two weeks are deleted. The record is written before the notice is
shown, so a record that cannot be written shows nothing rather than the
same entries every turn. A missing or unreadable bulletin shows nothing,
and the hook never blocks or fails the user's turn.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

_HOOK_NAME = "bulletin-hook"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_BULLETIN_PATH = Path("Library") / "Application Support" / "Claude" / "side-session-notes" / "bulletin.md"
_RECORD_DIRECTORY_NAME = "bulletin-notice"
_RECORD_SUFFIX = ".json"
_PARTIAL_SUFFIX = ".tmp"
_SEEN_KEY = "seen"
_SESSION_ID_KEY = "session_id"
_ENCODING = "utf-8"
_ENTRY = re.compile(r"^-[ \t]+(\d{4}-\d{2}-\d{2})\b")
_UNSAFE_NAME_CHARACTERS = re.compile(r"[^\w-]")
_RECENT_DAYS = 7
_RECORD_LIFETIME_SECONDS = 14 * 24 * 60 * 60
_SHOWN_LIMIT = 5
_ENTRY_LENGTH_LIMIT = 300
_HOOK_EVENT = "UserPromptSubmit"
_HEADING = (
    "[BULLETIN] New entries from other sessions in the shared session bulletin {path}, for information; "
    "each entry is shown once per session."
)
_MORE_ENTRIES = "The bulletin has {count} more new entries, older than these."


def main() -> int:
    try:
        _add_notice(_read_hook_input())
    except Exception as error:
        # The notice is advisory: a failure must not delay or break the user's turn.
        print(f"{_HOOK_NAME}: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


def _read_hook_input() -> dict[str, Any]:
    text = sys.stdin.read()
    hook_input = json.loads(text) if text.strip() else {}

    return hook_input if isinstance(hook_input, dict) else {}


def _add_notice(hook_input: dict[str, Any]) -> None:
    session_id = hook_input.get(_SESSION_ID_KEY)
    bulletin = Path.home() / _BULLETIN_PATH

    if isinstance(session_id, str) and session_id and bulletin.is_file():
        _show_new_entries(bulletin, _get_record_directory() / f"{_to_safe_name(session_id)}{_RECORD_SUFFIX}")


def _show_new_entries(bulletin: Path, record: Path) -> None:
    entries = {_hash_entry(entry): entry for entry in _read_recent_entries(bulletin)}
    is_new_session = not record.is_file()
    seen = _read_seen(record)
    new_entries = [entry for digest, entry in entries.items() if digest not in seen]

    _write_seen(record, set(entries))
    if new_entries:
        print(json.dumps(_make_hook_output(bulletin, new_entries)))
    if is_new_session:
        _remove_expired_records(record.parent)


def _get_record_directory() -> Path:
    config_directory = Path(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY).expanduser()

    return config_directory / _RECORD_DIRECTORY_NAME


def _to_safe_name(text: str) -> str:
    return _UNSAFE_NAME_CHARACTERS.sub("_", text)


def _read_recent_entries(bulletin: Path) -> list[str]:
    """Bulletin entries dated today or in the 6 days before, oldest first as the file appends them."""
    oldest = datetime.date.today() - datetime.timedelta(days=_RECENT_DAYS - 1)
    lines = bulletin.read_text(encoding=_ENCODING, errors="replace").splitlines()

    return [line.strip() for line in lines if _is_recent_entry(line, oldest)]


def _parse_entry_date(line: str) -> datetime.date | None:
    """The date an entry starts with, or None for a line that is not an entry or holds an impossible date."""
    try:
        match = _ENTRY.match(line.strip())

        return datetime.date.fromisoformat(match.group(1)) if match else None
    except ValueError:
        return None


def _hash_entry(entry: str) -> str:
    return hashlib.sha256(entry.encode(_ENCODING)).hexdigest()


def _read_seen(record: Path) -> set[str]:
    """An unreadable record counts as none, so a damaged file shows the week's entries again."""
    try:
        previous = json.loads(record.read_text(encoding=_ENCODING))
        seen = previous.get(_SEEN_KEY) if isinstance(previous, dict) else None

        return {value for value in seen if isinstance(value, str)} if isinstance(seen, list) else set()
    except (OSError, ValueError, RecursionError):
        return set()


def _write_seen(record: Path, seen: set[str]) -> None:
    """Keep only entries still in the week, and replace the record whole so a reader never sees half of it."""
    partial = record.with_name(f"{record.stem}.{os.getpid()}{_PARTIAL_SUFFIX}")

    record.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text(json.dumps({_SEEN_KEY: sorted(seen)}), encoding=_ENCODING)
    partial.replace(record)


def _make_hook_output(bulletin: Path, new_entries: list[str]) -> dict[str, Any]:
    shown = [entry[:_ENTRY_LENGTH_LIMIT] for entry in new_entries[-_SHOWN_LIMIT:]]
    hidden_count = len(new_entries) - len(shown)
    lines = [_HEADING.format(path=bulletin), *shown]

    if hidden_count:
        lines.append(_MORE_ENTRIES.format(count=hidden_count))
    return {"hookSpecificOutput": {"hookEventName": _HOOK_EVENT, "additionalContext": "\n".join(lines)}}


def _remove_expired_records(directory: Path) -> None:
    """Records and partial files a crash left behind; another session may clean up at the same moment."""
    try:
        deadline = time.time() - _RECORD_LIFETIME_SECONDS

        for path in directory.iterdir():
            if path.suffix in (_RECORD_SUFFIX, _PARTIAL_SUFFIX) and path.stat().st_mtime < deadline:
                path.unlink()
    except OSError as error:
        print(f"{_HOOK_NAME}: could not clear old records: {error}", file=sys.stderr)


def _is_recent_entry(line: str, oldest: datetime.date) -> bool:
    date = _parse_entry_date(line)

    return date is not None and date >= oldest


if __name__ == "__main__":
    sys.exit(main())
