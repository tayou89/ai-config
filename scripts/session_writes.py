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
write, unknown write, or call still running overlaps in time. Every other
write is contested, and the commit wrapper asks about its changes instead of
guessing. Evidence that cannot be read back stops the commit rather than being
skipped, since a missing record of another session would hand its change to
this one.

A snapshot that cannot be taken raises SessionWritesError instead of
returning an empty one: an empty snapshot would read as "nothing was dirty"
and hand another session's uncommitted lines to this session. The hook then
records what that snapshot would have covered as an UnknownWrite over the
call's time, so the call keeps contesting the writes that overlap it without
counting as running once it has ended. File contents are kept as bytes end to
end (decoded with surrogateescape), so line endings and undecodable bytes
survive the round trip.

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
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from own_lines import ContestedLine, FileHistory, RecordedWrite, build_own_content, list_foreign_lines
from shell_command import CommandCall, GitCommand, expand_shell_command, parse_git_command

_SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_RECORD_DIRECTORY_NAME = "session-writes"
_RECORD_SUFFIX = ".jsonl"
_PENDING_SUFFIX = ".pending.json"
_STARTED_FILE = "started"
_PROJECTS_DIRECTORY_NAME = "projects"
_SUBAGENTS_DIRECTORY_NAME = "subagents"
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
_BLOBS_FIELD = "blobs"
_TIME_FIELD = "time"
_APPROVALS_FILE = "approved-includes.jsonl"
_OFFERS_FILE = "offered-includes.jsonl"
_APPROVAL_LIFETIME_SECONDS = 30 * 60
_EDITING_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
_KIND_FIELD = "kind"
_UNKNOWN_KIND = "unknown"
_MAX_TRACKED_BYTES = 1024 * 1024
_GIT_TIMEOUT_SECONDS = 10
_RENAME_OR_COPY_STATUSES = "RC"
_REPORT_COMMAND = "report"
_HEAD_REVISION = "HEAD"
_TEXT_ENCODING = "utf-8"
_LOSSLESS_ERRORS = "surrogateescape"
_EXIT_USAGE = 4
_SCRIPTS_DIRECTORY = Path(__file__).resolve().parent
_COMMIT_WRAPPER_NAME = "claude-commit.py"
_SESSION_WRITES_NAME = "session_writes.py"
_PYTHON_NAMES = frozenset({"python", "python3"})
_NEVER_RAN_RESULT_PATTERN = re.compile(
    r"PreToolUse:\S+ hook error|The user doesn't want to proceed with this tool use"
    r"|Tool permission request aborted|The server-side auto mode classifier gave no verdict"
)
_TREE_KEEPING_PROGRAMS = frozenset(
    {
        "[",
        "basename",
        "cat",
        "cd",
        "cmp",
        "cut",
        "diff",
        "dirname",
        "echo",
        "exit",
        "export",
        "false",
        "for",
        "grep",
        "head",
        "ls",
        "printf",
        "pwd",
        "stat",
        "tail",
        "test",
        "true",
        "unset",
        "wc",
    }
)
_TREE_KEEPING_GIT_SUBCOMMANDS = frozenset(
    {
        "add",
        "cat-file",
        "describe",
        "diff",
        "hash-object",
        "log",
        "ls-files",
        "ls-tree",
        "merge-base",
        "rev-list",
        "rev-parse",
        "show",
        "status",
        "update-index",
        "write-tree",
    }
)
_TREE_KEEPING_GIT_OPTIONS = {
    "read-tree": frozenset({"--empty"}),
    "reset": frozenset({"-q", "--quiet", "--mixed", "--soft"}),
}
_PATHSPEC_SEPARATOR = "--"
_PROGRAM_RUNNING_GIT_OPTIONS = ("--output", "--ext-diff", "--textconv")
_SAFE_GIT_CONFIG_PREFIXES = ("core.quotepath", "color.")
_GIT_ENVIRONMENT_PREFIX = "GIT_"
_SAFE_GIT_VARIABLES = frozenset({"GIT_INDEX_FILE", "GIT_CONFIG_COUNT", "GIT_CONFIG_NOSYSTEM"})
_GIT_CONFIG_KEY_PATTERN = re.compile(r"GIT_CONFIG_KEY_\d+")
_GIT_CONFIG_VALUE_PATTERN = re.compile(r"GIT_CONFIG_VALUE_\d+")
_HOOKS_DIRECTORY_NAME = "hooks"
_COMMIT_HOOK_NAMES = ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "post-rewrite")
_PUSH_SUBCOMMAND = "push"
_PUSH_HOOK_NAMES = ("pre-push", "reference-transaction")
_TREE_KEEPING_PUSH_OPTIONS = frozenset(
    {
        "-q",
        "--quiet",
        "-v",
        "--verbose",
        "-u",
        "--set-upstream",
        "--porcelain",
        "--progress",
        "--no-progress",
        "-n",
        "--dry-run",
        "--no-verify",
        "-f",
        "--force",
        "--force-with-lease",
        "--force-if-includes",
        "--atomic",
        "-d",
        "--delete",
        "--tags",
        "--follow-tags",
        "-o",
        "--push-option",
    }
)
_PUSH_OPTIONS_WITH_ARGUMENT = frozenset({"-o", "--push-option"})
_NETWORK_URL_PATTERN = re.compile(r"(?:https?|ssh|git)://\S+|[\w.-]+@[\w.-]+:\S+")
_REMOTE_PREFIX = "remote."
_REMOTE_URL_SUFFIX = ".url"
_PROGRAM_RUNNING_PUSH_KEYS = frozenset({"core.sshcommand", "core.askpass", "core.gitproxy"})
_DEFAULT_REMOTE_KEY = re.compile(r"branch\..+\.(?:push)?remote|remote\.pushdefault")
_PROGRAM_RUNNING_REMOTE_SUFFIXES = (".receivepack", ".vcs")
_PUSH_SIGN_KEY = "push.gpgsign"
_FALSE_CONFIG_VALUES = frozenset({"false", "no", "off", "0", ""})
_SUBMODULE_PUSH_KEY = "push.recursesubmodules"
_SUBMODULE_PUSH_VALUES = frozenset({"on-demand", "only"})
_CREDENTIAL_HELPER_KEY = re.compile(r"credential\.(?:.+\.)?helper")
_KNOWN_CREDENTIAL_HELPERS = frozenset({"", "osxkeychain", "store", "cache", "manager", "manager-core", "libsecret"})
_GH_CREDENTIAL_HELPER_PATTERN = re.compile(r"!['\"]?(?:\S*/)?gh['\"]? auth git-credential")
_GH_PROGRAM = "gh"
_GH_AUTH_GROUP = "auth"
_TREE_KEEPING_GH_COMMANDS = {
    "pr": frozenset({"view", "list", "checks", "status", "diff"}),
    "run": frozenset({"view", "list", "watch"}),
}
_CONFIGURATION_LOCATION_VARIABLES = frozenset({"HOME", "XDG_CONFIG_HOME", "GH_CONFIG_DIR"})
_ABSOLUTE_PATH_PATTERN = re.compile(r"""(?:^|(?<=[\s'"=(:]))((?:/|~/)[^\s'"<>|;&()`$]+)""")


