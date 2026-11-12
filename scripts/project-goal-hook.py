#!/usr/bin/env python3
"""Remind a new session when its project has not recorded a goal.

A SessionStart hook. The Completeness check in CLAUDE.md asks each task to
say how it serves the project's goal, recorded in one or two sentences under
a goal heading in the project rules (user decision, 2026-11-12). When the
session's folder belongs to a project whose rules files exist but hold no
such heading, the hook adds a short notice to the session's context. It is
silent outside a project: a folder with no git repository, a repository
with no rules file, the Claude and Codex configuration folders, the shared
configuration checkout the last install recorded, and the home folder. It
stays silent after a compaction, which keeps the session's earlier notice,
and it never blocks or fails the session start.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_CODEX_HOME_VARIABLE = "CODEX_HOME"
_DEFAULT_CODEX_HOME = "~/.codex"
_CHECKOUT_RECORD = Path(".ai-config-install") / "checkout"
_PROJECT_ROOT_MARKER = ".git"
_RULES_FILES = ("CLAUDE.md", "AGENTS.md", str(Path(".claude") / "CLAUDE.md"))
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_GOAL_HEADINGS = ("goal", "goals", "project goal", "project goals", "목표", "프로젝트 목표")
_SILENT_SOURCES = ("compact",)
_HOOK_EVENT = "SessionStart"
_NOTICE = (
    '[PROJECT GOAL] The project rules in {root} ({files}) have no goal section: a "## Goal" heading with one or two '
    "sentences on what the project is for. The Completeness check in CLAUDE.md asks the user for that goal before the "
    "first task and records the answer under that heading."
)


def main() -> int:
    try:
        _add_notice(_read_hook_input())
    except Exception as error:
        # The reminder is advisory: a failure must not delay or break the session it runs for.
        print(f"project-goal-hook: {error}", file=sys.stderr)
    return 0


def _read_hook_input() -> dict[str, Any]:
    text = sys.stdin.read()

    return json.loads(text) if text.strip() else {}


def _add_notice(hook_input: dict[str, Any]) -> None:
    folder = Path(hook_input.get("cwd") or os.getcwd()).resolve()
    root = _find_project_root(folder)

    if hook_input.get("source") not in _SILENT_SOURCES and root and root not in _list_excluded_folders():
        rules_files = [root / name for name in _RULES_FILES if (root / name).is_file()]

        if rules_files and not any(_has_goal(path) for path in rules_files):
            print(json.dumps(_make_hook_output(root, rules_files)))


def _find_project_root(folder: Path) -> Path | None:
    roots = (directory for directory in [folder, *folder.parents] if (directory / _PROJECT_ROOT_MARKER).exists())

    return next(roots, None)


def _list_excluded_folders() -> list[Path]:
    config_directory = _expand(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY)
    codex_home = _expand(os.environ.get(_CODEX_HOME_VARIABLE) or _DEFAULT_CODEX_HOME)
    record = config_directory / _CHECKOUT_RECORD
    checkout = record.read_text(encoding="utf-8").strip() if record.is_file() else ""
    excluded = [config_directory, codex_home, Path.home().resolve()]

    if checkout:
        excluded.append(_expand(checkout))
    return excluded


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def _has_goal(path: Path) -> bool:
    headings = _HEADING.findall(path.read_text(encoding="utf-8", errors="replace"))

    return any(heading.casefold() in _GOAL_HEADINGS for heading in headings)


def _make_hook_output(root: Path, rules_files: list[Path]) -> dict[str, Any]:
    files = ", ".join(str(path.relative_to(root)) for path in rules_files)
    context = _NOTICE.format(root=root, files=files)

    return {"hookSpecificOutput": {"hookEventName": _HOOK_EVENT, "additionalContext": context}}


if __name__ == "__main__":
    sys.exit(main())
