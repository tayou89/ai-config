"""Record the file contents each Claude session writes, so a commit can keep only its own lines.

The hook (session-writes-hook.py) snapshots files before and after every tool
call that can write them and appends one event per changed file to the
session's record. Contents go into the repository's object store with
`git hash-object -w`, as `git stash` does, so the record holds only object
names. Snapshots per command follow Jujutsu, which snapshots the working copy
at every command. The commit wrapper reads a file's history back with
load_file_history().

Sessions share one folder, and a snapshot cannot tell who wrote a change made
while a call ran. So a write counts as the session's only on positive
evidence: an Edit or Write whose own reported change turns its before snapshot
into its after snapshot exactly, or any other write that no other session's
write, or call still running, overlaps in time. Every other write is
contested, and the commit wrapper asks about its changes instead of guessing.
Evidence that cannot be read back stops the commit rather than being skipped,
since a missing record of another session would hand its change to this one.

A snapshot that cannot be taken raises SessionWritesError instead of
returning an empty one: an empty snapshot would read as "nothing was dirty"
and hand another session's uncommitted lines to this session. File contents
are kept as bytes end to end (decoded with surrogateescape), so line endings
and undecodable bytes survive the round trip.

Usage (list what --own-lines would leave out):
    session_writes.py report <file1> [<file2> ...]
"""

from __future__ import annotations

import functools
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from own_lines import FileHistory, RecordedWrite, build_own_content, list_foreign_lines

_SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_RECORD_DIRECTORY_NAME = "session-writes"
_RECORD_SUFFIX = ".jsonl"
_PENDING_SUFFIX = ".pending.json"
_STARTED_FILE = "started"
_PROJECTS_DIRECTORY_NAME = "projects"
_TRANSCRIPT_SUFFIX = ".jsonl"
_TOOL_USE_MARKER = '"tool_use"'
_TOOL_RESULT_MARKER = '"tool_result"'
_TOOL_USE_TYPE = "tool_use"
_TOOL_RESULT_TYPE = "tool_result"
_RECORDED_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "Bash", "Monitor", "mcp__terminal__run_in_terminal"})
_TIMESTAMP_KEY = "timestamp"
_UTC_SUFFIX = "Z"
_UTC_OFFSET = "+00:00"
_UNKNOWN_TIME = 0.0
_INCOMPLETE_RECORD_WARNING = (
    "Note: this session's write record started after its first tool call, so lines it wrote earlier count as "
    "not its own here; the commit wrapper skips its own-lines check for this session."
)
_RECORD_LIFETIME_SECONDS = 14 * 24 * 60 * 60
_PENDING_LIFETIME_SECONDS = 60 * 60
_OVERLAP_MARGIN_SECONDS = 2.0
RECORD_FORMAT = 2
_EARLIEST_RECORD_FORMAT = 1
_PENDING_TOOL = "pending"
_RECORD_LINE_END = "\n"
_ROOT_FIELD = "root"
_MAX_TRACKED_BYTES = 1024 * 1024
_GIT_TIMEOUT_SECONDS = 10
_RENAME_OR_COPY_STATUSES = "RC"
_REPORT_COMMAND = "report"
_HEAD_REVISION = "HEAD"
_TEXT_ENCODING = "utf-8"
_LOSSLESS_ERRORS = "surrogateescape"
_EXIT_USAGE = 4
_ABSOLUTE_PATH_PATTERN = re.compile(r"""(?:^|(?<=[\s'"=(:]))((?:/|~/)[^\s'"<>|;&()`$]+)""")


class SessionWritesError(RuntimeError):
    pass


@dataclass(frozen=True)
class WriteEvent:
    """One file one tool call changed.

    `exact` is True when the call was an Edit or Write whose own reported
    change turns `before` into `after` exactly, False when such a call's
    snapshots hold anything else, and None for other tools. From
    `record_format` 2 on, `start` and `end` enclose both snapshots; earlier
    records have neither, and their times sit just inside their snapshots.
    """

    root: str
    path: str
    before: str | None
    after: str | None
    start: float
    end: float
    tool: str
    exact: bool | None = None
    record_format: int = _EARLIEST_RECORD_FORMAT


