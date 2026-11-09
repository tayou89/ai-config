#!/usr/bin/env python3
"""claude-commit.py — wrapper enforcing git-commit skill checklist.

Usage:
    claude-commit.py [--amend] [--no-stage] [--expect-head <sha>] [--reviewed] "<message>" [<file1> ...]

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

Exit codes:
    0  success (commit created)
    1  git error
    2  message validation failure
    3  staging mismatch
    4  usage error
    5  credential file or live-looking token staged
    6  HEAD moved since the index was built (nothing committed)
    7  large code change without --reviewed (nothing committed)
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
    CommitArguments,
    CommitArgumentsError,
    parse_commit_arguments,
)
from secret_files import STAGED_DIFF_ARGUMENTS, find_added_secret_tokens, get_credential_reason

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


def print_verification(
    subject_length: int,
    body_max_length: int,
    body_nonblank_count: int,
    bullet_count: int,
    expected_head: str | None,
    code_lines: int,
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
    if arguments.no_stage:
        print("=== Staging skipped (--no-stage) ===")
        run_git(["diff", "--cached", "--stat"])
    else:
        stage_files(files)
        print("=== Staged changes ===")
        run_git(["diff", "--cached", "--stat"])
    if files:
        verify_staging(files)
    check_credential_files()
    check_secret_tokens()
    code_lines = count_code_lines()
    check_large_commit_reviewed(code_lines, arguments.reviewed)
    print_verification(*metrics, expected_head, code_lines)
    print("=== Committing ===")
    do_commit(arguments.message, arguments.amend)
    check_commit_base(read_new_head(), expected_head, arguments.amend, files)
    print("=== Done ===")
    run_git(["log", "--oneline", "-1"])


if __name__ == "__main__":
    main(sys.argv[1:])
