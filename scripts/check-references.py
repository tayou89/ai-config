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
- an import (`@path`) in a file Claude loads as instructions (a CLAUDE.md
  or CLAUDE.local.md, a file in rules/ or in any .claude/rules/, and the
  files they import), which must name an existing text file that Claude
  reaches within the four hops it follows. It reads imports as Claude Code
  2.1.319 does: an `@` at the start of a line or after a space, outside
  code spans of any length, fences, HTML comments, and front matter,
  followed by a path that starts with a letter, digit, `.`, `_`, or `-`,
  ends before any `#`, writes a space as `\\ `, and counts from the folder
  the importing file really sits in. It follows each instruction file's
  imports depth first in their order and loads a file once, so a file
  first reached deep keeps that depth. A path starting with `~/` or `/`, or
  leading out of the repository, names this computer's files and is not
  checked. Unlike Claude Code, it follows each instruction file on its own,
  takes a file for text when it decodes as UTF-8 without NUL bytes, reads an
  `@` inside an indented code block or an HTML block, and misses one right
  after emphasis or a link bracket (`**@path**`);
- the bracketed notice tags CLAUDE.md lists for the hooks, each of which a
  hook must emit, and each tag a hook emits, which CLAUDE.md must list.

Every Markdown file the repository holds is checked, so a rule file in a
new place needs no listing here (user decision, 2026-11-24): in a Git work
tree the files Git would commit, and in a copy of the index, as the commit
check runs it, every file outside nested repositories. The sources in
shared-rules/ are left out, since the tools read only the copies generated
from them, which are checked. Files under codex/, and an AGENTS.md that no
Claude file imports, are read by Codex and checked against the Codex skills
and codex/AGENTS.md; the rest against the Claude skills, rules/, and
CLAUDE.md. A copy generated from shared-rules/ and an AGENTS.md that a
Claude file imports are read by both tools, so a skill they name needs to
exist for one of them, and the AGENTS.md may quote a section of either
tool's skill or rule. A skill from an enabled plugin counts as the tool's
skill, but its sections cannot be read here. A skill folder with its own
license file is an unchanged third-party copy and is not checked, and
fenced code is example text, not a reference. A project nested in this
repository is checked as part of it, so a path its files name in code
counts from this repository's root, and `AGENTS.md "Heading"` always means
codex/AGENTS.md.

Exit codes:
    0  every reference resolves
    1  a reference points at nothing
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from functools import cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

