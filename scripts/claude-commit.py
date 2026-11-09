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
(GIT_INDEX_FILE) requires it and the file list. After committing, the
wrapper checks that git commit replaced the expected HEAD and undoes the
commit with the same compare-and-swap when HEAD moved while it started.

Commits need no user approval (user decision, 2026-11-09), so a large
change gets a checklist review instead: when the staged change touches
LARGE_COMMIT_LINES or more lines outside documents and lock files, the
wrapper requires --reviewed, which the session passes only after that
review has run.

Git cannot tell which session wrote a line, so a whole-file add or a
hand-built partial file can carry another session's uncommitted lines.
In a Claude session whose write record exists (session_writes.py), the
wrapper refuses staged lines that record does not show as the session's
own, and --own-lines stages each file as the base plus only those lines.
--include-unrecorded <file> commits that file whole after the user has
seen its lines; the shell guard asks before such a call.

Exit codes:
    0  success (commit created)
    1  git error
    2  message validation failure
    3  staging mismatch
    4  usage error
    5  credential file or live-looking token staged
    6  HEAD moved since the index was built (nothing committed)
    7  large code change without --reviewed (nothing committed)
    8  staged lines this session's write record does not show as its own,
       or own lines that cannot be separated from them (nothing committed)
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from typing import NoReturn

from commit_arguments import (
    EXPECT_HEAD_OPTION,
    INCLUDE_UNRECORDED_OPTION,
    OWN_LINES_OPTION,
    CommitArguments,
    CommitArgumentsError,
    parse_commit_arguments,
)
from own_lines import FileHistory, OwnContent, build_own_content, list_foreign_lines
from secret_files import STAGED_DIFF_ARGUMENTS, find_added_secret_tokens, get_credential_reason
from session_writes import (
    SessionWritesError,
    decode_content,
    has_record,
    is_line_tracked,
    is_whole_file_own,
    load_file_history,
    read_session_id,
    store_text,
)

PRIVATE_INDEX_VARIABLE = "GIT_INDEX_FILE"
HEAD_REVISION = "HEAD"
PREVIOUS_HEAD_REVISION = "HEAD@{1}"
UNDO_REFLOG_MESSAGE = "claude-commit: undo a commit built on a moved HEAD"
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
NUMSTAT_SEPARATOR = "\0"
NUMSTAT_COLUMN_SEPARATOR = "\t"

EXIT_GIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_STAGING_MISMATCH = 3
EXIT_USAGE = 4
EXIT_CREDENTIAL_FILE = 5
EXIT_HEAD_MOVED = 6
EXIT_REVIEW_MISSING = 7
EXIT_FOREIGN_LINES = 8
SEQUENCER_STATE_FILES = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
REGULAR_FILE_MODE = "100644"
EXECUTABLE_FILE_MODE = "100755"
MAX_SHOWN_FOREIGN_LINES = 12


