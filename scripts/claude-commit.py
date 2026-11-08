#!/usr/bin/env python3
"""claude-commit.py — wrapper enforcing git-commit skill checklist.

Usage:
    claude-commit.py [--amend] [--no-stage] "<message>" [<file1> ...]

Validates message format (subject prefix/length, body line length/count,
bullet count, no internal terms), runs amend check, stages exact files,
verifies no extras, refuses staged .env and credential files (see
secret_files.py), prints verification table, then commits with
CLAUDE_SKILL_GIT_COMMIT=1 marker. Subprocess git commit does NOT trigger
Claude's PreToolUse hook (hooks fire at tool boundary only), so the
checklist becomes the single enforced path.

Use --no-stage when the index is already prepared by a prior git
operation (e.g. merge --no-commit, cherry-pick) and re-staging via
`git add` would fail (deleted/renamed paths) or override the prepared
state. With --no-stage, file args are not required.

Exit codes:
    0  success (commit created)
    1  git error
    2  message validation failure
    3  staging mismatch
    4  usage error
    5  credential file staged
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from typing import Sequence

from secret_files import get_credential_reason


VALID_PREFIXES = ("Feat", "Fix", "Refactor", "Chore", "Merge")
INTERNAL_TERM_RE = re.compile(r"\b(step|phase|pattern)\s+[0-9]+\b", re.IGNORECASE)
MAX_SUBJECT_LENGTH = 72
MAX_BODY_LINE_LENGTH = 72
MAX_BODY_NONBLANK_LINES = 8
MIN_BULLETS = 3
MAX_BULLETS = 5

EXIT_GIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_STAGING_MISMATCH = 3
EXIT_USAGE = 4
EXIT_CREDENTIAL_FILE = 5


def fail(message: str, code: int) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def run_git(args: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=False, text=True, **kwargs)


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
    if INTERNAL_TERM_RE.search(message):
        offenders = [
            f"{i + 1}: {line}"
            for i, line in enumerate(lines)
            if INTERNAL_TERM_RE.search(line)
        ]
        fail(
            "FAIL: internal terms (Step N / Phase N / Pattern N) detected\n"
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


def print_verification(
    subject_length: int,
    body_max_length: int,
    body_nonblank_count: int,
    bullet_count: int,
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


def main(argv: list[str]) -> None:
    amend = False
    no_stage = False
    while argv and argv[0].startswith("--"):
        if argv[0] == "--amend":
            amend = True
            argv = argv[1:]
        elif argv[0] == "--no-stage":
            no_stage = True
            argv = argv[1:]
        else:
            sys.stderr.write(f"Unknown option: {argv[0]}\n")
            sys.exit(EXIT_USAGE)
    min_args = 1 if no_stage else 2
    if len(argv) < min_args:
        sys.stderr.write(
            "Usage: claude-commit.py [--amend] [--no-stage] "
            "\"<message>\" [<file1> ...]\n"
        )
        sys.exit(EXIT_USAGE)
    message = argv[0]
    files = argv[1:]

    print_header()
    metrics = validate_message(message)
    print("=== Recent commits ===")
    run_git(["log", "--oneline", "-3"])
    if no_stage:
        print("=== Staging skipped (--no-stage) ===")
        run_git(["diff", "--cached", "--stat"])
    else:
        stage_files(files)
        print("=== Staged changes ===")
        run_git(["diff", "--cached", "--stat"])
        verify_staging(files)
    check_credential_files()
    print_verification(*metrics)
    print("=== Committing ===")
    do_commit(message, amend)
    print("=== Done ===")
    run_git(["log", "--oneline", "-1"])


if __name__ == "__main__":
    main(sys.argv[1:])
