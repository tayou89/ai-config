#!/usr/bin/env python3
"""Which session owns a task worktree: the one session that writes there.

Sessions that wrote in one worktree mixed each other's changes, and the
commit wrapper then guessed whose lines were whose from process lists and
write records, which kept asking the user (user decision, 2026-11-18). So
each linked worktree names its owner in its own Git folder
(`claude-owner.json`, which Git never tracks and `git worktree remove`
deletes with the folder), the folder guard (worktree-guard-hook.py) lets
only the owner write there, and the commit wrapper reads the record instead
of guessing. Codex runs, formatters, and a person's terminal in the folder
work for its owner.

A session is known by its desktop-app id first, which stays the same when
Claude Code restarts under it, and by its Claude Code id otherwise. An app
session has left when the app archived it; a terminal session has left when
its Claude Code process (its id and start time) no longer runs.

The first claim is atomic: the record is written to a temporary file and
hard-linked into place, which fails when another claim got there first. A
handover replaces the record under a lock that the operating system
releases when its holder exits, after checking that the owner is still the
one the caller read, as a compare-and-swap does. Gaps, the calls the guard
let through unjudged, are added and settled under the same lock, so a gap
added while a commit settles the ones it showed is kept for the next one.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from integration_lock import LockTimeoutError, hold_file_lock
from session_lineage import (
    APP_SESSION_PREFIX,
    CLI_SESSION_VARIABLE,
    HOST_SESSION_VARIABLE,
    find_session_record,
    is_archived,
    read_app_session_id,
    read_cli_session_id,
    read_session_title,
    read_worktree_path,
)
from workflow_policy import GIT_DIRECTORY_NAME, find_repository_root

OWNER_FILE_NAME = "claude-owner.json"
LOCK_FILE_NAME = "claude-owner.lock"
GAPS_FILE_NAME = "claude-owner-gaps.jsonl"
SHOW_COMMAND = "show"
CLAIM_COMMAND = "claim"
TAKE_COMMAND = "take"
HAND_OVER_COMMAND = "hand-over"
FROM_OPTION = "--from"
TO_OPTION = "--to"
UNKNOWN_NAME = "?"
_SETTLED_GAPS_FILE_NAME = "claude-owner-gaps-settled.jsonl"
_OFFERED_GAPS_FILE_NAME = "claude-owner-gaps-offered.jsonl"
_PROTECTED_PREFIX = "claude-owner"
_RECORD_FORMAT = 1
_FORMAT_KEY = "format"
_OWNER_KEY = "owner"
_TITLE_KEY = "title"
_CLAIMED_AT_KEY = "claimedAt"
_METHOD_KEY = "method"
_PREVIOUS_KEY = "previous"
_APP_SESSION_KEY = "appSession"
_CLI_SESSION_KEY = "cliSession"
_PROCESS_KEY = "process"
_PID_KEY = "pid"
_STARTED_KEY = "started"
_GAP_ID_KEY = "id"
_GAP_TIME_KEY = "time"
_GAP_SESSION_KEY = "session"
_GAP_WHAT_KEY = "what"
_GAP_CALL_KEY = "call"
_GITDIR_PREFIX = "gitdir:"
COMMONDIR_FILE = "commondir"
_LOCK_WAIT_SECONDS = 2.0
_LOCK_POLL_SECONDS = 0.02
_SESSION_PROCESS_NAME = "claude"
_MAX_ANCESTORS = 12
_PS_FIELD_COUNT = 7
_PS_TIMEOUT_SECONDS = 3
_PS_ENVIRONMENT = {"TZ": "UTC", "LC_ALL": "C"}
_GIT_TIMEOUT_SECONDS = 10
_ENCODING = "utf-8"
_EXIT_FAILED = 1
_Result = TypeVar("_Result")


class OwnerFileError(Exception):
    """The owner record exists but cannot be read, or changed while it was being claimed."""


class OwnerBusyError(Exception):
    """Another handover of the same folder held its lock for longer than this one waits."""


class _OwnerCommandError(Exception):
    """A command-line request that the folder's owner record does not allow."""


