#!/usr/bin/env python3
"""Copy the Claude desktop app's session notes folders before the app can delete them.

The app keeps a notes folder per session under `side-session-notes/` in its
data folder and deletes it, skipping the Trash, once the session that owns it
and every session it started are closed (app code, read 2026-11-20). Notes now
go in `~/Documents/session-notes/<project>/` from the start (`report-writing`
"Notes folders", user decision, 2026-11-23); this hook keeps a copy of what is
still in the app's folders in `~/Documents/session-notes-backup/app-notes/`,
adding new files and replacing changed ones but never deleting any. It is a
safety net for those older folders, not a guarantee for every way a session
closes: a session closed from the sidebar after an interrupted turn skips it.

- Stop: at the end of every turn it copies and never blocks, since blocking a
  turn's end would keep the session from stopping; a file it could not copy,
  or any other error, exits 1 so the turn ends with a hook error notice.
- PreToolUse on the tools that archive, delete, or move a session to the
  cloud: it always copies first and denies the call when any file could not
  be copied or anything else went wrong (`hook_failure.py`); the settings
  also block the call when the hook itself fails, times out, or gets input
  it cannot read (`onFailure`).
- `--now`: run by hand, it copies once and prints one line, exiting 1 when a
  file could not be copied; sessions started before this hook use it before
  they close another session.

One copy runs at a time, under a lock beside the copy, so a slow copy cannot
replace a newer file with what it read earlier. Each file is written to a
temporary folder outside the copy and then moved into place. Every run writes
its time and failures to `app-notes-status.json`, and also creates
`~/Documents/session-notes/`, which the settings grant every session.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import itertools
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, TextIO

from hook_failure import Decision, FailurePolicy, make_decision, run_hook
from report_check import APP_NOTES_PATH, PROJECT_NOTES_PATH

_HOOK_NAME = "session-notes-hook"
_BACKUP_PATH = Path("Documents") / "session-notes-backup"
_COPY_NAME = "app-notes"
_PARTIAL_FOLDER_NAME = ".app-notes-partial"
_LOCK_NAME = ".app-notes.lock"
_STATUS_NAME = "app-notes-status.json"
_STATUS_PARTIAL_SUFFIX = ".partial"
_COPY_PATH_SHOWN = "~/Documents/session-notes-backup/app-notes/"
_SKIPPED_NAMES = frozenset({".DS_Store"})
_CLOSING_LOCK_WAIT_SECONDS = 40.0
_TURN_END_LOCK_WAIT_SECONDS = 15.0
_LOCK_RETRY_SECONDS = 0.1
_STATUS_FAILURE_LIMIT = 20
_NOW_FLAG = "--now"
_EVENT_KEY = "hook_event_name"
_TOOL_KEY = "tool_name"
_PRE_TOOL_USE = "PreToolUse"
_ENCODING = "utf-8"
_DENY_REASON = f"Closing, deleting, or moving a session can make the desktop app delete its notes folder, and the copy to {_COPY_PATH_SHOWN} failed first: {{error}}. Fix the copy and try again, or ask the user to close the session from the sidebar knowing those files may be lost."
FAILURE_POLICY = FailurePolicy.blocking(_HOOK_NAME, Decision.DENY, _DENY_REASON)
# The PreToolUse matcher in settings.json names the same tools; a test keeps the two equal.
CLOSING_TOOLS = frozenset(
    {
        "mcp__ccd_session_mgmt__archive_session",
        "mcp__ccd_session_mgmt__delete_session",
        "mcp__ccd_session__move_to_cloud",
    },
)
_FAILURE_EXAMPLE_LIMIT = 3
_EXIT_FAILED = 1
_LOCK_TIMEOUT = "another copy held the lock for {seconds:.0f} seconds"
_NOW_DONE = "Copied the app's session notes from {source} to {target}."
_NOW_FAILED = "Copying the app's session notes from {source} to {target} failed: {details}."


@dataclass(frozen=True)
class _CopyResult:
    source: Path
    target: Path
    failures: tuple[str, ...]


class _LockTimeoutError(Exception):
    pass


class _CopyFailedError(Exception):
    pass


def main() -> int:
    if sys.argv[1:] == [_NOW_FLAG]:
        return _copy_now()
    else:
        return run_hook(_make_output, FAILURE_POLICY)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    """Copy, then refuse a closing call when a file was not copied; at a turn's end a failed file is an error to show."""
    is_closing = _is_closing_call(hook_input)
    wait_seconds = _CLOSING_LOCK_WAIT_SECONDS if is_closing else _TURN_END_LOCK_WAIT_SECONDS

    _make_notes_folder()
    result = _copy_app_notes(wait_seconds)

    if is_closing and result.failures:
        return _make_denial(_describe_failures(result.failures))
    elif result.failures:
        raise _CopyFailedError(_describe_failures(result.failures))
    else:
        return None


def _copy_now() -> int:
    _make_notes_folder()
    result = _copy_app_notes(_CLOSING_LOCK_WAIT_SECONDS)

    if result.failures:
        details = _describe_failures(result.failures)

        print(_NOW_FAILED.format(source=result.source, target=result.target, details=details))
        return _EXIT_FAILED
    else:
        print(_NOW_DONE.format(source=result.source, target=result.target))
        return 0


