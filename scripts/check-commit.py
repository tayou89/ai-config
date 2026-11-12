#!/usr/bin/env python3
"""check-commit.py — pre-commit check for this repository.

Git runs it through .githooks/pre-commit once core.hooksPath points
there (install.py sets it), for Claude's commit wrapper and for plain
git commit alike. Commits need no user approval (user decision,
2026-11-09), so these checks stand where that review was.

Several sessions share one checkout, so the working tree can hold other
sessions' unfinished edits. The check copies out the index being
committed (the private one when GIT_INDEX_FILE is set) and runs the
checks on that copy, with every GIT_ variable removed so the tests'
own repositories never touch the index being committed.

Exit codes:
    0  every check passed
    1  a check failed or the index could not be copied (git stops the commit)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from secret_files import STAGED_DIFF_ARGUMENTS, find_added_secret_tokens

CHECKS = (
    ("generated rule copies match shared-rules/", ["scripts/sync-shared-rules.py", "--check"]),
    ("always-loaded rules within their size budgets", ["scripts/check-rule-size.py"]),
    ("tests", ["-m", "unittest", "discover", "-s", "tests", "-q"]),
)
GIT_VARIABLE_PREFIX = "GIT_"
COPY_FAILURE = "could not copy the index being committed"
DIFF_FAILURE = "could not read the staged changes for the token scan"
SECRET_FAILURE = "live-looking secret on an added line: {finding}"
EXIT_PASSED = 0
EXIT_FAILED = 1


def main() -> int:
    failures = find_secret_failures() + run_checks_on_index()

    if failures:
        print("pre-commit check failed:\n" + "\n".join(f"  - {failure}" for failure in failures), file=sys.stderr)
        return EXIT_FAILED
    else:
        return EXIT_PASSED


def find_secret_failures() -> list[str]:
    diff = subprocess.run(["git", *STAGED_DIFF_ARGUMENTS], capture_output=True, text=True, check=False)

    if diff.returncode == 0:
        return [SECRET_FAILURE.format(finding=finding) for finding in find_added_secret_tokens(diff.stdout)]
    else:
        return [DIFF_FAILURE]


def run_checks_on_index() -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        if copy_index(directory):
            return [name for name, arguments in CHECKS if not run_check(arguments, directory)]
        else:
            return [COPY_FAILURE]


def copy_index(directory: str) -> bool:
    result = subprocess.run(["git", "checkout-index", "--all", f"--prefix={directory}{os.sep}"], check=False)

    return result.returncode == 0


def run_check(arguments: list[str], directory: str) -> bool:
    result = subprocess.run([sys.executable, *arguments], cwd=directory, env=make_clean_environment(), check=False)

    return result.returncode == 0


def make_clean_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if not name.startswith(GIT_VARIABLE_PREFIX)}


if __name__ == "__main__":
    sys.exit(main())