def fail(message: str, code: int) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def run_git(args: Sequence[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=False, text=True, capture_output=capture_output)


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


def stage_files(files: Sequence[str]) -> None:
    print("=== Staging files ===")
    for path in files:
        stage_file(path)


def stage_file(path: str) -> None:
    result = run_git(["add", "-A", "--", path])
    if result.returncode != 0:
        fail(f"FAIL: git add failed for {path}", EXIT_GIT_ERROR)
    print(f"  + {path}")


def verify_staging(expected_files: Sequence[str]) -> None:
    actual_proc = run_git(
        ["diff", "--cached", "--name-only"],
        capture_output=True,
    )
    if actual_proc.returncode != 0:
        fail("FAIL: git diff --cached failed", EXIT_GIT_ERROR)
    expected = set(expected_files)
    actual = {line for line in actual_proc.stdout.splitlines() if line}
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


def read_new_head() -> str:
    new_head = read_revision(HEAD_REVISION)

    if new_head is not None:
        return new_head
    else:
        fail("FAIL: git rev-parse HEAD failed after the commit", EXIT_GIT_ERROR)


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
        changed_proc = run_git(["log", "--format=", "--name-only", commit_range, "--", *files], capture_output=True)

        return sorted({line for line in changed_proc.stdout.splitlines() if line})
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
        return f"FAIL: could not read which commit {shorten(new_head)} replaced (HEAD has no reflog), so it is not known to be built on {shorten(expected_head)}. Check `git log` before pushing."
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


def check_head_unchanged(expected_head: str | None, files: Sequence[str]) -> None:
    current_head = read_revision(HEAD_REVISION)

    if current_head is not None and current_head != expected_head:
        moved_summary = (
            f"FAIL: HEAD moved from {shorten(expected_head)} to {shorten(current_head)} after the index was built"
        )

        fail(make_moved_head_message([moved_summary], expected_head, current_head, files), EXIT_HEAD_MOVED)
    elif current_head is None and expected_head is not None:
        fail(f"FAIL: HEAD no longer points to a commit (expected {shorten(expected_head)})", EXIT_HEAD_MOVED)


def check_commit_base(new_head: str, expected_head: str | None, amend: bool, files: Sequence[str]) -> None:
    replaced_revision = PREVIOUS_HEAD_REVISION if amend else f"{new_head}^1"
    replaced_head = read_revision(replaced_revision)

    if replaced_head != expected_head:
        undone = replaced_head is not None and undo_commit(new_head, replaced_head)

        fail(make_undo_message(expected_head, replaced_head, new_head, files, undone), EXIT_HEAD_MOVED)


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
    for path in staged_proc.stdout.split("\0"):
        reason = get_credential_reason(path, functools.partial(read_staged_content, path)) if path else None
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


def read_base_text(expected_head: str | None, path: str) -> str:
    """Base content as text that keeps its line endings and bytes, matching session_writes."""
    base_args = ["git", "cat-file", "blob", f"{expected_head}:{path}"]
    base_proc = subprocess.run(base_args, capture_output=True, check=False) if expected_head else None

    return decode_content(base_proc.stdout) if base_proc is not None and base_proc.returncode == 0 else ""


def read_base_blob(expected_head: str | None, path: str) -> str | None:
    return read_revision(f"{expected_head}:{path}") if expected_head else None


def read_own_content(session_id: str, root: str, path: str, base: str) -> OwnContent | None:
    history = read_file_history(session_id, root, path)

    if history is not None:
        return build_own_content(base, history)
    else:
        return None


def read_file_history(session_id: str, root: str, path: str) -> FileHistory | None:
    try:
        return load_file_history(session_id, root, path)
    except SessionWritesError as error:
        fail(
            f"FAIL: the write record for {path} cannot be read back ({error}), so its lines cannot be told apart.\n"
            f"Resolve: leave it out, or, if the user confirms its changes belong in this commit, add {INCLUDE_UNRECORDED_OPTION} {path}.",
            EXIT_FOREIGN_LINES,
        )


def read_index_mode(root: str, path: str) -> str:
    entry = run_git(["ls-files", "-s", "--", path], capture_output=True).stdout.split()

    if entry:
        return entry[0]
    else:
        return EXECUTABLE_FILE_MODE if os.access(os.path.join(root, path), os.X_OK) else REGULAR_FILE_MODE


def stage_changes(arguments: CommitArguments, expected_head: str | None) -> None:
    session_id = read_session_id()

    if arguments.no_stage:
        print("=== Staging skipped (--no-stage) ===")
    elif arguments.own_lines and session_id is not None and has_record(session_id):
        stage_own_lines(session_id, arguments.files, arguments.included_paths, expected_head)
    elif arguments.own_lines:
        fail(
            f"FAIL: {OWN_LINES_OPTION} needs this session's write record, which exists only in sessions started after the record hook was installed.\n"
            f"Resolve: commit without {OWN_LINES_OPTION}; for a file that also holds another session's uncommitted edits, build its content from `git show <base>:<path>` plus only your change, stage it with `git hash-object -w` and `git update-index --cacheinfo`, and run with --no-stage.",
            EXIT_USAGE,
        )
    else:
        stage_files(arguments.files)
    print("=== Staged changes ===")
    run_git(["diff", "--cached", "--stat"])


def stage_own_lines(session_id: str, files: Sequence[str], included: Sequence[str], expected_head: str | None) -> None:
    root = read_repository_root()

    print("=== Staging own lines ===")
    for path in files:
        if path in included or not is_line_tracked(root, path):
            stage_file(path)
        else:
            stage_own_content(session_id, root, path, read_base_text(expected_head, path))


def stage_own_content(session_id: str, root: str, path: str, base: str) -> None:
    own = read_own_content(session_id, root, path, base)

    if own is None:
        fail(make_unrecorded_message(path), EXIT_FOREIGN_LINES)
    elif own.entangled:
        fail(make_entangled_message(path, own.entangled), EXIT_FOREIGN_LINES)
    elif own.text == "" and not os.path.exists(os.path.join(root, path)):
        run_git(["update-index", "--force-remove", "--", path])
        print(f"  - {path} (removed)")
    else:
        cacheinfo = f"{read_index_mode(root, path)},{store_text(root, own.text)},{path}"
        result = run_git(["update-index", "--add", "--cacheinfo", cacheinfo])

        if result.returncode == 0:
            print(f"  + {path} (own lines)")
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
    elif any(os.path.exists(os.path.join(git_directory, name)) for name in SEQUENCER_STATE_FILES):
        return "not checked (merge in progress)"
    else:
        return None


def check_staged_lines(included: Sequence[str], expected_head: str | None) -> None:
    session_id = read_session_id() or ""
    root = read_repository_root()
    staged_output = run_git(["diff", "--cached", "--name-only", "-z", "--no-renames"], capture_output=True).stdout
    staged_paths = [path for path in staged_output.split("\0") if path]
    problems = [describe_foreign_lines(session_id, root, path, expected_head) for path in staged_paths if path not in included]
    shown = [problem for problem in problems if problem]

    if shown:
        resolve = f"Resolve: rerun with {OWN_LINES_OPTION} to stage only your lines, or, if the user confirms the listed lines belong in this commit, add {INCLUDE_UNRECORDED_OPTION} <file>."
        fail("\n".join([*shown, resolve]), EXIT_FOREIGN_LINES)


def describe_foreign_lines(session_id: str, root: str, path: str, expected_head: str | None) -> str | None:
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

        return make_entangled_message(path, entangled) if entangled else foreign_message


def describe_whole_file(session_id: str, root: str, path: str, expected_head: str | None) -> str | None:
    if is_whole_file_own(session_id, root, path, read_base_blob(expected_head, path)):
        return None
    else:
        return f"FAIL: {path} is binary, large, or removed, so it is committed whole, and its changes are not all this session's recorded writes."


def make_unrecorded_message(path: str) -> str:
    return f"FAIL: this session has no recorded writes to {path}, so none of its changes are known to be yours.\nResolve: leave it out, or, if the user confirms its changes belong in this commit, add {INCLUDE_UNRECORDED_OPTION} {path}."


def make_entangled_message(path: str, entangled: Sequence[str]) -> str:
    shown = [f"  ! {line.rstrip()}" for line in entangled[:MAX_SHOWN_FOREIGN_LINES]]
    lines = [
        f"FAIL: in {path}, {len(entangled)} of your lines change or split lines another session has not committed, so they cannot be committed apart:",
        *shown,
        f"Resolve: ask the user; the other session can commit first, or, if the user agrees, commit both with {INCLUDE_UNRECORDED_OPTION} {path}.",
    ]

    return "\n".join(lines)


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


def do_commit(message: str, amend: bool) -> None:
    env = os.environ.copy()
    env["CLAUDE_SKILL_GIT_COMMIT"] = "1"
    git_args = ["commit"]
    if amend:
        git_args.append("--amend")
    commit_proc = subprocess.run(
        ["git", *git_args, "-F", "-"],
        input=message,
        text=True,
        env=env,
        check=False,
    )
    if commit_proc.returncode != 0:
        sys.exit(EXIT_GIT_ERROR)


def read_commit_arguments(argv: list[str]) -> CommitArguments:
    try:
        return parse_commit_arguments(argv)
    except CommitArgumentsError as error:
        fail(str(error), EXIT_USAGE)


def main(argv: list[str]) -> None:
    arguments = read_commit_arguments(argv)
    files = arguments.files

    print_header()
    metrics = validate_message(arguments.message)
    check_private_index_arguments(arguments.expected_head, files)
    expected_head = resolve_expected_head(arguments.expected_head)
    check_head_unchanged(expected_head, files)
    print("=== Recent commits ===")
    run_git(["log", "--oneline", "-3"])
    stage_changes(arguments, expected_head)
    if files:
        verify_staging(files)
    check_credential_files()
    check_secret_tokens()
    own_lines_skip_reason = find_own_lines_skip_reason()
    if own_lines_skip_reason is None:
        check_staged_lines(arguments.included_paths, expected_head)
    code_lines = count_code_lines()
    check_large_commit_reviewed(code_lines, arguments.reviewed)
    print_verification(*metrics, expected_head, code_lines, own_lines_skip_reason or "OK")
    print("=== Committing ===")
    do_commit(arguments.message, arguments.amend)
    check_commit_base(read_new_head(), expected_head, arguments.amend, files)
    print("=== Done ===")
    run_git(["log", "--oneline", "-1"])


if __name__ == "__main__":
    main(sys.argv[1:])