_ROOT = Path(__file__).resolve().parent.parent
_CODEX_DIRECTORY = _ROOT / "codex"
_RULES_DIRECTORY = _ROOT / "rules"
_SCRIPTS_DIRECTORY = _ROOT / "scripts"
_SOURCE_DIRECTORY = _ROOT / "shared-rules"
_PROJECT_RULE_PARTS = (".claude", "rules")
_CLAUDE_INSTRUCTION_PATH = _ROOT / "CLAUDE.md"
_CODEX_INSTRUCTION_PATH = _CODEX_DIRECTORY / "AGENTS.md"
_README_PATH = _ROOT / "README.md"
_CLAUDE_SETTINGS_PATH = _ROOT / "settings.json"
_CODEX_SETTINGS_PATH = _CODEX_DIRECTORY / "settings.json"
_CLAUDE_INSTRUCTION_NAMES = frozenset({"CLAUDE.md", "CLAUDE.local.md"})
_PROJECT_INSTRUCTION_NAME = "AGENTS.md"
_TOP_LEVEL_COMMAND = ("git", "rev-parse", "--show-toplevel")
_LIST_FILES_COMMAND = ("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
_LIST_SEPARATOR = "\0"
_GIT_FOLDER = ".git"
_GIT_VARIABLE_PREFIX = "GIT_"
_HOOK_SCRIPT_PATTERN = "*-hook.py"
_RULE_PATTERN = "*.md"
_RULE_SUFFIX = ".md"
_IMPORT_HOP_LIMIT = 4
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
_ESCAPED_SPACE = "\\ "
_GENERATED_NOTICE = "<!-- Generated from shared-rules/"
_NOTICE_SEARCH_LENGTH = 1_000
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "file://", "#", "/", "~")
_FENCE_PATTERN = re.compile(r"^[ \t]*((`|~)\2{2,})[^\n]*\n.*?^[ \t]*\1\2*[ \t]*$", re.MULTILINE | re.DOTALL)
_CODE_SPAN_PATTERN = re.compile(r"(?<!`)(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\1(?!`)", re.DOTALL)
_CODE_SPAN_OR_COMMENT_PATTERN = re.compile(rf"{_CODE_SPAN_PATTERN.pattern}|<!--.*?-->", re.DOTALL)
_TEXT_CHARACTER_PATTERN = re.compile(r"[^\n]")
_NUL_BYTE = b"\0"
_LINK_PATTERN = re.compile(r"\[[^\]\n]*\]\((<[^>\n]+>|[^)\s]+)(?:[ \t]+\"[^\"\n]*\")?\)")
_LINK_DEFINITION_PATTERN = re.compile(r"^[ \t]*\[(?!\^)[^\]\n]+\]:[ \t]*(<[^>\n]+>|\S+)", re.MULTILINE)
_SECTION_PATTERN = re.compile(r"`([a-z0-9-]+)` \"([^\"\n]+)\"")
_FILE_SECTION_PATTERN = re.compile(r"`?(CLAUDE\.md|AGENTS\.md|README\.md)`? \"([^\"\n]+)\"")
_LOAD_PATTERN = re.compile(r"\b(?:[Rr]ead|[Ll]oad|[Ff]ollow|[Pp]ropose) `/?([a-z0-9-]+)`")
_USE_PATTERN = re.compile(r"\b[Uu]se `/?([a-z0-9-]+)`")
_NAMED_SKILL_PATTERN = re.compile(r"`/?([a-z0-9-]+)` skill\b")
_PATH_PATTERN = re.compile(r"`((?:scripts|tests)/[\w.-]+\.py|(?:shared-rules|rules)/[\w.-]+\.md)")
_IMPORT_PATTERN = re.compile(r"(?<!\S)@([A-Za-z0-9._-](?:[^\s\\]|\\ )*)")
_FRONT_MATTER_PATTERN = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
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


class _ImportGraph(NamedTuple):
    loaded: frozenset[Path]
    problems: list[_Problem]


_CLAUDE = _Tool(_ROOT / "skills", _RULES_DIRECTORY, _CLAUDE_INSTRUCTION_PATH, _CLAUDE_SETTINGS_PATH)
_CODEX = _Tool(_CODEX_DIRECTORY / "skills", None, _CODEX_INSTRUCTION_PATH, _CODEX_SETTINGS_PATH)


def main() -> int:
    documents = _list_documents()
    imports = _read_imports(documents)
    problems = [problem for document in documents for problem in _find_document_problems(document, imports.loaded)]
    problems += imports.problems + _find_hook_message_problems() + _find_tag_problems()

    for problem in problems:
        print(f"{problem.path.relative_to(_ROOT)}:{problem.line}: {problem.message}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} broken reference(s)", file=sys.stderr)
        return _EXIT_FAILED
    else:
        print("OK: every reference resolves")
        return _EXIT_PASSED


def _list_documents() -> list[Path]:
    files: Iterable[Path] = _list_git_files() if _is_work_tree_root() else _walk_files(_ROOT)
    documents = {path for path in files if path.suffix == _RULE_SUFFIX and path.is_file()}

    return sorted(path for path in documents if not _is_excluded_document(path))


def _list_git_files() -> list[Path]:
    result = _run_git(_LIST_FILES_COMMAND)

    result.check_returncode()
    return [_ROOT / name for name in result.stdout.split(_LIST_SEPARATOR) if name]


def _walk_files(folder: Path) -> Iterator[Path]:
    for entry in folder.iterdir():
        if entry.is_file():
            yield entry
        elif entry.is_dir() and not entry.is_symlink() and not (entry / _GIT_FOLDER).exists():
            yield from _walk_files(entry)


