#!/usr/bin/env python3
"""Measure the shape problems a reader meets in a Markdown report: its first screen, its tables, and its references.

The `report-writing` skill sets the limits (user decision, 2026-11-16): the
opening fits about one screen of the note app, record details stay out of the
text before the first section, table cells stay short because the note app's
reading column is about 700 pixels wide, only one summary stands before the
appendix, sections are linked by heading rather than by number, and file paths
are `file:///` links. `report-check-hook.py` runs these checks after a write to
a notes folder; `python3 report_check.py <file>...` runs them by hand.
The checks see shape only, not whether the conclusion is right or the words
are plain.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

INDEX_FILE_NAME = "INDEX.md"
APP_NOTES_PATH = Path("Library") / "Application Support" / "Claude" / "side-session-notes"
PROJECT_NOTES_PATH = Path("Documents") / "session-notes"
_NOTES_DIRECTORY_NAME = APP_NOTES_PATH.name
_PROJECT_NOTES_PARTS = PROJECT_NOTES_PATH.parts
_SESSION_FOLDER_PREFIX = "local_"
_REPORT_SUFFIX = ".md"
_USAGE = "usage: report_check.py <report.md>..."
_EXIT_FINDINGS = 1
_EXIT_USAGE = 2
_PREAMBLE_LIMIT = 400
_OPENING_LIMIT = 1500
_OPENING_CELL_LIMIT = 40
_BODY_CELL_LIMIT = 100
_COLUMN_LIMIT = 6
_OPENING_LATIN_LIMIT = 30
_OPENING_CODE_LIMIT = 3
_ENCODING = "utf-8"
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCED_CODE = re.compile(r"^(?:> ?)?```.*?^(?:> ?)?```[^\n]*\n?", re.MULTILINE | re.DOTALL)
_SECTION_START = re.compile(r"^## ", re.MULTILINE)
_TABLE_DIVIDER = re.compile(r"^\s*\|[\s|:-]+\|\s*$")
_TITLE_LINE = re.compile(r"\A\s*# [^\n]*\n?")
_SUMMARY_HEADING = re.compile(r"^## .*(요약|결론|summary)", re.MULTILINE | re.IGNORECASE)
_APPENDIX_HEADING = re.compile(r"^## (부록|appendix)", re.MULTILINE | re.IGNORECASE)
_SECTION_NUMBER = re.compile(r"(?<![\[#\w])\d+(?:\.\d+)?(?:절|장)")
_BARE_PATH = re.compile(r"(?<![/:\w`])(?:~/|/Users/)[^\s)]+")
_CELL_SEPARATOR = re.compile(r"(?<!\\)\|")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_CALLOUT_TYPE = re.compile(r"\[![\w-]+\][+-]?")
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_CALLOUT_LINE = ">"
_SUMMARY_CALLOUT = "> [!"
_TABLE_LINE = "|"
_SECTION_MARK = "## "


@dataclass(frozen=True)
class Finding:
    """One shape problem: the check that found it and what it measured."""

    check: str
    detail: str


def is_notes_report(path: str) -> bool:
    """Whether a path is a Markdown file in a project notes folder or a session folder of the app's notes folder."""
    candidate = Path(path).expanduser()
    parts = candidate.parts
    is_in_notes = _is_inside_app_notes(parts) or _is_inside_project_notes(parts)

    return is_in_notes and candidate.suffix.lower() == _REPORT_SUFFIX


def is_app_session_folder_file(path: str, home: Path | None = None) -> bool:
    """Whether a path lies inside a session folder of the desktop app's notes folder, which the app may delete.

    Only the real notes folder under the home folder counts, not a copy of it elsewhere, and only folders named
    after a session (`local_…`), since the app keeps its bulletin and other top-level files.
    """
    root = Path(os.path.normpath((home or Path.home()) / APP_NOTES_PATH))
    candidate = Path(os.path.normpath(os.path.expanduser(path)))

    try:
        relative_parts = candidate.relative_to(root).parts
    except ValueError:
        return False
    else:
        return len(relative_parts) > 1 and relative_parts[0].startswith(_SESSION_FOLDER_PREFIX)


def _is_inside_app_notes(parts: tuple[str, ...]) -> bool:
    """A file inside a folder of the app's notes folder, wherever that folder or a copy of it sits."""
    return _NOTES_DIRECTORY_NAME in parts and len(parts) - parts.index(_NOTES_DIRECTORY_NAME) > 2


def _is_inside_project_notes(parts: tuple[str, ...]) -> bool:
    """`Documents/session-notes/<project>/…`: a file at the folder's top or in its backup does not count."""
    width = len(_PROJECT_NOTES_PARTS)

    return any(
        parts[index : index + width] == _PROJECT_NOTES_PARTS and len(parts) - index > width + 1
        for index in range(len(parts) - width)
    )


