#!/usr/bin/env python3
"""check-codex-skills.py — keep the hand-adapted Codex skills from drifting from their Claude sources.

Usage:
    check-codex-skills.py                 fail when a Claude source changed after its Codex copy was adapted
    check-codex-skills.py --show NAME     print how the Claude sources changed since the copy was adapted
    check-codex-skills.py --accept NAME   record the current Claude sources as adapted, after porting what applies

Thirteen Codex skills are written by hand from Claude skills, since Codex
permissions and tools differ, so they cannot be generated (user decision,
2026-11-17). Each Codex copy names, in an `Adapted from` comment under its
front matter, the git blob of every Claude source file it was adapted from:
the skill's SKILL.md and each file under its references/ folder. When a
Claude source changes, the commit check stops until the same change decides
what the Codex copy needs, records in the copy what it leaves out or
changes on purpose and why, and records the new blobs with --accept. The
check verifies the recorded sources and that every copy is listed, not that
the adaptation or its record of differences is complete. A Codex skill that
shares a Claude skill's name or carries an `Adapted from` comment, and is
neither one scripts/sync-shared-rules.py generates nor a third-party copy
with its own license file, is adapted by hand, and the check fails until it
is listed here, so a new copy cannot drift unchecked.

Exit codes:
    0  every copy names its current sources, or --show or --accept finished
    1  a Claude source changed after its copy was adapted, or a copy adapted
       by hand is not listed here
    2  a copy has no usable adaptation comment, or a source cannot be read
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ADAPTED_SKILLS = (
    "agent-usage",
    "code-plan",
    "code-style",
    "config-files",
    "git-commit",
    "integration-testing",
    "knowledge-vault",
    "milestone-delivery",
    "pr-create",
    "python",
    "resolve-conflict",
    "typescript-migration",
    "typescript",
)
_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_SKILLS_DIRECTORY = _ROOT / "skills"
_CODEX_SKILLS_DIRECTORY = _ROOT / "codex" / "skills"
_SYNC_SCRIPT = _ROOT / "scripts" / "sync-shared-rules.py"
_SYNC_MODULE_NAME = "sync_shared_rules"
_LICENSE_PREFIX = "LICENSE"
_SKILL_FILE = "SKILL.md"
_REFERENCES_DIRECTORY = "references"
_MARKER_PATTERN = re.compile(r"^<!-- Adapted from (?P<sources>.+) -->$", re.MULTILINE)
_SOURCE_PATTERN = re.compile(r"^(?P<path>.+?) at (?P<blob>[0-9a-f]{40})$")
_MARKER_TEMPLATE = "<!-- Adapted from {sources} -->"
_SOURCE_TEMPLATE = "{path} at {blob}"
_SOURCE_SEPARATOR = ", "
_FRONT_MATTER_START = "---\n"
_FRONT_MATTER_END = "\n---\n"
_FRONT_MATTER_SEARCH_START = len(_FRONT_MATTER_START) - 1
_ENCODING = "utf-8"
_STALE_MESSAGE = (
    "{copy} was adapted from an older {path}: run `python3 scripts/check-codex-skills.py --show {name}`,"
    " port what applies to Codex, record in the copy what it leaves out or changes on purpose and why, then run it with `--accept {name}`"
)
_UNLISTED_MESSAGE = (
    "{copy} is adapted by hand from a Claude skill but missing from ADAPTED_SKILLS in"
    " scripts/check-codex-skills.py: add it there, then run it with `--accept`"
)
_PASSED_MESSAGE = "OK: every adapted Codex skill names its current Claude sources"
_CURRENT_MESSAGE = "{copy} already names its current Claude sources"
_ACCEPTED_MESSAGE = "RECORDED: {copy} now names its current Claude sources"
_EXIT_PASSED = 0
_EXIT_STALE = 1
_EXIT_UNUSABLE = 2


class UnusableCopyError(Exception):
    """Raised when a copy or a source cannot be compared; main reports it and exits 2, never 1."""


def main(arguments: Sequence[str]) -> int:
    options = _parse_options(arguments)

    try:
        if options.show:
            return _show_changes(options.show)
        elif options.accept:
            return _accept_sources(options.accept)
        else:
            return _check_all()
    except (UnusableCopyError, OSError, UnicodeDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return _EXIT_UNUSABLE


def _parse_options(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the hand-adapted Codex skills against their Claude sources.")
    group = parser.add_mutually_exclusive_group()

    group.add_argument("--show", choices=ADAPTED_SKILLS, help="print how the Claude sources changed")
    group.add_argument("--accept", choices=ADAPTED_SKILLS, help="record the current Claude sources as adapted")
    return parser.parse_args(arguments)


def _check_all() -> int:
    failures = [
        _STALE_MESSAGE.format(copy=_describe_copy(name), path=path, name=name)
        for name in ADAPTED_SKILLS
        for path in _find_stale_paths(name)
    ]
    failures += [_UNLISTED_MESSAGE.format(copy=_describe_copy(name)) for name in _find_unlisted_copies()]

    if failures:
        print("Codex skill check failed:\n" + "\n".join(f"  - {failure}" for failure in failures), file=sys.stderr)
        return _EXIT_STALE
    else:
        print(_PASSED_MESSAGE)
        return _EXIT_PASSED


def _show_changes(name: str) -> int:
    recorded = _read_recorded_sources(name)
    stale = _find_stale_paths(name)

    if stale:
        for path in stale:
            old = _read_blob_lines(recorded.get(path))
            new = _read_file_lines(_ROOT / path)

            sys.stdout.writelines(difflib.unified_diff(old, new, f"adapted/{path}", f"current/{path}"))
    else:
        print(_CURRENT_MESSAGE.format(copy=_describe_copy(name)))
    return _EXIT_PASSED


def _accept_sources(name: str) -> int:
    copy = _find_copy(name)
    text = copy.read_text(encoding=_ENCODING)
    sources = _SOURCE_SEPARATOR.join(
        _SOURCE_TEMPLATE.format(path=path, blob=blob) for path, blob in _list_current_sources(name).items()
    )
    marker = _MARKER_TEMPLATE.format(sources=sources)

    copy.write_text(_place_marker(text, marker, copy), encoding=_ENCODING)
    print(_ACCEPTED_MESSAGE.format(copy=_describe_copy(name)))
    return _EXIT_PASSED


def _place_marker(text: str, marker: str, copy: Path) -> str:
    if _MARKER_PATTERN.search(text):
        return _MARKER_PATTERN.sub(lambda _: marker, text, count=1)
    elif text.startswith(_FRONT_MATTER_START) and _FRONT_MATTER_END in text[_FRONT_MATTER_SEARCH_START:]:
        end = text.index(_FRONT_MATTER_END, _FRONT_MATTER_SEARCH_START) + len(_FRONT_MATTER_END)
        return f"{text[:end]}\n{marker}\n{text[end:]}"
    else:
        raise UnusableCopyError(f"{copy.relative_to(_ROOT)} has no front matter to place the adaptation comment under")


def _find_stale_paths(name: str) -> list[str]:
    recorded = _read_recorded_sources(name)
    current = _list_current_sources(name)

    return sorted(path for path in recorded.keys() | current.keys() if recorded.get(path) != current.get(path))


def _read_recorded_sources(name: str) -> dict[str, str]:
    copy = _find_copy(name)
    match = _MARKER_PATTERN.search(copy.read_text(encoding=_ENCODING))

    if match:
        return dict(_parse_source(entry, copy) for entry in match.group("sources").split(_SOURCE_SEPARATOR))
    else:
        raise UnusableCopyError(
            f"{copy.relative_to(_ROOT)} has no adaptation comment: port what applies, then run"
            f" `python3 scripts/check-codex-skills.py --accept {name}`"
        )


def _parse_source(entry: str, copy: Path) -> tuple[str, str]:
    match = _SOURCE_PATTERN.match(entry)

    if match:
        return (match.group("path"), match.group("blob"))
    else:
        raise UnusableCopyError(f"{copy.relative_to(_ROOT)} has an unreadable adaptation entry: {entry}")


def _find_unlisted_copies() -> list[str]:
    folders = sorted(path for path in _CODEX_SKILLS_DIRECTORY.iterdir() if path.is_dir())
    generated = _list_generated_skills()

    return [
        folder.name
        for folder in folders
        if folder.name not in ADAPTED_SKILLS and folder.name not in generated and _is_adapted_by_hand(folder)
    ]


# The sync script's own register decides what it generates, not a notice a copy may carry over.
def _list_generated_skills() -> frozenset[str]:
    specification = importlib.util.spec_from_file_location(_SYNC_MODULE_NAME, _SYNC_SCRIPT)

    if specification is not None and specification.loader is not None:
        module = importlib.util.module_from_spec(specification)

        specification.loader.exec_module(module)
        return frozenset(name for name, _ in module.SKILL_RULES.values())
    else:
        raise UnusableCopyError(f"{_SYNC_SCRIPT.relative_to(_ROOT)} cannot be loaded to list the generated skills")


def _is_adapted_by_hand(folder: Path) -> bool:
    copy = folder / _SKILL_FILE
    has_source = (_CLAUDE_SKILLS_DIRECTORY / folder.name).is_dir() or _has_marker(copy)
    is_licensed = any(entry.is_file() and entry.name.startswith(_LICENSE_PREFIX) for entry in folder.iterdir())

    return has_source and not is_licensed


def _has_marker(copy: Path) -> bool:
    return copy.is_file() and _MARKER_PATTERN.search(copy.read_text(encoding=_ENCODING)) is not None


def _list_current_sources(name: str) -> dict[str, str]:
    skill = _CLAUDE_SKILLS_DIRECTORY / name
    references = skill / _REFERENCES_DIRECTORY
    files = [path for path in [skill / _SKILL_FILE, *sorted(references.rglob("*"))] if path.is_file()]

    return {path.relative_to(_ROOT).as_posix(): _compute_blob(path) for path in files}


def _compute_blob(path: Path) -> str:
    content = path.read_bytes()

    return hashlib.sha1(b"blob %d\0" % len(content) + content, usedforsecurity=False).hexdigest()


def _read_blob_lines(blob: str | None) -> list[str]:
    if blob:
        result = subprocess.run(["git", "cat-file", "blob", blob], cwd=_ROOT, capture_output=True, check=False)

        if result.returncode == 0:
            return result.stdout.decode(_ENCODING).splitlines(keepends=True)
        else:
            raise UnusableCopyError(
                f"recorded blob {blob} is not in this repository: {result.stderr.decode(_ENCODING).strip()}"
            )
    else:
        return []


def _read_file_lines(path: Path) -> list[str]:
    if path.is_file():
        return path.read_text(encoding=_ENCODING).splitlines(keepends=True)
    else:
        return []


def _find_copy(name: str) -> Path:
    return _CODEX_SKILLS_DIRECTORY / name / _SKILL_FILE


def _describe_copy(name: str) -> str:
    return _find_copy(name).relative_to(_ROOT).as_posix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
