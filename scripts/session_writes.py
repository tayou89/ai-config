"""Record the file contents each Claude session writes, so a commit can keep only its own lines.

The hook (session-writes-hook.py) snapshots files before and after every tool
call that can write them and appends one event per changed file to the
session's record. Contents go into the repository's object store with
`git hash-object -w`, as `git stash` does, so the record holds only object
names. Snapshots per command follow Jujutsu, which snapshots the working copy
at every command. The commit wrapper reads a file's history back with
load_file_history().

A snapshot that cannot be taken raises SessionWritesError instead of
returning an empty one: an empty snapshot would read as "nothing was dirty"
and hand another session's uncommitted lines to this session. File contents
are kept as bytes end to end (decoded with surrogateescape), so line endings
and undecodable bytes survive the round trip.

Usage (list what --own-lines would leave out):
    session_writes.py report <file1> [<file2> ...]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from own_lines import FileHistory, OwnWrite, build_own_content, list_foreign_lines

_SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_RECORD_DIRECTORY_NAME = "session-writes"
_RECORD_SUFFIX = ".jsonl"
_PENDING_SUFFIX = ".pending.json"
_RECORD_LIFETIME_SECONDS = 14 * 24 * 60 * 60
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
    root: str
    path: str
    before: str | None
    after: str | None
    start: float
    end: float
    tool: str


@dataclass(frozen=True)
class RepositorySnapshot:
    root: str
    head: str | None
    blobs: dict[str, str | None]
    covers_repository: bool


def read_session_id() -> str | None:
    return os.environ.get(_SESSION_VARIABLE) or None


def has_record(session_id: str) -> bool:
    """Whether the record hook has run in this session; sessions started before it was installed have none."""
    return (get_record_directory() / session_id).is_dir()


def get_record_directory() -> Path:
    config_directory = os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY

    return Path(os.path.expanduser(config_directory)) / _RECORD_DIRECTORY_NAME


def find_repository_root(path: str) -> str | None:
    """The repository holding a path, judged from its nearest existing folder so new folders count too."""
    output = _try_git(_find_existing_directory(path), "rev-parse", "--show-toplevel")

    return decode_content(output).strip() or None if output is not None else None


def find_command_roots(session_id: str, command: str, cwd: str | None) -> list[str]:
    """Repositories a command may write: its folder, folders it names, and ones this session wrote before."""
    named = [os.path.expanduser(match) for match in _ABSOLUTE_PATH_PATTERN.findall(command)]
    candidates = [*([cwd] if cwd else []), *named]
    roots = {root for root in map(find_repository_root, candidates) if root}

    return sorted(roots | {event.root for event in _read_events(session_id)})


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


def save_pending(session_id: str, tool_use_id: str, snapshots: Sequence[RepositorySnapshot]) -> None:
    pending = get_record_directory() / session_id / f"{tool_use_id}{_PENDING_SUFFIX}"
    payload = {"start": time.time(), "snapshots": [asdict(snapshot) for snapshot in snapshots]}

    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(payload, ensure_ascii=False), encoding=_TEXT_ENCODING)


def take_pending(session_id: str, tool_use_id: str) -> tuple[float, list[RepositorySnapshot]] | None:
    pending = get_record_directory() / session_id / f"{tool_use_id}{_PENDING_SUFFIX}"

    if pending.exists():
        payload = json.loads(pending.read_text(encoding=_TEXT_ENCODING))
        pending.unlink()
        return payload["start"], [RepositorySnapshot(**snapshot) for snapshot in payload["snapshots"]]
    else:
        return None


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

    Raises SessionWritesError when the record names contents git no longer has.
    """
    own = [event for event in _read_events(session_id) if event.root == root and event.path == path]
    working_tree = Path(root, path)
    working_text = decode_content(working_tree.read_bytes()) if working_tree.is_file() else ""

    if own:
        others = _read_other_events(session_id, root, path)
        own_writes = tuple(_make_own_write(event, others) for event in sorted(own, key=_get_event_end))
        return FileHistory(_read_blob(root, own[0].before), own_writes, working_text)
    else:
        return None


def is_whole_file_own(session_id: str, root: str, path: str, base_blob: str | None) -> bool:
    """Whether every change from base to the working tree came from this session's recorded writes."""
    events = (event for event in _read_events(session_id) if event.root == root and event.path == path)
    own = sorted(events, key=_get_event_end)
    chain = [base_blob, *(blob for event in own for blob in (event.before, event.after))]
    working_tree = _store_files(root, [path])[path]
    is_unbroken = all(chain[index] == chain[index + 1] for index in range(0, len(chain) - 1, 2))

    return bool(own) and is_unbroken and chain[-1] == working_tree


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
        summary = f"{path}: {len(foreign)} changed lines are not this session's and would be left out{shown}"
        entangled = f"\n{len(own.entangled)} own lines cannot be separated from them:{tangled}" if own.entangled else ""

        return summary + entangled


def _read_events(session_id: str) -> list[WriteEvent]:
    record = get_record_directory() / f"{session_id}{_RECORD_SUFFIX}"
    lines = record.read_text(encoding=_TEXT_ENCODING).splitlines() if record.exists() else []

    return [WriteEvent(**json.loads(line)) for line in lines if line.strip()]


def _make_own_write(event: WriteEvent, others: list[WriteEvent]) -> OwnWrite:
    overlapping = [other for other in others if other.start < event.end and event.start < other.end]
    inserted = sum((_count_inserted(event.root, other) for other in overlapping), Counter())

    return OwnWrite(_read_blob(event.root, event.before), _read_blob(event.root, event.after), inserted)


def _count_inserted(root: str, event: WriteEvent) -> Counter[str]:
    before = Counter(_read_blob(root, event.before).splitlines(keepends=True))
    after = Counter(_read_blob(root, event.after).splitlines(keepends=True))

    return after - before


def _read_other_events(session_id: str, root: str, path: str) -> list[WriteEvent]:
    records = [record for record in get_record_directory().glob(f"*{_RECORD_SUFFIX}") if record.stem != session_id]

    return [
        event for record in records for event in _read_events(record.stem) if event.root == root and event.path == path
    ]


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