class SessionWritesError(RuntimeError):
    pass


class IncludeChangedError(SessionWritesError):
    """Files changed after the shell guard's dialog showed them, so the user's approval does not cover them."""

    def __init__(self, paths: Sequence[str]) -> None:
        super().__init__(", ".join(paths))
        self.paths = list(paths)


class ContestCause(str, Enum):
    """What overlapped the session's unproven writes to a file."""

    OTHER_EDIT = "other-edit"
    OTHER_COMMAND = "other-command"
    UNRECORDED = "unrecorded"


_CONTEST_CAUSE_TEXTS = {
    ContestCause.OTHER_EDIT: "another session edited this file while you wrote it, so they may be its",
    ContestCause.OTHER_COMMAND: (
        "another session's command was running in this repository while you wrote, "
        "so the record cannot tell whose these lines are"
    ),
    ContestCause.UNRECORDED: (
        "no other session's call overlapped your writes, so a person, an editor, a project hook, "
        "or a command the record did not see changed the file around them"
    ),
}


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

    def is_covering(self, root: str, path: str) -> bool:
        return self.root == root and self.path == path


@dataclass(frozen=True)
class SnapshotScope:
    """The files one snapshot covers: one file, or every file in the repository when `path` is None."""

    root: str
    path: str | None

    def is_covering(self, root: str, path: str) -> bool:
        return self.root == root and (self.path is None or self.path == path)


@dataclass(frozen=True)
class UnknownWrite:
    """Files one tool call may have changed in a way no snapshot shows, since one of its snapshots could not be taken.

    It covers its scope (`path` None for every file in the repository) from
    `start` to `end`. Other sessions' writes that overlap it are contested,
    and it never counts toward this session's own lines, so its change is
    left out of them. A running call reads as one that has not ended.
    """

    root: str
    path: str | None
    start: float
    end: float
    tool: str
    record_format: int = RECORD_FORMAT

    def is_covering(self, root: str, path: str) -> bool:
        return SnapshotScope(self.root, self.path).is_covering(root, path)


@dataclass(frozen=True)
class RepositorySnapshot:
    root: str
    head: str | None
    blobs: dict[str, str | None]
    covers_repository: bool

    def get_scope(self) -> SnapshotScope:
        """A file snapshot holds exactly its one file."""
        return SnapshotScope(self.root, None if self.covers_repository else next(iter(self.blobs)))


@dataclass(frozen=True)
class PendingCall:
    """A running call's start, its before snapshots, and the scopes whose before snapshot could not be taken."""

    start: float
    snapshots: list[RepositorySnapshot]
    unknown: list[SnapshotScope]

    def is_covering(self, root: str, path: str) -> bool:
        scopes = [*(snapshot.get_scope() for snapshot in self.snapshots), *self.unknown]

        return any(scope.is_covering(root, path) for scope in scopes)


@dataclass(frozen=True)
class _IncludeApproval:
    """Files with their unrecorded lines, each with its content: as the shell guard's dialog showed it, or as the
    user approved it."""

    root: str
    blobs: dict[str, str | None]
    time: float


_EVENT_FIELDS = frozenset(event_field.name for event_field in fields(WriteEvent))
_UNKNOWN_FIELDS = frozenset(unknown_field.name for unknown_field in fields(UnknownWrite))


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
    """Repositories a command may write.

    A command made only of calls that leave working trees as they were, such
    as reading files, the commit wrapper's git index work, a push that can
    run no program here, and GitHub CLI sign-in and viewing commands, writes
    none of them, except a repository it commits in whose commit hooks may
    change files. Like Jujutsu's --ignore-working-copy, such a command takes no
    snapshot, so another session's write while it ran is not read as its
    own, and it does not count as running for other sessions.
    """
    calls = _expand_known_command(command, cwd)

    if calls is not None and all(map(_is_tree_keeping, calls)):
        return sorted({root for root in map(_find_hooked_commit_root, calls) if root})
    else:
        return _find_possible_roots(session_id, command, cwd)


def _find_possible_roots(session_id: str, command: str, cwd: str | None) -> list[str]:
    """Repositories any command may write: its folder, folders it names, and ones this session wrote before.

    A repository this session wrote whose folder is gone is left out: there is
    nothing in it to snapshot, and a call that brings it back creates it as far
    as snapshots go, like any repository a call creates.
    """
    named = [os.path.expanduser(match) for match in _ABSOLUTE_PATH_PATTERN.findall(command)]
    candidates = [*([cwd] if cwd else []), *named]
    roots = {root for root in map(find_repository_root, candidates) if root}
    recorded = {root for root in _read_recorded_roots(session_id) if os.path.isdir(root)}

    return sorted(roots | recorded)


def take_snapshot(scope: SnapshotScope) -> RepositorySnapshot:
    if scope.path is None:
        return _take_repository_snapshot(scope.root)
    else:
        return _take_file_snapshot(scope.root, scope.path)


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


