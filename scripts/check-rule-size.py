#!/usr/bin/env python3
"""check-rule-size.py — keep always-loaded instructions within their size budgets.

Usage:
    check-rule-size.py

Every instruction line that loads in every session competes with all the
others for the model's attention, so the commit check measures the
always-loaded files in bytes (user decision, 2026-11-12):

- Claude loads CLAUDE.md and every file in rules/ in every session.
- Codex loads codex/AGENTS.md and then, in the folder it runs in, one
  project instruction file per directory from the project root down to that
  folder, and stops adding them at project_doc_max_bytes. The folders are
  the working folders of the Codex sessions of the last 30 days, read from
  the first line of each session log under CODEX_HOME (default ~/.codex).
  A log that cannot be read or parsed is skipped, so one bad log never
  stops a commit.

Exit codes:
    0  every total is within its budget (warnings may be printed)
    1  a total is over its budget
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

CODEX_HOME_VARIABLE = "CODEX_HOME"
CODEX_SESSIONS_DIRECTORY = "sessions"
CODEX_LIMIT_KEY = "project_doc_max_bytes"
CODEX_WARNING_BYTES = 32 * 1024
CODEX_FAILURE_SHARE = 0.85
CLAUDE_BUDGET_BYTES = 32 * 1024
SESSION_WINDOW_SECONDS = 30 * 24 * 60 * 60
_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_INSTRUCTION_PATH = _ROOT / "CLAUDE.md"
_CLAUDE_RULES_DIRECTORY = _ROOT / "rules"
_CODEX_AGENTS_PATH = _ROOT / "codex" / "AGENTS.md"
_CODEX_SETTINGS_PATH = _ROOT / "codex" / "settings.json"
_CODEX_DEFAULT_LIMIT_BYTES = 32 * 1024
_PROJECT_ROOT_MARKER = ".git"
_PROJECT_INSTRUCTION_NAMES = ("AGENTS.override.md", "AGENTS.md")
_CLAUDE_FAILURE = (
    "Claude always-loaded rules are {total:,} bytes, over the {budget:,}-byte budget:"
    " remove or move text to a skill as the rule-writing skill describes"
)
_CODEX_WARNING = (
    "Codex instructions in {root} are {total:,} bytes (global {global_bytes:,} + project {project_bytes:,}),"
    " over the {warning:,}-byte default limit"
)
_CODEX_FAILURE = (
    "Codex instructions in {root} are {total:,} bytes (global {global_bytes:,} + project {project_bytes:,}),"
    " over {failure:,} bytes ({share:.0%} of project_doc_max_bytes {limit:,}):"
    " shrink codex/AGENTS.md here or that project's AGENTS.md in its own session"
)
_EXIT_PASSED = 0
_EXIT_FAILED = 1


class _CodexProject(NamedTuple):
    root: Path
    project_bytes: int


def main() -> int:
    claude_total = _measure_claude_rules()
    global_bytes = _CODEX_AGENTS_PATH.stat().st_size
    limit = _read_codex_limit()
    projects = _collect_codex_projects()
    largest = max((project.project_bytes for project in projects), default=0)
    failures = _find_claude_failures(claude_total) + _find_codex_failures(global_bytes, limit, projects)

    print(f"Claude always-loaded rules: {claude_total:,} of {CLAUDE_BUDGET_BYTES:,} bytes")
    print(f"Codex instructions: global {global_bytes:,} bytes, largest project {largest:,}, limit {limit:,}")
    for warning in _find_codex_warnings(global_bytes, limit, projects):
        print(f"warning: {warning}")
    if failures:
        print("rule size check failed:\n" + "\n".join(f"  - {failure}" for failure in failures), file=sys.stderr)
        return _EXIT_FAILED
    else:
        return _EXIT_PASSED


def _measure_claude_rules() -> int:
    rule_bytes = sum(path.stat().st_size for path in _CLAUDE_RULES_DIRECTORY.rglob("*.md"))

    return _CLAUDE_INSTRUCTION_PATH.stat().st_size + rule_bytes


def _read_codex_limit() -> int:
    settings = json.loads(_CODEX_SETTINGS_PATH.read_text(encoding="utf-8"))
    limit = settings.get("config", {}).get(CODEX_LIMIT_KEY, _CODEX_DEFAULT_LIMIT_BYTES)

    return int(limit)


def _collect_codex_projects() -> list[_CodexProject]:
    largest: dict[Path, _CodexProject] = {}

    for folder in _find_codex_folders():
        root = _find_project_root(folder)
        key = root or folder
        project = _CodexProject(key, _measure_project_instructions(folder, root))

        if key not in largest or project.project_bytes > largest[key].project_bytes:
            largest[key] = project
    return sorted(largest.values(), key=lambda project: str(project.root))


def _find_codex_folders() -> set[Path]:
    sessions = _find_codex_home() / CODEX_SESSIONS_DIRECTORY
    oldest = time.time() - SESSION_WINDOW_SECONDS
    folders = (_read_session_folder(log, oldest) for log in sessions.rglob("*.jsonl"))

    return {folder for folder in folders if folder and folder.is_dir()}


def _find_codex_home() -> Path:
    if os.environ.get(CODEX_HOME_VARIABLE):
        return Path(os.environ[CODEX_HOME_VARIABLE])
    else:
        return Path.home() / ".codex"


def _read_session_folder(log: Path, oldest: float) -> Path | None:
    try:
        if log.is_file() and log.stat().st_mtime >= oldest:
            with log.open(encoding="utf-8", errors="replace") as stream:
                return _parse_session_folder(stream.readline())
        else:
            return None
    except OSError:
        return None


def _parse_session_folder(first_line: str) -> Path | None:
    try:
        record: Any = json.loads(first_line)
    except ValueError:
        record = None
    payload = record.get("payload") if isinstance(record, dict) else None
    folder = payload.get("cwd") if isinstance(payload, dict) else None

    if isinstance(folder, str) and folder:
        return Path(folder)
    else:
        return None


def _measure_project_instructions(folder: Path, root: Path | None) -> int:
    files = (_find_instruction_file(directory) for directory in _list_project_directories(folder, root))

    return sum(path.stat().st_size for path in files if path)


def _list_project_directories(folder: Path, root: Path | None) -> list[Path]:
    if root:
        return [directory for directory in [folder, *folder.parents] if directory.is_relative_to(root)]
    else:
        return [folder]


def _find_project_root(folder: Path) -> Path | None:
    roots = (directory for directory in [folder, *folder.parents] if (directory / _PROJECT_ROOT_MARKER).exists())

    return next(roots, None)


def _find_instruction_file(directory: Path) -> Path | None:
    return next((directory / name for name in _PROJECT_INSTRUCTION_NAMES if (directory / name).is_file()), None)


def _find_claude_failures(total: int) -> list[str]:
    if total > CLAUDE_BUDGET_BYTES:
        return [_CLAUDE_FAILURE.format(total=total, budget=CLAUDE_BUDGET_BYTES)]
    else:
        return []


def _find_codex_failures(global_bytes: int, limit: int, projects: list[_CodexProject]) -> list[str]:
    failure = int(limit * CODEX_FAILURE_SHARE)
    over = [project for project in projects if global_bytes + project.project_bytes > failure]

    return [
        _CODEX_FAILURE.format(
            **_describe_codex_total(global_bytes, project),
            failure=failure,
            share=CODEX_FAILURE_SHARE,
            limit=limit,
        )
        for project in over
    ]


def _find_codex_warnings(global_bytes: int, limit: int, projects: list[_CodexProject]) -> list[str]:
    failure = int(limit * CODEX_FAILURE_SHARE)
    warned = [project for project in projects if CODEX_WARNING_BYTES < global_bytes + project.project_bytes <= failure]

    return [
        _CODEX_WARNING.format(**_describe_codex_total(global_bytes, project), warning=CODEX_WARNING_BYTES)
        for project in warned
    ]


def _describe_codex_total(global_bytes: int, project: _CodexProject) -> dict[str, Any]:
    return {
        "root": project.root,
        "total": global_bytes + project.project_bytes,
        "global_bytes": global_bytes,
        "project_bytes": project.project_bytes,
    }


if __name__ == "__main__":
    sys.exit(main())
