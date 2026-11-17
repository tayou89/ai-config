#!/usr/bin/env python3
"""claude-commit.py — wrapper enforcing git-commit skill checklist.

Usage:
    claude-commit.py [--amend] [--no-stage | --own-lines] [--expect-head <sha>] [--reviewed]
                     [--include-unrecorded <file>]... "<message>" [<file1> ...]

Validates message format (subject prefix/length, body line length/count,
bullet count, no internal terms), runs amend check, stages exact files,
verifies no extras, refuses staged .env and credential files and added
lines that hold a live-looking token (see secret_files.py), refuses a
large code change without --reviewed, prints verification table, then
commits with
CLAUDE_SKILL_GIT_COMMIT=1 marker. Subprocess git commit does NOT trigger
Claude's PreToolUse hook (hooks fire at tool boundary only), so the
checklist becomes the single enforced path.

Use --no-stage when the index is already prepared by a prior git
operation (e.g. merge --no-commit, cherry-pick) and re-staging via
`git add` would fail (deleted/renamed paths) or override the prepared
state. With --no-stage, file args are not required; when given, the
staged paths are still checked against them.

An index records file contents, not the commit it was read from, so an
index built before another session's commit carries that commit's files
in their old state, and committing it reverts them. --expect-head names
the commit the index was built from; the wrapper refuses when HEAD is
another commit, like `git update-ref <ref> <new> <old>` and
`git push --force-with-lease=<ref>:<expect>`. A private index
(GIT_INDEX_FILE) requires it and the file list. After committing from
it, the wrapper points the shared index's entries for those files at the
new commit, as --private-index does. After committing, the wrapper checks that git commit replaced the expected HEAD and undoes the
commit with the same compare-and-swap when HEAD moved while it started.
Another session can commit the moment git commit returns, so reading HEAD
again could name that session's commit and undo it. So the wrapper marks
its git commit with GIT_REFLOG_ACTION, as git's own rebase and pull do,
reads the commit from the reflog entry that carries the mark and the
subject, and moves HEAD back only while HEAD is still that commit.

A pre-commit check can run for minutes, and git commit refuses to move
HEAD when another commit landed meanwhile, so the slower of two sessions
used to throw its check away. The wrapper therefore holds this
checkout's lock (integration_lock.py; in the original checkout it is the
lock the integration tools hold) from the HEAD check until its commit
has landed, as a commit queue does: a later commit waits instead of
checking a base about to be replaced. When the commits that landed
meanwhile sit on top of the expected HEAD on the same branch and leave
the listed files as they were, the wrapper rebuilds the private index on the new HEAD (a
hand-built one by git's own three-way index merge) and commits there,
so the check runs once, on the tree that is committed, as a merge queue
checks the combined result. Otherwise it refuses as before.

Commits need no user approval (user decision, 2026-11-09), so a large
change gets a checklist review instead: when the staged change touches
LARGE_COMMIT_LINES or more lines outside documents and lock files, the
wrapper requires --reviewed, which the session passes only after that
review has run.

Git cannot tell which session wrote a line, so a whole-file add or a
hand-built partial file can carry another session's uncommitted lines.
In a Claude session whose write record covers the whole session
(session_writes.py), the wrapper refuses staged lines that record does
not show as the session's own, and --own-lines stages each file as the
base plus only those lines. It reads every listed file before refusing,
so one refusal names them all and one rerun, with one dialog, settles
the commit.
--include-unrecorded <file> commits that file whole after the user has
seen its lines; the shell guard asks before such a call and keeps the
content its dialog showed. The wrapper commits exactly that content, so
a call still running over the file cannot slip a later change in, and
refuses a file that changed after the dialog, which the user has not seen.

Sessions that share a checkout commit as the same person, so --amend can
rewrite a commit another session landed a moment earlier: --expect-head
does not stop it, since one command can read that commit as its base. So
the wrapper adds each commit it makes to its session's list
(session_commits.py) and refuses --amend unless HEAD is on that list and
on no remote-tracking branch, as Perforce lets only a changelist's owner
change it and Mercurial refuses to amend a public changeset.

A repository must say how its work reaches its base branch before anything
is committed there, or sessions fall back to committing straight to it
unseen (user decision, 2026-11-17). So the wrapper commits nothing in a
checkout without a readable .agent-workflow.json (workflow_policy.py) and
tells the session to ask the user which mode the repository uses.

Exit codes:
    0  success (commit created)
    1  git error
    2  message validation failure
    3  staging mismatch
    4  usage error
    5  credential file or live-looking token staged
    6  HEAD moved since the index was built, and the commits since change
       a listed file or the index cannot be rebuilt on them (nothing
       committed)
    7  large code change without --reviewed (nothing committed)
    8  staged lines this session's write record does not show as its own,
       own lines that cannot be separated from them, or an
       --include-unrecorded file that changed after the shell guard's
       dialog showed it (nothing committed)
    9  --amend of a commit this session did not make, or one already
       pushed (nothing committed)
   10  another commit or integration held this checkout's lock longer
       than the wrapper waits (nothing committed)
   11  the repository records no Git workflow, or its .agent-workflow.json
       cannot be read (nothing committed)
"""

from __future__ import annotations

import contextlib
import functools
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from commit_arguments import (
    EXPECT_HEAD_OPTION,
    INCLUDE_UNRECORDED_OPTION,
    OWN_LINES_OPTION,
    PRIVATE_INDEX_OPTION,
    CommitArguments,
    CommitArgumentsError,
    parse_commit_arguments,
)
from integration_commands import CommandError, announce_wait, is_ancestor, list_worktree_users
from integration_lock import DEFAULT_WAIT_SECONDS, LockTimeoutError, hold_integration_lock
from own_lines import ContestedLine, OwnCommitContent, build_own_content, list_foreign_lines
from secret_files import STAGED_DIFF_ARGUMENTS, find_added_secret_tokens, get_credential_reason
from session_commits import add_session_commit, is_session_commit
from session_writes import (
    ContestCause,
    IncludeChangedError,
    SessionWritesError,
    SnapshotScope,
    approve_include,
    decode_content,
    describe_contest_cause,
    find_contest_cause,
    find_repository_paths,
    finish_call_record,
    has_commit_hooks,
    has_other_session_writes,
    has_record,
    is_line_tracked,
    is_record_complete,
    is_whole_file_own,
    load_file_history,
    read_index_blobs,
    read_session_id,
    start_call_record,
    store_text,
)
from workflow_policy import (
    POLICY_FILE,
    PolicyError,
    WorkflowMode,
    WorkflowPolicy,
    read_checkout_policy,
    read_original_checkout,
    read_worktree_creation_time,
)

