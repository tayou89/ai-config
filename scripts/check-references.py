#!/usr/bin/env python3
"""check-references.py — find references in the rule files that point at nothing.

Usage:
    check-references.py

Rules, skills, and instruction files point at each other, and a rename or
a move breaks those pointers without any error, so the commit check runs
this script (user decision, 2026-11-16). It checks:

- relative Markdown links and link definitions, which must name an
  existing file;
- a skill or always-loaded rule with a quoted section (`name` "Heading")
  and an instruction file with a quoted section (CLAUDE.md "Heading",
  also in the hook scripts' messages), whose heading must exist outside
  fenced code;
- a skill a document says to read, load, follow, or propose (Read `name`),
  which must exist for the tool that reads the document, and a skill it
  says to use (Use `name`, `name` skill), which must not be one only the
  other tool has, since "use" also introduces commands and code words;
- a repository path in code (`scripts/name.py`), which must exist;
- the bracketed notice tags CLAUDE.md lists for the hooks, each of which a
  hook must emit, and each tag a hook emits, which CLAUDE.md must list.

Files under codex/ are read by Codex and checked against the Codex skills
and codex/AGENTS.md; the rest against the Claude skills, rules/, and
CLAUDE.md. A copy generated from shared-rules/ is read by both tools, so a
skill it names needs to exist for one of them. A skill from an enabled
plugin counts as the tool's skill, but its sections cannot be read here.
A skill folder with its own license file is an unchanged third-party copy
and is not checked, and fenced code is example text, not a reference.

Exit codes:
    0  every reference resolves
    1  a reference points at nothing
"""

from __future__ import annotations

import json
import re
import sys
from functools import cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

_ROOT = Path(__file__).resolve().parent.parent
_CODEX_DIRECTORY = _ROOT / "codex"
_RULES_DIRECTORY = _ROOT / "rules"
_SCRIPTS_DIRECTORY = _ROOT / "scripts"
_CLAUDE_INSTRUCTION_PATH = _ROOT / "CLAUDE.md"
_CODEX_INSTRUCTION_PATH = _CODEX_DIRECTORY / "AGENTS.md"
_README_PATH = _ROOT / "README.md"
_CLAUDE_SETTINGS_PATH = _ROOT / "settings.json"
_CODEX_SETTINGS_PATH = _CODEX_DIRECTORY / "settings.json"
_CLAUDE_DOCUMENT_PATTERNS = ("CLAUDE.md", "README.md", "rules/*.md", "skills/*/**/*.md")
_CODEX_DOCUMENT_PATTERNS = ("codex/*.md", "codex/skills/*/**/*.md")
_HOOK_SCRIPT_PATTERN = "*-hook.py"
_RULE_PATTERN = "*.md"
_RULE_SUFFIX = ".md"
_SKILL_FILE = "SKILL.md"
_LICENSE_PREFIX = "LICENSE"
_ENABLED_PLUGINS_KEY = "enabledPlugins"
_PLUGINS_KEY = "plugins"
_PLUGIN_SEPARATOR = "@"
_ENCODING = "utf-8"
_NEWLINE = "\n"
_SPACE = " "
_CODE_MARK = "`"
_ANGLE_BRACKETS = "<>"
_FRAGMENT_SEPARATOR = "#"
_QUOTE = '"'
_ESCAPED_QUOTE = '\\"'
_GENERATED_NOTICE = "<!-- Generated from shared-rules/"
_NOTICE_SEARCH_LENGTH = 1_000
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "file://", "#", "/", "~")
_FENCE_PATTERN = re.compile(r"^[ \t]*((`|~)\2{2,})[^\n]*\n.*?^[ \t]*\1\2*[ \t]*$", re.MULTILINE | re.DOTALL)
_CODE_SPAN_PATTERN = re.compile(r"`[^`\n]*`")
_LINK_PATTERN = re.compile(r"\[[^\]\n]*\]\((<[^>\n]+>|[^)\s]+)(?:[ \t]+\"[^\"\n]*\")?\)")
_LINK_DEFINITION_PATTERN = re.compile(r"^[ \t]*\[(?!\^)[^\]\n]+\]:[ \t]*(<[^>\n]+>|\S+)", re.MULTILINE)
_SECTION_PATTERN = re.compile(r"`([a-z0-9-]+)` \"([^\"\n]+)\"")
_FILE_SECTION_PATTERN = re.compile(r"`?(CLAUDE\.md|AGENTS\.md|README\.md)`? \"([^\"\n]+)\"")
_LOAD_PATTERN = re.compile(r"\b(?:[Rr]ead|[Ll]oad|[Ff]ollow|[Pp]ropose) `/?([a-z0-9-]+)`")
_USE_PATTERN = re.compile(r"\b[Uu]se `/?([a-z0-9-]+)`")
_NAMED_SKILL_PATTERN = re.compile(r"`/?([a-z0-9-]+)` skill\b")
_PATH_PATTERN = re.compile(r"`((?:scripts|tests)/[\w.-]+\.py|(?:shared-rules|rules)/[\w.-]+\.md)")
_TAG_PATTERN = re.compile(r"\[([A-Z]{2,}(?:[ -][A-Z]+)*)\]")
_HEADING_PATTERN = re.compile(r"^#+ (.+?)\s*$", re.MULTILINE)
_HEADING_NUMBER_PATTERN = re.compile(r"^\d+[a-z]?\.\s+")
_INSTRUCTION_FILES = {
    "CLAUDE.md": _CLAUDE_INSTRUCTION_PATH,
    "AGENTS.md": _CODEX_INSTRUCTION_PATH,
    "README.md": _README_PATH,
}
_EXIT_PASSED = 0
_EXIT_FAILED = 1


