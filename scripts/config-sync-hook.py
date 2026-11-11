#!/usr/bin/env python3
"""Tell a new session when this computer and the shared AI configuration differ.

A SessionStart hook. It runs `install.py --check` from the checkout that the
last install recorded and adds the report to the session's context. A check
that cannot run is reported too, since a silent failure would hide every
later difference. The same notice is shown again only after a day, so
sessions that start together do not all repeat it. The hook never blocks or
fails the session start.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple


class _Notice(NamedTuple):
    heading: str
    report: str


_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_STATE_DIRECTORY = ".ai-config-install"
_CHECKOUT_RECORD = "checkout"
_NOTICE_RECORD = "last-notice.json"
_INSTALL_SCRIPT = Path("scripts") / "install.py"
# install.py exits with this code when --check found differences.
_EXIT_DIFFERENCES_FOUND = 7
_CHECK_TIMEOUT_SECONDS = 25
_NOTICE_REPEAT_SECONDS = 24 * 60 * 60
_REPORT_LIMIT = 4000
_DIGEST_KEY = "digest"
_SHOWN_AT_KEY = "shown_at"
_HOOK_EVENT = "SessionStart"
_DIFFERENCES_HEADING = (
    "[CONFIG SYNC] This computer and the shared AI configuration repository differ (install.py --check below). "
    "Tell the user once, in one line, in your first reply. Resolve it only when your task is the shared "
    "configuration; otherwise leave it to such a session (the settings sync rule in CLAUDE.md)."
)
_FAILURE_HEADING = (
    "[CONFIG SYNC] The check that compares this computer with the shared AI configuration repository could not run "
    "(details below), so differences would go unnoticed. Tell the user once, in one line, in your first reply. Fix it "
    "only when your task is the shared configuration; otherwise leave it to such a session (the settings sync rule in "
    "CLAUDE.md)."
)


def main() -> int:
    try:
        _add_notice(Path(os.path.expanduser(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY)))
    except Exception as error:
        # The check is advisory: a failure must not delay or break the session it runs for.
        print(f"config-sync-hook: {error}", file=sys.stderr)
    return 0


def _add_notice(config_directory: Path) -> None:
    record = config_directory / _STATE_DIRECTORY / _NOTICE_RECORD
    notice = _run_check(config_directory)
    digest = hashlib.sha256(f"{notice.heading}\n{notice.report}".encode()).hexdigest() if notice else ""
    now = time.time()

    if notice and _is_new_report(record, digest, now):
        print(json.dumps(_make_hook_output(notice)))
        _record_notice(record, digest, now)


def _run_check(config_directory: Path) -> _Notice | None:
    """Return the notice to show, or None when the computer matches or nothing was ever installed here."""
    checkout = _find_checkout(config_directory)

    if checkout is None:
        return None
    elif (checkout / _INSTALL_SCRIPT).is_file():
        return _read_check_result(checkout)
    else:
        missing_script = f"The checkout the last install recorded, {checkout}, has no {_INSTALL_SCRIPT}."

        return _Notice(_FAILURE_HEADING, missing_script)


def _read_check_result(checkout: Path) -> _Notice | None:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    try:
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(checkout / _INSTALL_SCRIPT), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_CHECK_TIMEOUT_SECONDS,
            env=environment,
        )

        if result.returncode == _EXIT_DIFFERENCES_FOUND:
            return _Notice(_DIFFERENCES_HEADING, result.stdout.strip())
        elif result.returncode == 0:
            return None
        else:
            return _Notice(_FAILURE_HEADING, _describe_failure(result))
    except subprocess.TimeoutExpired:
        return _Notice(_FAILURE_HEADING, f"install.py --check did not finish within {_CHECK_TIMEOUT_SECONDS} seconds.")


def _describe_failure(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout).strip()

    return f"install.py --check stopped with exit code {result.returncode}: {output}"


def _find_checkout(config_directory: Path) -> Path | None:
    """The Claude folder itself when it is a checkout, else the checkout the last install recorded."""
    record = config_directory / _STATE_DIRECTORY / _CHECKOUT_RECORD

    if (config_directory / _INSTALL_SCRIPT).is_file():
        return config_directory
    elif record.is_file():
        return Path(record.read_text(encoding="utf-8").strip())
    else:
        return None


def _make_hook_output(notice: _Notice) -> dict:
    context = f"{notice.heading}\n{notice.report[:_REPORT_LIMIT]}"

    return {"hookSpecificOutput": {"hookEventName": _HOOK_EVENT, "additionalContext": context}}


def _record_notice(record: Path, digest: str, now: float) -> None:
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({_DIGEST_KEY: digest, _SHOWN_AT_KEY: now}), encoding="utf-8")


def _read_notice_record(record: Path) -> dict:
    """An unreadable record counts as none, so a half-written file cannot silence the notice for good."""
    try:
        previous = json.loads(record.read_text(encoding="utf-8"))

        return previous if isinstance(previous, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_new_report(record: Path, digest: str, now: float) -> bool:
    previous = _read_notice_record(record)
    shown_at = previous.get(_SHOWN_AT_KEY)
    is_recent = isinstance(shown_at, (int, float)) and now - shown_at <= _NOTICE_REPEAT_SECONDS

    return previous.get(_DIGEST_KEY) != digest or not is_recent


if __name__ == "__main__":
    sys.exit(main())