def check_report(text: str) -> list[Finding]:
    unified = text.replace("\r\n", "\n")
    body = _FENCED_CODE.sub("", _FRONTMATTER.sub("", unified, count=1))
    sections = _SECTION_START.split(body)
    preamble_lines = _TITLE_LINE.sub("", sections[0], count=1).splitlines() if len(sections) > 1 else []
    preamble = "\n".join(line for line in preamble_lines if not line.startswith(_CALLOUT_LINE)).strip()
    has_summary_callout = any(line.startswith(_SUMMARY_CALLOUT) for line in preamble_lines)
    opening = sections[0] if has_summary_callout else _SECTION_MARK.join(sections[:2])
    rest = body[len(opening) :]

    return [
        *_check_opening(preamble, opening),
        *_check_tables(rest, _BODY_CELL_LIMIT, "body"),
        *_check_structure(body),
    ]


def format_findings(name: str, findings: list[Finding]) -> str:
    details = "; ".join(f"{finding.detail} [{finding.check}]" for finding in findings)

    return f"{name}: {details}"


def _check_opening(preamble: str, opening: str) -> list[Finding]:
    plain = _CALLOUT_TYPE.sub("", _INLINE_CODE.sub("", _LINK_TARGET.sub("]", opening)))
    hangul = len(_HANGUL.findall(plain))
    latin = len(_LATIN.findall(plain))
    latin_share = round(100 * latin / max(hangul + latin, 1))
    code_spans = len(_INLINE_CODE.findall(opening))
    findings = []

    if len(preamble) > _PREAMBLE_LIMIT:
        findings.append(
            Finding(
                "preamble",
                f"text before the first section outside callouts is {len(preamble)} characters (limit {_PREAMBLE_LIMIT})",
            )
        )
    if len(opening) > _OPENING_LIMIT:
        findings.append(Finding("opening", f"the opening is {len(opening)} characters (limit {_OPENING_LIMIT})"))
    if latin_share > _OPENING_LATIN_LIMIT:
        findings.append(
            Finding(
                "opening-latin",
                f"Latin letters are {latin_share}% of the opening's letters (limit {_OPENING_LATIN_LIMIT}%)",
            )
        )
    if code_spans > _OPENING_CODE_LIMIT:
        findings.append(
            Finding("opening-code", f"the opening has {code_spans} code spans (limit {_OPENING_CODE_LIMIT})")
        )
    return [*findings, *_check_tables(opening, _OPENING_CELL_LIMIT, "opening")]


def _check_tables(text: str, cell_limit: int, place: str) -> list[Finding]:
    rows = _list_table_rows(text)
    long_cells = [length for row in rows for length in map(_measure_cell, row) if length > cell_limit]
    wide_rows = [row for row in rows if len(row) > _COLUMN_LIMIT]
    findings = []

    if long_cells:
        findings.append(
            Finding(
                "table-cell",
                f"{len(long_cells)} {place} table cells over {cell_limit} characters (longest {max(long_cells)})",
            )
        )
    if wide_rows:
        findings.append(Finding("table-columns", f"{len(wide_rows)} {place} table rows over {_COLUMN_LIMIT} columns"))
    return findings


def _list_table_rows(text: str) -> list[list[str]]:
    lines = [line.lstrip("> ").rstrip() if line.startswith(_CALLOUT_LINE) else line for line in text.splitlines()]
    rows = []
    in_table = False

    for index, line in enumerate(lines):
        is_row = line.lstrip().startswith(_TABLE_LINE)
        next_is_divider = index + 1 < len(lines) and bool(_TABLE_DIVIDER.match(lines[index + 1]))

        in_table = is_row and (in_table or next_is_divider)
        if in_table and not _TABLE_DIVIDER.match(line):
            rows.append([cell.strip() for cell in _CELL_SEPARATOR.split(line.strip().strip(_TABLE_LINE))])
    return rows


def _measure_cell(cell: str) -> int:
    return len(_LINK_TARGET.sub("]", cell))


def _check_structure(body: str) -> list[Finding]:
    appendix = _APPENDIX_HEADING.search(body)
    main_part = _INLINE_CODE.sub("", body[: appendix.start()] if appendix else body)
    summaries = _SUMMARY_HEADING.findall(main_part)
    section_numbers = _SECTION_NUMBER.findall(main_part)
    bare_paths = _BARE_PATH.findall(_INLINE_CODE.sub("", body))
    findings = []

    if len(summaries) > 1:
        findings.append(Finding("history", f"{len(summaries)} summary or conclusion headings before the appendix"))
    if section_numbers:
        findings.append(
            Finding("section-number", f"{len(section_numbers)} references by section number instead of a heading link")
        )
    if bare_paths:
        findings.append(Finding("bare-path", f"{len(bare_paths)} absolute paths outside file:/// links"))
    return findings


def main(arguments: list[str]) -> int:
    if arguments:
        failures = [_check_file(Path(argument)) for argument in arguments]
        return _EXIT_FINDINGS if any(failures) else 0
    else:
        print(_USAGE, file=sys.stderr)
        return _EXIT_USAGE


def _check_file(path: Path) -> bool:
    """Print one file's findings; True when it has some or cannot be read."""
    try:
        findings = check_report(path.read_text(encoding=_ENCODING))

        print(format_findings(path.name, findings) if findings else f"{path.name}: no findings")
        return bool(findings)
    except OSError as error:
        print(f"{path.name}: cannot be read ({error.strerror})", file=sys.stderr)
        return True


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