class _ProcessLookupError(Exception):
    """`ps` could not say whether a process runs: it failed, timed out, or printed what this does not read."""


class ClaimMethod(str, Enum):
    CREATED = "created"
    APP_WORKTREE = "app-worktree"
    FIRST_WRITE = "first-write"
    HANDED_OVER = "handed-over"
    TAKEN_OVER = "taken-over"


class OwnerState(Enum):
    ACTIVE = "active"
    GONE = "gone"
    UNKNOWN = "unknown"


class AccessKind(Enum):
    """What a session may do in a task worktree before it writes there."""

    OWN = "own"
    CLAIM = "claim"
    CLAIM_ASKING = "claim-asking"
    TAKE = "take"
    TAKE_ASKING = "take-asking"
    OTHERS = "others"

    @property
    def is_asking(self) -> bool:
        return self in (AccessKind.CLAIM_ASKING, AccessKind.TAKE_ASKING)

    @property
    def is_taking(self) -> bool:
        return self in (AccessKind.CLAIM, AccessKind.CLAIM_ASKING, AccessKind.TAKE, AccessKind.TAKE_ASKING)


@dataclass(frozen=True)
class ProcessMark:
    """A process and its start time, which tells it apart from a later process given the same id."""

    pid: int
    started: str


@dataclass(frozen=True)
class SessionIdentity:
    app_session: str | None
    cli_session: str | None
    process: ProcessMark | None

    @property
    def name(self) -> str:
        return self.app_session or self.cli_session or UNKNOWN_NAME

    def is_same(self, other: SessionIdentity) -> bool:
        """The app id decides when both have one, since Claude Code's id changes when the app restarts it."""
        if self.app_session and other.app_session:
            return self.app_session == other.app_session
        else:
            return bool(self.cli_session) and self.cli_session == other.cli_session

    def _is_named(self, name: str | None) -> bool:
        """Whether a recorded session name, which is the app id or else the Claude Code id, names this session."""
        return name is not None and name in (self.app_session, self.cli_session)


@dataclass(frozen=True)
class FolderOwner:
    owner: SessionIdentity
    title: str
    claimed_at: float
    method: ClaimMethod
    previous: SessionIdentity | None

    def describe(self) -> str:
        return f"{self.title} ({self.owner.name})" if self.title else self.owner.name


@dataclass(frozen=True)
class LinkedWorktree:
    """A worktree `git worktree add` made, and its own Git folder inside the repository's common one."""

    root: Path
    git_directory: Path

    @property
    def owner_file(self) -> Path:
        return self.git_directory / OWNER_FILE_NAME

    @property
    def lock_file(self) -> Path:
        return self.git_directory / LOCK_FILE_NAME

    @property
    def gaps_file(self) -> Path:
        return self.git_directory / GAPS_FILE_NAME

    @property
    def settled_gaps_file(self) -> Path:
        return self.git_directory / _SETTLED_GAPS_FILE_NAME

    @property
    def offered_gaps_file(self) -> Path:
        return self.git_directory / _OFFERED_GAPS_FILE_NAME


@dataclass(frozen=True)
class FolderAccess:
    worktree: LinkedWorktree
    kind: AccessKind
    owner: FolderOwner | None
    method: ClaimMethod | None


@dataclass(frozen=True)
class GuardGap:
    """A call the folder guard could not judge and let through, so a write it should have refused may have run.

    `line` is the record's line as read, which settling matches, so a gap added after it was read stays;
    `call_id` is the tool call's id, None for a record written before ids were kept.
    """

    time: float
    session: str | None
    what: str
    line: str
    call_id: str | None = None


@dataclass(frozen=True)
class _ProcessRow:
    parent: int
    started: str
    command: str