@dataclass(frozen=True)
class RepositorySnapshot:
    root: str
    head: str | None
    blobs: dict[str, str | None]
    covers_repository: bool


_EVENT_FIELDS = frozenset(event_field.name for event_field in fields(WriteEvent))


def read_session_id() -> str | None:
    return os.environ.get(_SESSION_VARIABLE) or None


def has_record(session_id: str) -> bool:
    """Whether the record hook has run in this session; sessions started before it was installed have none."""
    return (get_record_directory() / session_id).is_dir()


def is_record_complete(session_id: str) -> bool:
    """Whether the record began before this session's first recorded tool call ended, so it holds every write.

    A session that was already running when the hook was installed starts its
    record mid-session: its earlier writes are missing and would read as
    another session's lines, so such a record is not complete.
    """
    started = _read_record_start(session_id)
    transcript = _find_transcript(session_id)
    first_result = _read_first_recorded_result_time(transcript) if transcript is not None else None

    return started is not None and (first_result is None or started <= first_result)


def get_record_directory() -> Path:
    return _get_config_directory() / _RECORD_DIRECTORY_NAME


def find_repository_root(path: str) -> str | None:
    """The repository holding a path, judged from its nearest existing folder so new folders count too."""
    output = _try_git(_find_existing_directory(path), "rev-parse", "--show-toplevel")

    return decode_content(output).strip() or None if output is not None else None


def find_command_roots(session_id: str, command: str, cwd: str | None) -> list[str]:
    """Repositories a command may write: its folder, folders it names, and ones this session wrote before."""
    named = [os.path.expanduser(match) for match in _ABSOLUTE_PATH_PATTERN.findall(command)]
    candidates = [*([cwd] if cwd else []), *named]
    roots = {root for root in map(find_repository_root, candidates) if root}

    return sorted(roots | _read_recorded_roots(session_id))


def take_file_snapshot(root: str, path: str) -> RepositorySnapshot:
    return RepositorySnapshot(root, _read_head(root), _store_files(root, [path]), covers_repository=False)


def take_snapshot_again(snapshot: RepositorySnapshot) -> RepositorySnapshot:
    if snapshot.covers_repository:
        return take_snapshot(snapshot.root)
    else:
        return take_file_snapshot(snapshot.root, next(iter(snapshot.blobs)))


def take_snapshot(root: str) -> RepositorySnapshot:
    status = _run_git(root, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths = _parse_status_paths(decode_content(status))

    return RepositorySnapshot(root, _read_head(root), _store_files(root, paths), covers_repository=True)


def store_text(root: str, text: str) -> str:
    return decode_content(_run_git(root, "hash-object", "-w", "--stdin", input_bytes=encode_content(text))).strip()


def read_blob_text(root: str, blob: str | None) -> str | None:
    """A stored content as text, or None for a file that did not exist."""
    if blob:
        return _read_blob(root, blob)
    else:
        return None


def list_changes(before: RepositorySnapshot, after: RepositorySnapshot) -> list[tuple[str, str | None, str | None]]:
    """Files whose content differs between two snapshots; a file missing from one was clean there."""
    paths = sorted(set(before.blobs) | set(after.blobs))
    changes = [(path, _read_snapshot_blob(before, path), _read_snapshot_blob(after, path)) for path in paths]

    return [change for change in changes if change[1] != change[2]]


def append_events(session_id: str, events: Iterable[WriteEvent]) -> None:
    lines = [json.dumps(asdict(event), ensure_ascii=False) + "\n" for event in events]
    record = get_record_directory() / f"{session_id}{_RECORD_SUFFIX}"

    if lines:
        record.parent.mkdir(parents=True, exist_ok=True)
        with record.open("a", encoding=_TEXT_ENCODING) as file:
            file.write("".join(lines))


def save_pending(
    session_id: str,
    tool_use_id: str,
    snapshots: Sequence[RepositorySnapshot],
    start: float | None = None,
) -> None:
    """Keep a call's before snapshots until it ends; `start` is taken before the first snapshot."""
    directory = get_record_directory() / session_id
    pending = directory / f"{tool_use_id}{_PENDING_SUFFIX}"
    started = time.time() if start is None else start
    payload = {"start": started, "snapshots": [asdict(snapshot) for snapshot in snapshots]}

    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _STARTED_FILE).write_text(str(payload["start"]), encoding=_TEXT_ENCODING)
    pending.write_text(json.dumps(payload, ensure_ascii=False), encoding=_TEXT_ENCODING)