class _Tool(NamedTuple):
    skills: Path
    rules: Path | None
    instruction: Path
    settings: Path


class _Location(NamedTuple):
    path: Path
    line: int


class _Problem(NamedTuple):
    path: Path
    line: int
    message: str


_CLAUDE = _Tool(_ROOT / "skills", _RULES_DIRECTORY, _CLAUDE_INSTRUCTION_PATH, _CLAUDE_SETTINGS_PATH)
_CODEX = _Tool(_CODEX_DIRECTORY / "skills", None, _CODEX_INSTRUCTION_PATH, _CODEX_SETTINGS_PATH)


def main() -> int:
    problems = [problem for document in _list_documents() for problem in _find_document_problems(document)]
    problems += _find_hook_message_problems() + _find_tag_problems()

    for problem in problems:
        print(f"{problem.path.relative_to(_ROOT)}:{problem.line}: {problem.message}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} broken reference(s)", file=sys.stderr)
        return _EXIT_FAILED
    else:
        print("OK: every reference resolves")
        return _EXIT_PASSED


def _list_documents() -> list[Path]:
    patterns = _CLAUDE_DOCUMENT_PATTERNS + _CODEX_DOCUMENT_PATTERNS
    documents = {path for pattern in patterns for path in _ROOT.glob(pattern)}

    return sorted(path for path in documents if path.is_file() and not _is_third_party_copy(path))


def _is_third_party_copy(document: Path) -> bool:
    folders = (folder for folder in (_CLAUDE.skills, _CODEX.skills) if document.is_relative_to(folder))
    folder = next(folders, None)

    if folder is not None:
        skill = folder / document.relative_to(folder).parts[0]

        return any(entry.name.startswith(_LICENSE_PREFIX) for entry in skill.iterdir())
    else:
        return False


def _find_tool(document: Path) -> _Tool:
    if document.is_relative_to(_CODEX_DIRECTORY):
        return _CODEX
    else:
        return _CLAUDE


def _find_document_problems(document: Path) -> list[_Problem]:
    text = _blank_fences(_read_text(document))
    tool = _find_tool(document)

    return (
        _find_link_problems(document, text)
        + _find_section_problems(document, text, tool)
        + _find_file_section_problems(document, text)
        + _find_skill_problems(document, text, tool)
        + _find_path_problems(document, text)
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding=_ENCODING)


def _blank_fences(text: str) -> str:
    return _FENCE_PATTERN.sub(lambda match: _NEWLINE * match.group(0).count(_NEWLINE), text)


def _find_link_problems(document: Path, text: str) -> list[_Problem]:
    prose = _CODE_SPAN_PATTERN.sub(lambda match: _SPACE * len(match.group(0)), text)
    matches = [*_LINK_PATTERN.finditer(prose), *_LINK_DEFINITION_PATTERN.finditer(prose)]
    problems = []

    for match in matches:
        target = unquote(match.group(1).strip(_ANGLE_BRACKETS).split(_FRAGMENT_SEPARATOR)[0])

        if target and not target.startswith(_EXTERNAL_PREFIXES) and not (document.parent / target).exists():
            problems.append(_make_problem(document, text, match.start(), f"link to missing file {target}"))
    return problems


def _find_section_problems(document: Path, text: str, tool: _Tool) -> list[_Problem]:
    problems = []

    for match in _SECTION_PATTERN.finditer(text):
        name, heading = match.groups()
        target = _resolve_named_file(name, tool)

        if target is not None:
            if not _has_heading(target, heading):
                problems.append(_make_problem(document, text, match.start(), f'`{name}` has no section "{heading}"'))
        elif name not in _read_plugin_names(tool.settings):
            problems.append(_make_problem(document, text, match.start(), f"`{name}` names no skill or rule"))
    return problems


def _find_file_section_problems(document: Path, text: str) -> list[_Problem]:
    problems = []

    for match in _FILE_SECTION_PATTERN.finditer(text):
        file_name, heading = match.groups()

        if not _has_heading(_INSTRUCTION_FILES[file_name], heading):
            problems.append(_make_problem(document, text, match.start(), f'{file_name} has no section "{heading}"'))
    return problems