def find_linked_worktree(path: Path) -> LinkedWorktree | None:
    """The linked worktree holding a path, or None in an original checkout, a submodule, or outside Git.

    It reads only the worktree's `.git` file, so the guard can ask it of
    every path a command names without running Git.
    """
    root = find_repository_root(path)
    git_file = root / GIT_DIRECTORY_NAME if root is not None else None
    git_directory = _read_git_file(git_file) if git_file is not None and git_file.is_file() else None

    if root is not None and git_directory is not None and (git_directory / COMMONDIR_FILE).is_file():
        return LinkedWorktree(root.resolve(), git_directory)
    else:
        return None


def read_current_identity(cli_session: str | None = None) -> SessionIdentity | None:
    """This session's identity from the app's variable and Claude Code's id, or None outside a Claude session.

    A hook passes the id its input names, since Claude Code may not export it to hooks.
    """
    app_session = os.environ.get(HOST_SESSION_VARIABLE) or None
    cli = cli_session or os.environ.get(CLI_SESSION_VARIABLE) or None

    if app_session or cli:
        return SessionIdentity(app_session, cli, None if app_session else _find_session_process())
    else:
        return None


def read_identity_title(identity: SessionIdentity) -> str:
    record = find_session_record(identity.app_session) if identity.app_session else None

    return read_session_title(record) if record is not None else ""


def read_owner(worktree: LinkedWorktree) -> FolderOwner | None:
    """The folder's owner, or None when nobody claimed it. Raises OwnerFileError when the record is unreadable."""
    try:
        text = worktree.owner_file.read_text(encoding=_ENCODING)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise OwnerFileError(f"{worktree.owner_file} cannot be read: {error}") from error
    else:
        return _parse_owner(text, worktree.owner_file)


def read_owner_state(owner: FolderOwner) -> OwnerState:
    """Whether the owner session still runs; one the app has no record of, or with no process known, is unknown."""
    identity = owner.owner
    record = find_session_record(identity.app_session) if identity.app_session else None

    if identity.app_session and record is None:
        return OwnerState.UNKNOWN
    elif identity.app_session:
        return OwnerState.GONE if is_archived(record) else OwnerState.ACTIVE
    elif identity.process is not None:
        return _read_process_state(identity.process)
    else:
        return OwnerState.UNKNOWN


def judge_access(worktree: LinkedWorktree, identity: SessionIdentity) -> FolderAccess:
    """What this session may do in the folder: write as its owner, claim or take it (asking or not), or only read.

    Raises OwnerFileError when the owner record is unreadable.
    """
    owner = read_owner(worktree)

    if owner is not None and owner.owner.is_same(identity):
        return FolderAccess(worktree, AccessKind.OWN, owner, None)
    elif owner is not None and read_owner_state(owner) != OwnerState.GONE:
        return FolderAccess(worktree, AccessKind.OTHERS, owner, None)
    elif owner is not None:
        kind = AccessKind.TAKE_ASKING if has_uncommitted_changes(worktree) else AccessKind.TAKE

        return FolderAccess(worktree, kind, owner, ClaimMethod.TAKEN_OVER)
    elif is_app_worktree(worktree, identity):
        return FolderAccess(worktree, AccessKind.CLAIM, None, ClaimMethod.APP_WORKTREE)
    else:
        kind = AccessKind.CLAIM_ASKING if has_uncommitted_changes(worktree) else AccessKind.CLAIM

        return FolderAccess(worktree, kind, None, ClaimMethod.FIRST_WRITE)


def take_folder(access: FolderAccess, identity: SessionIdentity) -> FolderOwner:
    """Claim or take the folder as `access` judged it; the result names whoever owns it afterwards."""
    title = read_identity_title(identity)
    method = access.method or ClaimMethod.FIRST_WRITE

    if access.owner is None:
        return claim_folder(access.worktree, identity, title, method)
    else:
        return transfer_folder(access.worktree, access.owner, identity, title, method)