def append_events(session_id: str, events: Iterable[WriteEvent | UnknownWrite]) -> None:
    lines = [json.dumps(_serialize_event(event), ensure_ascii=False) + "\n" for event in events]
    record = get_record_directory() / f"{session_id}{_RECORD_SUFFIX}"

    if lines:
        record.parent.mkdir(parents=True, exist_ok=True)
        with record.open("a", encoding=_TEXT_ENCODING) as file:
            file.write("".join(lines))


def save_pending(session_id: str, tool_use_id: str, call: PendingCall) -> None:
    """Keep a call's before snapshots until it ends; its start is taken before the first snapshot."""
    directory = get_record_directory() / session_id
    pending = directory / f"{tool_use_id}{_PENDING_SUFFIX}"

    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _STARTED_FILE).write_text(str(call.start), encoding=_TEXT_ENCODING)
    pending.write_text(json.dumps(asdict(call), ensure_ascii=False), encoding=_TEXT_ENCODING)


def read_pending(session_id: str, tool_use_id: str) -> PendingCall | None:
    """A call's pending file, which stays until remove_pending(), after its events are kept."""
    pending = get_record_directory() / session_id / f"{tool_use_id}{_PENDING_SUFFIX}"

    if pending.exists():
        return _parse_pending_call(pending.read_text(encoding=_TEXT_ENCODING))
    else:
        return None


def is_pending(session_id: str, tool_use_id: str) -> bool:
    """Whether a call's pending file is still there, so its events may not be in the record yet."""
    return (get_record_directory() / session_id / f"{tool_use_id}{_PENDING_SUFFIX}").exists()


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


def load_file_history(
    session_id: str,
    root: str,
    path: str,
    working_text: str | None = None,
) -> FileHistory | None:
    """The chain of this session's writes to one file, or None when it recorded none.

    `working_text` stands for the file's current content when the caller has
    already read it, so the history and what the caller shows agree.
    Raises SessionWritesError when a record, or contents it names, cannot be
    read back.
    """
    events = _read_file_events(root, path)
    own = _list_own_writes(session_id, events)
    current_text = working_text if working_text is not None else _read_working_text(root, path)

    if own:
        foreign = _list_foreign_writes(session_id, root, path, events)
        own_writes = tuple(_make_recorded_write(event, foreign) for event in own)
        unexplained = _list_unexplained_steps(root, own, foreign, current_text)

        return FileHistory(_read_blob(root, own[0].before), own_writes, current_text, unexplained)
    else:
        return None


def find_contest_cause(session_id: str, root: str, path: str) -> ContestCause:
    """What overlapped this session's writes to the file that the record cannot prove.

    Another session's Edit or Write among the overlapping writes may have
    written the lines; a command of another session, which snapshots the
    whole repository, only ran meanwhile, so the record cannot tell whose
    they are. With neither, something the record does not see changed the
    file. Raises SessionWritesError when a record cannot be read back.
    """
    events = _read_file_events(root, path)
    unproven = [event for event in _list_own_writes(session_id, events) if event.exact is not True]
    foreign = _list_foreign_writes(session_id, root, path, events)
    overlapping = [other for other in foreign if any(_is_overlapping(event, other) for event in unproven)]

    if any(_is_editing_write(other) for other in overlapping):
        return ContestCause.OTHER_EDIT
    elif overlapping:
        return ContestCause.OTHER_COMMAND
    else:
        return ContestCause.UNRECORDED


def describe_contest_cause(cause: ContestCause) -> str:
    return _CONTEST_CAUSE_TEXTS[cause]


def is_whole_file_own(session_id: str, root: str, path: str, base_blob: str | None) -> bool:
    """Whether every change from base to the working tree came from this session's uncontested writes.

    Records that cannot be read back count as not all its own.
    """
    try:
        events = _read_file_events(root, path)
        own = _list_own_writes(session_id, events)
        foreign = _list_foreign_writes(session_id, root, path, events)
        chain = [base_blob, *(blob for event in own for blob in (event.before, event.after))]
        working_tree = _store_files(root, [path])[path]
        is_unbroken = all(chain[index] == chain[index + 1] for index in range(0, len(chain) - 1, 2))
        is_uncontested = not any(_is_contested(event, foreign) for event in own)

        return bool(own) and is_unbroken and chain[-1] == working_tree and is_uncontested
    except SessionWritesError:
        return False


def has_other_session_writes(session_id: str, root: str, since: float) -> bool:
    """Whether another session wrote in the repository at or after a time, or has a call there that may still run.

    The commit wrapper asks it about a linked worktree from when the worktree
    was added. A record or pending file that cannot be read counts as a
    write, and so does a time before the records' lifetime, whose records
    may already be gone, so a doubtful case keeps the own-lines check.
    """
    try:
        sessions = [record.stem for record in get_record_directory().glob(f"*{_RECORD_SUFFIX}")]
        events = [event for session in sessions if session != session_id for event in _read_events(session)]
    except SessionWritesError:
        return True
    else:
        is_before_records = since < time.time() - _RECORD_LIFETIME_SECONDS
        written = any(event.root == root and event.end >= since for event in events)
        running = any(_is_pending_in(pending, root, since) for pending in _list_other_pendings(session_id))

        return is_before_records or written or running


def is_line_tracked(root: str, path: str) -> bool:
    """Whether the file is a regular text file small enough to separate line by line."""
    file = Path(root, path)

    if file.is_symlink():
        return False
    elif file.exists():
        return file.stat().st_size <= _MAX_TRACKED_BYTES and _is_text(file.read_bytes())
    else:
        return True


def list_unproven_lines(session_id: str, root: str, path: str, blob: str | None) -> list[str]:
    """The changes in a stored content of the file that this session's record does not show as its own.

    They come as `+ text` and `- text`, and are what --include-unrecorded
    commits beyond the session's own lines; every change counts when the
    session recorded no write to the file. `blob` is the content the shell
    guard's dialog shows, None for a file that is gone. Raises
    SessionWritesError when the record cannot be read back.
    """
    base = _read_blob(root, _read_committed_blob(root, _HEAD_REVISION, path))
    working_text = _read_blob(root, blob)
    history = load_file_history(session_id, root, path, working_text)
    own = build_own_content(base, history).text if history is not None else base

    return [f"{line.kind.value} {line.text}" for line in list_foreign_lines(base, working_text, own)]