def _is_work_tree_root() -> bool:
    result = _run_git(_TOP_LEVEL_COMMAND)

    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == _ROOT


def _run_git(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    # A GIT_DIR or GIT_WORK_TREE left by a hook would point Git at another repository.
    environment = {name: value for name, value in os.environ.items() if not name.startswith(_GIT_VARIABLE_PREFIX)}

    return subprocess.run(command, cwd=_ROOT, env=environment, capture_output=True, encoding=_ENCODING, check=False)


def _is_excluded_document(document: Path) -> bool:
    return document.is_relative_to(_SOURCE_DIRECTORY) or _is_third_party_copy(document)


def _is_third_party_copy(document: Path) -> bool:
    folders = (folder for folder in (_CLAUDE.skills, _CODEX.skills) if document.is_relative_to(folder))
    folder = next(folders, None)

    if folder is not None:
        skill = folder / document.relative_to(folder).parts[0]

        return skill.is_dir() and any(
            entry.is_file() and entry.name.startswith(_LICENSE_PREFIX) for entry in skill.iterdir()
        )
    else:
        return False


def _find_tools(document: Path, loaded: frozenset[Path]) -> tuple[_Tool, ...]:
    is_project_instruction = document.name == _PROJECT_INSTRUCTION_NAME

    if document.is_relative_to(_CODEX_DIRECTORY):
        return (_CODEX,)
    elif is_project_instruction and document.resolve() in loaded:
        return (_CLAUDE, _CODEX)
    elif is_project_instruction:
        return (_CODEX,)
    else:
        return (_CLAUDE,)


def _find_document_problems(document: Path, loaded: frozenset[Path]) -> list[_Problem]:
    text = _blank_fences(_read_text(document))
    tools = _find_tools(document, loaded)

    return (
        _find_link_problems(document, text)
        + _find_section_problems(document, text, tools)
        + _find_file_section_problems(document, text)
        + _find_skill_problems(document, text, tools)
        + _find_path_problems(document, text)
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding=_ENCODING)


def _blank_fences(text: str) -> str:
    return _FENCE_PATTERN.sub(_blank_match, text)


def _blank_match(match: re.Match[str]) -> str:
    return _TEXT_CHARACTER_PATTERN.sub(_SPACE, match.group(0))


def _blank_code_spans(text: str) -> str:
    return _CODE_SPAN_PATTERN.sub(_blank_match, text)


def _find_link_problems(document: Path, text: str) -> list[_Problem]:
    prose = _blank_code_spans(text)
    matches = [*_LINK_PATTERN.finditer(prose), *_LINK_DEFINITION_PATTERN.finditer(prose)]
    problems = []

    for match in matches:
        target = unquote(match.group(1).strip(_ANGLE_BRACKETS).split(_FRAGMENT_SEPARATOR)[0])

        if target and not target.startswith(_EXTERNAL_PREFIXES) and not (document.parent / target).exists():
            problems.append(_make_problem(document, text, match.start(), f"link to missing file {target}"))
    return problems


def _find_section_problems(document: Path, text: str, tools: tuple[_Tool, ...]) -> list[_Problem]:
    problems = []

    for match in _SECTION_PATTERN.finditer(text):
        name, heading = match.groups()
        targets = [target for target in (_resolve_named_file(name, tool) for tool in tools) if target is not None]

        if targets:
            if not any(_has_heading(target, heading) for target in targets):
                problems.append(_make_problem(document, text, match.start(), f'`{name}` has no section "{heading}"'))
        elif not any(name in _read_plugin_names(tool.settings) for tool in tools):
            problems.append(_make_problem(document, text, match.start(), f"`{name}` names no skill or rule"))
    return problems


def _find_file_section_problems(document: Path, text: str) -> list[_Problem]:
    problems = []

    for match in _FILE_SECTION_PATTERN.finditer(text):
        file_name, heading = match.groups()

        if not _has_heading(_INSTRUCTION_FILES[file_name], heading):
            problems.append(_make_problem(document, text, match.start(), f'{file_name} has no section "{heading}"'))
    return problems


def _find_skill_problems(document: Path, text: str, tools: tuple[_Tool, ...]) -> list[_Problem]:
    known = _list_skill_names(_CLAUDE) | _list_skill_names(_CODEX)
    available = known if _is_generated(text) else frozenset().union(*map(_list_skill_names, tools))
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


def _read_imports(documents: list[Path]) -> _ImportGraph:
    loaded: set[Path] = set()
    problems: dict[_Problem, None] = {}

    for root in (document for document in documents if _is_claude_instruction(document)):
        visited: set[Path] = set()

        problems.update(dict.fromkeys(_follow_imports(root, 0, visited)))
        loaded |= visited
    return _ImportGraph(frozenset(loaded), list(problems))


# Claude Code loads a file once and follows its imports at once, in their order.
def _follow_imports(document: Path, hop: int, visited: set[Path]) -> list[_Problem]:
    text = _read_import_text(document)
    problems = []

    visited.add(document.resolve())
    for match in _IMPORT_PATTERN.finditer(text):
        target = _resolve_import(document, match.group(1))
        message = _describe_import_problem(match.group(1), target, hop + 1)

        if message is not None:
            problems.append(_make_problem(document, text, match.start(), message))
        elif target is not None and target not in visited:
            problems += _follow_imports(target, hop + 1, visited)
    return problems


def _read_import_text(document: Path) -> str:
    text = _blank_fences(_FRONT_MATTER_PATTERN.sub(_blank_match, _read_text(document)))

    return _CODE_SPAN_OR_COMMENT_PATTERN.sub(_blank_match, text)


def _resolve_import(document: Path, written: str) -> Path | None:
    name = written.split(_FRAGMENT_SEPARATOR)[0].replace(_ESCAPED_SPACE, _SPACE)
    target = (document.resolve().parent / name).resolve()

    return target if name and target.is_relative_to(_ROOT) else None


def _describe_import_problem(written: str, target: Path | None, hop: int) -> str | None:
    if target is None:
        return None
    elif target.is_file():
        return _describe_loaded_import(written, target, hop)
    else:
        return f"import of missing file `{written}`"


def _describe_loaded_import(written: str, target: Path, hop: int) -> str | None:
    if hop > _IMPORT_HOP_LIMIT:
        return f"import of `{written}` is more than {_IMPORT_HOP_LIMIT} hops deep, so Claude skips it"
    elif _is_text_file(target):
        return None
    else:
        return f"import of `{written}` names a file Claude does not read as text"


def _is_text_file(path: Path) -> bool:
    content = path.read_bytes()

    try:
        content.decode(_ENCODING)
    except UnicodeDecodeError:
        return False
    else:
        return _NUL_BYTE not in content


def _find_hook_message_problems() -> list[_Problem]:
    scripts = [(script, _read_text(script).replace(_ESCAPED_QUOTE, _QUOTE)) for script in _list_hook_scripts()]

    return [
        problem
        for script, text in scripts
        for problem in _find_section_problems(script, text, (_CLAUDE,)) + _find_file_section_problems(script, text)
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


def _is_claude_instruction(document: Path) -> bool:
    parts = document.relative_to(_ROOT).parts
    is_project_rule = any(parts[index : index + 2] == _PROJECT_RULE_PARTS for index in range(len(parts) - 1))

    return document.name in _CLAUDE_INSTRUCTION_NAMES or document.is_relative_to(_RULES_DIRECTORY) or is_project_rule


def _is_generated(text: str) -> bool:
    return _GENERATED_NOTICE in text[:_NOTICE_SEARCH_LENGTH]


def _make_problem(document: Path, text: str, position: int, message: str) -> _Problem:
    return _Problem(document, _line_number(text, position), message)


def _line_number(text: str, position: int) -> int:
    return text.count(_NEWLINE, 0, position) + 1


if __name__ == "__main__":
    sys.exit(main())