def read_pending(session_id: str, tool_use_id: str) -> tuple[float, list[RepositorySnapshot]] | None:
    """A call's start and before snapshots; the file stays until remove_pending(), after its events are kept."""
    pending = get_record_directory() / session_id / f"{tool_use_id}{_PENDING_SUFFIX}"

    if pending.exists():
        payload = json.loads(pending.read_text(encoding=_TEXT_ENCODING))
        return payload["start"], [RepositorySnapshot(**snapshot) for snapshot in payload["snapshots"]]
    else:
        return None


def remove_pending(session_id: str, tool_use_id: str) -> None:
    """Drop a call's pending file once its events are in the record, so it is never in neither."""
    pending = get_record_directory() / session_id / f"{tool_use_id}{_PENDING_SUFFIX}"

    pending.unlink(missing_ok=True)


def remove_expired_records() -> None:
    """Drop records older than git's default prune age, after which their objects may be gone."""
    directory = get_record_directory()
    limit = time.time() - _RECORD_LIFETIME_SECONDS
    entries = list(directory.rglob("*")) if directory.exists() else []

    for entry in entries:
        if entry.is_file() and entry.stat().st_mtime < limit:
            entry.unlink()


def load_file_history(session_id: str, root: str, path: str) -> FileHistory | None:
    """The chain of this session's writes to one file, or None when it recorded none.

    Raises SessionWritesError when a record, or contents it names, cannot be read back.
    """
    events = _read_file_events(root, path)
    own = [event for session, event in events if session == session_id]
    working_tree = Path(root, path)
    working_text = decode_content(working_tree.read_bytes()) if working_tree.is_file() else ""

    if own:
        foreign = _list_foreign_writes(session_id, root, path, events)
        own_writes = tuple(_make_recorded_write(event, foreign) for event in sorted(own, key=_get_event_end))
        return FileHistory(_read_blob(root, own[0].before), own_writes, working_text)
    else:
        return None


def is_whole_file_own(session_id: str, root: str, path: str, base_blob: str | None) -> bool:
    """Whether every change from base to the working tree came from this session's uncontested writes.

    Records that cannot be read back count as not all its own.
    """
    try:
        events = _read_file_events(root, path)
        own = sorted((event for session, event in events if session == session_id), key=_get_event_end)
        foreign = _list_foreign_writes(session_id, root, path, events)
        chain = [base_blob, *(blob for event in own for blob in (event.before, event.after))]
        working_tree = _store_files(root, [path])[path]
        is_unbroken = all(chain[index] == chain[index + 1] for index in range(0, len(chain) - 1, 2))
        is_uncontested = not any(_is_contested(event, foreign) for event in own)

        return bool(own) and is_unbroken and chain[-1] == working_tree and is_uncontested
    except SessionWritesError:
        return False


def is_line_tracked(root: str, path: str) -> bool:
    """Whether the file is a regular text file small enough to separate line by line."""
    file = Path(root, path)

    if file.is_symlink():
        return False
    elif file.exists():
        return file.stat().st_size <= _MAX_TRACKED_BYTES and _is_text(file.read_bytes())
    else:
        return True


def decode_content(content: bytes) -> str:
    """Decode file content so that encode_content() gives back the same bytes."""
    return content.decode(_TEXT_ENCODING, _LOSSLESS_ERRORS)


def encode_content(text: str) -> bytes:
    return text.encode(_TEXT_ENCODING, _LOSSLESS_ERRORS)


def _print_report(paths: Sequence[str]) -> int:
    session_id = read_session_id()

    if session_id:
        if not is_record_complete(session_id):
            print(_INCOMPLETE_RECORD_WARNING)
        for path in paths:
            print(_describe_file(session_id, os.path.realpath(path)))
        return 0
    else:
        message = f"No {_SESSION_VARIABLE}: this shell is not a Claude session, so there is no write record."

        print(message, file=sys.stderr)
        return 1