def find_repository_paths(cwd: str, paths: Sequence[str]) -> tuple[str, list[str]] | None:
    """The repository holding paths given relative to cwd, as the commit wrapper takes them, and the paths in it."""
    root = find_repository_root(cwd)
    real_root = os.path.realpath(root) if root else ""

    if root:
        return root, [os.path.relpath(os.path.realpath(os.path.join(cwd, path)), real_root) for path in paths]
    else:
        return None


def add_include_offer(session_id: str, root: str, paths: Sequence[str]) -> dict[str, str | None]:
    """Keep the files' contents as the shell guard's dialog is about to show them, and return them.

    The guard counts the lines it lists from these contents, and the commit
    wrapper commits exactly them, so what the user approves is what lands.
    A session without a record folder keeps nothing, and its wrapper then
    takes the files as they are when it starts. Raises SessionWritesError
    when a file cannot be stored.
    """
    blobs = _store_files(root, paths)

    _append_include_entry(session_id, _OFFERS_FILE, root, blobs)
    return blobs


def approve_include(session_id: str, root: str, paths: Sequence[str]) -> dict[str, str | None]:
    """Keep that the user let this session commit these files' unrecorded lines, and return the contents approved.

    The commit wrapper calls it as it starts with --include-unrecorded: the
    shell guard asks before every such call, and a call the user declines
    never runs, so the wrapper running is the approval of what the newest
    dialog for these files showed. A file that differs from it raises
    IncludeChangedError, since the user has not seen it. Without such a
    dialog, the files as they are now. An approval that already covers the
    contents keeps its time, so a chain of retries does not stretch it.
    """
    blobs = _store_files(root, paths)
    offer = _find_latest_include_offer(session_id, root, paths)
    changed = [path for path in paths if offer is not None and offer.blobs[path] != blobs[path]]

    if changed:
        raise IncludeChangedError(changed)
    else:
        _keep_include_approval(session_id, root, blobs)
        return blobs


def is_include_approved(session_id: str, root: str, blobs: dict[str, str | None]) -> bool:
    """Whether the user let this session commit these contents of the files in the last half hour.

    Like sudo's timestamp, an approval covers a retry of the same commit,
    such as one after HEAD moved. The wrapper commits the contents the dialog
    showed, so a call still running over a file cannot slip a later change
    in, and only a file whose content differs from the approval is asked
    again, whoever changed it.
    """
    return _find_include_approval(session_id, root, blobs) is not None


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
        summary = f"{path}: {len(foreign)} changed lines are not this session's and would be left out{shown}"
        entangled = f"\n{len(own.entangled)} own lines cannot be separated from them:{tangled}" if own.entangled else ""
        contested = _describe_contested_lines(session_id, root, path, own.contested) if own.contested else ""

        return summary + entangled + contested


def _describe_contested_lines(session_id: str, root: str, path: str, contested: Sequence[ContestedLine]) -> str:
    """Name another session only for what overlapped the session's unproven writes."""
    unsure = "".join(f"\n  ? {line.kind.value} {line.text.rstrip()}" for line in contested)
    reason = describe_contest_cause(find_contest_cause(session_id, root, path))

    return f"\n{len(contested)} changes the record cannot prove: {reason}; they may be this session's:{unsure}"


def _read_working_text(root: str, path: str) -> str:
    working_tree = Path(root, path)

    return decode_content(working_tree.read_bytes()) if working_tree.is_file() else ""


def _is_editing_write(event: WriteEvent | UnknownWrite) -> bool:
    """Whether a write came from a tool that changes the one file it names, unlike a command's repository snapshot."""
    return isinstance(event, WriteEvent) and event.tool in _EDITING_TOOLS


def _keep_include_approval(session_id: str, root: str, blobs: dict[str, str | None]) -> None:
    if _find_include_approval(session_id, root, blobs) is None:
        _append_include_entry(session_id, _APPROVALS_FILE, root, blobs)


def _append_include_entry(session_id: str, name: str, root: str, blobs: dict[str, str | None]) -> None:
    """Add a dialog's or an approval's contents to the session's folder, which a session without a record lacks."""
    directory = get_record_directory() / session_id
    entry = {_ROOT_FIELD: root, _BLOBS_FIELD: blobs, _TIME_FIELD: time.time()}

    if directory.is_dir():
        with (directory / name).open("a", encoding=_TEXT_ENCODING) as file:
            file.write(json.dumps(entry, ensure_ascii=False) + _RECORD_LINE_END)


def _find_latest_include_offer(session_id: str, root: str, paths: Sequence[str]) -> _IncludeApproval | None:
    """The newest dialog the shell guard showed for exactly these files, which is the one the user just answered."""
    offers = _read_include_entries(session_id, _OFFERS_FILE)
    matching = [offer for offer in offers if offer.root == root and set(offer.blobs) == set(paths)]

    return matching[-1] if matching else None


def _find_include_approval(session_id: str, root: str, blobs: dict[str, str | None]) -> _IncludeApproval | None:
    """The session's approval in the last half hour that holds every one of these files with this content."""
    limit = time.time() - _APPROVAL_LIFETIME_SECONDS
    approvals = _read_include_entries(session_id, _APPROVALS_FILE)
    covering = [
        approval
        for approval in approvals
        if approval.root == root
        and approval.time >= limit
        and all(path in approval.blobs and approval.blobs[path] == blob for path, blob in blobs.items())
    ]

    return covering[0] if covering else None


def _read_include_entries(session_id: str, name: str) -> list[_IncludeApproval]:
    """A session's dialog or approval entries, oldest first, without lines that do not read as one."""
    record = get_record_directory() / session_id / name
    entries = [_parse_approval(line) for line in _read_record_text(record).split(_RECORD_LINE_END) if line.strip()]

    return [entry for entry in entries if entry is not None]