def _make_notes_folder() -> None:
    """The settings grant this folder to every session; a failure here must not stop the copy."""
    try:
        (Path.home() / PROJECT_NOTES_PATH).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"{_HOOK_NAME}: could not create the notes folder: {error}", file=sys.stderr)


def _copy_app_notes(wait_seconds: float) -> _CopyResult:
    home = Path.home()
    source = home / APP_NOTES_PATH
    backup = home / _BACKUP_PATH
    target = backup / _COPY_NAME

    if source.is_dir():
        backup.mkdir(parents=True, exist_ok=True)
        with _hold_copy_lock(backup / _LOCK_NAME, wait_seconds):
            failures = _copy_tree(source, target, backup / _PARTIAL_FOLDER_NAME)
            result = _CopyResult(source, target, tuple(failures))

            _write_status(backup / _STATUS_NAME, result)
        return result
    else:
        return _CopyResult(source, target, ())


@contextlib.contextmanager
def _hold_copy_lock(lock_path: Path, wait_seconds: float) -> Iterator[None]:
    """One copy at a time; the lock is released when the process ends, so a killed run cannot leave it held."""
    with lock_path.open("a") as lock_file:
        if _wait_for_lock(lock_file, wait_seconds):
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        else:
            raise _LockTimeoutError(_LOCK_TIMEOUT.format(seconds=wait_seconds))


def _wait_for_lock(lock_file: TextIO, wait_seconds: float) -> bool:
    deadline = time.monotonic() + wait_seconds
    is_locked = _try_lock(lock_file)

    while not is_locked and time.monotonic() <= deadline:
        time.sleep(_LOCK_RETRY_SECONDS)
        is_locked = _try_lock(lock_file)
    return is_locked


def _try_lock(lock_file: TextIO) -> bool:
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _copy_tree(source: Path, target: Path, partial_folder: Path) -> list[str]:
    """Every file under source that could not be copied, with folders that could not be listed."""
    failures: list[str] = []
    partial_names = (f"{os.getpid()}-{number}" for number in itertools.count())

    def add_walk_failure(error: OSError) -> None:
        failures.append(f"{error.filename or source} ({error.strerror or type(error).__name__})")

    # Under the lock no other copy is running, so whatever this folder holds was left by a run that was cut off.
    shutil.rmtree(partial_folder, ignore_errors=True)
    partial_folder.mkdir(parents=True, exist_ok=True)
    for directory, _, file_names in os.walk(source, onerror=add_walk_failure):
        relative = Path(directory).relative_to(source)

        for name in file_names:
            if name not in _SKIPPED_NAMES:
                partial = partial_folder / next(partial_names)

                failures.extend(_copy_file(Path(directory) / name, target / relative / name, partial))
    return failures


def _copy_file(source_file: Path, target_file: Path, partial: Path) -> tuple[str, ...]:
    """The time is set after the move, so a run cut off in between only copies the file again next time."""
    try:
        if not source_file.is_symlink():
            source_status = source_file.stat()

            if _is_changed(source_status, target_file):
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_file, partial)
                os.replace(partial, target_file)
                os.utime(target_file, ns=(source_status.st_atime_ns, source_status.st_mtime_ns))
        return ()
    except OSError as error:
        _remove_partial(partial)
        return (f"{source_file} ({error.strerror or type(error).__name__})",)


def _remove_partial(partial: Path) -> None:
    try:
        partial.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"{_HOOK_NAME}: could not remove {partial}: {error}", file=sys.stderr)


def _write_status(status_path: Path, result: _CopyResult) -> None:
    """A record of the last run, so a copy that fails quietly at a turn's end can still be seen."""
    try:
        status = {
            "last_run": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "failure_count": len(result.failures),
            "failures": list(result.failures[:_STATUS_FAILURE_LIMIT]),
        }
        partial = status_path.with_name(status_path.name + _STATUS_PARTIAL_SUFFIX)

        partial.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding=_ENCODING)
        os.replace(partial, status_path)
    except OSError as error:
        print(f"{_HOOK_NAME}: could not write the status: {error}", file=sys.stderr)


def _make_denial(details: str) -> dict[str, Any]:
    return make_decision(Decision.DENY, [_DENY_REASON.format(error=details)])


def _describe_failures(failures: tuple[str, ...]) -> str:
    shown = "; ".join(failures[:_FAILURE_EXAMPLE_LIMIT])
    hidden_count = len(failures) - _FAILURE_EXAMPLE_LIMIT

    return f"{shown}; and {hidden_count} more" if hidden_count > 0 else shown


def _is_closing_call(hook_input: dict[str, Any]) -> bool:
    tool_name = hook_input.get(_TOOL_KEY)

    return hook_input.get(_EVENT_KEY) == _PRE_TOOL_USE and isinstance(tool_name, str) and tool_name in CLOSING_TOOLS


def _is_changed(source_status: os.stat_result, target_file: Path) -> bool:
    try:
        target_status = target_file.stat()
    except FileNotFoundError:
        return True
    else:
        return source_status.st_size != target_status.st_size or source_status.st_mtime_ns != target_status.st_mtime_ns


if __name__ == "__main__":
    sys.exit(main())