def _describe_file(session_id: str, absolute_path: str) -> str:
    root = find_repository_root(absolute_path) or ""
    path = os.path.relpath(absolute_path, root) if root else absolute_path
    history = load_file_history(session_id, root, path) if root else None
    base = _read_blob(root, _read_committed_blob(root, _HEAD_REVISION, path)) if root else ""

    if history is None:
        return f"{path}: no writes recorded for this session; committing it needs --include-unrecorded {path}"
    else:
        own = build_own_content(base, history)
        foreign = list_foreign_lines(base, history.working_tree, own.text)
        shown = "".join(f"\n  {line.kind.value} {line.line_number}: {line.text}" for line in foreign)
        tangled = "".join(f"\n  ! {line.rstrip()}" for line in own.entangled)
        unsure = "".join(f"\n  ? {line.kind.value} {line.text.rstrip()}" for line in own.contested)
        summary = f"{path}: {len(foreign)} changed lines are not this session's and would be left out{shown}"
        entangled = f"\n{len(own.entangled)} own lines cannot be separated from them:{tangled}" if own.entangled else ""
        contested = (
            f"\n{len(own.contested)} of them changed while another session also wrote this file, so they may be this session's:{unsure}"
            if own.contested
            else ""
        )

        return summary + entangled + contested


def _get_config_directory() -> Path:
    return Path(os.path.expanduser(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY))


def _read_record_start(session_id: str) -> float | None:
    """When the record began: the marker the hook writes, or the folder's creation time for older records."""
    directory = get_record_directory() / session_id
    marker = directory / _STARTED_FILE

    if marker.is_file():
        return _parse_float(marker.read_text(encoding=_TEXT_ENCODING))
    elif directory.is_dir():
        return getattr(directory.stat(), "st_birthtime", None)
    else:
        return None


def _find_transcript(session_id: str) -> Path | None:
    matches = sorted((_get_config_directory() / _PROJECTS_DIRECTORY_NAME).glob(f"*/{session_id}{_TRANSCRIPT_SUFFIX}"))

    return matches[0] if matches else None


def _read_first_recorded_result_time(transcript: Path) -> float | None:
    """When the session's first call of a tool the hook records ended; an unreadable time counts as earliest."""
    recorded_ids: set[str] = set()

    with transcript.open(encoding=_TEXT_ENCODING, errors="replace") as file:
        for line in file:
            result_time = _read_recorded_result_time(line, recorded_ids)

            if result_time is not None:
                return result_time
    return None


def _read_recorded_result_time(line: str, recorded_ids: set[str]) -> float | None:
    """Collect recorded tool calls from one transcript line, and return its time if it holds one's result."""
    blocks = _read_content_blocks(line) if _TOOL_USE_MARKER in line or _TOOL_RESULT_MARKER in line else []
    is_result = False

    for block in blocks:
        if block.get("type") == _TOOL_USE_TYPE and block.get("name") in _RECORDED_TOOLS:
            recorded_ids.add(str(block.get("id")))
        elif block.get("type") == _TOOL_RESULT_TYPE and block.get("tool_use_id") in recorded_ids:
            is_result = True
    return _parse_entry_time(line) if is_result else None


def _read_content_blocks(line: str) -> list[dict[str, object]]:
    try:
        content = json.loads(line).get("message", {}).get("content")
    except (ValueError, AttributeError):
        return []
    else:
        return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _parse_entry_time(line: str) -> float:
    try:
        stamp = str(json.loads(line)[_TIMESTAMP_KEY]).replace(_UTC_SUFFIX, _UTC_OFFSET)
        entry_time = datetime.fromisoformat(stamp).timestamp()
    except (ValueError, KeyError, TypeError):
        return _UNKNOWN_TIME
    else:
        return entry_time