def _parse_approval(line: str) -> _IncludeApproval | None:
    """A kept dialog or approval entry, or None for a line that does not read as one."""
    try:
        entry = json.loads(line)
        blobs = {str(path): None if blob is None else str(blob) for path, blob in dict(entry[_BLOBS_FIELD]).items()}
        approval = _IncludeApproval(str(entry[_ROOT_FIELD]), blobs, float(entry[_TIME_FIELD]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    else:
        return approval


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


def _read_events(session_id: str) -> list[WriteEvent | UnknownWrite]:
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


def _serialize_event(event: WriteEvent | UnknownWrite) -> dict[str, object]:
    """A record line's fields; only an unknown write names its kind, so records from before it read unchanged."""
    if isinstance(event, UnknownWrite):
        return {_KIND_FIELD: _UNKNOWN_KIND, **asdict(event)}
    else:
        return asdict(event)


def _parse_event(record: Path, number: int, line: str) -> WriteEvent | UnknownWrite:
    try:
        entry = json.loads(line)
        is_unknown = entry.get(_KIND_FIELD) == _UNKNOWN_KIND
        known = {
            key: value for key, value in entry.items() if key in (_UNKNOWN_FIELDS if is_unknown else _EVENT_FIELDS)
        }
        event: WriteEvent | UnknownWrite = UnknownWrite(**known) if is_unknown else WriteEvent(**known)
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


def _read_file_events(root: str, path: str) -> list[tuple[str, WriteEvent | UnknownWrite]]:
    """Every session's events covering one file, each with the session that recorded it."""
    sessions = [record.stem for record in get_record_directory().glob(f"*{_RECORD_SUFFIX}")]

    return [
        (session, event) for session in sessions for event in _read_events(session) if event.is_covering(root, path)
    ]


def _list_own_writes(session_id: str, events: Sequence[tuple[str, WriteEvent | UnknownWrite]]) -> list[WriteEvent]:
    """This session's writes to the file, aligned.

    An unknown write has no content to chain, so the chain shows a gap there.
    """
    return _align_parallel_writes(
        event for session, event in events if session == session_id and isinstance(event, WriteEvent)
    )


def _list_foreign_writes(
    session_id: str,
    root: str,
    path: str,
    events: Sequence[tuple[str, WriteEvent | UnknownWrite]],
) -> list[WriteEvent | UnknownWrite]:
    """Other sessions' writes to the file, including their unknown writes and calls still running over it."""
    recorded = [event for session, event in events if session != session_id]

    return [*recorded, *_read_running_calls(session_id, root, path)]


def _read_running_calls(session_id: str, root: str, path: str) -> list[UnknownWrite]:
    """Other sessions' calls still running whose snapshots cover the file, as unknown writes that have not ended.

    A pending file that does not parse counts as covering it from when it was
    written.
    """
    calls = [_read_running_call(pending, root, path) for pending in _list_other_pendings(session_id)]

    return [call for call in calls if call is not None]


def _list_other_pendings(session_id: str) -> list[Path]:
    """Other sessions' pending files; calls pending longer than the pending lifetime are taken as left by a session
    that ended."""
    limit = time.time() - _PENDING_LIFETIME_SECONDS

    return [
        pending
        for pending in get_record_directory().glob(f"*/*{_PENDING_SUFFIX}")
        if pending.parent.name != session_id and (_read_modified_time(pending) or 0.0) >= limit
    ]


def _is_pending_in(pending: Path, root: str, since: float) -> bool:
    """Whether a pending call snapshots the repository and ran at or after a time; one that does not parse does."""
    try:
        call = _parse_pending_call(pending.read_text(encoding=_TEXT_ENCODING))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return True
    else:
        roots = {scope.root for scope in [*(snapshot.get_scope() for snapshot in call.snapshots), *call.unknown]}
        never_ran_at = _find_never_ran_time(pending.parent.name, pending.name[: -len(_PENDING_SUFFIX)])

        return root in roots and (never_ran_at is None or never_ran_at >= since)


def _read_running_call(pending: Path, root: str, path: str) -> UnknownWrite | None:
    """A pending call covering the file, as an unknown write that has not ended, or ended when the call never ran.

    A call a hook denied, or one the user declined or stopped at its
    permission prompt, gets no end hook, so its pending file stays; its
    result in the transcript says it never ran. Any other result keeps it
    covering, since a call that ran may have left processes still writing.
    """
    start = _read_pending_start(pending, root, path)

    if start is not None:
        never_ran_at = _find_never_ran_time(pending.parent.name, pending.name[: -len(_PENDING_SUFFIX)])
        end = math.inf if never_ran_at is None else max(start, never_ran_at)

        return UnknownWrite(root, path, start, end, _PENDING_TOOL, _EARLIEST_RECORD_FORMAT)
    else:
        return None


@functools.cache
def _find_never_ran_time(session_id: str, tool_use_id: str) -> float | None:
    """When a result saying the call never ran reached its session's transcript or a subagent's, or None.

    A result whose time cannot be read, or whose text is not one of the
    known refusals, counts as none, so the call keeps covering.
    """
    projects = _get_config_directory() / _PROJECTS_DIRECTORY_NAME
    transcripts = [
        *projects.glob(f"*/{session_id}{_TRANSCRIPT_SUFFIX}"),
        *projects.glob(f"*/{session_id}/{_SUBAGENTS_DIRECTORY_NAME}/*{_TRANSCRIPT_SUFFIX}"),
    ]
    result_times = [_read_never_ran_time(transcript, tool_use_id) for transcript in transcripts]
    known = [result_time for result_time in result_times if result_time is not None]

    return min(known) if known else None


def _read_never_ran_time(transcript: Path, tool_use_id: str) -> float | None:
    try:
        content = transcript.read_bytes()
    except OSError:
        return None
    else:
        return _find_never_ran_line_time(content, tool_use_id)


def _find_never_ran_line_time(content: bytes, tool_use_id: str) -> float | None:
    marker = tool_use_id.encode(_TEXT_ENCODING)
    lines = [line.decode(_TEXT_ENCODING, errors="replace") for line in content.splitlines() if marker in line]
    times = [_parse_entry_time(line) for line in lines if _is_never_ran_line(line, tool_use_id)]
    known = [entry_time for entry_time in times if entry_time != _UNKNOWN_TIME]

    return min(known) if known else None


def _is_never_ran_line(line: str, tool_use_id: str) -> bool:
    blocks = _read_content_blocks(line) if _TOOL_RESULT_MARKER in line else []

    return any(_is_never_ran_result(block, tool_use_id) for block in blocks)


def _is_never_ran_result(block: dict[str, object], tool_use_id: str) -> bool:
    is_error_result = (
        block.get("type") == _TOOL_RESULT_TYPE
        and block.get("tool_use_id") == tool_use_id
        and block.get("is_error") is True
    )

    return is_error_result and _NEVER_RAN_RESULT_PATTERN.match(_read_block_text(block).lstrip()) is not None


def _read_block_text(block: dict[str, object]) -> str:
    content = block.get("content")

    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    else:
        return ""


def _read_pending_start(pending: Path, root: str, path: str) -> float | None:
    """When a running call began, if its snapshots cover the file, or None when they do not.

    A pending file removed meanwhile belongs to a call whose events are
    already in its record, so it is skipped.
    """
    try:
        call = _parse_pending_call(pending.read_text(encoding=_TEXT_ENCODING))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return _read_modified_time(pending)
    else:
        return call.start if call.is_covering(root, path) else None


def _parse_pending_call(text: str) -> PendingCall:
    """A pending file's call; files written before unknown scopes were kept have none."""
    payload = json.loads(text)
    snapshots = [RepositorySnapshot(**snapshot) for snapshot in payload["snapshots"]]
    unknown = [SnapshotScope(**scope) for scope in payload.get("unknown", [])]

    return PendingCall(float(payload["start"]), snapshots, unknown)


def _read_modified_time(path: Path) -> float | None:
    """A file's modification time, or None when it was removed meanwhile, as hooks remove pending files."""
    try:
        modified = path.stat().st_mtime
    except FileNotFoundError:
        return None
    else:
        return modified


def _align_parallel_writes(own: Iterable[WriteEvent]) -> list[WriteEvent]:
    """This session's writes in the order they ended, each starting where its parallel own writes left the file.

    One session can run calls side by side, and a call takes its before
    snapshot as it starts, so a call that ends later also shows what an
    earlier-ending call wrote meanwhile. Read as they are, the chain would
    show someone else undoing that write and the later call redoing it,
    which tangles the session's own lines with lines of no one.
    """
    aligned: list[WriteEvent] = []

    for event in sorted(own, key=_get_event_end):
        is_parallel = (
            bool(aligned) and aligned[-1].after != event.before and _is_taken_before_own_writes(aligned, event)
        )

        aligned.append(replace(event, before=aligned[-1].after) if is_parallel else event)
    return aligned


def _is_taken_before_own_writes(aligned: Sequence[WriteEvent], event: WriteEvent) -> bool:
    """Whether nothing but own writes that ran during the event changed the file after its before snapshot.

    That holds when the latest own writes, followed back as an unbroken chain
    while each overlaps the event, reach one that started from the event's
    before snapshot. Overlap here takes no margin: a margin would widen what
    counts as the session's own, while a missed overlap only leaves the chain
    as recorded, so the commit asks.
    """
    chain_after = aligned[-1].after

    for previous in reversed(aligned):
        if previous.after != chain_after or not _is_overlapping_within(previous, event, 0.0):
            return False
        elif previous.before == event.before:
            return True
        else:
            chain_after = previous.before
    return False


def _list_unexplained_steps(
    root: str,
    own: Sequence[WriteEvent],
    foreign: Sequence[WriteEvent | UnknownWrite],
    working_text: str,
) -> frozenset[int]:
    """The gaps in this session's chain of writes that no chain of other sessions' writes bridges.

    A gap is what changed after one own write and before the next, or after
    the last one; its step is the number of the own write after it, or one
    past the last. Contents are compared as text, as the line weave compares
    them, so a file that is gone and an empty one are the same. Only another
    session's recorded contents explain a gap: a snapshot that failed or a
    call still running says nothing about what it wrote, so such a gap stays
    unexplained and the commit asks. Writes that ended before this session's
    first one cannot bridge a gap, so their contents are not read.
    """
    ends = [_read_blob(root, event.after) for event in own]
    starts = [*(_read_blob(root, event.before) for event in own[1:]), working_text]
    edges = [
        (_read_blob(root, event.before), _read_blob(root, event.after), event.end)
        for event in foreign
        if isinstance(event, WriteEvent) and event.end >= own[0].start
    ]
    gaps = enumerate(zip(own, ends, starts), start=2)

    return frozenset(
        step
        for step, (previous, end, start) in gaps
        if end != start and not _is_bridged(end, start, previous.start, edges)
    )


def _is_bridged(start: str, target: str, since: float, edges: Sequence[tuple[str, str, float]]) -> bool:
    """Whether other sessions' writes, as (before, after, end) texts, that ended after `since` lead from one content
    to another."""
    reached = {start}
    frontier = [start]

    while frontier:
        content = frontier.pop()
        following = [after for before, after, end in edges if before == content and end >= since]
        new = [after for after in following if after not in reached]

        reached.update(new)
        frontier.extend(new)
    return target in reached


def _make_recorded_write(event: WriteEvent, foreign: Sequence[WriteEvent | UnknownWrite]) -> RecordedWrite:
    before = _read_blob(event.root, event.before)
    after = _read_blob(event.root, event.after)

    return RecordedWrite(before, after, _is_contested(event, foreign))


def _is_contested(event: WriteEvent, foreign: Sequence[WriteEvent | UnknownWrite]) -> bool:
    """Whether another session may have written part of this write's change.

    An Edit or Write that its own reported change explains exactly is the
    session's whatever ran meanwhile, and one with anything else in its
    snapshots is not. Any other write is contested when another session's
    write, unknown write, or running call overlaps its time.
    """
    if event.exact is not None:
        return not event.exact
    else:
        return any(_is_overlapping(event, other) for other in foreign)


def _is_overlapping(event: WriteEvent, other: WriteEvent | UnknownWrite) -> bool:
    """Whether two writes' times overlap, widened by a margin when either is a record whose times do not enclose
    its snapshots (an earlier record format, or a running call's pending file)."""
    is_enclosed = min(event.record_format, other.record_format) >= RECORD_FORMAT
    margin = 0.0 if is_enclosed else _OVERLAP_MARGIN_SECONDS

    return _is_overlapping_within(event, other, margin)


def _is_overlapping_within(event: WriteEvent, other: WriteEvent | UnknownWrite, margin: float) -> bool:
    return other.start < event.end + margin and event.start < other.end + margin


def _get_event_end(event: WriteEvent) -> float:
    return event.end


def _read_snapshot_blob(snapshot: RepositorySnapshot, path: str) -> str | None:
    if path in snapshot.blobs:
        return snapshot.blobs[path]
    else:
        return _read_committed_blob(snapshot.root, snapshot.head, path)


def _expand_known_command(command: str, cwd: str | None) -> list[CommandCall] | None:
    """The calls a command line runs, or None when it cannot be followed, so it is taken as one that may write.

    Any failure of the expansion counts, since leaving a command unexpanded
    only keeps today's snapshots.
    """
    try:
        calls = expand_shell_command(command, cwd, _get_config_directory())
    except Exception:
        return None
    else:
        return calls


def _is_tree_keeping(call: CommandCall) -> bool:
    """Whether a call leaves every working tree as it was, judged only from programs whose effect is known.

    A call that sets or exports where git and gh read their configuration
    does not count, since a push or gh call it reaches may then run what
    that configuration names.
    """
    moves_configuration = any(name in _CONFIGURATION_LOCATION_VARIABLES for name, _ in _list_call_variables(call))

    if call.writes_file or call.dynamic_name or moves_configuration or not _is_git_environment_safe(call):
        return False
    elif call.argv:
        return _is_tree_keeping_program(call)
    else:
        return True


def _is_tree_keeping_program(call: CommandCall) -> bool:
    """A local script can carry a reading program's name, so a program counts by its bare name, and the
    commit wrapper and the report only as the installed scripts beside this module."""
    argv = call.argv
    cwd = call.cwd
    git_command = parse_git_command(argv, cwd)
    script, script_arguments = _read_installed_script(argv, cwd)

    if git_command is not None:
        return _is_tree_keeping_git(git_command) and os.sep not in argv[0]
    elif argv[0] == _GH_PROGRAM:
        return _is_tree_keeping_gh(call)
    elif script == _COMMIT_WRAPPER_NAME:
        return cwd is not None
    elif script == _SESSION_WRITES_NAME:
        return script_arguments[:1] == [_REPORT_COMMAND]
    else:
        return os.sep not in argv[0] and argv[0] in _TREE_KEEPING_PROGRAMS


def _is_tree_keeping_git(git_command: GitCommand) -> bool:
    """Read and index subcommands, with only options that cannot touch the working tree or run another program."""
    subcommand = git_command.subcommand
    arguments = git_command.arguments
    is_safe_config = all(key.startswith(_SAFE_GIT_CONFIG_PREFIXES) for key in git_command.config_values)
    runs_program = any(argument.startswith(_PROGRAM_RUNNING_GIT_OPTIONS) for argument in arguments)

    if subcommand in _TREE_KEEPING_GIT_OPTIONS:
        return is_safe_config and _has_only_options(arguments, _TREE_KEEPING_GIT_OPTIONS[subcommand])
    elif subcommand == _PUSH_SUBCOMMAND:
        return is_safe_config and _is_tree_keeping_push(git_command)
    else:
        return is_safe_config and not runs_program and subcommand in _TREE_KEEPING_GIT_SUBCOMMANDS


def _is_tree_keeping_push(git_command: GitCommand) -> bool:
    """Whether a push can run no program on this computer, so it changes only the remote and remote-tracking refs.

    git 2.54's githooks, git-push, and git-config pages name what a push may
    run here: the pre-push and reference-transaction hooks, the receiving
    side's hooks when the remote is a local folder, --receive-pack and
    --exec, a remote helper (`<transport>::`, remote.<name>.vcs), the ssh
    command, a password prompt program, signing, submodule pushes, and
    credential helpers. A push that may use any of them, or with options or
    a remote this does not know, keeps its snapshot.
    """
    root = find_repository_root(git_command.work_dir) if git_command.work_dir else None
    options, positionals = _split_push_arguments(git_command.arguments)
    has_only_config_options = len(git_command.global_options) == 2 * len(git_command.config_values)
    config = _read_git_config(root) if root else None

    if root and config is not None:
        remotes = _list_remote_names(config)
        defaults = [value for key, values in config.items() if _DEFAULT_REMOTE_KEY.fullmatch(key) for value in values]

        return (
            has_only_config_options
            and bool(remotes)
            and all(option.split("=", 1)[0] in _TREE_KEEPING_PUSH_OPTIONS for option in options)
            and (not positionals or positionals[0] in remotes)
            and all(default in remotes for default in defaults)
            and not _has_hooks(root, _PUSH_HOOK_NAMES)
            and all(_is_network_remote(root, remote) for remote in remotes)
            and not _has_program_running_push_config(config)
        )
    else:
        return False


def _split_push_arguments(arguments: list[str]) -> tuple[list[str], list[str]]:
    """A push's options and its repository and refspecs, skipping the values of options that take one."""
    options: list[str] = []
    positionals: list[str] = []
    index = 0

    while index < len(arguments):
        argument = arguments[index]

        if argument.startswith("-"):
            options.append(argument)
            index += 2 if argument in _PUSH_OPTIONS_WITH_ARGUMENT else 1
        else:
            positionals.append(argument)
            index += 1
    return options, positionals


def _read_git_config(root: str) -> dict[str, list[str]] | None:
    """Every value git reads in the repository, by key, or None when git cannot list them."""
    output = _try_git(root, "config", "-z", "--list")

    if output is not None:
        entries = [entry.partition("\n") for entry in decode_content(output).split("\0") if entry]
        config: dict[str, list[str]] = {}

        for key, _, value in entries:
            config.setdefault(key, []).append(value)
        return config
    else:
        return None


def _list_remote_names(config: dict[str, list[str]]) -> list[str]:
    return [
        key[len(_REMOTE_PREFIX) : -len(_REMOTE_URL_SUFFIX)]
        for key in config
        if key.startswith(_REMOTE_PREFIX) and key.endswith(_REMOTE_URL_SUFFIX)
    ]


def _is_network_remote(root: str, remote: str) -> bool:
    """Whether every push address of a remote, after git's rewriting, is a network one and not a remote helper."""
    output = _try_git(root, "remote", "get-url", "--push", "--all", remote)
    urls = decode_content(output).split() if output else []

    return bool(urls) and all(_NETWORK_URL_PATTERN.fullmatch(url) and "::" not in url for url in urls)


def _has_program_running_push_config(config: dict[str, list[str]]) -> bool:
    signing = config.get(_PUSH_SIGN_KEY, [])
    submodules = config.get(_SUBMODULE_PUSH_KEY, [])
    is_signing = any(value.lower() not in _FALSE_CONFIG_VALUES for value in signing)
    is_pushing_submodules = any(value.lower() in _SUBMODULE_PUSH_VALUES for value in submodules)
    has_program_key = any(
        key in _PROGRAM_RUNNING_PUSH_KEYS
        or (key.startswith(_REMOTE_PREFIX) and key.endswith(_PROGRAM_RUNNING_REMOTE_SUFFIXES))
        for key in config
    )
    helpers = [value for key, values in config.items() if _CREDENTIAL_HELPER_KEY.fullmatch(key) for value in values]

    return is_signing or is_pushing_submodules or has_program_key or not all(map(_is_known_credential_helper, helpers))


def _is_known_credential_helper(helper: str) -> bool:
    """The helpers git ships, an empty value that resets the list, and GitHub CLI's, which keep credentials only."""
    return helper in _KNOWN_CREDENTIAL_HELPERS or _GH_CREDENTIAL_HELPER_PATTERN.fullmatch(helper) is not None


def _is_tree_keeping_gh(call: CommandCall) -> bool:
    """GitHub CLI sign-in commands, and commands that only show pull requests and runs, which write only gh's own
    configuration and the keychain."""
    group = call.argv[1] if len(call.argv) > 1 else ""
    command = call.argv[2] if len(call.argv) > 2 else ""

    return group == _GH_AUTH_GROUP or command in _TREE_KEEPING_GH_COMMANDS.get(group, frozenset())


def _has_only_options(arguments: list[str], allowed: frozenset[str]) -> bool:
    """Whether every option before the pathspec separator is one of allowed; abbreviated and bundled options are not."""
    options = arguments[: arguments.index(_PATHSPEC_SEPARATOR)] if _PATHSPEC_SEPARATOR in arguments else arguments

    return all(option in allowed for option in options if option.startswith("-"))


def _list_call_variables(call: CommandCall) -> list[tuple[str, str]]:
    """The variables a call sets for itself or exports."""
    exported = [_split_variable(argument) for argument in call.argv[1:]] if call.argv[:1] == ["export"] else []

    return [*call.assignments, *exported]


def _is_git_environment_safe(call: CommandCall) -> bool:
    """Whether the git variables a call sets, or exports, only pick an index or a display setting."""
    variables = _list_call_variables(call)
    config_keys = [value for name, value in variables if _GIT_CONFIG_KEY_PATTERN.fullmatch(name)]
    unsafe = [
        name
        for name, _ in variables
        if name.startswith(_GIT_ENVIRONMENT_PREFIX)
        and name not in _SAFE_GIT_VARIABLES
        and not _GIT_CONFIG_KEY_PATTERN.fullmatch(name)
        and not _GIT_CONFIG_VALUE_PATTERN.fullmatch(name)
    ]

    return not unsafe and all(key.startswith(_SAFE_GIT_CONFIG_PREFIXES) for key in config_keys)


def _split_variable(argument: str) -> tuple[str, str]:
    name, _, value = argument.partition("=")

    return name, value


def _read_installed_script(argv: list[str], cwd: str | None) -> tuple[str, list[str]]:
    """The installed script beside this module that a call runs, directly or as `python3 <script>`, and its arguments.

    Any other program, or a relative path from an unknown folder, gives an empty name.
    """
    is_interpreted = argv[0] in _PYTHON_NAMES and len(argv) > 1
    script = os.path.expanduser(argv[1] if is_interpreted else argv[0])
    arguments = argv[2:] if is_interpreted else argv[1:]
    is_located = os.sep in script and (os.path.isabs(script) or cwd is not None)
    location = Path(cwd or "", script).resolve().parent if is_located else None

    if location == _SCRIPTS_DIRECTORY:
        return os.path.basename(script), arguments
    else:
        return "", arguments


def _find_hooked_commit_root(call: CommandCall) -> str | None:
    """The repository a commit-wrapper call commits in, when git commit runs hooks there that may change files."""
    is_commit = bool(call.argv) and _read_installed_script(call.argv, call.cwd)[0] == _COMMIT_WRAPPER_NAME
    root = find_repository_root(call.cwd) if is_commit and call.cwd else None

    return root if root and _has_hooks(root, _COMMIT_HOOK_NAMES) else None


def _has_hooks(root: str, names: Sequence[str]) -> bool:
    """Whether git may run one of these hooks in the repository; a hooks folder git cannot name counts as having one."""
    output = _try_git(root, "rev-parse", "--git-path", _HOOKS_DIRECTORY_NAME)
    hooks = Path(root, decode_content(output).strip()) if output is not None else None

    return hooks is None or any(os.access(hooks / name, os.X_OK) for name in names)


def _find_existing_directory(path: str) -> str:
    directory = path if os.path.isdir(path) else os.path.dirname(path)

    while directory and not os.path.isdir(directory):
        directory = os.path.dirname(directory)
    return directory or os.sep


def _take_repository_snapshot(root: str) -> RepositorySnapshot:
    status = _run_git(root, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths = _parse_status_paths(decode_content(status))

    return RepositorySnapshot(root, _read_head(root), _store_files(root, paths), covers_repository=True)


def _take_file_snapshot(root: str, path: str) -> RepositorySnapshot:
    return RepositorySnapshot(root, _read_head(root), _store_files(root, [path]), covers_repository=False)


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