def claim_folder(worktree: LinkedWorktree, identity: SessionIdentity, title: str, method: ClaimMethod) -> FolderOwner:
    """Record the first owner of a folder; when another claim came first, return that one unchanged."""
    owner = FolderOwner(identity, title, time.time(), method, None)

    with _write_temporary_record(worktree, owner) as temporary:
        try:
            os.link(temporary, worktree.owner_file)
        except FileExistsError:
            existing = read_owner(worktree)

            if existing is not None:
                return existing
            else:
                raise OwnerFileError(f"{worktree.owner_file} changed while it was being claimed") from None
        else:
            return owner


def transfer_folder(
    worktree: LinkedWorktree,
    expected: FolderOwner,
    identity: SessionIdentity,
    title: str,
    method: ClaimMethod,
) -> FolderOwner:
    """Replace the owner only while it is still `expected`, and return whoever owns the folder afterwards.

    Raises OwnerBusyError when another handover holds the lock too long.
    """
    return _run_locked(worktree, lambda: _replace_owner(worktree, expected, identity, title, method))


def is_app_worktree(worktree: LinkedWorktree, identity: SessionIdentity) -> bool:
    """Whether the desktop app made this worktree for this session, as its record of the session names it."""
    record = find_session_record(identity.app_session) if identity.app_session else None
    named = read_worktree_path(record) if record is not None else None

    return named is not None and Path(named).resolve() == worktree.root


def has_uncommitted_changes(worktree: LinkedWorktree) -> bool:
    """Tracked changes or untracked files not ignored; a folder Git cannot read counts as changed."""
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree.root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding=_ENCODING,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    else:
        return result.returncode != 0 or bool(result.stdout.strip())


def add_gap(worktree: LinkedWorktree, identity: SessionIdentity | None, what: str, call_id: str | None) -> None:
    """Note one call the guard could not judge. Its runs before and after the call note the same call once, by
    `call_id`. Raises OwnerBusyError when a handover or a commit holds the folder's lock too long.
    """
    gap = {
        _GAP_ID_KEY: uuid.uuid4().hex,
        _GAP_CALL_KEY: call_id,
        _GAP_TIME_KEY: time.time(),
        _GAP_SESSION_KEY: identity.name if identity else None,
        _GAP_WHAT_KEY: what,
    }

    _run_locked(worktree, lambda: _append_new_gap(worktree, gap))


def read_gaps(worktree: LinkedWorktree) -> list[GuardGap]:
    """The gaps not settled yet. A line that cannot be parsed still counts, and so does a file that cannot be read,
    so a damaged record never hides a gap."""
    try:
        lines = worktree.gaps_file.read_text(encoding=_ENCODING).splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as error:
        return [GuardGap(0.0, None, f"the gap record cannot be read: {error}", "")]
    else:
        return [_parse_gap(line) for line in lines if line.strip()]


def list_asked_gaps(worktree: LinkedWorktree, gaps: Sequence[GuardGap]) -> list[GuardGap]:
    """The gaps the owner's commit asks about. The guard lets the owner write in its own folder anyway, so a gap its
    own call left after it took the folder cannot hide another session's write; a gap of no known session, one from
    before the owner took the folder, or any gap when the owner record cannot be read still asks."""
    owner = _try_read_owner(worktree)

    return [gap for gap in gaps if not _is_owner_gap(gap, owner)]


def settle_gaps(worktree: LinkedWorktree, shown: Sequence[GuardGap]) -> None:
    """Move the gaps a commit showed the user to the settled record; gaps added since stay for the next commit.

    Raises OwnerBusyError when the lock is held too long, and OSError when the records cannot be rewritten.
    """
    _run_locked(worktree, lambda: _move_settled_gaps(worktree, {gap.line for gap in shown}))