def _find_skill_problems(document: Path, text: str, tool: _Tool) -> list[_Problem]:
    known = _list_skill_names(_CLAUDE) | _list_skill_names(_CODEX)
    available = known if _is_generated(text) else _list_skill_names(tool)
    loads = {match.start(1): match.group(1) for match in _LOAD_PATTERN.finditer(text)}
    uses = {
        match.start(1): match.group(1)
        for pattern in (_USE_PATTERN, _NAMED_SKILL_PATTERN)
        for match in pattern.finditer(text)
        if match.start(1) not in loads
    }
    problems = []

    for position, name in sorted(loads.items()):
        if name not in available:
            problems.append(_make_problem(document, text, position, _describe_missing_skill(name, known)))
    for position, name in sorted(uses.items()):
        if name in known and name not in available:
            problems.append(_make_problem(document, text, position, _describe_missing_skill(name, known)))
    return problems


def _describe_missing_skill(name: str, known: frozenset[str]) -> str:
    if name in known:
        return f"`{name}` is not a skill this tool has"
    else:
        return f"`{name}` names no skill"


def _find_path_problems(document: Path, text: str) -> list[_Problem]:
    return [
        _make_problem(document, text, match.start(), f"path {match.group(1)} does not exist")
        for match in _PATH_PATTERN.finditer(text)
        if not (_ROOT / match.group(1)).exists()
    ]


def _find_hook_message_problems() -> list[_Problem]:
    scripts = [(script, _read_text(script).replace(_ESCAPED_QUOTE, _QUOTE)) for script in _list_hook_scripts()]

    return [
        problem
        for script, text in scripts
        for problem in _find_section_problems(script, text, _CLAUDE) + _find_file_section_problems(script, text)
    ]


def _find_tag_problems() -> list[_Problem]:
    listed = _read_listed_tags()
    emitted = _read_emitted_tags()
    missing = [
        _Problem(_CLAUDE_INSTRUCTION_PATH, line, f"no hook emits [{tag}]")
        for tag, line in sorted(listed.items())
        if tag not in emitted
    ]
    unlisted = [
        _Problem(location.path, location.line, f"emits [{tag}], which CLAUDE.md does not list")
        for tag, location in sorted(emitted.items())
        if tag not in listed
    ]

    return missing + unlisted


def _read_listed_tags() -> dict[str, int]:
    text = _read_text(_CLAUDE_INSTRUCTION_PATH)
    tags: dict[str, int] = {}

    for match in _TAG_PATTERN.finditer(text):
        tags.setdefault(match.group(1), _line_number(text, match.start()))
    return tags


def _read_emitted_tags() -> dict[str, _Location]:
    tags: dict[str, _Location] = {}

    for source in [_CLAUDE_SETTINGS_PATH, *_list_hook_scripts()]:
        text = _read_text(source)

        for match in _TAG_PATTERN.finditer(text):
            tags.setdefault(match.group(1), _Location(source, _line_number(text, match.start())))
    return tags


def _list_hook_scripts() -> list[Path]:
    return sorted(_SCRIPTS_DIRECTORY.glob(_HOOK_SCRIPT_PATTERN))


def _resolve_named_file(name: str, tool: _Tool) -> Path | None:
    skill = tool.skills / name / _SKILL_FILE
    rule = _RULES_DIRECTORY / f"{name}{_RULE_SUFFIX}"

    if skill.is_file():
        return skill
    elif rule.is_file():
        return tool.rules / rule.name if tool.rules is not None else tool.instruction
    else:
        return None


def _has_heading(path: Path, heading: str) -> bool:
    return _normalize_heading(heading) in _read_headings(path)


@cache
def _read_headings(path: Path) -> frozenset[str]:
    headings = _HEADING_PATTERN.findall(_blank_fences(_read_text(path)))

    return frozenset(_normalize_heading(heading) for heading in headings)


def _normalize_heading(heading: str) -> str:
    return _HEADING_NUMBER_PATTERN.sub("", heading.replace(_CODE_MARK, "")).casefold()


@cache
def _list_skill_names(tool: _Tool) -> frozenset[str]:
    folders = {path.name for path in tool.skills.iterdir() if path.is_dir()}
    rules = {path.stem for path in _RULES_DIRECTORY.glob(_RULE_PATTERN)}

    return frozenset(folders | rules | _read_plugin_names(tool.settings))


@cache
def _read_plugin_names(settings: Path) -> frozenset[str]:
    data = json.loads(_read_text(settings))
    enabled = data.get(_ENABLED_PLUGINS_KEY, {})
    plugins = [name for name, is_enabled in enabled.items() if is_enabled] + data.get(_PLUGINS_KEY, [])

    return frozenset(plugin.split(_PLUGIN_SEPARATOR)[0] for plugin in plugins)


def _is_generated(text: str) -> bool:
    return _GENERATED_NOTICE in text[:_NOTICE_SEARCH_LENGTH]


def _make_problem(document: Path, text: str, position: int, message: str) -> _Problem:
    return _Problem(document, _line_number(text, position), message)


def _line_number(text: str, position: int) -> int:
    return text.count(_NEWLINE, 0, position) + 1


if __name__ == "__main__":
    sys.exit(main())
