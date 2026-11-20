"""Where a project's rules live, their headings, and the role list the session hooks read from them.

A project's rules are the CLAUDE.md, AGENTS.md, and .claude/CLAUDE.md files at
the root of its Git repository, the nearest folder holding `.git`, so a task
worktree reads its own copy. A heading is a line of one to six `#` and a
space; lines split on any line break, Windows `\\r\\n` included. The role list is the one
`session-management` "Project master and role sessions" defines (user
decision, 2026-11-16): under a "Role sessions" heading of any level, with any
parenthetical after it, one list item per session that starts with its title
in backticks, the project master's included, followed by `(not main)` for a
top-level role the user set apart from the main session. The list ends at the
next heading. An unreadable file counts as empty.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT_MARKER = ".git"
RULES_FILES = ("CLAUDE.md", "AGENTS.md", str(Path(".claude") / "CLAUDE.md"))
_ENCODING = "utf-8"
_ROLE_HEADING = "role sessions"
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$")
_PARENTHETICAL = re.compile(r"[ \t]*\(.*\)$")
_ROLE_ITEM = re.compile(r"^[ \t]*[-*+][ \t]+`([^`]+)`")
_NOT_MAIN_ITEM = re.compile(_ROLE_ITEM.pattern + r"[ \t]*\(not main\)", re.IGNORECASE)


def find_project_root(folder: Path) -> Path | None:
    roots = (directory for directory in [folder, *folder.parents] if (directory / PROJECT_ROOT_MARKER).exists())

    return next(roots, None)


def list_rules_files(root: Path) -> list[Path]:
    return [root / name for name in RULES_FILES if (root / name).is_file()]


def list_headings(text: str) -> list[str]:
    return [heading.group(1) for line in text.splitlines() if (heading := _HEADING.match(line)) is not None]


def list_role_titles(folder: Path) -> set[str]:
    return _list_matching_role_titles(folder, _ROLE_ITEM)


def list_not_main_role_titles(folder: Path) -> set[str]:
    return _list_matching_role_titles(folder, _NOT_MAIN_ITEM)


def _list_matching_role_titles(folder: Path, item_pattern: re.Pattern[str]) -> set[str]:
    root = find_project_root(folder)
    rules_files = list_rules_files(root) if root is not None else []

    return {title for path in rules_files for title in _parse_role_titles(_read_text(path), item_pattern)}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding=_ENCODING, errors="replace")
    except OSError:
        return ""


def _parse_role_titles(text: str, item_pattern: re.Pattern[str]) -> list[str]:
    titles = []
    in_role_list = False

    for line in text.splitlines():
        heading = _HEADING.match(line)

        if heading is not None:
            in_role_list = _PARENTHETICAL.sub("", heading.group(1)).casefold() == _ROLE_HEADING
        elif in_role_list:
            item = item_pattern.match(line)

            if item is not None:
                titles.append(item.group(1).strip())
    return titles
