#!/usr/bin/env python3
"""Refuse gated writes and questions until the skills that decide them load.

Code, screen files, notes-folder reports, and Figma designs need their writing
skills. Question dialogs and next-step boxes need the decision skill for two
or more choices, the session skill when they mention a session, and the commit
skill when they mention committing, pushing, or merging. Only questions,
option labels, and next-step action names count, not recaps, headers, or descriptions.
A write, a brief to another session or agent, or a `codex` prompt that sets a
motion's length or easing needs the motion skills; motion a write leaves as it was does not count.

PostToolUse on the Skill tool, and UserPromptExpansion of a slash command the
user typed, record each skill load in a record per session and agent: a
subagent starts without its parent's context, so its own loads count, while a
fork starts with that context and counts its parent's loads too. PreCompact
notes where the transcript ended when a compaction began, since a compaction
keeps only the skills it restores; the hook reads their names from the
invoked_skills attachment after the compaction boundary the first time a gated
call needs them, instead of reading the whole transcript on every call.

SKILL_GATE=off in the settings env turns the refusal off while loads are still
recorded. Any failure lets the call through, since a wrong refusal stops the
work of every session.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from motion_values import TextKind, has_in_place_motion, has_motion_text, is_motion_change
from report_check import is_notes_report
from shell_command import IN_PLACE_EDITORS, CommandCall, ExpansionLimitError, expand_shell_command

_GATE_VARIABLE = "SKILL_GATE"
_GATE_OFF = "off"
_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_RECORD_DIRECTORY_NAME = "skill-gate"
_RECORD_SUFFIX = ".jsonl"
_MAIN_RECORD_NAME = "main"
_RECORD_LIFETIME_SECONDS = 14 * 24 * 60 * 60
_UNSAFE_NAME_CHARACTERS = re.compile(r"[^\w-]")
_EMPTY_NAME = "_"
_ENCODING = "utf-8"
_LINE_END = "\n"
_EVENT_KEY = "hook_event_name"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"
_SESSION_ID_KEY = "session_id"
_AGENT_ID_KEY = "agent_id"
_AGENT_TYPE_KEY = "agent_type"
_TRANSCRIPT_PATH_KEY = "transcript_path"
_CWD_KEY = "cwd"
_EXPANSION_TYPE_KEY = "expansion_type"
_COMMAND_NAME_KEY = "command_name"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_HOOK_EVENT_NAME_KEY = "hookEventName"
_PERMISSION_DECISION_KEY = "permissionDecision"
_PERMISSION_REASON_KEY = "permissionDecisionReason"
_DENY = "deny"
_PRE_TOOL_USE = "PreToolUse"
_POST_TOOL_USE = "PostToolUse"
_PROMPT_EXPANSION = "UserPromptExpansion"
_PRE_COMPACT = "PreCompact"
_SLASH_COMMAND = "slash_command"
_SKILL_TOOL = "Skill"
_FORK_AGENT_TYPE = "fork"
_SKILL_AGENT_TYPES = frozenset({"general-purpose", "claude", "Explore", "Plan", _FORK_AGENT_TYPE})
_SUBAGENTS_DIRECTORY_NAME = "subagents"
_AGENT_TRANSCRIPT_PREFIX = "agent-"
_KIND_KEY = "kind"
_SKILL_KEY = "skill"
_SKILLS_KEY = "skills"
_NAME_KEY = "name"
_TRANSCRIPT_KEY = "transcript"
_OFFSET_KEY = "offset"
_TIME_KEY = "time"
_LOAD_KIND = "load"
_COMPACTION_KIND = "compaction"
_RESTORED_KIND = "restored"
_TYPE_KEY = "type"
_SUBTYPE_KEY = "subtype"
_ATTACHMENT_KEY = "attachment"
_COMPACT_BOUNDARY = "compact_boundary"
_INVOKED_SKILLS_TYPE = "invoked_skills"
_ASSISTANT_TYPE = "assistant"
_TRANSCRIPT_MARKERS = (f'"{_COMPACT_BOUNDARY}"'.encode(), f'"{_ASSISTANT_TYPE}"'.encode())
_MAX_RESTORE_SCAN_LINES = 400
_CODE_STYLE = "code-style"
_TYPESCRIPT = "typescript"
_PYTHON = "python"
_UX_DESIGN = "ux-design"
_FRONTEND_DESIGN = "frontend-design"
_REPORT_WRITING = "report-writing"
_DECISIONS = "decisions-and-collaboration"
_SESSION_MANAGEMENT = "session-management"
_GIT_COMMIT = "git-commit"
_QUESTION_TOOL = "AskUserQuestion"
_WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
_QUESTIONS_KEY = "questions"
_QUESTION_KEY = "question"
_OPTIONS_KEY = "options"
_ASK_KEY = "ask"
_ACTIONS_KEY = "actions"
_ACTION_KEY = "action"
_LABEL_KEY = "label"
_CHOICE_COUNT = 2
_QUESTION_TARGET_PARTS = {
    _DECISIONS: "with two or more choices",
    _SESSION_MANAGEMENT: "about a session",
    _GIT_COMMIT: "about committing, pushing, or merging",
}
# English words are bounded by Latin letters on both sides, since a Korean particle right after one ("PR을") is a word character to \b.
_SESSION_WORDS = re.compile(r"세션|(?<![A-Za-z])sessions?(?![A-Za-z])", re.IGNORECASE)
_COMMIT_WORDS = re.compile(
    r"커밋|푸시|푸쉬|병합|머지|합[치친쳐쳤칠칩]"
    r"|(?<![A-Za-z])(?:commit(?:s|ted|ting)?|push(?:es|ed|ing)?|merg(?:e|es|ed|ing)|pull\s+requests?|PRs?)(?![A-Za-z])",
    re.IGNORECASE,
)
_SESSION_ACTION = "new_session"
_COMMIT_ACTIONS = frozenset({"commit", "create_pr", "merge"})
_TYPESCRIPT_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})
_PYTHON_EXTENSIONS = frozenset({".py", ".pyi", ".ipynb"})
_SCREEN_EXTENSIONS = frozenset(
    {".html", ".htm", ".css", ".scss", ".sass", ".less", ".svg", ".vue", ".svelte", ".astro", ".jsx", ".tsx"},
)
_OTHER_CODE_EXTENSIONS = frozenset(
    {".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".zsh"},
)
_SCREEN_CODE_EXTENSIONS = frozenset({".vue", ".svelte", ".astro"})
_CODE_EXTENSIONS = _TYPESCRIPT_EXTENSIONS | _PYTHON_EXTENSIONS | _OTHER_CODE_EXTENSIONS | _SCREEN_CODE_EXTENSIONS
_EMIL_DESIGN_ENG = "emil-design-eng"
_WEB_MOTION_TECHNIQUES = "web-motion-techniques"
_MOTION_SKILLS = (_UX_DESIGN, _EMIL_DESIGN_ENG, _WEB_MOTION_TECHNIQUES)
_MOTION_TARGET_SUFFIX = " with a motion value"
_MESSAGE_TARGET = "a message"
_CODEX_TARGET = "a `codex` prompt"
_CODEX_PROGRAM = "codex"
_CODEX_SUBCOMMANDS = frozenset({"exec", "e"})
_CODEX_VALUE_OPTIONS = frozenset(
    {
        "-m",
        "--model",
        "-c",
        "--config",
        "-s",
        "--sandbox",
        "-C",
        "--cd",
        "-o",
        "--output-last-message",
        "-p",
        "--profile",
        "-a",
        "--ask-for-approval",
        "--color",
        "--output-schema",
        "--add-dir",
        "--enable",
        "--disable",
        "--local-provider",
    },
)
# An option that takes one or more values up to the next option, such as several image files.
_CODEX_LIST_OPTIONS = frozenset({"-i", "--image"})
_OPTIONS_END = "--"
_STYLE_EXTENSIONS = _SCREEN_EXTENSIONS - _TYPESCRIPT_EXTENSIONS
_TOKEN_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml"})
_ARGUMENT_WRITERS = frozenset({"echo", "printf"})
_INPUT_WRITERS = frozenset({"cat", "tee"})
_EXISTING_TEXT_LIMIT = 2 * 1024 * 1024
_NEW_TEXT_KEYS = ("content", "new_string", "new_source")
_OLD_TEXT_KEY = "old_string"
_REPLACE_ALL_KEY = "replace_all"
_EVERY_MATCH = -1
_FIRST_MATCH = 1
_MESSAGE_TOOLS = frozenset({"SendMessage", "Agent", "mcp__ccd_session__start_session"})
_FILE_PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
_SHELL_TOOLS = frozenset({"Bash", "Monitor", "mcp__terminal__run_in_terminal"})
_COMMAND_KEY = "command"
_MCP_PREFIX = "mcp__"
_MCP_SEPARATOR = "__"
_DESIGN_CODE_TOOL = "use_figma"
_DESIGN_CODE_KEY = "code"
_DESIGN_TOOL_NAMES = frozenset({_DESIGN_CODE_TOOL, "upload_assets", "create_new_file", "send_code_to_figma"})
_SHORT_OPTION_CLUSTER = re.compile(r"-[A-Za-z]{2,}")
_OPTION_PREFIX = "-"
_STANDARD_INPUT = "-"
_UV = "uv"
_UV_RUN = "run"
_PYTHON_PROGRAM = re.compile(r"(?:python|pypy)\d*(?:\.\d+)?")
_JAVASCRIPT_PROGRAMS = frozenset({"node", "nodejs", "bun", "tsx", "ts-node"})
_INLINE_PROGRAM_TARGET = "this command's inline program"
_DENY_REASON = "Writing {target} needs {skills} loaded with the Skill tool first (CLAUDE.md \"Skill Triggers\"), and {owner} has no finished Skill-tool load of {pronoun} since {since}."
_QUESTION_DENY_NOTE = " The session and commit skills say which of these steps are taken without asking."
_SESSION_OWNER = "this session"
_SESSION_SINCE = "its start or last compaction"
_AGENT_OWNER = "this agent"
_AGENT_SINCE = "it started (its parent session's loads do not count here)"


@dataclass(frozen=True)
class Requirement:
    """What a tool call writes, as the refusal names it, and the skills that must be loaded before it."""

    target: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class _ProgramRules:
    skills: tuple[str, ...]
    inline_options: frozenset[str]
    options_with_argument: frozenset[str]
    module_option: str | None


@dataclass(frozen=True)
class _CompactionOutcome:
    """A settled compaction: the skills it restored, or `happened` False when the conversation went on without one."""

    happened: bool
    restored: tuple[str, ...]


@dataclass(frozen=True)
class _FileChange:
    """A file before and after a write, and the ranges of `after` an Edit really changed (none for a whole Write)."""

    before: str
    after: str
    changed: tuple[tuple[int, int], ...]


_PYTHON_RULES = _ProgramRules(
    (_CODE_STYLE, _PYTHON),
    frozenset({"-c"}),
    frozenset({"-W", "-X", "--check-hash-based-pycs"}),
    "-m",
)
_JAVASCRIPT_RULES = _ProgramRules(
    (_CODE_STYLE, _TYPESCRIPT),
    frozenset({"-e", "--eval", "-p", "--print"}),
    frozenset({"-r", "--require", "--import", "--loader", "--input-type", "-C"}),
    None,
)


def main() -> None:
    try:
        output = handle_event(json.load(sys.stdin), os.environ.get(_GATE_VARIABLE, ""))

        if output:
            print(json.dumps(output))
    except Exception as error:
        print(f"skill-gate-hook: {type(error).__name__}: {error}", file=sys.stderr)


def handle_event(hook_input: dict[str, Any], gate_setting: str) -> dict[str, Any] | None:
    """Record the loads and compactions an event reports; for a gated call that lacks its skills, the refusal."""
    event = hook_input.get(_EVENT_KEY)

    if event == _PRE_TOOL_USE and gate_setting != _GATE_OFF:
        return _make_decision(hook_input)
    elif event == _POST_TOOL_USE and hook_input.get(_TOOL_NAME_KEY) == _SKILL_TOOL:
        _record_skill_load(hook_input, str((hook_input.get(_TOOL_INPUT_KEY) or {}).get(_SKILL_KEY) or ""))
        return None
    elif event == _PROMPT_EXPANSION and hook_input.get(_EXPANSION_TYPE_KEY) == _SLASH_COMMAND:
        _record_skill_load(hook_input, str(hook_input.get(_COMMAND_NAME_KEY) or ""))
        return None
    elif event == _PRE_COMPACT:
        _record_compaction(hook_input)
        return None
    else:
        return None


def find_requirement(tool_name: str, tool_input: dict[str, Any], cwd: str | None) -> Requirement | None:
    file_path = tool_input.get(_FILE_PATH_KEYS.get(tool_name, ""))

    if isinstance(file_path, str) and file_path:
        return _find_file_requirement(file_path, tool_input)
    elif tool_name in _SHELL_TOOLS:
        return _find_command_requirement(str(tool_input.get(_COMMAND_KEY) or ""), cwd)
    elif _is_design_tool(tool_name):
        design_tool = tool_name.rsplit(_MCP_SEPARATOR, 1)[-1]

        return _make_requirement(f"with `{design_tool}`", _list_design_tool_skills(design_tool, tool_input))
    elif tool_name in (_QUESTION_TOOL, _WRAP_UP_TOOL):
        return _find_question_requirement(tool_name, tool_input)
    elif tool_name in _MESSAGE_TOOLS:
        return _find_message_requirement(tool_input)
    else:
        return None


def _find_file_requirement(path: str, tool_input: dict[str, Any]) -> Requirement | None:
    target = f"`{os.path.basename(path)}`"

    if _is_motion_write(path, tool_input):
        return _make_motion_requirement(target, list_file_skills(path))
    else:
        return _make_requirement(target, list_file_skills(path))


def _find_message_requirement(tool_input: dict[str, Any]) -> Requirement | None:
    """A brief to another session, a subagent, or a new session that sets a motion value needs the motion skills."""
    texts = [value for value in tool_input.values() if isinstance(value, str)]

    if has_motion_text(_LINE_END.join(texts)):
        return _make_motion_requirement(_MESSAGE_TARGET, [])
    else:
        return None


def _is_motion_write(path: str, tool_input: dict[str, Any]) -> bool:
    """Whether a file write sets or changes motion; other code files are not read for it."""
    kind = _find_motion_kind(path)

    if kind:
        change = _read_change(path, tool_input)

        return is_motion_change(kind, change.before, change.after, change.changed)
    else:
        return False


def _find_motion_kind(path: str) -> TextKind | None:
    extension = os.path.splitext(path)[1].lower()

    if extension in _STYLE_EXTENSIONS:
        return TextKind.STYLE
    elif extension in _TYPESCRIPT_EXTENSIONS:
        return TextKind.SCRIPT
    elif extension in _CODE_EXTENSIONS:
        return None
    elif extension in _TOKEN_EXTENSIONS:
        return TextKind.TOKENS
    else:
        return TextKind.PROSE


def _read_change(path: str, tool_input: dict[str, Any]) -> _FileChange:
    """The file before and after the call: an Edit applied to the file it changes, or a Write over the file it replaces.

    When the file cannot be read, or an Edit's text is not in it, only the
    replaced and the new text are compared.
    """
    new_text = next((value for value in map(tool_input.get, _NEW_TEXT_KEYS) if isinstance(value, str)), "")
    old_text = tool_input.get(_OLD_TEXT_KEY)
    existing = _read_existing_text(path)

    if isinstance(old_text, str) and existing is not None and old_text and old_text in existing:
        return _apply_edit(existing, old_text, new_text, bool(tool_input.get(_REPLACE_ALL_KEY)))
    elif isinstance(old_text, str):
        return _FileChange(old_text, new_text, ())
    else:
        return _FileChange(existing or "", new_text, ())


def _apply_edit(existing: str, old_text: str, new_text: str, is_every_match: bool) -> _FileChange:
    """The file after an Edit and the ranges that really changed, leaving out what both strings share at each end."""
    starts = _list_match_starts(existing, old_text, is_every_match)
    prefix = len(os.path.commonprefix([old_text, new_text]))
    shared_suffix = len(os.path.commonprefix([old_text[::-1], new_text[::-1]]))
    suffix = min(shared_suffix, min(len(old_text), len(new_text)) - prefix)
    growth = len(new_text) - len(old_text)
    changed = tuple(
        (start + order * growth + prefix, start + order * growth + len(new_text) - suffix)
        for order, start in enumerate(starts)
    )
    count = _EVERY_MATCH if is_every_match else _FIRST_MATCH

    return _FileChange(existing, existing.replace(old_text, new_text, count), changed)


def _list_match_starts(text: str, part: str, is_every_match: bool) -> list[int]:
    starts = [match.start() for match in re.finditer(re.escape(part), text)]

    return starts if is_every_match else starts[:_FIRST_MATCH]


def _read_existing_text(path: str) -> str | None:
    """The file's text, or None when it is missing, not a regular file, unreadable, or too large to compare."""
    try:
        is_comparable = os.path.isfile(path) and os.path.getsize(path) <= _EXISTING_TEXT_LIMIT

        return Path(path).read_text(encoding=_ENCODING, errors="replace") if is_comparable else None
    except OSError:
        return None


