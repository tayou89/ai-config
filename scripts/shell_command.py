"""Expand a shell command line into the simple commands it would run.

PreToolUse hooks use this to see through quoting, chaining, command
substitution, here-documents, shell aliases and functions, wrappers
such as sudo or env, and commands handed to another shell (sh -c,
eval, source, shell scripts, git aliases). Expansion is conservative:
a construct that cannot be resolved still yields every command it
might run, and ExpansionLimitError is raised instead of giving up
silently when the expansion grows too deep.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


GitAliasReader = Callable[[Optional[str], str], Optional[str]]
TextFileReader = Callable[[str], Optional[str]]

VARIABLE_MARK = "\x00"
UNKNOWN_MARK = "\x01"
DYNAMIC_PLACEHOLDER = "${...}"
MAX_EXPANSION_DEPTH = 12
MAX_EXPANDED_COMMANDS = 4000
MAX_SCRIPT_BYTES = 256 * 1024
GIT_TIMEOUT_SECONDS = 3
SNAPSHOT_DIRECTORY_NAME = "shell-snapshots"
SNAPSHOT_FILE_PATTERN = "snapshot-*.sh"
SNAPSHOT_LIMIT = 5
WORD_BREAK_CHARACTERS = frozenset(" \t\n;&|()")
OPERATORS = (";;&", ";;", ";&", "&&", "||", "|&", "|", "&", ";")
PIPE_OPERATORS = frozenset({"|", "|&"})
REDIRECTION_PATTERN = re.compile(r"&>>|&>|\d*(?:<<<|<<-|<<|<>|<&|>&|>>|>\||<|>)")
HEREDOC_OPERATORS = frozenset({"<<", "<<-"})
HERE_STRING_OPERATOR = "<<<"
NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SPECIAL_PARAMETER_CHARACTERS = frozenset("0123456789@*#?$!-")
ASSIGNMENT_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=")
MARKED_VARIABLE_PATTERN = re.compile(f"{VARIABLE_MARK}([A-Za-z_][A-Za-z0-9_]*){VARIABLE_MARK}")
ANSI_C_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
ANSI_C_NUMERIC_ESCAPE_PATTERN = re.compile(r"x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|([0-7]{1,3})")
ALIAS_LINE_PATTERN = re.compile(r"^alias (?:-- )?([^=\s]+)=(.*)$")
FUNCTION_START_PATTERN = re.compile(
    r"^(?:function\s+([^\s()]+)\s*(?:\(\))?|([^\s()]+)\s*\(\))\s*(\{)?\s*$"
)
FUNCTION_END_PATTERN = re.compile(r"^\}\s*$")
SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish"})
SHELL_OPTIONS_WITH_ARGUMENT = frozenset({"--rcfile", "--init-file"})
GIT_EXECUTABLES = frozenset({"git", "git.exe", "hub"})
GIT_OPTIONS_WITH_ARGUMENT = frozenset({
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
    "--super-prefix",
})
GIT_BUILTIN_COMMANDS = frozenset({
    "add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch",
    "bugreport", "bundle", "cat-file", "check-attr", "check-ignore",
    "check-mailmap", "check-ref-format", "checkout", "checkout-index",
    "cherry", "cherry-pick", "citool", "clean", "clone", "column", "commit",
    "commit-graph", "commit-tree", "config", "count-objects", "credential",
    "describe", "diagnose", "diff", "diff-files", "diff-index", "diff-tree",
    "difftool", "fast-export", "fast-import", "fetch", "fetch-pack",
    "filter-branch", "fmt-merge-msg", "for-each-ref", "for-each-repo",
    "format-patch", "fsck", "gc", "get-tar-commit-id", "grep", "gui",
    "hash-object", "help", "hook", "index-pack", "init", "instaweb",
    "interpret-trailers", "log", "ls-files", "ls-remote", "ls-tree",
    "mailinfo", "mailsplit", "maintenance", "merge", "merge-base",
    "merge-file", "merge-index", "merge-tree", "mergetool", "mktag", "mktree",
    "multi-pack-index", "mv", "name-rev", "notes", "pack-objects",
    "pack-refs", "patch-id", "prune", "prune-packed", "pull", "push",
    "range-diff", "read-tree", "rebase", "receive-pack", "reflog", "remote",
    "repack", "replace", "replay", "request-pull", "rerere", "reset",
    "restore", "rev-list", "rev-parse", "revert", "rm", "send-email",
    "send-pack", "shortlog", "show", "show-branch", "show-index", "show-ref",
    "sparse-checkout", "stage", "stash", "status", "stripspace", "submodule",
    "switch", "symbolic-ref", "tag", "unpack-file", "unpack-objects",
    "update-index", "update-ref", "update-server-info", "upload-archive",
    "upload-pack", "var", "verify-commit", "verify-pack", "verify-tag",
    "version", "whatchanged", "worktree", "write-tree",
})
GIT_ALIAS_PREFIX = "git-alias:"
GIT_SUBMODULE_FOREACH_OPTIONS = frozenset({"--recursive", "-q", "--quiet"})
GIT_REBASE_EXEC_OPTIONS = frozenset({"-x", "--exec"})
RESERVED_LEADING_WORDS = frozenset({"if", "then", "elif", "else", "while", "until", "do", "!", "{", "coproc"})
RESERVED_CLOSING_WORDS = frozenset({"fi", "done", "esac", "}", "]]"})
FUNCTION_KEYWORDS = frozenset({"function"})
OPTION_END = "--"
SUDO_OPTIONS_WITH_ARGUMENT = frozenset({
    "-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t",
    "--user", "--group", "--host", "--prompt", "--close-from", "--chdir",
    "--chroot", "--role", "--type", "--other-user", "--command-timeout",
})
WRAPPER_OPTIONS_WITH_ARGUMENT = {
    "nohup": frozenset(),
    "builtin": frozenset(),
    "noglob": frozenset(),
    "nocorrect": frozenset(),
    "time": frozenset(),
    "unbuffer": frozenset(),
    "arch": frozenset(),
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "caffeinate": frozenset({"-t", "-w"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "ionice": frozenset({"-c", "-n", "-p"}),
    "xcrun": frozenset({"-sdk", "--sdk", "-toolchain", "--toolchain"}),
    "doas": frozenset({"-u", "-C"}),
    "sudo": SUDO_OPTIONS_WITH_ARGUMENT,
}
WRAPPER_DIRECTORY_OPTIONS = frozenset({"-D", "--chdir"})
COMMAND_LOOKUP_OPTIONS = frozenset({"-v", "-V"})
TIMEOUT_NAMES = frozenset({"timeout", "gtimeout"})
TIMEOUT_OPTIONS_WITH_ARGUMENT = frozenset({"-s", "--signal", "-k", "--kill-after"})
ENV_OPTIONS_WITH_ARGUMENT = frozenset({"-u", "--unset", "-P"})
ENV_DIRECTORY_OPTIONS = frozenset({"-C", "--chdir"})
ENV_SPLIT_OPTIONS = frozenset({"-S", "--split-string"})
ENV_IGNORE_ENVIRONMENT = "-"
ENV_ALL_OPTIONS_WITH_ARGUMENT = ENV_OPTIONS_WITH_ARGUMENT | ENV_DIRECTORY_OPTIONS | ENV_SPLIT_OPTIONS
XARGS_OPTIONS_WITH_ARGUMENT = frozenset({
    "-I", "-J", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "-R", "-S",
    "--max-args", "--max-procs", "--max-lines", "--delimiter",
    "--max-chars", "--eof", "--arg-file", "--replace",
})
XARGS_DEFAULT_COMMAND = "echo"
WATCH_OPTIONS_WITH_ARGUMENT = frozenset({"-n", "--interval", "-q", "--equexit"})
WATCH_EXEC_OPTIONS = frozenset({"-x", "--exec"})
FIND_EXEC_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
FIND_EXEC_TERMINATORS = frozenset({";", "+"})
CD_NAMES = frozenset({"cd", "pushd"})
CD_PREVIOUS_DIRECTORY = "-"
EVAL_NAME = "eval"
SOURCE_NAMES = frozenset({"source", "."})
ALIAS_BUILTIN = "alias"
PIPE_TEXT_COMMANDS = frozenset({"echo", "printf"})
PIPE_FILE_COMMANDS = frozenset({"cat"})
SHEBANG = "#!"
ENV_NAME = "env"


class ExpansionLimitError(Exception):
    """Raised when a command expands deeper or wider than the hook can follow."""


@dataclass
class Word:
    text: str
    quoted: bool
    unquoted_prefix: str


@dataclass
class SimpleCommand:
    words: list[Word] = field(default_factory=list)
    stdin_texts: list[str] = field(default_factory=list)
    piped_from: SimpleCommand | None = None


@dataclass
class CommandCall:
    argv: list[str]
    assignments: list[tuple[str, str]]
    cwd: str | None
    dynamic_name: bool
    unknown_arguments: bool
    origin: str


@dataclass
class GitCommand:
    subcommand: str
    arguments: list[str]
    global_options: list[str]
    config_values: dict[str, str]
    work_dir: str | None
    subcommand_index: int


@dataclass
class ShellDefinitions:
    aliases: dict[str, str]
    functions: dict[str, str]


@dataclass
class _PendingHeredoc:
    delimiter: str
    strip_tabs: bool
    expands: bool
    owner: SimpleCommand


@dataclass
class _Argument:
    text: str
    quoted: bool
    dynamic: bool
    unquoted_prefix: str


@dataclass
class _ExpansionState:
    cwd: str | None
    variables: dict[str, str]
    aliases: dict[str, str]
    active_names: frozenset[str]
    depth: int
    origin: str
    unknown_arguments: bool

    def enter(self, origin: str, name: str) -> _ExpansionState:
        if self.depth >= MAX_EXPANSION_DEPTH:
            raise ExpansionLimitError(f"expansion deeper than {MAX_EXPANSION_DEPTH} levels at {origin}")
        else:
            combined_origin = f"{self.origin} > {origin}" if self.origin else origin
            return dataclasses.replace(
                self,
                active_names=self.active_names | {name},
                depth=self.depth + 1,
                origin=combined_origin,
            )


class _Lexer:
    def __init__(self, text: str) -> None:
        self._text = text
        self._index = 0
        self._commands: list[SimpleCommand] = []
        self._pending_heredocs: list[_PendingHeredoc] = []

    def parse(self) -> list[SimpleCommand]:
        self._parse_sequence("")
        return self._commands

    def read_single_word(self) -> str:
        while self._index < len(self._text) and self._text[self._index] in " \t":
            self._index += 1
        return restore_marks(self._read_word().text)

    def scan_substitutions(self) -> list[SimpleCommand]:
        self._read_double_quoted("")
        return self._commands

    def _parse_sequence(self, terminator: str) -> None:
        current = SimpleCommand()

        while self._index < len(self._text) and not self._is_at_terminator(terminator):
            current = self._parse_next(current)
        if self._is_at_terminator(terminator):
            self._index += len(terminator)
        self._finish(current)

    def _parse_next(self, current: SimpleCommand) -> SimpleCommand:
        character = self._text[self._index]

        if character in " \t":
            self._index += 1
            return current
        elif self._text.startswith("\\\n", self._index):
            self._index += 2
            return current
        elif character == "\n":
            self._index += 1
            self._finish(current)
            self._read_heredoc_bodies()
            return SimpleCommand()
        elif character == "#":
            self._skip_comment()
            return current
        elif self._text.startswith("((", self._index) and not current.words:
            self._skip_balanced_parentheses()
            return current
        elif character == "(":
            self._index += 1
            self._finish(current)
            self._parse_sequence(")")
            return SimpleCommand()
        elif character == ")":
            self._index += 1
            self._finish(current)
            return SimpleCommand()
        elif self._is_process_substitution():
            current.words.append(self._read_word())
            return current
        elif REDIRECTION_PATTERN.match(self._text, self._index):
            self._read_redirection(current)
            return current
        elif character in ";&|":
            return self._read_operator(current)
        else:
            current.words.append(self._read_word())
            return current

    def _finish(self, command: SimpleCommand) -> None:
        if command.words or command.stdin_texts:
            self._commands.append(command)

    def _read_operator(self, current: SimpleCommand) -> SimpleCommand:
        operator = next(item for item in OPERATORS if self._text.startswith(item, self._index))
        following = SimpleCommand()

        self._index += len(operator)
        self._finish(current)
        if operator in PIPE_OPERATORS and current.words:
            following.piped_from = current
        return following

    def _read_redirection(self, current: SimpleCommand) -> None:
        match = REDIRECTION_PATTERN.match(self._text, self._index)
        operator = match.group().lstrip("0123456789")

        self._index = match.end()
        while self._index < len(self._text) and self._text[self._index] in " \t":
            self._index += 1
        has_target = self._index < len(self._text) and not self._is_word_break()
        target = self._read_word() if has_target else Word("", False, "")
        if operator in HEREDOC_OPERATORS:
            pending = _PendingHeredoc(restore_marks(target.text), operator == "<<-", not target.quoted, current)
            self._pending_heredocs.append(pending)
        elif operator == HERE_STRING_OPERATOR:
            current.stdin_texts.append(restore_marks(target.text))

    def _read_heredoc_bodies(self) -> None:
        for pending in self._pending_heredocs:
            body = self._read_heredoc_body(pending)

            pending.owner.stdin_texts.append(body)
            if pending.expands:
                self._commands.extend(_Lexer(body).scan_substitutions())
        self._pending_heredocs = []

    def _read_heredoc_body(self, pending: _PendingHeredoc) -> str:
        lines: list[str] = []

        while self._index < len(self._text):
            end = self._text.find("\n", self._index)
            line_end = end if end >= 0 else len(self._text)
            line = self._text[self._index:line_end]
            compared = line.lstrip("\t") if pending.strip_tabs else line

            self._index = line_end + 1
            if compared == pending.delimiter:
                break
            else:
                lines.append(line)
        return "\n".join(lines)

    def _read_word(self) -> Word:
        parts: list[str] = []
        prefix: list[str] = []
        start = self._index
        quoted = False
        literal = True

        while self._index < len(self._text) and not self._is_word_break():
            character = self._text[self._index]

            if self._text.startswith("\\\n", self._index):
                self._index += 2
            elif character == "\\":
                parts.append(self._text[self._index + 1:self._index + 2])
                self._index += 2
                quoted = True
                literal = False
            elif character == "'":
                parts.append(self._read_single_quoted())
                quoted = True
                literal = False
            elif character == '"':
                self._index += 1
                parts.append(self._read_double_quoted('"'))
                quoted = True
                literal = False
            elif character == "`":
                parts.append(self._read_backtick())
                literal = False
            elif character == "$":
                text, is_quoted = self._read_dollar(False)

                parts.append(text)
                quoted = quoted or is_quoted
                literal = False
            elif character in "<>":
                parts.append(self._read_process_substitution())
                literal = False
            else:
                parts.append(character)
                self._index += 1
                if literal:
                    prefix.append(character)
        if self._index == start:
            self._index += 1
        return Word("".join(parts), quoted, "".join(prefix))

    def _read_single_quoted(self) -> str:
        end = self._text.find("'", self._index + 1)
        closing = end if end >= 0 else len(self._text)
        content = self._text[self._index + 1:closing]

        self._index = closing + 1
        return content

    def _read_double_quoted(self, closing: str) -> str:
        parts: list[str] = []

        while self._index < len(self._text) and not (closing and self._text[self._index] == closing):
            character = self._text[self._index]
            following = self._text[self._index + 1:self._index + 2]

            if character == "\\" and following == "\n":
                self._index += 2
            elif character == "\\" and following in ('$', '`', '"', "\\"):
                parts.append(following)
                self._index += 2
            elif character == "$":
                parts.append(self._read_dollar(True)[0])
            elif character == "`":
                parts.append(self._read_backtick())
            else:
                parts.append(character)
                self._index += 1
        if closing and self._index < len(self._text):
            self._index += 1
        return "".join(parts)

    def _read_dollar(self, inside_double_quotes: bool) -> tuple[str, bool]:
        following = self._text[self._index + 1:self._index + 2]

        if following == "'" and not inside_double_quotes:
            return self._read_ansi_c_quoted(), True
        elif following == '"' and not inside_double_quotes:
            self._index += 2
            return self._read_double_quoted('"'), True
        elif self._text.startswith("$((", self._index):
            self._index += 1
            self._skip_balanced_parentheses()
            return UNKNOWN_MARK, False
        elif following == "(":
            self._index += 2
            self._parse_sequence(")")
            return UNKNOWN_MARK, False
        elif following == "{":
            return self._read_braced_parameter(), False
        elif NAME_PATTERN.match(self._text, self._index + 1):
            match = NAME_PATTERN.match(self._text, self._index + 1)

            self._index = match.end()
            return f"{VARIABLE_MARK}{match.group()}{VARIABLE_MARK}", False
        elif following and following in SPECIAL_PARAMETER_CHARACTERS:
            self._index += 2
            return UNKNOWN_MARK, False
        else:
            self._index += 1
            return "$", False

    def _read_ansi_c_quoted(self) -> str:
        parts: list[str] = []

        self._index += 2
        while self._index < len(self._text) and self._text[self._index] != "'":
            character = self._text[self._index]

            if character == "\\" and self._index + 1 < len(self._text):
                parts.append(self._read_ansi_c_escape())
            else:
                parts.append(character)
                self._index += 1
        self._index += 1
        return "".join(parts)

    def _read_ansi_c_escape(self) -> str:
        following = self._text[self._index + 1]
        hex_match = ANSI_C_NUMERIC_ESCAPE_PATTERN.match(self._text, self._index + 1)

        if following in ANSI_C_ESCAPES:
            self._index += 2
            return ANSI_C_ESCAPES[following]
        elif hex_match:
            digits = next(group for group in hex_match.groups() if group)
            base = 8 if hex_match.group(4) else 16

            self._index = hex_match.end()
            return chr(int(digits, base))
        else:
            self._index += 2
            return following

    def _read_braced_parameter(self) -> str:
        depth = 0
        end = self._index + 1

        while end < len(self._text):
            character = self._text[end]

            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        inner = self._text[self._index + 2:end]
        self._index = end + 1
        if NAME_PATTERN.fullmatch(inner):
            return f"{VARIABLE_MARK}{inner}{VARIABLE_MARK}"
        else:
            self._commands.extend(_Lexer(inner).scan_substitutions())
            return UNKNOWN_MARK

    def _read_backtick(self) -> str:
        end = self._index + 1

        while end < len(self._text) and self._text[end] != "`":
            end += 2 if self._text[end] == "\\" else 1
        inner = re.sub(r"\\([`\\$])", r"\1", self._text[self._index + 1:end])
        self._index = end + 1
        self._commands.extend(_Lexer(inner).parse())
        return UNKNOWN_MARK

    def _read_process_substitution(self) -> str:
        self._index += 2
        self._parse_sequence(")")
        return UNKNOWN_MARK

    def _skip_balanced_parentheses(self) -> None:
        depth = 0

        while self._index < len(self._text):
            character = self._text[self._index]

            self._index += 1
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    break

    def _skip_comment(self) -> None:
        end = self._text.find("\n", self._index)

        self._index = end if end >= 0 else len(self._text)

    def _is_at_terminator(self, terminator: str) -> bool:
        return bool(terminator) and self._text.startswith(terminator, self._index)

    def _is_process_substitution(self) -> bool:
        return self._text[self._index] in "<>" and self._text[self._index + 1:self._index + 2] == "("

    def _is_word_break(self) -> bool:
        character = self._text[self._index]

        if character in WORD_BREAK_CHARACTERS:
            return True
        elif character in "<>":
            return not self._is_process_substitution()
        else:
            return False


class CommandExpander:
    """Turn a command line into CommandCall entries, one per simple command it may run."""

    def __init__(
        self,
        definitions: ShellDefinitions,
        read_git_alias: GitAliasReader,
        read_text_file: TextFileReader,
    ) -> None:
        self._definitions = definitions
        self._read_git_alias = read_git_alias
        self._read_text_file = read_text_file
        self._expanded_count = 0
        self._handlers = {
            ENV_NAME: self._expand_env,
            "xargs": self._expand_xargs,
            "find": self._expand_find,
            "watch": self._expand_watch,
            EVAL_NAME: self._expand_eval,
            ALIAS_BUILTIN: self._expand_alias_definition,
        }
        self._handlers.update({name: self._expand_wrapper for name in WRAPPER_OPTIONS_WITH_ARGUMENT})
        self._handlers.update({name: self._expand_timeout for name in TIMEOUT_NAMES})
        self._handlers.update({name: self._expand_shell for name in SHELL_NAMES})
        self._handlers.update({name: self._expand_source for name in SOURCE_NAMES})
        self._handlers.update({name: self._expand_cd for name in CD_NAMES})
        self._handlers.update({name: self._expand_git for name in GIT_EXECUTABLES})

    def expand(self, text: str, cwd: str | None) -> list[CommandCall]:
        state = _ExpansionState(
            cwd=cwd,
            variables={},
            aliases=dict(self._definitions.aliases),
            active_names=frozenset(),
            depth=0,
            origin="",
            unknown_arguments=False,
        )

        return self._expand_text(text, state)

    def _expand_text(self, text: str, state: _ExpansionState) -> list[CommandCall]:
        calls: list[CommandCall] = []

        for command in _Lexer(text).parse():
            calls.extend(self._expand_command(command, state))
        return calls

    def _expand_command(self, command: SimpleCommand, state: _ExpansionState) -> list[CommandCall]:
        arguments = [self._resolve_word(word, state) for word in command.words]
        stdin_texts = self._collect_stdin_texts(command, state)

        return self._expand_arguments(arguments, [], stdin_texts, state)

    def _expand_arguments(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        count = _count_leading_assignments(arguments)
        all_assignments = assignments + [_split_assignment(argument.text) for argument in arguments[:count]]
        remaining = arguments[count:]

        self._expanded_count += 1
        self._check_expansion_budget()
        if remaining:
            return self._expand_named(remaining, all_assignments, stdin_texts, state)
        elif all_assignments:
            state.variables.update(all_assignments)
            return [self._make_call([], all_assignments, state)]
        else:
            return []

    def _expand_named(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        name = arguments[0]
        base = os.path.basename(name.text)
        handler = self._handlers.get(base)

        if self._is_reserved(name, RESERVED_LEADING_WORDS) or self._is_reserved(name, RESERVED_CLOSING_WORDS):
            return self._expand_arguments(arguments[1:], assignments, stdin_texts, state)
        elif self._is_reserved(name, FUNCTION_KEYWORDS):
            return self._expand_arguments(arguments[2:], assignments, stdin_texts, state)
        elif self._is_expandable_name(name, state.aliases, state):
            return self._expand_alias(arguments, assignments, stdin_texts, state)
        elif self._is_expandable_name(name, self._definitions.functions, state):
            return self._expand_function(arguments, assignments, stdin_texts, state)
        elif handler and not name.dynamic:
            return handler(arguments, assignments, stdin_texts, state)
        elif "/" in name.text and not name.dynamic:
            return self._expand_script_file(name.text, state, True) + [self._make_call(arguments, assignments, state)]
        else:
            return [self._make_call(arguments, assignments, state)]

    def _expand_alias(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        name = arguments[0].text
        child = state.enter(f"alias {name}", name)
        commands = _Lexer(state.aliases[name]).parse() or [SimpleCommand()]
        calls: list[CommandCall] = []
        last = commands[-1]

        for command in commands[:-1]:
            calls.extend(self._expand_command(command, child))
        last_arguments = [self._resolve_word(word, child) for word in last.words] + arguments[1:]
        last_stdin = self._collect_stdin_texts(last, child) + stdin_texts
        calls.extend(self._expand_arguments(last_arguments, assignments, last_stdin, child))
        return calls

    def _expand_function(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        name = arguments[0].text
        child = state.enter(f"function {name}", name)
        same_name_command = dataclasses.replace(state, active_names=state.active_names | {name})
        body_calls = self._expand_text(self._definitions.functions[name], child)

        return body_calls + self._expand_named(arguments, assignments, stdin_texts, same_name_command)

    def _expand_wrapper(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        name = os.path.basename(arguments[0].text)
        options_with_argument = WRAPPER_OPTIONS_WITH_ARGUMENT[name]
        end = _find_options_end(arguments, options_with_argument)
        work_dir = state.cwd
        lookup_only = False

        for index in range(1, end):
            option, _, inline_value = arguments[index].text.partition("=")

            lookup_only = lookup_only or (name == "command" and option in COMMAND_LOOKUP_OPTIONS)
            if option in WRAPPER_DIRECTORY_OPTIONS and name == "sudo":
                work_dir = _option_directory(arguments, index, inline_value, work_dir)
        rest = arguments[_skip_option_end(arguments, end):]
        if lookup_only or not rest:
            return []
        else:
            return self._expand_arguments(rest, assignments, stdin_texts, dataclasses.replace(state, cwd=work_dir))

    def _expand_timeout(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        end = _find_options_end(arguments, TIMEOUT_OPTIONS_WITH_ARGUMENT)
        duration_index = _skip_option_end(arguments, end)

        return self._expand_arguments(arguments[duration_index + 1:], assignments, stdin_texts, state)

    def _expand_env(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        end = _find_options_end(arguments, ENV_ALL_OPTIONS_WITH_ARGUMENT, ENV_IGNORE_ENVIRONMENT)
        work_dir = state.cwd
        split_text = ""

        for index in range(1, end):
            option, _, inline_value = arguments[index].text.partition("=")

            if option in ENV_DIRECTORY_OPTIONS:
                work_dir = _option_directory(arguments, index, inline_value, work_dir)
            elif option in ENV_SPLIT_OPTIONS:
                split_text = inline_value or _text_at(arguments, index + 1)
        child = dataclasses.replace(state, cwd=work_dir)
        rest = arguments[_skip_option_end(arguments, end):]
        if split_text:
            split_arguments = [_Argument(word, False, False, word) for word in _split_words(split_text)]
            return self._expand_arguments(split_arguments + rest, assignments, stdin_texts, child)
        else:
            return self._expand_arguments(rest, assignments, stdin_texts, child)

    def _expand_xargs(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        end = _find_options_end(arguments, XARGS_OPTIONS_WITH_ARGUMENT)
        child = dataclasses.replace(state, unknown_arguments=True)
        default_command = [_Argument(XARGS_DEFAULT_COMMAND, False, False, XARGS_DEFAULT_COMMAND)]
        rest = arguments[_skip_option_end(arguments, end):] or default_command

        return self._expand_arguments(rest, assignments, [], child)

    def _expand_find(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        calls = [self._make_call(arguments, assignments, state)]
        child = dataclasses.replace(state, unknown_arguments=True)
        index = 1

        while index < len(arguments):
            if arguments[index].text in FIND_EXEC_ACTIONS:
                terminators = [
                    position
                    for position in range(index + 1, len(arguments))
                    if arguments[position].text in FIND_EXEC_TERMINATORS
                ]
                end = terminators[0] if terminators else len(arguments)

                calls.extend(self._expand_arguments(arguments[index + 1:end], [], [], child))
                index = end
            index += 1
        return calls

    def _expand_watch(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        end = _find_options_end(arguments, WATCH_OPTIONS_WITH_ARGUMENT)
        exec_mode = any(argument.text in WATCH_EXEC_OPTIONS for argument in arguments[1:end])
        rest = arguments[_skip_option_end(arguments, end):]

        if exec_mode:
            return self._expand_arguments(rest, assignments, [], state)
        else:
            return self._expand_text(" ".join(argument.text for argument in rest), state.enter("watch", "watch"))

    def _expand_shell(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        name = os.path.basename(arguments[0].text)
        child = state.enter(f"{name} -c", f"shell:{state.depth}")
        index = 1
        command_mode = False
        stdin_mode = False
        calls = [self._make_call(arguments, assignments, state)]

        while index < len(arguments) and _is_shell_option(arguments[index].text):
            option = arguments[index].text
            letters = option[1:] if not option.startswith(OPTION_END) else ""

            command_mode = command_mode or "c" in letters
            stdin_mode = stdin_mode or "s" in letters
            index += 2 if option in SHELL_OPTIONS_WITH_ARGUMENT or "o" in letters or "O" in letters else 1
            if option == OPTION_END:
                break
        positional = arguments[index:]
        if command_mode and positional:
            calls.extend(self._expand_text(positional[0].text, child))
        elif positional and not stdin_mode:
            calls.extend(self._expand_script_file(positional[0].text, child, False))
        else:
            for text in stdin_texts:
                calls.extend(self._expand_text(text, child))
        return calls

    def _expand_eval(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        text = " ".join(argument.text for argument in arguments[1:])

        return self._expand_text(text, state.enter(EVAL_NAME, f"eval:{state.depth}"))

    def _expand_source(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        calls = [self._make_call(arguments, assignments, state)]

        if len(arguments) > 1:
            calls.extend(self._expand_script_file(arguments[1].text, state, False))
        return calls

    def _expand_alias_definition(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        calls = [self._make_call(arguments, assignments, state)]

        for argument in arguments[1:]:
            name, separator, value = argument.text.partition("=")

            if separator and not _is_option(argument.text):
                state.aliases[name] = value
                calls.extend(self._expand_text(value, state.enter(f"alias {name}", f"alias-definition:{name}")))
        return calls

    def _expand_cd(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        targets = [
            argument
            for argument in arguments[1:]
            if not _is_option(argument.text) or argument.text == CD_PREVIOUS_DIRECTORY
        ]
        call = self._make_call(arguments, assignments, state)

        if not targets:
            state.cwd = str(Path.home())
        elif targets[0].dynamic or targets[0].text == CD_PREVIOUS_DIRECTORY:
            state.cwd = None
        else:
            state.cwd = join_directory(state.cwd, targets[0].text)
        return [call]

    def _expand_git(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        call = self._make_call(arguments, assignments, state)
        git_command = parse_git_command(call.argv, state.cwd)
        alias_value = self._find_git_alias(git_command, state) if git_command else ""

        if not git_command:
            return [call]
        elif alias_value:
            return self._expand_git_alias(arguments, git_command, alias_value, assignments, stdin_texts, state) + [call]
        else:
            return [call] + self._expand_git_nested(git_command, state)

    def _expand_git_alias(
        self,
        arguments: list[_Argument],
        git_command: GitCommand,
        value: str,
        assignments: list[tuple[str, str]],
        stdin_texts: list[str],
        state: _ExpansionState,
    ) -> list[CommandCall]:
        name = git_command.subcommand
        child = state.enter(f"git alias {name}", f"{GIT_ALIAS_PREFIX}{name}")

        if value.startswith("!"):
            text = " ".join([value[1:], *[shlex.quote(argument) for argument in git_command.arguments]])
            return self._expand_text(text, child)
        else:
            alias_words = [_Argument(word, False, False, word) for word in _split_words(value)]
            index = git_command.subcommand_index
            replaced = arguments[:index] + alias_words + arguments[index + 1:]
            return self._expand_arguments(replaced, assignments, stdin_texts, child)

    def _expand_git_nested(self, git_command: GitCommand, state: _ExpansionState) -> list[CommandCall]:
        arguments = git_command.arguments
        child = dataclasses.replace(state, cwd=git_command.work_dir)

        if git_command.subcommand == "submodule" and "foreach" in arguments:
            command_words = arguments[arguments.index("foreach") + 1:]
            rest = [argument for argument in command_words if argument not in GIT_SUBMODULE_FOREACH_OPTIONS]
            return self._expand_text(" ".join(rest), child.enter("git submodule foreach", f"git-nested:{state.depth}"))
        elif git_command.subcommand == "rebase":
            texts = _find_option_values(arguments, GIT_REBASE_EXEC_OPTIONS)
            nested = child.enter("git rebase --exec", f"git-nested:{state.depth}")
            return [call for text in texts for call in self._expand_text(text, nested)]
        elif git_command.subcommand == "bisect" and arguments[:1] == ["run"]:
            nested_arguments = [_Argument(argument, False, False, argument) for argument in arguments[1:]]
            nested = child.enter("git bisect run", f"git-nested:{state.depth}")
            return self._expand_arguments(nested_arguments, [], [], nested)
        else:
            return []

    def _expand_script_file(self, path: str, state: _ExpansionState, executed_directly: bool) -> list[CommandCall]:
        full_path = join_directory(state.cwd, path)
        content = self._read_text_file(full_path) if full_path else None
        is_text = bool(content) and "\x00" not in content

        if is_text and (not executed_directly or not content.startswith(SHEBANG) or _has_shell_shebang(content)):
            return self._expand_text(content, state.enter(f"script {path}", f"script:{full_path}"))
        else:
            return []

    def _collect_stdin_texts(self, command: SimpleCommand, state: _ExpansionState) -> list[str]:
        texts = list(command.stdin_texts)
        source = command.piped_from

        if source:
            texts.extend(source.stdin_texts)
            texts.extend(self._read_piped_output(source, state))
        return texts

    def _read_piped_output(self, source: SimpleCommand, state: _ExpansionState) -> list[str]:
        arguments = [self._resolve_word(word, state).text for word in source.words]
        name = os.path.basename(arguments[0]) if arguments else ""
        operands = [argument for argument in arguments[1:] if not _is_option(argument)]

        if name in PIPE_TEXT_COMMANDS:
            return [" ".join(operands)]
        elif name in PIPE_FILE_COMMANDS:
            contents = [self._read_text_file(join_directory(state.cwd, operand) or operand) for operand in operands]
            return [content for content in contents if content]
        else:
            return []

    def _find_git_alias(self, git_command: GitCommand, state: _ExpansionState) -> str:
        name = git_command.subcommand
        configured = git_command.config_values.get(f"alias.{name.lower()}")
        is_candidate = name not in GIT_BUILTIN_COMMANDS and f"{GIT_ALIAS_PREFIX}{name}" not in state.active_names

        if not is_candidate:
            return ""
        elif configured is not None:
            return configured
        else:
            return self._read_git_alias(git_command.work_dir, name) or ""

    def _resolve_word(self, word: Word, state: _ExpansionState) -> _Argument:
        unresolved = False

        def substitute(match: re.Match[str]) -> str:
            nonlocal unresolved
            name = match.group(1)
            value = state.variables.get(name, os.environ.get(name))

            if value is None:
                unresolved = True
                return f"${name}"
            else:
                return value

        text = MARKED_VARIABLE_PATTERN.sub(substitute, word.text)
        dynamic = unresolved or UNKNOWN_MARK in text
        return _Argument(text.replace(UNKNOWN_MARK, DYNAMIC_PLACEHOLDER), word.quoted, dynamic, word.unquoted_prefix)

    def _make_call(
        self,
        arguments: list[_Argument],
        assignments: list[tuple[str, str]],
        state: _ExpansionState,
    ) -> CommandCall:
        return CommandCall(
            argv=[argument.text for argument in arguments],
            assignments=list(assignments),
            cwd=state.cwd,
            dynamic_name=bool(arguments) and arguments[0].dynamic,
            unknown_arguments=state.unknown_arguments,
            origin=state.origin,
        )

    def _check_expansion_budget(self) -> None:
        if self._expanded_count > MAX_EXPANDED_COMMANDS:
            raise ExpansionLimitError(f"more than {MAX_EXPANDED_COMMANDS} commands to follow")

    def _is_reserved(self, name: _Argument, words: frozenset[str]) -> bool:
        return not name.quoted and name.text in words

    def _is_expandable_name(self, name: _Argument, definitions: dict[str, str], state: _ExpansionState) -> bool:
        return not name.quoted and not name.dynamic and name.text in definitions and name.text not in state.active_names



def parse_git_command(argv: list[str], cwd: str | None) -> GitCommand | None:
    if not argv or os.path.basename(argv[0]) not in GIT_EXECUTABLES:
        return None
    else:
        return _parse_git_options(argv, cwd)


def _parse_git_options(argv: list[str], cwd: str | None) -> GitCommand | None:
    index = 1
    work_dir = cwd
    global_options: list[str] = []
    config_values: dict[str, str] = {}

    while index < len(argv) and argv[index].startswith("-"):
        option = argv[index]
        value = _text_value_at(argv, index + 1)

        if option == "-C":
            work_dir = join_directory(work_dir, value)
            index += 2
        elif option == "-c":
            key, _, config_value = value.partition("=")
            config_values[key.lower()] = config_value
            global_options.extend([option, value])
            index += 2
        elif option in GIT_OPTIONS_WITH_ARGUMENT:
            global_options.extend([option, value])
            index += 2
        else:
            global_options.append(option)
            index += 1
    if index < len(argv):
        return GitCommand(argv[index], argv[index + 1:], global_options, config_values, work_dir, index)
    else:
        return None


def load_shell_definitions(config_directory: Path) -> ShellDefinitions:
    snapshot_directory = config_directory / SNAPSHOT_DIRECTORY_NAME
    definitions = ShellDefinitions({}, {})
    seen_digests: set[str] = set()

    for content in _read_snapshots(snapshot_directory):
        digest = hashlib.sha1(content).hexdigest()

        if digest not in seen_digests:
            seen_digests.add(digest)
            _read_snapshot(content.decode("utf-8", errors="replace").splitlines(), definitions)
    return definitions


def _read_snapshots(snapshot_directory: Path) -> list[bytes]:
    try:
        snapshots = sorted(snapshot_directory.glob(SNAPSHOT_FILE_PATTERN), key=lambda path: path.stat().st_mtime)
        return [snapshot.read_bytes() for snapshot in snapshots[-SNAPSHOT_LIMIT:]]
    except OSError:
        return []


def _read_snapshot(lines: list[str], definitions: ShellDefinitions) -> None:
    index = 0

    while index < len(lines):
        index = _read_snapshot_entry(lines, index, definitions)


def _read_snapshot_entry(lines: list[str], index: int, definitions: ShellDefinitions) -> int:
    line = lines[index]
    alias_match = ALIAS_LINE_PATTERN.match(line)
    function_match = FUNCTION_START_PATTERN.match(line)

    if alias_match:
        definitions.aliases[alias_match.group(1)] = decode_shell_word(alias_match.group(2))
        return index + 1
    elif function_match:
        name = function_match.group(1) or function_match.group(2)
        has_brace = bool(function_match.group(3))
        body_start = index + 1 if has_brace or lines[index + 1:index + 2] != ["{"] else index + 2
        body_end = next(
            (position for position in range(body_start, len(lines)) if FUNCTION_END_PATTERN.match(lines[position])),
            len(lines),
        )

        definitions.functions[name] = "\n".join(lines[body_start:body_end])
        return body_end + 1
    else:
        return index + 1


def decode_shell_word(raw: str) -> str:
    return _Lexer(raw).read_single_word()


def restore_marks(text: str) -> str:
    return MARKED_VARIABLE_PATTERN.sub(r"$\1", text).replace(UNKNOWN_MARK, "")


def join_directory(base: str | None, path: str) -> str | None:
    expanded = os.path.expanduser(path)

    if not path or "$" in path:
        return None
    elif os.path.isabs(expanded):
        return os.path.normpath(expanded)
    elif base:
        return os.path.normpath(os.path.join(base, expanded))
    else:
        return None


def read_git_alias(work_dir: str | None, name: str) -> str | None:
    location = ["-C", work_dir] if work_dir else []
    command = ["git", *location, "config", "--get", f"alias.{name}"]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def read_text_file(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_SCRIPT_BYTES)
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def _count_leading_assignments(arguments: list[_Argument]) -> int:
    count = 0

    while count < len(arguments) and ASSIGNMENT_PATTERN.match(arguments[count].unquoted_prefix):
        count += 1
    return count


def _split_assignment(text: str) -> tuple[str, str]:
    name, _, value = text.partition("=")

    return name.rstrip("+"), value


def _split_words(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _find_option_values(arguments: list[str], options: frozenset[str]) -> list[str]:
    values: list[str] = []

    for index, argument in enumerate(arguments):
        option, separator, inline_value = argument.partition("=")

        if option in options and separator:
            values.append(inline_value)
        elif argument in options and index + 1 < len(arguments):
            values.append(arguments[index + 1])
    return values


def _find_options_end(arguments: list[_Argument], options_with_argument: frozenset[str], bare_option: str = "") -> int:
    index = 1

    while index < len(arguments) and _is_wrapper_option(arguments[index].text, bare_option):
        index += 2 if arguments[index].text in options_with_argument else 1
    return min(index, len(arguments))


def _skip_option_end(arguments: list[_Argument], end: int) -> int:
    return end + 1 if end < len(arguments) and arguments[end].text == OPTION_END else end


def _option_directory(arguments: list[_Argument], index: int, inline_value: str, base: str | None) -> str | None:
    value_argument = arguments[index + 1] if index + 1 < len(arguments) else None

    if inline_value:
        return join_directory(base, inline_value)
    elif value_argument and not value_argument.dynamic:
        return join_directory(base, value_argument.text)
    else:
        return None


def _text_at(arguments: list[_Argument], index: int) -> str:
    return arguments[index].text if index < len(arguments) else ""


def _text_value_at(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def _is_option(text: str) -> bool:
    return text.startswith("-") and len(text) > 1


def _is_wrapper_option(text: str, bare_option: str) -> bool:
    return text != OPTION_END and (_is_option(text) or bool(bare_option) and text == bare_option)


def _is_shell_option(text: str) -> bool:
    return (text.startswith("-") or text.startswith("+")) and len(text) > 1


def _has_shell_shebang(content: str) -> bool:
    first_line = content.split("\n", 1)[0]
    words = first_line[len(SHEBANG):].split() if first_line.startswith(SHEBANG) else []
    interpreter = os.path.basename(words[0]) if words else ""
    arguments = [word for word in words[1:] if not word.startswith("-")]

    if interpreter == ENV_NAME:
        return bool(arguments) and os.path.basename(arguments[0]) in SHELL_NAMES
    else:
        return interpreter in SHELL_NAMES