PRIVATE_INDEX_VARIABLE = "GIT_INDEX_FILE"
PRIVATE_INDEX_PREFIX = "claude-commit-index-"
HEAD_REVISION = "HEAD"
UNDO_REFLOG_MESSAGE = "claude-commit: undo a commit built on a moved HEAD"
REFLOG_ACTION_VARIABLE = "GIT_REFLOG_ACTION"
COMMIT_REFLOG_ACTION = "commit"
AMEND_REFLOG_ACTION = "commit (amend)"
REFLOG_MARK_BYTES = 8
COMMIT_HOOK_TOOL = "commit hook"
COMMIT_HOOK_CALL_PREFIX = "commit-hooks-"
COMMIT_HOOK_RECORD_NOTE = "Note: the commit hooks' writes could not be recorded ({error})."
REFLOG_ENTRY_FORMAT = "--format=%H %gs"
REFLOG_ENTRY_RE = re.compile(r"([0-9a-f]{40,64}) (.*)")
REFLOG_SPACE_RE = re.compile(r"[\t\n\r ]+")
SHORT_SHA_LENGTH = 7
OBJECT_NAME_RE = re.compile(r"[0-9a-f]{7,64}")
VALID_PREFIXES = ("Feat", "Fix", "Refactor", "Chore", "Merge")
INTERNAL_TERM_RE = re.compile(r"\b(step|phase|pattern)\s+[0-9]+\b", re.IGNORECASE)
DOCUMENT_SECTION_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z_]{2,}(?:\.md)?\s+[0-9]{1,2}(?:-[0-9]{1,3})+(?![0-9])")
MAX_SUBJECT_LENGTH = 72
MAX_BODY_LINE_LENGTH = 72
MAX_BODY_NONBLANK_LINES = 8
MIN_BULLETS = 3
MAX_BULLETS = 5
LARGE_COMMIT_LINES = 200
UNCOUNTED_SUFFIXES = (".md", ".txt", ".lock")
UNCOUNTED_NAMES = frozenset({"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "go.sum"})
BINARY_NUMSTAT = "-"
PATH_LIST_SEPARATOR = "\0"
NUMSTAT_SEPARATOR = PATH_LIST_SEPARATOR
READABLE_PATHS_OPTIONS = ("-c", "core.quotepath=false")
NUMSTAT_COLUMN_SEPARATOR = "\t"

EXIT_GIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_STAGING_MISMATCH = 3
EXIT_USAGE = 4
EXIT_CREDENTIAL_FILE = 5
EXIT_HEAD_MOVED = 6
EXIT_REVIEW_MISSING = 7
EXIT_FOREIGN_LINES = 8
EXIT_AMEND_TARGET = 9
EXIT_LOCK_TIMEOUT = 10
EXIT_NO_WORKFLOW = 11
NO_WORKFLOW_MESSAGE = "{root} records no Git workflow in {file}, so nothing was committed. Ask the user which workflow this repository uses and record it there as the git-commit skill's 'Repository Workflow' describes: {modes}."
UNREADABLE_WORKFLOW_MESSAGE = "{error}; nothing was committed. Fix {path}, then commit again."
NOT_REPOSITORY_MESSAGE = "FAIL: this folder is not inside a Git repository, so nothing was committed"
MOVED_BASE_NOTE = "HEAD moved from {old} to {new}; the commits since leave your files as they were, so the index is rebuilt on {new} and checked there"
REMOTE_BRANCH_FORMAT = "--format=%(if)%(symref)%(then)%(else)%(refname:short)%(end)"
NO_SESSION_REASON = "cannot be shown to be this session's: only a Claude session lists the commits it makes"
OTHER_COMMIT_REASON = "this session did not make through this wrapper (another session's or a person's commit, or one made before the wrapper listed commits)"
SEQUENCER_STATE_FILES = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
REGULAR_FILE_MODE = "100644"
EXECUTABLE_FILE_MODE = "100755"
MAX_SHOWN_FOREIGN_LINES = 12
OWN_WORKTREE_NOTE = "this session's own worktree: no other session or process works in it"


@dataclass(frozen=True)
class ReflogEntry:
    commit: str
    message: str


@dataclass(frozen=True)
class ApprovedContent:
    """An --include-unrecorded file's content as the shell guard's dialog showed it, and its path in the repository,
    which `git update-index --cacheinfo` takes from the repository's top folder whatever the current folder is."""

    repository_path: str
    blob: str | None


@dataclass(frozen=True)
class OwnLinesReading:
    """One file's own content, or why the write record cannot separate it."""

    path: str
    content: OwnCommitContent | None
    problem: str | None


@dataclass(frozen=True)
class MovedBase:
    """The HEAD the index was built from, and the newer HEAD the wrapper commits on instead."""

    old: str
    new: str


@dataclass(frozen=True)
class MadeCommit:
    """The commit git commit made, and the HEAD the reflog entry just before it names, which an amend replaced."""

    commit: str
    previous_head: str | None


def fail(message: str, code: int) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def run_git(
    args: Sequence[str],
    capture_output: bool = False,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=False, text=True, capture_output=capture_output, env=environment)


def contains_internal_term(text: str) -> bool:
    return bool(INTERNAL_TERM_RE.search(text) or DOCUMENT_SECTION_RE.search(text))


def validate_message(message: str) -> tuple[int, int, int, int]:
    lines = message.split("\n")
    if not lines or not lines[0]:
        fail("FAIL: empty subject", EXIT_VALIDATION)
    subject = lines[0]
    subject_length = len(subject)
    if subject_length > MAX_SUBJECT_LENGTH:
        fail(
            f"FAIL: subject {subject_length} chars > {MAX_SUBJECT_LENGTH}\n"
            f"  subject: {subject}",
            EXIT_VALIDATION,
        )
    if not any(subject.startswith(f"{prefix}: ") for prefix in VALID_PREFIXES):
        prefixes = "|".join(f"{prefix}:" for prefix in VALID_PREFIXES)
        fail(
            f"FAIL: subject must start with {prefixes}\n  got: {subject}",
            EXIT_VALIDATION,
        )
    if len(lines) >= 2 and lines[1]:
        fail("FAIL: line 2 must be blank", EXIT_VALIDATION)
    body_lines = lines[2:] if len(lines) > 2 else []
    body_max_length = max((len(line) for line in body_lines), default=0)
    if body_max_length > MAX_BODY_LINE_LENGTH:
        offenders = [
            f"{i + 3}: {len(line)} chars: {line}"
            for i, line in enumerate(body_lines)
            if len(line) > MAX_BODY_LINE_LENGTH
        ]
        fail(
            f"FAIL: body line max {body_max_length} chars > {MAX_BODY_LINE_LENGTH}\n"
            + "\n".join(offenders),
            EXIT_VALIDATION,
        )
    body_nonblank_count = sum(1 for line in body_lines if line.strip())
    if body_nonblank_count < 1:
        fail("FAIL: empty body", EXIT_VALIDATION)
    if body_nonblank_count > MAX_BODY_NONBLANK_LINES:
        fail(
            f"FAIL: body has {body_nonblank_count} non-blank lines, "
            f"max {MAX_BODY_NONBLANK_LINES}",
            EXIT_VALIDATION,
        )
    bullet_count = sum(1 for line in body_lines if line.startswith("- "))
    if bullet_count < MIN_BULLETS:
        fail(
            f"FAIL: only {bullet_count} bullets, need {MIN_BULLETS}-{MAX_BULLETS}",
            EXIT_VALIDATION,
        )
    if bullet_count > MAX_BULLETS:
        fail(f"FAIL: {bullet_count} bullets, max {MAX_BULLETS}", EXIT_VALIDATION)
    if contains_internal_term(message):
        offenders = [
            f"{i + 1}: {line}"
            for i, line in enumerate(lines)
            if contains_internal_term(line)
        ]
        fail(
            "FAIL: internal terms (Step N / Phase N / Pattern N, or a document section number such as HANDOFF 0-16) detected\n"
            + "\n".join(offenders),
            EXIT_VALIDATION,
        )
    return subject_length, body_max_length, body_nonblank_count, bullet_count


def print_header() -> None:
    print("====================")
    print(" claude-commit.py")
    print("====================")


def stage_files(files: Sequence[str], approved: dict[str, ApprovedContent]) -> None:
    root = read_repository_root()

    print("=== Staging files ===")
    for path in files:
        stage_listed_file(root, path, approved, {})


def stage_file(path: str) -> None:
    result = run_git(["add", "-A", "--", path])
    if result.returncode != 0:
        fail(f"FAIL: git add failed for {path}", EXIT_GIT_ERROR)
    print(f"  + {path}")


def split_path_list(output: str) -> list[str]:
    """Paths from a `-z` listing, which names each path as it is instead of quoting non-ASCII bytes in octal."""
    return [path for path in output.split(PATH_LIST_SEPARATOR) if path]


def verify_staging(expected_files: Sequence[str]) -> None:
    actual_proc = run_git(
        ["diff", "--cached", "--name-only", "-z"],
        capture_output=True,
    )
    if actual_proc.returncode != 0:
        fail("FAIL: git diff --cached failed", EXIT_GIT_ERROR)
    expected = set(expected_files)
    actual = set(split_path_list(actual_proc.stdout))
    extra = actual - expected
    if extra:
        sys.stderr.write("FAIL: files staged but not in argv:\n")
        for path in sorted(extra):
            sys.stderr.write(f"{path}\n")
        sys.stderr.write("\nResolve: git restore --staged <unwanted-files>\n")
        sys.exit(EXIT_STAGING_MISMATCH)


def read_revision(revision: str) -> str | None:
    revision_proc = run_git(["rev-parse", "--verify", "--quiet", revision], capture_output=True)

    if revision_proc.returncode == 0:
        return revision_proc.stdout.strip()
    else:
        return None


def resolve_expected_head(expected_head_argument: str | None) -> str | None:
    if expected_head_argument is None:
        return read_revision(HEAD_REVISION)
    else:
        check_object_name(expected_head_argument)
        expected_head = read_revision(f"{expected_head_argument}^{{commit}}")

        if expected_head is not None:
            return expected_head
        else:
            fail(f"FAIL: {EXPECT_HEAD_OPTION} {expected_head_argument} is not a commit in this repository", EXIT_USAGE)


def shorten(commit: str | None) -> str:
    if commit is not None:
        return commit[:SHORT_SHA_LENGTH]
    else:
        return "(no commit)"


def make_commit_range(expected_head: str | None, moved_head: str) -> str:
    if expected_head is not None:
        return f"{expected_head}..{moved_head}"
    else:
        return moved_head


def list_changed_files(commit_range: str, files: Sequence[str]) -> list[str]:
    if files:
        log_arguments = ["log", "-z", "--format=", "--name-only", commit_range, "--", *files]
        changed_proc = run_git(log_arguments, capture_output=True)

        return sorted(set(split_path_list(changed_proc.stdout)))
    else:
        return []


def make_moved_head_message(
    summary_lines: list[str], expected_head: str | None, moved_head: str, files: Sequence[str]
) -> str:
    commit_range = make_commit_range(expected_head, moved_head)
    commits = run_git(["log", "--oneline", commit_range], capture_output=True).stdout.splitlines()
    overlapping_files = list_changed_files(commit_range, files)
    message_lines = [
        *summary_lines,
        "Commits that landed after the index was built (committing it would revert them):",
        *(f"  {commit}" for commit in commits),
        f"Files they changed that you also commit: {', '.join(overlapping_files) or 'none'}",
        f"Resolve: rebuild the index from {moved_head} (`git read-tree {moved_head}` for a private index), recreate partial-file blobs from `git show {moved_head}:<path>`, and rerun with {EXPECT_HEAD_OPTION} {moved_head}",
    ]

    return "\n".join(message_lines)


def make_undo_message(
    expected_head: str | None,
    replaced_head: str | None,
    new_head: str,
    files: Sequence[str],
    undone: bool,
) -> str:
    moved_summary = (
        f"FAIL: HEAD moved from {shorten(expected_head)} to {shorten(replaced_head)} while git commit started"
    )

    if replaced_head is None:
        return f"FAIL: could not read which commit {shorten(new_head)} replaced (it has no parent, or for --amend no HEAD reflog entry before it), so it is not known to be built on {shorten(expected_head)}. Check `git log` before pushing."
    elif undone:
        undone_lines = [
            f"{moved_summary}.",
            f"Its commit {shorten(new_head)} was undone; HEAD is back at {shorten(replaced_head)}.",
        ]

        return make_moved_head_message(undone_lines, expected_head, replaced_head, files)
    else:
        kept_lines = [
            f"{moved_summary}, and another commit already sits on {shorten(new_head)}, so it was not undone.",
            "Do not push; tell the user.",
        ]

        return make_moved_head_message(kept_lines, expected_head, replaced_head, files)


def undo_commit(new_head: str, replaced_head: str) -> bool:
    undo_args = ["update-ref", "-m", UNDO_REFLOG_MESSAGE, HEAD_REVISION, replaced_head, new_head]

    return run_git(undo_args, capture_output=True).returncode == 0


def check_object_name(expected_head_argument: str) -> None:
    if not OBJECT_NAME_RE.fullmatch(expected_head_argument):
        fail(
            f"FAIL: {EXPECT_HEAD_OPTION} takes the sha `git rev-parse HEAD` printed when the index was built, not {expected_head_argument}",
            EXIT_USAGE,
        )


def check_private_index_arguments(expected_head_argument: str | None, files: Sequence[str]) -> None:
    usage_lines = [
        f"FAIL: a private index ({PRIVATE_INDEX_VARIABLE}) needs {EXPECT_HEAD_OPTION} <sha> and the file list.",
        "Record the base with `git rev-parse HEAD`, build the index with `git read-tree <base>`,",
        f"and pass {EXPECT_HEAD_OPTION} <base> with your files, so a commit landing meanwhile is not reverted.",
    ]

    if PRIVATE_INDEX_VARIABLE in os.environ and (expected_head_argument is None or not files):
        fail("\n".join(usage_lines), EXIT_USAGE)


def approve_included_files(arguments: CommitArguments) -> dict[str, ApprovedContent]:
    """The contents of the --include-unrecorded files the user approved, by the path given on the command line.

    The shell guard asks before every such call, so this run is the user's
    approval of what its dialog showed, and the wrapper commits those
    contents rather than the files as they are when staged. With --no-stage
    the prepared index is committed as it is, so the dialog showed the
    index's contents and the approval covers them; nothing is staged from
    it. A session without a record folder has no dialog contents kept, so
    the files count as they are now. It is empty outside a session and when
    the approval cannot be kept; the files are then staged as they are. Only
    regular files and removed ones are staged from a content: a folder, a
    link, or a submodule is staged by `git add` as before.
    """
    session_id = read_session_id()
    included = list(arguments.included_paths)
    scope = find_repository_paths(os.getcwd(), included) if session_id and included else None

    if session_id and scope is not None and arguments.no_stage:
        try_approve_include(session_id, *scope, from_index=True)
        return {}
    elif session_id and scope is not None:
        root, repository_paths = scope
        blobs = try_approve_include(session_id, root, repository_paths, from_index=False)
        pairs = zip(included, repository_paths)

        return {
            argument: ApprovedContent(path, blobs[path])
            for argument, path in pairs
            if path in blobs and is_content_stageable(root, path)
        }
    else:
        return {}


def is_content_stageable(root: str, path: str) -> bool:
    """Whether a file is staged right from its content: a regular file, or one that is gone."""
    file = os.path.join(root, path)

    return not os.path.lexists(file) or (os.path.isfile(file) and not os.path.islink(file))


def try_approve_include(session_id: str, root: str, paths: Sequence[str], from_index: bool) -> dict[str, str | None]:
    """Approve what the dialog showed: the files, or with `from_index` the prepared index this commit takes.

    A relative index path counts from this folder, as the shell guard reads it from the command's folder.
    """
    try:
        index_file = os.environ.get(PRIVATE_INDEX_VARIABLE)
        index_path = os.path.abspath(index_file) if index_file else None
        index_blobs = read_index_blobs(root, paths, index_path) if from_index else None

        return approve_include(session_id, root, paths, index_blobs)
    except IncludeChangedError as error:
        fail(make_changed_include_message(error.paths), EXIT_FOREIGN_LINES)
    except (SessionWritesError, OSError) as error:
        print(f"Note: the approval could not be kept ({error}), so a retry is asked again.", file=sys.stderr)
        return {}


def make_changed_include_message(paths: Sequence[str]) -> str:
    lines = [
        f"FAIL: {', '.join(paths)} changed after the shell guard's dialog showed them, so the user has not seen what would be committed. Nothing was committed.",
        "Resolve: rerun the same command without asking in chat first: the shell guard shows the files as they are now and asks there.",
    ]

    return "\n".join(lines)


def check_private_index_environment(private_index: bool) -> None:
    if private_index and PRIVATE_INDEX_VARIABLE in os.environ:
        fail(
            f"FAIL: {PRIVATE_INDEX_OPTION} makes an index of its own, so run it without {PRIVATE_INDEX_VARIABLE}",
            EXIT_USAGE,
        )


@contextlib.contextmanager
def use_private_index(base: str | None, files: Sequence[str]) -> Iterator[None]:
    """Stage in an index of the wrapper's own, read from base, then remove it and refresh the shared index.

    It does what the shared-checkout recipe did by hand with mktemp,
    read-tree, rm and `git reset`, so a commit is one wrapper call that
    leaves working trees alone, which the write record then does not
    snapshot. The shared index is refreshed on every exit, as the recipe
    did: a commit may exist even when a later check fails.
    """
    descriptor, path = tempfile.mkstemp(prefix=PRIVATE_INDEX_PREFIX)

    os.close(descriptor)
    os.environ[PRIVATE_INDEX_VARIABLE] = path
    try:
        read_index_base(base)
        yield
    finally:
        del os.environ[PRIVATE_INDEX_VARIABLE]
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        refresh_shared_index(files)


@contextlib.contextmanager
def use_caller_index(files: Sequence[str]) -> Iterator[None]:
    """Commit from the index the caller built, then refresh the shared index as use_private_index does.

    Git updates only the index a commit is made from, so without this the
    shared index keeps the committed files' old contents and `git status`
    shows them as changed both staged and unstaged until someone resets them.
    """
    try:
        yield
    finally:
        refresh_shared_index(files)


def use_commit_index(
    arguments: CommitArguments,
    expected_head: str | None,
) -> contextlib.AbstractContextManager[None]:
    if arguments.private_index:
        return use_private_index(expected_head, arguments.files)
    elif PRIVATE_INDEX_VARIABLE in os.environ:
        return use_caller_index(arguments.files)
    else:
        return contextlib.nullcontext()


def read_index_base(base: str | None) -> None:
    read_tree_proc = run_git(["read-tree", base] if base else ["read-tree", "--empty"], capture_output=True)

    if read_tree_proc.returncode != 0:
        fail(f"FAIL: git read-tree could not build the private index from {shorten(base)}", EXIT_GIT_ERROR)


def refresh_shared_index(files: Sequence[str]) -> None:
    """A shared index left at the old commit makes the next commit from it revert these files."""
    shared_environment = {name: value for name, value in os.environ.items() if name != PRIVATE_INDEX_VARIABLE}
    reset_proc = run_git(["reset", "-q", "--", *files], capture_output=True, environment=shared_environment)

    if reset_proc.returncode != 0:
        print(
            f"Warning: the shared index could not be refreshed; run `git reset -q -- {' '.join(files)}`.",
            file=sys.stderr,
        )


@contextlib.contextmanager
def hold_commit_lock() -> Iterator[None]:
    """Hold this checkout's lock from the HEAD check until the commit has landed, as a commit queue does.

    A commit that waits here runs its check once, on the HEAD it then commits on, instead of on a base another commit
    is about to replace. Each checkout has its own Git folder, so worktrees on other branches never wait for each other.
    """
    try:
        with hold_integration_lock(read_git_directory(), DEFAULT_WAIT_SECONDS, announce_wait):
            yield
    except LockTimeoutError as error:
        fail(f"FAIL: {error}. Nothing was committed.", EXIT_LOCK_TIMEOUT)


def read_git_directory() -> Path:
    git_directory_proc = run_git(["rev-parse", "--absolute-git-dir"], capture_output=True)

    if git_directory_proc.returncode == 0:
        return Path(git_directory_proc.stdout.strip())
    else:
        fail(
            "FAIL: git could not find this checkout's Git folder, so the commit lock could not be taken", EXIT_GIT_ERROR
        )


def read_head_branch() -> str | None:
    branch_proc = run_git(["symbolic-ref", "--quiet", HEAD_REVISION], capture_output=True)

    if branch_proc.returncode == 0:
        return branch_proc.stdout.strip()
    else:
        return None


def find_commit_base(arguments: CommitArguments, expected_head: str | None, starting_branch: str | None) -> str | None:
    """The HEAD to commit on: the expected one, or a newer one on the same branch that leaves the listed files alone."""
    moved_base = find_moved_base(arguments, expected_head, starting_branch)

    if moved_base is not None:
        move_index_base(arguments, moved_base)
        print(f"=== {MOVED_BASE_NOTE.format(old=shorten(moved_base.old), new=shorten(moved_base.new))} ===")
        return moved_base.new
    else:
        check_head_unchanged(expected_head, arguments.files)
        return expected_head


def find_moved_base(
    arguments: CommitArguments,
    expected_head: str | None,
    starting_branch: str | None,
) -> MovedBase | None:
    current_head = read_revision(HEAD_REVISION)

    if (
        expected_head is not None
        and current_head is not None
        and read_head_branch() == starting_branch
        and is_index_movable(arguments, expected_head, current_head)
    ):
        return MovedBase(expected_head, current_head)
    else:
        return None


def move_index_base(arguments: CommitArguments, moved_base: MovedBase) -> None:
    """The wrapper reads its own index from the new base later; a caller-built index is moved here."""
    if not arguments.private_index:
        move_caller_index(moved_base, arguments.files)


def move_caller_index(moved_base: MovedBase, files: Sequence[str]) -> None:
    tree_proc = run_git(["write-tree"], capture_output=True)
    caller_tree = tree_proc.stdout.strip()

    if tree_proc.returncode != 0:
        fail_head_moved(moved_base.old, moved_base.new, files)
    elif not merge_index_onto(moved_base, caller_tree):
        run_git(["read-tree", caller_tree], capture_output=True)
        fail_head_moved(moved_base.old, moved_base.new, files)


def merge_index_onto(moved_base: MovedBase, tree: str) -> bool:
    """Git's three-way index merge takes each path from the side that changed it, leaving one both changed unmerged.

    It needs the index to match the new base first, and -i keeps it from looking at the working tree, which holds
    other sessions' edits.
    """
    reset_proc = run_git(["read-tree", moved_base.new], capture_output=True)
    merge_arguments = ["read-tree", "-m", "-i", "--aggressive", moved_base.old, moved_base.new, tree]
    merge_proc = run_git(merge_arguments, capture_output=True) if reset_proc.returncode == 0 else reset_proc
    unmerged_proc = run_git(["ls-files", "--unmerged"], capture_output=True)

    return merge_proc.returncode == 0 and unmerged_proc.returncode == 0 and not unmerged_proc.stdout


def check_head_unchanged(expected_head: str | None, files: Sequence[str]) -> None:
    current_head = read_revision(HEAD_REVISION)

    if current_head is not None and current_head != expected_head:
        fail_head_moved(expected_head, current_head, files)
    elif current_head is None and expected_head is not None:
        fail(f"FAIL: HEAD no longer points to a commit (expected {shorten(expected_head)})", EXIT_HEAD_MOVED)


def fail_head_moved(expected_head: str | None, current_head: str, files: Sequence[str]) -> NoReturn:
    moved_summary = (
        f"FAIL: HEAD moved from {shorten(expected_head)} to {shorten(current_head)} after the index was built"
    )

    fail(make_moved_head_message([moved_summary], expected_head, current_head, files), EXIT_HEAD_MOVED)


def list_net_changed_files(old: str, new: str, files: Sequence[str]) -> list[str]:
    """The listed files the newer commit holds differently, merges included, which a log of each commit can miss."""
    diff_proc = run_git(["diff", "--name-only", "-z", old, new, "--", *files], capture_output=True)

    if diff_proc.returncode == 0:
        return split_path_list(diff_proc.stdout)
    else:
        return list(files)


def is_index_movable(arguments: CommitArguments, expected_head: str, current_head: str) -> bool:
    """Only a new commit on commits that leave the listed files as they were, from an index of this commit alone.

    An amend replaces the expected HEAD itself, and the shared index may hold what a merge or cherry-pick prepared.
    """
    return (
        current_head != expected_head
        and not arguments.amend
        and bool(arguments.files)
        and (arguments.private_index or PRIVATE_INDEX_VARIABLE in os.environ)
        and is_ancestor(Path(read_repository_root()), expected_head, current_head)
        and not list_net_changed_files(expected_head, current_head, arguments.files)
    )


def check_commit_base(made: MadeCommit, expected_head: str | None, amend: bool, files: Sequence[str]) -> None:
    replaced_head = made.previous_head if amend else read_revision(f"{made.commit}^1")

    if replaced_head != expected_head:
        undone = replaced_head is not None and undo_commit(made.commit, replaced_head)

        fail(make_undo_message(expected_head, replaced_head, made.commit, files, undone), EXIT_HEAD_MOVED)


def check_amend_target(amend: bool, expected_head: str | None) -> None:
    if amend and expected_head is not None:
        check_session_commit(expected_head)
        check_commit_unpushed(expected_head)
    elif amend:
        fail("FAIL: --amend needs a commit to rewrite, and HEAD has none", EXIT_AMEND_TARGET)


def check_session_commit(head: str) -> None:
    session_id = read_session_id()

    if session_id is None:
        fail(make_amend_refusal(head, NO_SESSION_REASON), EXIT_AMEND_TARGET)
    elif not is_session_commit(session_id, read_repository_root(), head):
        fail(make_amend_refusal(head, OTHER_COMMIT_REASON), EXIT_AMEND_TARGET)


def check_commit_unpushed(head: str) -> None:
    remote_branches = list_remote_branches(head)

    if remote_branches:
        pushed_reason = f"is already on {', '.join(remote_branches)}, so rewriting it would need a force push"

        fail(make_amend_refusal(head, pushed_reason), EXIT_AMEND_TARGET)


def list_remote_branches(commit: str) -> list[str]:
    """Remote-tracking branches that hold the commit, leaving out symbolic ones such as origin/HEAD.

    A failed lookup stops the amend, since an empty answer would read as unpushed.
    """
    branches_proc = run_git(["branch", "--remotes", "--contains", commit, REMOTE_BRANCH_FORMAT], capture_output=True)

    if branches_proc.returncode == 0:
        return branches_proc.stdout.split()
    else:
        fail(f"FAIL: git could not list the branches that hold {shorten(commit)}, so --amend stopped", EXIT_GIT_ERROR)


def make_amend_refusal(head: str, reason: str) -> str:
    subject = run_git(["log", "-1", "--format=%s", head], capture_output=True).stdout.strip()
    lines = [
        f'FAIL: --amend would rewrite {shorten(head)} "{subject}", which {reason}.',
        "Resolve: commit your change as a new commit on top of it, taking HEAD as the base with `git rev-parse HEAD` in a command of its own.",
    ]

    return "\n".join(lines)


def record_session_commit(commit: str) -> None:
    session_id = read_session_id()

    if session_id is not None and not add_session_commit(session_id, read_repository_root(), commit):
        print(
            f"Note: {shorten(commit)} could not be added to this session's commit list, so --amend will refuse it.",
            file=sys.stderr,
        )


def read_staged_content(path: str) -> bytes | None:
    blob_proc = subprocess.run(
        ["git", "cat-file", "blob", f":{path}"],
        capture_output=True,
        check=False,
    )
    return blob_proc.stdout if blob_proc.returncode == 0 else None


def check_credential_files() -> None:
    staged_proc = run_git(
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=d"],
        capture_output=True,
    )
    if staged_proc.returncode != 0:
        fail("FAIL: git diff --cached failed", EXIT_GIT_ERROR)
    offenders = []
    for path in split_path_list(staged_proc.stdout):
        reason = get_credential_reason(path, functools.partial(read_staged_content, path))
        if reason:
            offenders.append(f"  {path} ({reason})")
    if offenders:
        fail(
            "FAIL: credential files staged:\n"
            + "\n".join(offenders)
            + "\n\nResolve: git restore --staged <file>, add it to .gitignore, "
            "and commit a redacted copy such as .env.example instead",
            EXIT_CREDENTIAL_FILE,
        )


def check_secret_tokens() -> None:
    diff_proc = run_git(STAGED_DIFF_ARGUMENTS, capture_output=True)
    if diff_proc.returncode != 0:
        fail("FAIL: git diff --cached failed", EXIT_GIT_ERROR)
    offenders = find_added_secret_tokens(diff_proc.stdout)
    if offenders:
        fail(
            "FAIL: staged lines hold what looks like a live secret:\n"
            + "\n".join(f"  {offender}" for offender in offenders)
            + "\n\nResolve: remove the value, load it from an environment variable "
            "or an ignored .env file, and stage the file again",
            EXIT_CREDENTIAL_FILE,
        )


def check_large_commit_reviewed(changed_lines: int, reviewed: bool) -> None:
    if changed_lines >= LARGE_COMMIT_LINES and not reviewed:
        message = (
            f"FAIL: {changed_lines} changed lines outside documents (limit {LARGE_COMMIT_LINES}) "
            "need a checklist review first\n"
            "Resolve: run a checklist review of the staged diff against the code and commit rules "
            "(git-commit skill), fix what it finds, then pass --reviewed"
        )
        fail(message, EXIT_REVIEW_MISSING)


def count_code_lines() -> int:
    numstat_proc = run_git(["diff", "--cached", "--numstat", "-z"], capture_output=True)
    if numstat_proc.returncode != 0:
        fail("FAIL: git diff --cached --numstat failed", EXIT_GIT_ERROR)
    return sum(count_numstat_lines(*entry) for entry in parse_numstat(numstat_proc.stdout))


def parse_numstat(output: str) -> list[tuple[str, str, str]]:
    """Read `git diff --numstat -z`: a rename leaves the path empty and gives the old and new paths next."""
    fields = output.split(NUMSTAT_SEPARATOR)
    entries: list[tuple[str, str, str]] = []
    index = 0

    while index < len(fields) and fields[index]:
        added, deleted, path = fields[index].split(NUMSTAT_COLUMN_SEPARATOR, 2)
        is_rename = not path

        entries.append((added, deleted, fields[index + 2] if is_rename else path))
        index += 3 if is_rename else 1
    return entries


def count_numstat_lines(added: str, deleted: str, path: str) -> int:
    is_counted = BINARY_NUMSTAT not in (added, deleted) and not is_uncounted_path(path)

    return int(added) + int(deleted) if is_counted else 0


def is_uncounted_path(path: str) -> bool:
    name = os.path.basename(path)

    return name in UNCOUNTED_NAMES or name.lower().endswith(UNCOUNTED_SUFFIXES)


def read_repository_root() -> str:
    return run_git(["rev-parse", "--show-toplevel"], capture_output=True).stdout.strip()


def check_workflow_recorded() -> None:
    root = read_repository_root()

    if root:
        check_workflow_file(Path(root))
    else:
        fail(NOT_REPOSITORY_MESSAGE, EXIT_GIT_ERROR)


def check_workflow_file(root: Path) -> None:
    """The checkout's own copy counts, so the commit that first records a workflow, or a team's untracked one, passes."""
    modes = ", ".join(mode.value for mode in WorkflowMode)

    try:
        policy = read_recorded_policy(root)
    except PolicyError as error:
        fail(UNREADABLE_WORKFLOW_MESSAGE.format(error=error, path=root / POLICY_FILE), EXIT_NO_WORKFLOW)
    else:
        if policy is None:
            fail(NO_WORKFLOW_MESSAGE.format(root=root, file=POLICY_FILE, modes=modes), EXIT_NO_WORKFLOW)


def read_recorded_policy(root: Path) -> WorkflowPolicy | None:
    """A worktree of a team's repository has no copy of the untracked file, so the original checkout's copy counts too."""
    policy = read_checkout_policy(root)
    original = read_original_checkout(root)

    if policy is None and original is not None:
        return read_checkout_policy(original)
    else:
        return policy


def read_base_text(expected_head: str | None, path: str) -> str:
    """Base content as text that keeps its line endings and bytes, matching session_writes."""
    base_args = ["git", "cat-file", "blob", f"{expected_head}:{path}"]
    base_proc = subprocess.run(base_args, capture_output=True, check=False) if expected_head else None

    return decode_content(base_proc.stdout) if base_proc is not None and base_proc.returncode == 0 else ""


def read_base_blob(expected_head: str | None, path: str) -> str | None:
    return read_revision(f"{expected_head}:{path}") if expected_head else None


def read_own_content(session_id: str, root: str, path: str, base: str) -> OwnCommitContent | None:
    """Raises SessionWritesError when the record cannot be read back."""
    history = load_file_history(session_id, root, path)

    if history is not None:
        return build_own_content(base, history)
    else:
        return None


def read_own_lines(session_id: str, root: str, path: str, expected_head: str | None) -> OwnLinesReading:
    """One file's own content, or the problem that keeps it out, so every file is read before any is refused."""
    try:
        own = read_own_content(session_id, root, path, read_base_text(expected_head, path))
        problem = describe_own_content_problem(session_id, root, path, own)
    except SessionWritesError as error:
        return OwnLinesReading(path, None, make_unreadable_record_message(path, error))
    else:
        return OwnLinesReading(path, own, problem)


def describe_own_content_problem(
    session_id: str, root: str, path: str, own: OwnCommitContent | None
) -> str | None:
    if own is None:
        return make_unrecorded_message(path)
    elif own.entangled:
        return make_entangled_message(path, own.entangled)
    elif own.contested:
        return make_contested_message(path, own.contested, find_contest_cause(session_id, root, path))
    else:
        return None


def make_unreadable_record_message(path: str, error: SessionWritesError) -> str:
    return f"FAIL: the write record for {path} cannot be read back ({error}), so its lines cannot be told apart."


def make_foreign_lines_report(problems: Sequence[str], paths: Sequence[str], suggest_own_lines: bool) -> str:
    """Every file's problem, then one way to resolve them all, so the user sees one dialog for the commit."""
    flags = " ".join(f"{INCLUDE_UNRECORDED_OPTION} {path}" for path in paths)
    own_lines = f"rerun with {OWN_LINES_OPTION} to stage only your lines, or " if suggest_own_lines else ""
    resolve = f"Resolve: {own_lines}rerun once with {flags} without asking in chat first: the shell guard shows the user every listed file's lines in one dialog and asks there. If another session wrote tangled lines and can commit first, message it and wait. If the user declines, leave those files out."

    return "\n".join([*problems, resolve])


def read_index_mode(root: str, path: str) -> str:
    """The mode of a path in the repository, read from the index or, for a new file, from the file."""
    entry = run_git(["-C", root, "ls-files", "-s", "--", path], capture_output=True).stdout.split()

    if entry:
        return entry[0]
    else:
        return EXECUTABLE_FILE_MODE if os.access(os.path.join(root, path), os.X_OK) else REGULAR_FILE_MODE


def stage_changes(arguments: CommitArguments, expected_head: str | None, approved: dict[str, ApprovedContent]) -> None:
    session_id = read_session_id()

    if arguments.no_stage:
        print("=== Staging skipped (--no-stage) ===")
    elif arguments.own_lines and session_id is not None and is_own_worktree(session_id, read_repository_root()):
        print(f"=== {OWN_WORKTREE_NOTE}: {OWN_LINES_OPTION} stages whole files ===")
        stage_files(arguments.files, approved)
    elif arguments.own_lines and session_id is not None and is_record_complete(session_id):
        stage_own_lines(session_id, arguments.files, arguments.included_paths, approved, expected_head)
    elif arguments.own_lines:
        fail(
            f"FAIL: {OWN_LINES_OPTION} needs a write record that covers this whole session, which only sessions started after the record hook was installed have.\n"
            f"Resolve: commit without {OWN_LINES_OPTION}; for a file that also holds another session's uncommitted edits, build its content from `git show <base>:<path>` plus only your change, stage it with `git hash-object -w` and `git update-index --cacheinfo`, and run with --no-stage.",
            EXIT_USAGE,
        )
    else:
        stage_files(arguments.files, approved)
    print("=== Staged changes ===")
    run_git([*READABLE_PATHS_OPTIONS, "diff", "--cached", "--stat"])


def stage_own_lines(
    session_id: str,
    files: Sequence[str],
    included: Sequence[str],
    approved: dict[str, ApprovedContent],
    expected_head: str | None,
) -> None:
    """Read every file's own lines first and refuse them all at once, so one rerun and one dialog settle the commit."""
    root = read_repository_root()
    separated = [path for path in files if path not in included and is_line_tracked(root, path)]
    readings = [read_own_lines(session_id, root, path, expected_head) for path in separated]
    problems = [reading.problem for reading in readings if reading.problem]
    unproven_paths = [reading.path for reading in readings if reading.problem]
    own_contents = {reading.path: reading.content for reading in readings if reading.content is not None}

    if problems:
        fail(make_foreign_lines_report(problems, unproven_paths, suggest_own_lines=False), EXIT_FOREIGN_LINES)
    else:
        print("=== Staging own lines ===")
        for path in files:
            stage_listed_file(root, path, approved, own_contents)


def stage_listed_file(
    root: str, path: str, approved: dict[str, ApprovedContent], own_contents: dict[str, OwnCommitContent]
) -> None:
    """Stage one listed file: as the dialog showed it, as its own lines, or whole as it is now."""
    if path in approved:
        stage_content(root, approved[path].repository_path, approved[path].blob, "as the dialog showed")
    elif path in own_contents:
        stage_own_text(root, path, own_contents[path].text)
    else:
        stage_file(path)


def stage_own_text(root: str, path: str, text: str) -> None:
    is_removed = text == "" and not os.path.exists(os.path.join(root, path))

    stage_content(root, path, None if is_removed else store_text(root, text), "own lines")


def stage_content(root: str, path: str, blob: str | None, note: str) -> None:
    """Put a stored content in the index at a path in the repository, or remove the path for None."""
    if blob is None:
        run_git(["-C", root, "update-index", "--force-remove", "--", path])
        print(f"  - {path} (removed, {note})")
    else:
        stage_blob(root, path, blob, note)


def stage_blob(root: str, path: str, blob: str, note: str) -> None:
    cacheinfo = f"{read_index_mode(root, path)},{blob},{path}"
    result = run_git(["-C", root, "update-index", "--add", "--cacheinfo", cacheinfo])

    if result.returncode == 0:
        print(f"  + {path} ({note})")
    else:
        fail(f"FAIL: git update-index failed for {path}", EXIT_GIT_ERROR)


def find_own_lines_skip_reason() -> str | None:
    """Why the own-lines check does not apply to this commit, or None when it does."""
    session_id = read_session_id()
    git_directory = run_git(["rev-parse", "--git-dir"], capture_output=True).stdout.strip()

    if session_id is None:
        return "not checked (not a Claude session)"
    elif not has_record(session_id):
        return "not checked (no write record: the session started before the record hook)"
    elif not is_record_complete(session_id):
        return "not checked (write record started mid-session: new sessions are checked)"
    elif any(os.path.exists(os.path.join(git_directory, name)) for name in SEQUENCER_STATE_FILES):
        return "not checked (merge in progress)"
    elif is_own_worktree(session_id, read_repository_root()):
        return f"not checked ({OWN_WORKTREE_NOTE})"
    else:
        return None


@functools.cache
def is_own_worktree(session_id: str, root: str) -> bool:
    """Whether the commit runs in a linked worktree that no other session has written and no other process works in.

    Every change there is the task's, as worktree-cleanup.py judges a
    worktree still in use, so a project hook's or the user's edit there is
    the task's too. A check that cannot run counts as not, so the own-lines
    check stays; an original checkout is never one.
    """
    worktree = Path(root).resolve()
    created = read_worktree_creation_time(worktree)

    if created is not None:
        return is_unused_by_others(session_id, worktree, created)
    else:
        return False


def is_unused_by_others(session_id: str, worktree: Path, created: float) -> bool:
    try:
        users = list_worktree_users(worktree)
    except CommandError:
        return False
    else:
        return not users and not has_other_session_writes(session_id, str(worktree), created)


def check_staged_lines(included: Sequence[str], expected_head: str | None) -> None:
    session_id = read_session_id() or ""
    root = read_repository_root()
    staged_output = run_git(["diff", "--cached", "--name-only", "-z", "--no-renames"], capture_output=True).stdout
    staged_paths = split_path_list(staged_output)
    checked = [path for path in staged_paths if path not in included]
    problems = {path: describe_staged_problem(session_id, root, path, expected_head) for path in checked}
    shown = {path: problem for path, problem in problems.items() if problem}

    if shown:
        fail(make_foreign_lines_report(list(shown.values()), list(shown), suggest_own_lines=True), EXIT_FOREIGN_LINES)


def describe_staged_problem(session_id: str, root: str, path: str, expected_head: str | None) -> str | None:
    try:
        return describe_foreign_lines(session_id, root, path, expected_head)
    except SessionWritesError as error:
        return make_unreadable_record_message(path, error)


def describe_foreign_lines(session_id: str, root: str, path: str, expected_head: str | None) -> str | None:
    """Raises SessionWritesError when the record cannot be read back."""
    staged = read_staged_content(path)

    if staged is None or not is_line_tracked(root, path) or not is_utf8(staged):
        return describe_whole_file(session_id, root, path, expected_head)
    else:
        base = read_base_text(expected_head, path)
        own = read_own_content(session_id, root, path, base)
        foreign = list_foreign_lines(base, decode_content(staged), own.text if own is not None else base)
        entangled = own.entangled if own is not None else ()
        shown = [f"  {line.kind.value} {line.line_number}: {line.text}" for line in foreign[:MAX_SHOWN_FOREIGN_LINES]]
        more = [f"  ... and {len(foreign) - MAX_SHOWN_FOREIGN_LINES} more"] if len(foreign) > MAX_SHOWN_FOREIGN_LINES else []
        header = f"FAIL: {path} has {len(foreign)} staged lines this session's write record does not show as its own (another session's, a person's, or a command the record did not see):"
        foreign_message = "\n".join([header, *shown, *more]) if foreign else None
        contested = own.contested if own is not None else ()

        if entangled:
            return make_entangled_message(path, entangled)
        elif foreign_message and contested:
            contested_message = make_contested_message(path, contested, find_contest_cause(session_id, root, path))

            return f"{foreign_message}\n{contested_message}"
        else:
            return foreign_message


def describe_whole_file(session_id: str, root: str, path: str, expected_head: str | None) -> str | None:
    if is_whole_file_own(session_id, root, path, read_base_blob(expected_head, path)):
        return None
    else:
        return f"FAIL: {path} is binary, large, or removed, so it is committed whole, and its changes are not all this session's recorded writes."


def make_unrecorded_message(path: str) -> str:
    return f"FAIL: this session has no recorded writes to {path}, so none of its changes are known to be yours."


def make_entangled_message(path: str, entangled: Sequence[str]) -> str:
    shown = [f"  ! {line.rstrip()}" for line in entangled[:MAX_SHOWN_FOREIGN_LINES]]
    header = f"FAIL: in {path}, {len(entangled)} of your changes are tangled with uncommitted lines this session's write record does not show as its own (another session's, a person's, or a command the record did not see): your line changes or splits them, or they changed your replacement, so they cannot be committed apart:"

    return "\n".join([header, *shown])


def make_contested_message(path: str, contested: Sequence[ContestedLine], cause: ContestCause) -> str:
    """Say what overlapped the unproven writes: another session's edit, only its command, or neither."""
    shown = [f"  ? {line.kind.value} {line.text.rstrip()}" for line in contested[:MAX_SHOWN_FOREIGN_LINES]]
    header = f"FAIL: in {path}, the record could not prove {len(contested)} changes are yours: {describe_contest_cause(cause)}:"

    return "\n".join([header, *shown])


def is_utf8(content: bytes) -> bool:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    else:
        return True


def print_verification(
    subject_length: int,
    body_max_length: int,
    body_nonblank_count: int,
    bullet_count: int,
    expected_head: str | None,
    code_lines: int,
    own_lines_status: str,
    amend: bool,
) -> None:
    print("=== Verification ===")
    print(f"| {'Item':<30} | Status")
    print(f"| {'-' * 30} | --------")
    print(f"| {'Type prefix':<30} | OK")
    print(f"| {'Subject <=72 chars':<30} | OK ({subject_length})")
    print(f"| {'Body line max <=72':<30} | OK ({body_max_length})")
    print(f"| {'Body lines 1-8':<30} | OK ({body_nonblank_count})")
    print(f"| {'Bullets 3-5':<30} | OK ({bullet_count})")
    print(f"| {'No internal terms':<30} | OK")
    print(f"| {'Stage matches argv':<30} | OK")
    print(f"| {'No credential files':<30} | OK")
    print(f"| {'No live-looking tokens':<30} | OK")
    print(f"| {'Large change reviewed':<30} | OK ({code_lines} code lines)")
    print(f"| {'HEAD is the index base':<30} | OK ({shorten(expected_head)})")
    print(f"| {'Only own lines staged':<30} | {own_lines_status}")
    print(f"| {'Amends own unpushed commit':<30} | {'OK' if amend else 'n/a (new commit)'}")


def do_commit(message: str, amend: bool, expected_head: str | None, files: Sequence[str]) -> MadeCommit:
    """Git commit itself refuses to move HEAD when another commit landed while the pre-commit check ran."""
    reflog_action = make_reflog_action(amend)
    env = os.environ.copy()
    env["CLAUDE_SKILL_GIT_COMMIT"] = "1"
    env[REFLOG_ACTION_VARIABLE] = reflog_action
    git_args = ["commit"]
    if amend:
        git_args.append("--amend")
    with record_commit_hooks():
        commit_proc = subprocess.run(
            ["git", *git_args, "-F", "-"],
            input=message,
            text=True,
            env=env,
            check=False,
        )
    if commit_proc.returncode != 0:
        check_head_unchanged(expected_head, files)
        sys.exit(EXIT_GIT_ERROR)
    return find_made_commit(make_commit_reflog_message(reflog_action, message))


@contextlib.contextmanager
def record_commit_hooks() -> Iterator[None]:
    """Record what the repository's commit hooks write while git commit runs, as this session's command write.

    The record hook takes no snapshot for the wrapper's own call, so a commit
    that waits for the shell guard's dialog holds up no other session; only
    the hooks' run counts as running for them. Recording only helps: a
    failure leaves the hooks' writes unrecorded, which a later commit asks
    about, so it never stops this commit.
    """
    record = start_commit_hook_record()

    try:
        yield
    finally:
        finish_commit_hook_record(record)


def start_commit_hook_record() -> tuple[str, str] | None:
    """The session and call id of a record started around git commit, or None when there is nothing to record."""
    session_id = read_session_id()
    call_id = f"{COMMIT_HOOK_CALL_PREFIX}{secrets.token_hex(REFLOG_MARK_BYTES)}"

    try:
        root = read_repository_root()

        if session_id is not None and has_record(session_id) and has_commit_hooks(root):
            start_call_record(session_id, call_id, [SnapshotScope(root, None)])
            return session_id, call_id
        else:
            return None
    except Exception as error:
        print(COMMIT_HOOK_RECORD_NOTE.format(error=error), file=sys.stderr)
        return None


def finish_commit_hook_record(record: tuple[str, str] | None) -> None:
    """Any failure here only leaves the hooks' writes unrecorded, after the commit is already made."""
    if record is not None:
        try:
            finish_call_record(*record, COMMIT_HOOK_TOOL)
        except Exception as error:
            print(COMMIT_HOOK_RECORD_NOTE.format(error=error), file=sys.stderr)


def make_reflog_action(amend: bool) -> str:
    action = AMEND_REFLOG_ACTION if amend else COMMIT_REFLOG_ACTION

    return f"{action} via claude-commit {secrets.token_hex(REFLOG_MARK_BYTES)}"


def make_commit_reflog_message(reflog_action: str, message: str) -> str:
    """The HEAD reflog message git commit writes: the action and the subject, whitespace runs collapsed as git does."""
    subject = message.split("\n", 1)[0]

    return REFLOG_SPACE_RE.sub(" ", f"{reflog_action}: {subject}").strip(" ")


def find_made_commit(reflog_message: str) -> MadeCommit:
    """Read the commit from the reflog entry git commit wrote, in one pass that later entries cannot shift.

    Commands a hook runs inherit the mark, so the entry must carry the subject too, which a hook's `git reset`
    ("<mark>: updating HEAD") does not. A post-commit hook committing the same subject writes after it, so the oldest
    matching entry is the one git commit wrote.
    """
    entries = read_head_reflog()
    matching = [position for position, entry in enumerate(entries) if entry.message == reflog_message]

    if matching:
        position = matching[-1]
        previous_head = entries[position + 1].commit if position + 1 < len(entries) else None

        return MadeCommit(entries[position].commit, previous_head)
    else:
        fail(
            f"FAIL: git commit finished, but no HEAD reflog entry carries its mark and subject ({reflog_message}), so which commit it made is not known and nothing was checked. Check `git log` before pushing, and tell the user.",
            EXIT_GIT_ERROR,
        )


def read_head_reflog() -> list[ReflogEntry]:
    """HEAD's reflog, newest first; a HEAD without one reads as empty."""
    reflog_proc = run_git(["log", "--walk-reflogs", REFLOG_ENTRY_FORMAT, HEAD_REVISION], capture_output=True)
    matches = (REFLOG_ENTRY_RE.fullmatch(line) for line in reflog_proc.stdout.splitlines())

    return [ReflogEntry(match[1], match[2]) for match in matches if match]


def read_commit_arguments(argv: list[str]) -> CommitArguments:
    try:
        return parse_commit_arguments(argv)
    except CommitArgumentsError as error:
        fail(str(error), EXIT_USAGE)


def main(argv: list[str]) -> None:
    arguments = read_commit_arguments(argv)
    files = arguments.files

    print_header()
    check_workflow_recorded()
    approved = approve_included_files(arguments)
    metrics = validate_message(arguments.message)
    check_private_index_environment(arguments.private_index)
    check_private_index_arguments(arguments.expected_head, files)
    requested_head = resolve_expected_head(arguments.expected_head)
    starting_branch = read_head_branch()
    with hold_commit_lock():
        expected_head = requested_head if arguments.expected_head is not None else read_revision(HEAD_REVISION)
        base = find_commit_base(arguments, expected_head, starting_branch)
        check_amend_target(arguments.amend, base)
        print("=== Recent commits ===")
        run_git(["log", "--oneline", "-3"])
        with use_commit_index(arguments, base):
            made = stage_and_commit(arguments, base, approved, metrics)
    print("=== Done ===")
    run_git(["log", "--oneline", "-1", made.commit])


def stage_and_commit(
    arguments: CommitArguments,
    expected_head: str | None,
    approved: dict[str, ApprovedContent],
    metrics: tuple[int, int, int, int],
) -> MadeCommit:
    stage_changes(arguments, expected_head, approved)
    if arguments.files:
        verify_staging(arguments.files)
    check_credential_files()
    check_secret_tokens()
    own_lines_skip_reason = find_own_lines_skip_reason()
    if own_lines_skip_reason is None:
        check_staged_lines(arguments.included_paths, expected_head)
    code_lines = count_code_lines()
    check_large_commit_reviewed(code_lines, arguments.reviewed)
    print_verification(*metrics, expected_head, code_lines, own_lines_skip_reason or "OK", arguments.amend)
    print("=== Committing ===")
    made = do_commit(arguments.message, arguments.amend, expected_head, arguments.files)
    check_commit_base(made, expected_head, arguments.amend, arguments.files)
    record_session_commit(made.commit)
    return made


if __name__ == "__main__":
    main(sys.argv[1:])