def is_protected_file(path: Path) -> bool:
    """Whether a path is an owner record, its lock, or a gap record, which only this module writes."""
    return path.name.startswith(_PROTECTED_PREFIX) and GIT_DIRECTORY_NAME in path.parts


def read_process_mark(pid: int) -> ProcessMark | None:
    """The process's mark, or None when it does not run or `ps` cannot tell."""
    try:
        row = _read_process_row(pid)
    except _ProcessLookupError:
        return None
    else:
        return ProcessMark(pid, row.started) if row is not None else None


def refresh_owner_process(worktree: LinkedWorktree, owner: FolderOwner, identity: SessionIdentity) -> None:
    """Record the owner's own Claude Code process once it acts, for a terminal session handed a folder by id alone,
    whose end could not be told otherwise; only the owner's own call can name its process."""
    if owner.owner.is_same(identity) and owner.owner.process is None and identity.process is not None:
        _run_locked(worktree, lambda: _store_if_unchanged(worktree, owner, dataclasses.replace(owner, owner=identity)))


def offer_gaps(worktree: LinkedWorktree, gaps: Sequence[GuardGap]) -> None:
    """Keep the gaps a dialog showed, so the commit takes in only gaps the user saw."""
    offered = _read_offered_lines(worktree)
    lines = [gap.line for gap in gaps if gap.line not in offered]

    _run_locked(worktree, lambda: _append_lines(worktree.offered_gaps_file, lines))


def list_unoffered_gaps(worktree: LinkedWorktree, gaps: Sequence[GuardGap]) -> list[GuardGap]:
    offered = _read_offered_lines(worktree)

    return [gap for gap in gaps if gap.line not in offered]


def parse_owner_command(arguments: Sequence[str]) -> argparse.Namespace | None:
    """This script's command line as it reads it, or None for one it would refuse, so the folder guard judges the
    same command and folder the script acts on."""
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return _parse_command_line(arguments, exit_on_error=False)
    except (argparse.ArgumentError, SystemExit):
        return None


def _run_locked(worktree: LinkedWorktree, action: Callable[[], _Result]) -> _Result:
    try:
        with hold_file_lock(worktree.lock_file, _LOCK_WAIT_SECONDS, _LOCK_POLL_SECONDS):
            return action()
    except LockTimeoutError as error:
        raise OwnerBusyError(f"another session is changing who owns {worktree.root}; try again") from error


def _store_if_unchanged(worktree: LinkedWorktree, expected: FolderOwner, owner: FolderOwner) -> None:
    if read_owner(worktree) == expected:
        with _write_temporary_record(worktree, owner) as temporary:
            os.replace(temporary, worktree.owner_file)


def _replace_owner(
    worktree: LinkedWorktree,
    expected: FolderOwner,
    identity: SessionIdentity,
    title: str,
    method: ClaimMethod,
) -> FolderOwner:
    current = read_owner(worktree)

    if current == expected:
        owner = FolderOwner(identity, title, time.time(), method, expected.owner)

        _store_if_unchanged(worktree, expected, owner)
        return owner
    elif current is not None:
        return current
    else:
        return claim_folder(worktree, identity, title, method)