def _parse_float(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    else:
        return value


def _read_events(session_id: str) -> list[WriteEvent]:
    """The session's events, skipping fields a newer hook added.

    Raises SessionWritesError on a line that does not parse, such as one
    another hook is still appending.
    """
    record = get_record_directory() / f"{session_id}{_RECORD_SUFFIX}"
    lines = _read_record_text(record).split(_RECORD_LINE_END)

    return [_parse_event(record, number, line) for number, line in enumerate(lines, start=1) if line.strip()]


def _read_record_text(record: Path) -> str:
    """A record's text, empty when it does not exist (none yet, or expired); raises SessionWritesError otherwise."""
    try:
        content = record.read_bytes()
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise SessionWritesError(f"{record.name} cannot be read ({error})") from error
    else:
        return content.decode(_TEXT_ENCODING, errors="replace")


def _parse_event(record: Path, number: int, line: str) -> WriteEvent:
    try:
        entry = json.loads(line)
        event = WriteEvent(**{key: value for key, value in entry.items() if key in _EVENT_FIELDS})
    except (ValueError, TypeError, AttributeError) as error:
        raise SessionWritesError(f"{record.name} line {number} cannot be read ({error})") from error
    else:
        return event


def _read_recorded_roots(session_id: str) -> set[str]:
    """Repositories the session's record names; a line or record that cannot be read loses only its own roots."""
    record = get_record_directory() / f"{session_id}{_RECORD_SUFFIX}"

    try:
        lines = _read_record_text(record).split(_RECORD_LINE_END)
    except SessionWritesError:
        return set()
    else:
        return {root for root in map(_parse_root, lines) if root}


def _parse_root(line: str) -> str | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    else:
        root = entry.get(_ROOT_FIELD) if isinstance(entry, dict) else None

        return root if isinstance(root, str) else None


def _read_file_events(root: str, path: str) -> list[tuple[str, WriteEvent]]:
    """Every session's events for one file, each with the session that recorded it."""
    sessions = [record.stem for record in get_record_directory().glob(f"*{_RECORD_SUFFIX}")]

    return [
        (session, event)
        for session in sessions
        for event in _read_events(session)
        if event.root == root and event.path == path
    ]


def _list_foreign_writes(
    session_id: str,
    root: str,
    path: str,
    events: Sequence[tuple[str, WriteEvent]],
) -> list[WriteEvent]:
    """Other sessions' recorded writes to the file and their calls still running over it."""
    recorded = [event for session, event in events if session != session_id]

    return [*recorded, *_read_running_calls(session_id, root, path)]


def _read_running_calls(session_id: str, root: str, path: str) -> list[WriteEvent]:
    """Other sessions' calls still running whose snapshots cover the file, as writes that have not ended.

    A pending file that does not parse counts as covering it from when it was
    written. Calls pending longer than the pending lifetime are taken as left
    by a session that ended.
    """
    limit = time.time() - _PENDING_LIFETIME_SECONDS
    pendings = [
        pending
        for pending in get_record_directory().glob(f"*/*{_PENDING_SUFFIX}")
        if pending.parent.name != session_id and (_read_modified_time(pending) or 0.0) >= limit
    ]
    starts = [_read_pending_start(pending, root, path) for pending in pendings]

    return [WriteEvent(root, path, None, None, start, math.inf, _PENDING_TOOL) for start in starts if start is not None]


def _read_pending_start(pending: Path, root: str, path: str) -> float | None:
    """When a running call began, if its snapshots cover the file, or None when they do not.

    A pending file removed meanwhile belongs to a call whose events are
    already in its record, so it is skipped.
    """
    try:
        payload = json.loads(pending.read_text(encoding=_TEXT_ENCODING))
        snapshots = [RepositorySnapshot(**snapshot) for snapshot in payload["snapshots"]]
        start = float(payload["start"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError):
        return _read_modified_time(pending)
    else:
        is_covering = any(
            snapshot.root == root and (snapshot.covers_repository or path in snapshot.blobs) for snapshot in snapshots
        )

        return start if is_covering else None


def _read_modified_time(path: Path) -> float | None:
    """A file's modification time, or None when it was removed meanwhile, as hooks remove pending files."""
    try:
        modified = path.stat().st_mtime
    except FileNotFoundError:
        return None
    else:
        return modified


def _make_recorded_write(event: WriteEvent, foreign: Sequence[WriteEvent]) -> RecordedWrite:
    before = _read_blob(event.root, event.before)
    after = _read_blob(event.root, event.after)

    return RecordedWrite(before, after, _is_contested(event, foreign))


def _is_contested(event: WriteEvent, foreign: Sequence[WriteEvent]) -> bool:
    """Whether another session may have written part of this write's change.

    An Edit or Write that its own reported change explains exactly is the
    session's whatever ran meanwhile, and one with anything else in its
    snapshots is not. Any other write is contested when another session's
    write or running call overlaps its time.
    """
    if event.exact is not None:
        return not event.exact
    else:
        return any(_is_overlapping(event, other) for other in foreign)


def _is_overlapping(event: WriteEvent, other: WriteEvent) -> bool:
    """Whether two writes' times overlap, widened by a margin when either is a record whose times do not enclose
    its snapshots (an earlier record format, or a running call's pending file)."""
    is_enclosed = min(event.record_format, other.record_format) >= RECORD_FORMAT
    margin = 0.0 if is_enclosed else _OVERLAP_MARGIN_SECONDS

    return other.start < event.end + margin and event.start < other.end + margin


def _get_event_end(event: WriteEvent) -> float:
    return event.end


def _read_snapshot_blob(snapshot: RepositorySnapshot, path: str) -> str | None:
    if path in snapshot.blobs:
        return snapshot.blobs[path]
    else:
        return _read_committed_blob(snapshot.root, snapshot.head, path)


def _find_existing_directory(path: str) -> str:
    directory = path if os.path.isdir(path) else os.path.dirname(path)

    while directory and not os.path.isdir(directory):
        directory = os.path.dirname(directory)
    return directory or os.sep


def _parse_status_paths(output: str) -> list[str]:
    entries = output.split("\0")
    paths: list[str] = []
    index = 0

    while index < len(entries):
        entry = entries[index]

        index += 2 if entry[:1] and entry[0] in _RENAME_OR_COPY_STATUSES else 1
        if len(entry) > 3:
            paths.append(entry[3:])
    return paths


def _store_files(root: str, paths: Sequence[str]) -> dict[str, str | None]:
    present = [path for path in paths if Path(root, path).is_file()]
    listing = "".join(f"{path}\n" for path in present)
    names = decode_content(
        _run_git(root, "hash-object", "-w", "--stdin-paths", input_bytes=encode_content(listing))
    ).split()

    return {**dict.fromkeys(paths), **dict(zip(present, names))}


def _read_head(root: str) -> str | None:
    output = _try_git(root, "rev-parse", "--verify", "--quiet", _HEAD_REVISION)

    return decode_content(output).strip() or None if output is not None else None


def _read_committed_blob(root: str, head: str | None, path: str) -> str | None:
    output = _try_git(root, "rev-parse", "--verify", "--quiet", f"{head}:{path}") if head else None

    return decode_content(output).strip() or None if output is not None else None


@functools.cache
def _read_blob(root: str, blob: str | None) -> str:
    if blob:
        return decode_content(_run_git(root, "cat-file", "blob", blob))
    else:
        return ""


def _is_text(content: bytes) -> bool:
    try:
        content.decode(_TEXT_ENCODING)
    except UnicodeDecodeError:
        return False
    else:
        return b"\0" not in content


def _run_git(directory: str, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    """Run git and return its output, raising SessionWritesError when it fails or hangs."""
    output = _try_git(directory, *arguments, input_bytes=input_bytes)

    if output is not None:
        return output
    else:
        raise SessionWritesError(f"git {' '.join(arguments[:2])} failed in {directory}")


def _try_git(directory: str, *arguments: str, input_bytes: bytes | None = None) -> bytes | None:
    """Run git and return its output, or None when it fails, for queries whose failure is an answer."""
    command = ["git", "-C", directory, *arguments]

    try:
        result = subprocess.run(
            command,
            input=input_bytes,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    else:
        return result.stdout if result.returncode == 0 else None


if __name__ == "__main__":
    if sys.argv[1:2] == [_REPORT_COMMAND] and sys.argv[2:]:
        sys.exit(_print_report(sys.argv[2:]))
    else:
        print(__doc__.split("Usage", 1)[1].strip(), file=sys.stderr)
        sys.exit(_EXIT_USAGE)