def _make_motion_requirement(target: str, skills: Iterable[str]) -> Requirement | None:
    return _make_requirement(f"{target}{_MOTION_TARGET_SUFFIX}", [*skills, *_MOTION_SKILLS])


def _find_question_requirement(tool_name: str, tool_input: dict[str, Any]) -> Requirement | None:
    texts = _list_question_texts(tool_name, tool_input)
    action_names = set(_list_item_values(tool_input.get(_ACTIONS_KEY), _ACTION_KEY))
    skills: list[str] = []

    if _is_choice_question(tool_name, tool_input):
        skills.append(_DECISIONS)
    if _SESSION_ACTION in action_names or any(_SESSION_WORDS.search(text) for text in texts):
        skills.append(_SESSION_MANAGEMENT)
    if action_names & _COMMIT_ACTIONS or any(_COMMIT_WORDS.search(text) for text in texts):
        skills.append(_GIT_COMMIT)
    target = "a question " + " and ".join(_QUESTION_TARGET_PARTS[skill] for skill in skills)

    return _make_requirement(target, skills)


def _list_question_texts(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """The question and its choice labels; app action names are read apart, as names rather than words."""
    if tool_name == _QUESTION_TOOL:
        questions = [question for question in _read_list(tool_input.get(_QUESTIONS_KEY)) if isinstance(question, dict)]
        texts: list[str] = []

        for question in questions:
            labels = _list_item_values(question.get(_OPTIONS_KEY), _LABEL_KEY)

            texts.extend([str(question.get(_QUESTION_KEY) or ""), *labels])
        return texts
    else:
        return [str(tool_input.get(_ASK_KEY) or ""), *_list_item_values(tool_input.get(_ACTIONS_KEY), _LABEL_KEY)]


def _list_item_values(items: Any, key: str) -> list[str]:
    """One field of each object in a list that may arrive as JSON text."""
    return [str(item.get(key) or "") for item in _read_list(items) if isinstance(item, dict)]


def _is_choice_question(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """A question dialog with two or more options, or a next-step box with two or more buttons."""
    if tool_name == _QUESTION_TOOL:
        questions = [question for question in _read_list(tool_input.get(_QUESTIONS_KEY)) if isinstance(question, dict)]

        return any(len(_read_list(question.get(_OPTIONS_KEY))) >= _CHOICE_COUNT for question in questions)
    elif tool_name == _WRAP_UP_TOOL:
        return len(_read_list(tool_input.get(_ACTIONS_KEY))) >= _CHOICE_COUNT
    else:
        return False


def _read_list(value: Any) -> list[Any]:
    """A list as given, or as JSON text, which is how a tool's array argument sometimes arrives."""
    if isinstance(value, list):
        return value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        else:
            return parsed if isinstance(parsed, list) else []
    else:
        return []


def _list_design_tool_skills(design_tool: str, tool_input: dict[str, Any]) -> list[str]:
    """A design write needs `ux-design`; plugin code of several lines is a one-off JavaScript program too."""
    code = tool_input.get(_DESIGN_CODE_KEY)
    is_code_program = design_tool == _DESIGN_CODE_TOOL and isinstance(code, str) and _is_multi_line(code)

    return [_UX_DESIGN, *_JAVASCRIPT_RULES.skills] if is_code_program else [_UX_DESIGN]


def list_file_skills(path: str) -> list[str]:
    extension = os.path.splitext(path)[1].lower()
    code = [_CODE_STYLE, *_list_language_skills(extension)] if extension in _CODE_EXTENSIONS else []
    screen = [_UX_DESIGN, _FRONTEND_DESIGN] if extension in _SCREEN_EXTENSIONS else []
    report = [_REPORT_WRITING] if is_notes_report(path) else []

    return [*code, *screen, *report]


def _list_language_skills(extension: str) -> list[str]:
    if extension in _TYPESCRIPT_EXTENSIONS:
        return [_TYPESCRIPT]
    elif extension in _PYTHON_EXTENSIONS:
        return [_PYTHON]
    else:
        return []


def _find_command_requirement(command: str, cwd: str | None) -> Requirement | None:
    """The skills a command line needs; what a script file on disk does when the line runs it is not written here."""
    try:
        expanded = expand_shell_command(command, cwd, _get_config_directory())
    except ExpansionLimitError:
        return None
    else:
        calls = [call for call in expanded if not call.is_from_script_file]
        skills = [skill for call in calls for skill in _list_call_skills(call)]
        motion_targets = [target for target in (_find_call_motion_target(call, cwd) for call in calls) if target]

        if motion_targets:
            return _make_motion_requirement(motion_targets[0], skills)
        else:
            return _make_requirement(_name_command_target(calls), skills)


def _name_command_target(calls: Sequence[CommandCall]) -> str:
    written = [path for call in calls for path in call.list_written_files() if list_file_skills(path)]

    return f"`{os.path.basename(written[0])}`" if written else _INLINE_PROGRAM_TARGET


def _find_call_motion_target(call: CommandCall, cwd: str | None) -> str | None:
    """What a call writes motion into: a file it writes literal text to or edits in place, or a `codex` prompt."""
    program = os.path.basename(call.argv[0]) if call.argv else ""
    files = [f"`{os.path.basename(path)}`" for path in _list_motion_files(call, program, cwd)]

    if files:
        return files[0]
    elif program == _CODEX_PROGRAM and has_motion_text(_LINE_END.join(_list_codex_prompts(call))):
        return _CODEX_TARGET
    else:
        return None


def _list_motion_files(call: CommandCall, program: str, cwd: str | None) -> list[str]:
    """The files that receive motion: echo and printf write their arguments, cat and tee their input.

    In-place editors count by their expression and the file they edit. Any
    other program's output is not known without running it, so its input does
    not count.
    """
    paths = call.list_written_files()
    written = [(path, _find_motion_kind(path)) for path in paths]
    arguments = " ".join(argument for argument in call.argv[1:] if argument not in paths)
    literal = _read_literal_output(call, program, arguments)

    if literal is not None:
        return [path for path, kind in written if kind and is_motion_change(kind, "", literal)]
    elif program in IN_PLACE_EDITORS:
        return [path for path, kind in written if kind and _is_in_place_motion(kind, arguments, path, call.cwd or cwd)]
    else:
        return []


def _read_literal_output(call: CommandCall, program: str, arguments: str) -> str | None:
    if program in _ARGUMENT_WRITERS:
        return arguments
    elif program in _INPUT_WRITERS:
        return _LINE_END.join(call.stdin_texts)
    else:
        return None


def _is_in_place_motion(kind: TextKind, expression: str, path: str, cwd: str | None) -> bool:
    full_path = os.path.join(cwd, path) if cwd else path

    return has_in_place_motion(kind, expression, _read_existing_text(full_path) or "")


def _list_codex_prompts(call: CommandCall) -> list[str]:
    """The prompt operands of a `codex` command line, then its input, which codex appends to a prompt given too."""
    prompts: list[str] = []
    takes_value = False
    takes_list = False
    is_after_options_end = False

    for argument in call.argv[1:]:
        is_option = not is_after_options_end and argument.startswith(_OPTION_PREFIX) and argument != _STANDARD_INPUT
        is_value = not is_option and not is_after_options_end and (takes_value or takes_list)
        is_subcommand = not prompts and argument in _CODEX_SUBCOMMANDS

        if not (is_option or is_value or is_subcommand):
            prompts.append(argument)
        is_after_options_end = is_after_options_end or argument == _OPTIONS_END
        takes_value = is_option and argument in _CODEX_VALUE_OPTIONS
        takes_list = (is_option and argument in _CODEX_LIST_OPTIONS) or (takes_list and is_value)
    return [*prompts, *call.stdin_texts]


def _list_call_skills(call: CommandCall) -> list[str]:
    written = [skill for path in call.list_written_files() for skill in list_file_skills(path)]

    return [*written, *_list_program_skills(call)]


def _list_program_skills(call: CommandCall) -> list[str]:
    argv = _skip_uv_run(call.argv)
    rules = _find_program_rules(os.path.basename(argv[0])) if argv else None

    if rules and _is_inline_program(argv[1:], call.stdin_texts, rules):
        return list(rules.skills)
    else:
        return []


def _find_program_rules(program: str) -> _ProgramRules | None:
    if _PYTHON_PROGRAM.fullmatch(program):
        return _PYTHON_RULES
    elif program in _JAVASCRIPT_PROGRAMS:
        return _JAVASCRIPT_RULES
    else:
        return None


def _skip_uv_run(argv: list[str]) -> list[str]:
    """The command line `uv run [options] <command> ...` runs, or argv itself for any other program."""
    is_uv_run = len(argv) > 2 and os.path.basename(argv[0]) == _UV and argv[1] == _UV_RUN
    start = _find_uv_run_command(argv) if is_uv_run else None

    return argv[start:] if start is not None else argv


def _find_uv_run_command(argv: Sequence[str]) -> int | None:
    """Where the command after `uv run` starts: the first word that is an interpreter or that follows no option.

    A word right after an option may be that option's value (`--with rich`),
    so it is skipped unless it names an interpreter.
    """
    for index in range(2, len(argv)):
        argument = argv[index]
        is_interpreter = _find_program_rules(os.path.basename(argument)) is not None
        is_option_value = index > 2 and argv[index - 1].startswith(_OPTION_PREFIX)

        if is_interpreter or not (argument.startswith(_OPTION_PREFIX) or is_option_value):
            return index
    return None


def _is_inline_program(arguments: Sequence[str], stdin_texts: Sequence[str], rules: _ProgramRules) -> bool:
    """Whether the interpreter runs a program written on the command line rather than a file or module.

    A program of several lines passed with an inline option, or one read from a
    here-document or pipe when no script operand is given, counts; a one-line
    expression such as `python3 -c 'print(1)'` does not. A cluster of short
    options (`-Sc`) acts as its last option.
    """
    index = 0

    while index < len(arguments):
        argument = arguments[index]
        option = f"{_OPTION_PREFIX}{argument[-1]}" if _SHORT_OPTION_CLUSTER.fullmatch(argument) else argument

        if option in rules.inline_options:
            return index + 1 < len(arguments) and _is_multi_line(arguments[index + 1])
        elif option == rules.module_option:
            return False
        elif option in rules.options_with_argument:
            index += 2
        elif argument.startswith(_OPTION_PREFIX) and argument != _STANDARD_INPUT:
            index += 1
        else:
            return argument == _STANDARD_INPUT and bool(stdin_texts)
    return bool(stdin_texts)


def _make_requirement(target: str, skills: Iterable[str]) -> Requirement | None:
    unique = tuple(dict.fromkeys(skills))

    if unique:
        return Requirement(target, unique)
    else:
        return None


def _make_decision(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(hook_input.get(_TOOL_NAME_KEY) or "")
    tool_input = hook_input.get(_TOOL_INPUT_KEY) or {}
    cwd = hook_input.get(_CWD_KEY)
    requirement = find_requirement(tool_name, tool_input, cwd) if _is_gated(hook_input) else None
    missing = _list_missing_skills(hook_input, requirement.skills) if requirement else []

    if requirement and missing:
        return {
            _HOOK_OUTPUT_KEY: {
                _HOOK_EVENT_NAME_KEY: _PRE_TOOL_USE,
                _PERMISSION_DECISION_KEY: _DENY,
                _PERMISSION_REASON_KEY: _make_deny_reason(hook_input, requirement.target, missing),
            },
        }
    else:
        return None


def _make_deny_reason(hook_input: dict[str, Any], target: str, missing: Sequence[str]) -> str:
    is_agent = bool(hook_input.get(_AGENT_ID_KEY)) and hook_input.get(_AGENT_TYPE_KEY) != _FORK_AGENT_TYPE
    reason = _DENY_REASON.format(
        target=target,
        skills=" and ".join(f"`{skill}`" for skill in missing),
        owner=_AGENT_OWNER if is_agent else _SESSION_OWNER,
        pronoun="them" if len(missing) > 1 else "it",
        since=_AGENT_SINCE if is_agent else _SESSION_SINCE,
    )

    if _SESSION_MANAGEMENT in missing or _GIT_COMMIT in missing:
        return reason + _QUESTION_DENY_NOTE
    else:
        return reason


def _list_missing_skills(hook_input: dict[str, Any], required: Sequence[str]) -> list[str]:
    loaded = {skill for record in _list_record_paths(hook_input) for skill in _settle_record_skills(record)}

    return [skill for skill in required if not _is_loaded(skill, loaded)]


def _record_skill_load(hook_input: dict[str, Any], skill: str) -> None:
    name = skill.strip().lstrip("/")

    if name:
        _append_event(_list_record_paths(hook_input)[0], {_KIND_KEY: _LOAD_KIND, _SKILL_KEY: name})


def _record_compaction(hook_input: dict[str, Any]) -> None:
    transcript = _find_transcript(hook_input)

    if transcript:
        event = {_KIND_KEY: _COMPACTION_KIND, _TRANSCRIPT_KEY: str(transcript), _OFFSET_KEY: transcript.stat().st_size}

        _append_event(_list_record_paths(hook_input)[0], event)


def _find_transcript(hook_input: dict[str, Any]) -> Path | None:
    """The transcript of the conversation being compacted; a subagent keeps its own beside the session's."""
    transcript_path = str(hook_input.get(_TRANSCRIPT_PATH_KEY) or "")
    agent_id = str(hook_input.get(_AGENT_ID_KEY) or "")
    session_id = str(hook_input.get(_SESSION_ID_KEY) or "")
    main_transcript = Path(transcript_path) if transcript_path else None

    if main_transcript and agent_id:
        agent_name = f"{_AGENT_TRANSCRIPT_PREFIX}{agent_id}{_RECORD_SUFFIX}"
        agent_transcript = main_transcript.parent / session_id / _SUBAGENTS_DIRECTORY_NAME / agent_name

        return agent_transcript if agent_transcript.exists() else None
    elif main_transcript and main_transcript.exists():
        return main_transcript
    else:
        return None


def _list_record_paths(hook_input: dict[str, Any]) -> list[Path]:
    """The records whose loads count for the caller, its own first, where its loads are written."""
    session_directory = _get_record_directory() / _to_safe_name(str(hook_input.get(_SESSION_ID_KEY) or ""))
    main_record = session_directory / f"{_MAIN_RECORD_NAME}{_RECORD_SUFFIX}"
    agent_id = str(hook_input.get(_AGENT_ID_KEY) or "")
    agent_record = session_directory / f"{_to_safe_name(agent_id)}{_RECORD_SUFFIX}"

    if agent_id and hook_input.get(_AGENT_TYPE_KEY) == _FORK_AGENT_TYPE:
        return [agent_record, main_record]
    elif agent_id:
        return [agent_record]
    else:
        return [main_record]


def _settle_record_skills(record: Path) -> set[str]:
    """The skills a record counts now, after adding to it how a pending compaction settled.

    A compaction keeps only what it restored, or everything when the
    conversation went on without it. Its outcome is read from the transcript
    once and then recorded, so later calls do not read the transcript again.
    """
    loaded: set[str] = set()
    before: set[str] = set()
    pending: dict[str, Any] | None = None

    for event in _read_events(record):
        kind = event.get(_KIND_KEY)

        if kind == _LOAD_KIND:
            loaded.add(str(event.get(_SKILL_KEY)))
        elif kind == _COMPACTION_KIND:
            before = before | loaded if pending else loaded
            loaded = set()
            pending = event
        elif kind == _RESTORED_KIND:
            loaded |= {str(skill) for skill in event.get(_SKILLS_KEY) or []}
            before = set()
            pending = None
    outcome = _read_compaction_outcome(pending) if pending else None
    kept = (set(outcome.restored) if outcome.happened else before) if outcome else set()

    if outcome:
        _append_event(record, {_KIND_KEY: _RESTORED_KIND, _SKILLS_KEY: sorted(kept)})
    return loaded | kept


def _read_compaction_outcome(compaction: dict[str, Any]) -> _CompactionOutcome | None:
    """How a compaction settled, or None while that is not in a readable transcript yet."""
    try:
        with open(str(compaction.get(_TRANSCRIPT_KEY)), "rb") as transcript:
            transcript.seek(int(compaction.get(_OFFSET_KEY) or 0))
            lines = [transcript.readline() for _ in range(_MAX_RESTORE_SCAN_LINES)]
    except (OSError, ValueError):
        return None
    else:
        return _find_compaction_outcome(lines)


def _find_compaction_outcome(lines: Sequence[bytes]) -> _CompactionOutcome | None:
    """Scan the lines after a compaction began, up to the model's next reply.

    The boundary, the summary, a manual compaction's command lines, and the
    restored attachments all come before that reply, so a reply with no
    invoked_skills attachment before it means nothing was restored, and a reply
    before any boundary means the compaction never happened.
    """
    boundary_seen = False

    for line in lines:
        is_candidate = boundary_seen or any(marker in line for marker in _TRANSCRIPT_MARKERS)
        entry = _parse_entry(line.decode(_ENCODING, errors="replace")) if is_candidate else None

        if entry and entry.get(_TYPE_KEY) == _ASSISTANT_TYPE:
            return _CompactionOutcome(boundary_seen, ())
        elif entry and not boundary_seen:
            boundary_seen = entry.get(_SUBTYPE_KEY) == _COMPACT_BOUNDARY
        elif entry and _is_restored_skills_entry(entry):
            restored = tuple(str(skill.get(_NAME_KEY)) for skill in entry[_ATTACHMENT_KEY].get(_SKILLS_KEY) or [])

            return _CompactionOutcome(True, restored)
    return None


def _read_events(record: Path) -> list[dict[str, Any]]:
    if record.exists():
        entries = [_parse_entry(line) for line in record.read_text(encoding=_ENCODING, errors="replace").splitlines()]

        return [entry for entry in entries if entry]
    else:
        return []


def _append_event(record: Path, event: dict[str, Any]) -> None:
    """Append one event line; a session's first record also clears records past their lifetime."""
    is_new_session = not record.parent.exists()

    record.parent.mkdir(parents=True, exist_ok=True)
    with open(record, "a", encoding=_ENCODING) as stream:
        stream.write(json.dumps({**event, _TIME_KEY: time.time()}) + _LINE_END)
    if is_new_session:
        _remove_expired_records(record.parent.parent)


def _remove_expired_records(directory: Path) -> None:
    """Drop record files no event touched for the lifetime, then the session folders left empty.

    A file's own time counts, since appending to a file leaves its folder's
    time unchanged. Another session may clean up at the same moment, so a
    failure here is only reported.
    """
    try:
        deadline = time.time() - _RECORD_LIFETIME_SECONDS

        for record in directory.glob(f"*/*{_RECORD_SUFFIX}"):
            if record.stat().st_mtime < deadline:
                record.unlink()
        for session_directory in directory.iterdir():
            if session_directory.is_dir() and not any(session_directory.iterdir()):
                session_directory.rmdir()
    except OSError as error:
        print(f"skill-gate-hook: could not clear old records: {error}", file=sys.stderr)


def _parse_entry(line: str) -> dict[str, Any] | None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    else:
        return entry if isinstance(entry, dict) else None


def _get_config_directory() -> Path:
    return Path(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY).expanduser()


def _get_record_directory() -> Path:
    return _get_config_directory() / _RECORD_DIRECTORY_NAME


def _to_safe_name(text: str) -> str:
    return _UNSAFE_NAME_CHARACTERS.sub(_EMPTY_NAME, text) or _EMPTY_NAME


def _is_gated(hook_input: dict[str, Any]) -> bool:
    """The main thread, and subagent types that have the Skill tool; any other agent could never pass."""
    return not hook_input.get(_AGENT_ID_KEY) or hook_input.get(_AGENT_TYPE_KEY) in _SKILL_AGENT_TYPES


def _is_loaded(skill: str, loaded: set[str]) -> bool:
    """A plugin skill loads as `plugin:skill`, so a name ending in `:skill` counts too."""
    return any(name == skill or name.endswith(f":{skill}") for name in loaded)


def _is_restored_skills_entry(entry: dict[str, Any]) -> bool:
    attachment = entry.get(_ATTACHMENT_KEY)

    return isinstance(attachment, dict) and attachment.get(_TYPE_KEY) == _INVOKED_SKILLS_TYPE


def _is_design_tool(tool_name: str) -> bool:
    return tool_name.startswith(_MCP_PREFIX) and tool_name.rsplit(_MCP_SEPARATOR, 1)[-1] in _DESIGN_TOOL_NAMES


def _is_multi_line(text: str) -> bool:
    return _LINE_END in text.strip()


if __name__ == "__main__":
    main()