@contextlib.contextmanager
def _write_temporary_record(worktree: LinkedWorktree, owner: FolderOwner) -> Iterator[Path]:
    """A complete record in a temporary file beside the real one, removed afterwards if it was not moved there."""
    descriptor, name = tempfile.mkstemp(prefix=f"{OWNER_FILE_NAME}.", suffix=".tmp", dir=worktree.git_directory)
    temporary = Path(name)

    try:
        with os.fdopen(descriptor, "w", encoding=_ENCODING) as file:
            json.dump(_dump_owner(owner), file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _move_settled_gaps(worktree: LinkedWorktree, settled_lines: set[str]) -> None:
    gaps = read_gaps(worktree)
    kept = [gap.line for gap in gaps if gap.line not in settled_lines]
    moved = [gap.line for gap in gaps if gap.line in settled_lines]
    offered = sorted(_read_offered_lines(worktree) - settled_lines)

    _append_lines(worktree.settled_gaps_file, moved)
    _replace_lines(worktree.gaps_file, kept)
    _replace_lines(worktree.offered_gaps_file, offered)


def _append_new_gap(worktree: LinkedWorktree, gap: dict[str, Any]) -> None:
    call_id = gap[_GAP_CALL_KEY]
    is_noted = call_id is not None and any(known.call_id == call_id for known in read_gaps(worktree))

    if not is_noted:
        _append_line(worktree.gaps_file, json.dumps(gap, ensure_ascii=False))


def _is_owner_gap(gap: GuardGap, owner: FolderOwner | None) -> bool:
    return owner is not None and owner.owner._is_named(gap.session) and gap.time >= owner.claimed_at


def _try_read_owner(worktree: LinkedWorktree) -> FolderOwner | None:
    try:
        return read_owner(worktree)
    except OwnerFileError:
        return None


def _read_offered_lines(worktree: LinkedWorktree) -> set[str]:
    try:
        return set(worktree.offered_gaps_file.read_text(encoding=_ENCODING).splitlines())
    except FileNotFoundError:
        return set()


def _read_process_state(mark: ProcessMark) -> OwnerState:
    try:
        row = _read_process_row(mark.pid)
    except _ProcessLookupError:
        return OwnerState.UNKNOWN
    else:
        return OwnerState.ACTIVE if row is not None and row.started == mark.started else OwnerState.GONE


def _append_line(path: Path, line: str) -> None:
    _append_lines(path, [line])


def _append_lines(path: Path, lines: Sequence[str]) -> None:
    with path.open("a", encoding=_ENCODING) as file:
        file.write("".join(f"{line}\n" for line in lines))


def _replace_lines(path: Path, lines: list[str]) -> None:
    if lines:
        descriptor, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)

        with os.fdopen(descriptor, "w", encoding=_ENCODING) as file:
            file.write("".join(f"{line}\n" for line in lines))
        os.replace(name, path)
    else:
        path.unlink(missing_ok=True)


def _dump_owner(owner: FolderOwner) -> dict[str, Any]:
    return {
        _FORMAT_KEY: _RECORD_FORMAT,
        _OWNER_KEY: _dump_identity(owner.owner),
        _TITLE_KEY: owner.title,
        _CLAIMED_AT_KEY: owner.claimed_at,
        _METHOD_KEY: owner.method.value,
        _PREVIOUS_KEY: _dump_identity(owner.previous) if owner.previous is not None else None,
    }


def _dump_identity(identity: SessionIdentity) -> dict[str, Any]:
    process = identity.process

    return {
        _APP_SESSION_KEY: identity.app_session,
        _CLI_SESSION_KEY: identity.cli_session,
        _PROCESS_KEY: {_PID_KEY: process.pid, _STARTED_KEY: process.started} if process is not None else None,
    }


def _parse_owner(text: str, path: Path) -> FolderOwner:
    try:
        data = json.loads(text)
        previous = data.get(_PREVIOUS_KEY)

        return FolderOwner(
            owner=_parse_identity(data[_OWNER_KEY]),
            title=str(data.get(_TITLE_KEY) or ""),
            claimed_at=float(data[_CLAIMED_AT_KEY]),
            method=ClaimMethod(data[_METHOD_KEY]),
            previous=_parse_identity(previous) if previous is not None else None,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise OwnerFileError(f"{path} is not an owner record: {error}") from error


def _parse_identity(data: dict[str, Any]) -> SessionIdentity:
    process = data.get(_PROCESS_KEY)
    mark = ProcessMark(int(process[_PID_KEY]), str(process[_STARTED_KEY])) if process is not None else None

    return SessionIdentity(data.get(_APP_SESSION_KEY) or None, data.get(_CLI_SESSION_KEY) or None, mark)


def _parse_gap(line: str) -> GuardGap:
    try:
        data = json.loads(line)

        return GuardGap(
            float(data[_GAP_TIME_KEY]),
            data.get(_GAP_SESSION_KEY),
            str(data[_GAP_WHAT_KEY]),
            line,
            data.get(_GAP_CALL_KEY),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return GuardGap(0.0, None, line.strip(), line)


def _read_git_file(git_file: Path) -> Path | None:
    """The Git folder a worktree's `.git` file points to (`gitdir: <path>`), or None when it holds none."""
    try:
        text = git_file.read_text(encoding=_ENCODING).strip()
    except (OSError, UnicodeDecodeError):
        return None
    else:
        location = text[len(_GITDIR_PREFIX) :].strip() if text.startswith(_GITDIR_PREFIX) else ""

        return (git_file.parent / location).resolve() if location else None


def _find_session_process() -> ProcessMark | None:
    """The Claude Code process this call runs under, found among its ancestors by the program's name."""
    pid = os.getppid()

    for _ in range(_MAX_ANCESTORS):
        row = _read_process_row_or_none(pid)

        if row is None:
            return None
        elif os.path.basename(row.command) == _SESSION_PROCESS_NAME:
            return ProcessMark(pid, row.started)
        elif row.parent <= 1:
            return None
        else:
            pid = row.parent
    return None


def _read_process_row_or_none(pid: int) -> _ProcessRow | None:
    try:
        return _read_process_row(pid)
    except _ProcessLookupError:
        return None


def _read_process_row(pid: int) -> _ProcessRow | None:
    """A process's parent, start time, and program from `ps`, or None when it does not run.

    The start time is read in one fixed time zone and language, since processes that compare marks may run with
    different ones. Raises _ProcessLookupError when `ps` cannot tell, which must not read as an ended process.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-o", "lstart=", "-o", "comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding=_ENCODING,
            errors="replace",
            env={**os.environ, **_PS_ENVIRONMENT},
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _ProcessLookupError(str(error)) from error
    else:
        return _parse_process_row(result)


def _parse_process_row(result: subprocess.CompletedProcess[str]) -> _ProcessRow | None:
    """`ps -p` exits 1 with no output when no such process runs; anything else it prints must be a full row."""
    fields = result.stdout.strip().split(None, _PS_FIELD_COUNT - 1)

    if result.returncode == 0 and len(fields) == _PS_FIELD_COUNT and fields[0].isdigit():
        return _ProcessRow(int(fields[0]), " ".join(fields[1:6]), fields[6])
    elif result.returncode == 1 and not result.stdout.strip():
        return None
    else:
        raise _ProcessLookupError(f"ps exited {result.returncode} with {result.stdout.strip()!r}")


def _resolve_session(text: str) -> SessionIdentity:
    """A session named by its app id (`local_…`) or its Claude Code id, with the other id from the app's record."""
    record = find_session_record(text)

    if text.startswith(APP_SESSION_PREFIX):
        return SessionIdentity(text, read_cli_session_id(record) if record else None, None)
    else:
        return SessionIdentity(read_app_session_id(record) if record else None, text, None)


def _parse_command_line(arguments: Sequence[str], exit_on_error: bool = True) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="folder_owner.py",
        description="Show or change who owns a task worktree.",
        exit_on_error=exit_on_error,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser(TAKE_COMMAND, help="take the folder over from its owner (the guard may ask the user)")
    hand_over = commands.add_parser(HAND_OVER_COMMAND, help="hand the folder you own to another session")

    commands.add_parser(SHOW_COMMAND, help="name the folder's owner").add_argument("folder", nargs="?")
    commands.add_parser(CLAIM_COMMAND, help="claim a folder with no owner or one whose owner left").add_argument(
        "folder", nargs="?"
    )
    take.add_argument(FROM_OPTION, dest="session", required=True)
    take.add_argument("folder", nargs="?")
    hand_over.add_argument(TO_OPTION, dest="session", required=True)
    hand_over.add_argument("folder", nargs="?")
    return parser.parse_args(arguments)


def _run_command(arguments: argparse.Namespace) -> int:
    folder = arguments.folder or os.getcwd()
    worktree = find_linked_worktree(Path(folder).resolve())
    identity = read_current_identity()

    if worktree is None:
        raise _OwnerCommandError(f"{folder} is not inside a task worktree (a worktree `git worktree add` made)")
    elif arguments.command == SHOW_COMMAND:
        print(_describe_folder(worktree))
        return 0
    elif identity is None:
        raise _OwnerCommandError("run this in a Claude session: only a session can own a task worktree")
    elif arguments.command == CLAIM_COMMAND:
        return _claim_from_command(worktree, identity)
    elif arguments.command == TAKE_COMMAND:
        return _take_from_command(worktree, identity, _resolve_session(arguments.session))
    else:
        return _hand_over_from_command(worktree, identity, _resolve_session(arguments.session))


def _describe_folder(worktree: LinkedWorktree) -> str:
    owner = read_owner(worktree)

    if owner is not None:
        state = read_owner_state(owner).value

        return f"{worktree.root}: owned by {owner.describe()}, {state}, {owner.method.value}"
    else:
        return f"{worktree.root}: no owner recorded"


def _claim_from_command(worktree: LinkedWorktree, identity: SessionIdentity) -> int:
    access = judge_access(worktree, identity)

    if access.kind == AccessKind.OTHERS and access.owner is not None:
        raise _OwnerCommandError(
            f"{worktree.root} belongs to {access.owner.describe()}, which is still open or whose state is unknown; "
            f"take it with `folder_owner.py {TAKE_COMMAND} {FROM_OPTION} {access.owner.owner.name}` (the user is asked first), or ask that session to hand it over"
        )
    elif access.kind == AccessKind.OWN:
        print(f"{worktree.root} is already this session's")
        return 0
    else:
        return _report_result(worktree, take_folder(access, identity), identity)


def _take_from_command(worktree: LinkedWorktree, identity: SessionIdentity, sender: SessionIdentity) -> int:
    owner = read_owner(worktree)

    if owner is None:
        raise _OwnerCommandError(f"{worktree.root} has no owner; claim it with `folder_owner.py {CLAIM_COMMAND}`")
    elif owner.owner.is_same(sender):
        title = read_identity_title(identity)
        result = transfer_folder(worktree, owner, identity, title, ClaimMethod.TAKEN_OVER)

        return _report_result(worktree, result, identity)
    else:
        raise _OwnerCommandError(f"{worktree.root} belongs to {owner.describe()}, not {sender.name}")


def _hand_over_from_command(worktree: LinkedWorktree, identity: SessionIdentity, receiver: SessionIdentity) -> int:
    owner = read_owner(worktree)

    if owner is not None and owner.owner.is_same(identity):
        title = read_identity_title(receiver)
        result = transfer_folder(worktree, owner, receiver, title, ClaimMethod.HANDED_OVER)

        return _report_result(worktree, result, receiver)
    else:
        shown = owner.describe() if owner is not None else "nobody"

        raise _OwnerCommandError(f"only the owner hands {worktree.root} over, and it belongs to {shown}")


def _report_result(worktree: LinkedWorktree, owner: FolderOwner, wanted: SessionIdentity) -> int:
    if owner.owner.is_same(wanted):
        print(f"{worktree.root} now belongs to {owner.describe()}")
        return 0
    else:
        print(f"FAIL: {worktree.root} went to {owner.describe()} meanwhile", file=sys.stderr)
        return _EXIT_FAILED


def main(arguments: Sequence[str]) -> int:
    """Run one command; argparse itself exits with status 2 on a command line it does not take."""
    try:
        return _run_command(_parse_command_line(arguments))
    except (_OwnerCommandError, OwnerFileError, OwnerBusyError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return _EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
