#!/usr/bin/env python3
"""PreToolUse hook: stop irreversible shell mistakes before they run.

Expands the command with shell_command (aliases, functions, wrappers,
sh -c, eval, scripts, cd and git -C) and applies these checks:

  deny  direct git commit; commits go through claude-commit.py
  deny  a commit-wrapper call whose file list is known only at run time
  deny  staging or committing .env and other credential files
  ask   a commit-wrapper call with --include-unrecorded, which commits
        lines this session's write record does not show as its own
  ask   a git push that is not routine: force, deletion, mirror, tags,
        all branches, another remote or ref, unknown options, or a
        repository that sets shellGuard.askBeforePush
  ask   starting an application in production, staging, or live mode

Routine commits and pushes need no approval (user decision, 2026-11-09):
a push of the current branch to its own remote passes without a prompt.

Prints nothing when no check matches, so the normal permission flow
decides. When the command cannot be analyzed, it asks instead of
letting the call through.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from commit_arguments import CommitArguments, CommitArgumentsError, parse_commit_arguments
from secret_files import get_credential_reason, read_file_head
from shell_command import (
    CommandCall,
    CommandExpander,
    GitCommand,
    join_directory,
    load_shell_definitions,
    parse_git_command,
    read_git_alias,
    read_text_file,
)


TERMINAL_TOOL = "mcp__terminal__run_in_terminal"
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
DEFAULT_CONFIG_DIRECTORY = "~/.claude"
DECISION_DENY = "deny"
DECISION_ASK = "ask"
HOOK_EVENT_NAME = "PreToolUse"
COMMIT_REASON = (
    "Direct git commit blocked. Use ~/.claude/scripts/claude-commit.py "
    "with message and files as args (prepend --amend if amending). "
    "The wrapper validates subject/body format (type prefix, line "
    "length, body lines, bullet count, no internal terms), runs "
    "amend check, stages exact files, prints diff --cached --stat "
    "plus verification table, then invokes git commit via subprocess "
    "so this hook does not re-fire."
)
CREDENTIAL_REASON = (
    "Staging or committing credential files is blocked: {files}. Keep "
    "secrets out of git: add the file to .gitignore and commit a redacted "
    "copy named like .env.example instead. If the user really wants this "
    "file committed, the user must run the command personally."
)
DYNAMIC_FILES_REASON = (
    "The commit wrapper got file names known only when the command runs (a "
    "variable, a wildcard, or a list piped in): "
    "{command}{origin}. List each file you changed by name, never from git "
    "status output, so the commit holds only your own work."
)
INCLUDE_UNRECORDED_REASON = (
    "This commit keeps lines that this session's write record does not show as its own, in "
    "{files}: {command}{origin}. Approve only if you have seen those lines (python3 "
    "~/.claude/scripts/session_writes.py report <file> lists them) and want them in this commit."
)
PUSH_REASON = "This push needs your approval because it {risks}: {command}{origin}."
PRODUCTION_REASON = (
    "This starts an application in production, staging, or live mode "
    "({trigger}): {command}{origin}. Approve only after checking which "
    "config and endpoints it loads."
)
ANALYSIS_FAILURE_REASON = (
    "The shell guard hook could not analyze this command ({error}). Approve "
    "only if it does not rewrite or delete remote history, commit credential "
    "files, or start production mode."
)
FORCE_PUSH_RISK = "rewrites history on the remote (force push)"
DELETE_PUSH_RISK = "deletes a branch or tag on the remote"
MIRROR_PUSH_RISK = "makes the remote match every local ref (mirror)"
ALL_BRANCHES_PUSH_RISK = "pushes every branch"
TAG_PUSH_RISK = "publishes tags"
OTHER_OPTION_PUSH_RISK = "uses options outside a routine push ({options})"
DYNAMIC_PUSH_RISK = "has a command name, folder, or arguments known only when it runs"
GIT_ENVIRONMENT_PUSH_RISK = "sets Git environment variables that change what it pushes ({names})"
FOLLOW_TAGS_PUSH_RISK = "publishes tags through push.followTags"
UNKNOWN_FOLDER_PUSH_RISK = "runs in a folder the guard cannot see"
DETACHED_PUSH_RISK = "runs with no current branch"
OTHER_REMOTE_PUSH_RISK = "pushes to {remote} instead of the branch's remote {expected}"
OTHER_REF_PUSH_RISK = "pushes refs other than the current branch ({refs})"
OTHER_UPSTREAM_PUSH_RISK = "pushes to the upstream branch {merge}, not one named like the current branch"
PUSH_DEFAULT_RISK = "relies on push.default={value}, which pushes more than the current branch"
CONFIGURED_REFSPEC_PUSH_RISK = "follows the refs set in remote.{remote}.push"
REPOSITORY_ASKS_PUSH_RISK = "is in a repository that asks before every push (shellGuard.askBeforePush)"
RISK_SEPARATOR = "; "
MAX_SHOWN_COMMAND_LENGTH = 160
COMMIT_WRAPPER_NAME = "claude-commit.py"
PYTHON_PATTERN = re.compile(r"^python(?:\d+(?:\.\d+)?)?(?:\.exe)?$")
DYNAMIC_MARKER = "$"
COMMIT_SUBCOMMAND = "commit"
PUSH_SUBCOMMAND = "push"
STAGE_SUBCOMMANDS = frozenset({"add", "stage"})
RISKY_GIT_SUBCOMMANDS = frozenset({COMMIT_SUBCOMMAND, PUSH_SUBCOMMAND}) | STAGE_SUBCOMMANDS
ADD_ALL_FLAGS = frozenset({"-A", "--all", "--no-ignore-removal"})
ADD_UPDATE_FLAGS = frozenset({"-u", "--update"})
ADD_FORCE_FLAGS = frozenset({"-f", "--force"})
DRY_RUN_FLAGS = frozenset({"-n", "--dry-run"})
HELP_FLAGS = frozenset({"-h", "--help"})
PATHSPEC_FILE_OPTION = "--pathspec-from-file"
ADD_OPTIONS_WITH_ARGUMENT = frozenset({PATHSPEC_FILE_OPTION})
WHOLE_TREE_PATHSPEC = ":/"
PATHSPEC_MAGIC_PREFIX = ":"
OPTION_END = "--"
STATUS_TIMEOUT_SECONDS = 10
UNTRACKED_STATUS = "??"
IGNORED_STATUS = "!!"
RENAME_OR_COPY_STATUSES = "RC"
UNSTAGED_UNCHANGED_OR_DELETED = " D"
PUSH_FORCE_FLAGS = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes"})
PUSH_DELETE_FLAGS = frozenset({"-d", "--delete", "--prune"})
PUSH_MIRROR_FLAG = "--mirror"
PUSH_ALL_BRANCH_FLAGS = frozenset({"--all", "--branches"})
PUSH_TAG_FLAGS = frozenset({"--tags", "--follow-tags"})
RISKY_PUSH_FLAGS = PUSH_FORCE_FLAGS | PUSH_DELETE_FLAGS | PUSH_ALL_BRANCH_FLAGS | PUSH_TAG_FLAGS | {PUSH_MIRROR_FLAG}
ROUTINE_PUSH_FLAGS = frozenset({
    "-q",
    "--quiet",
    "-v",
    "--verbose",
    "-u",
    "--set-upstream",
    "--porcelain",
    "--progress",
    "--no-progress",
})
ROUTINE_PUSH_DEFAULTS = frozenset({"", "simple", "current", "upstream"})
UPSTREAM_PUSH_DEFAULT = "upstream"
PUSH_DEFAULT_KEY = "push.default"
PUSH_FOLLOW_TAGS_KEY = "push.followtags"
REMOTE_PUSH_DEFAULT_KEY = "remote.pushdefault"
ASK_BEFORE_PUSH_KEY = "shellguard.askbeforepush"
BRANCH_PUSH_REMOTE_KEY = "branch.{branch}.pushremote"
BRANCH_REMOTE_KEY = "branch.{branch}.remote"
BRANCH_MERGE_KEY = "branch.{branch}.merge"
REMOTE_PUSH_REFSPEC_KEY = "remote.{remote}.push"
REMOTE_MIRROR_KEY = "remote.{remote}.mirror"
CURRENT_BRANCH_ARGUMENTS = ["symbolic-ref", "--quiet", "--short", "HEAD"]
CONFIG_LIST_ARGUMENTS = ["config", "--list", "-z"]
CONFIG_ENTRY_SEPARATOR = "\0"
CONFIG_VALUE_SEPARATOR = "\n"
GIT_VARIABLE_PREFIX = "GIT_"
NO_DRY_RUN_FLAG = "--no-dry-run"
GLOB_CHARACTERS = frozenset("*?")
DEFAULT_REMOTE = "origin"
HEAD_REF = "HEAD"
BRANCH_REF_PREFIX = "refs/heads/"
REFSPEC_SEPARATOR = ":"
GIT_TRUE_VALUES = frozenset({"true", "yes", "on", "1"})
IMPLICIT_TRUE = "true"
PUSH_OPTIONS_WITH_ARGUMENT = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
FORCE_REFSPEC_PREFIX = "+"
DELETE_REFSPEC_PREFIX = ":"
PRODUCTION_TOKENS = frozenset({
    "production",
    "prod",
    "prd",
    "preprod",
    "preproduction",
    "live",
    "staging",
    "stage",
    "stg",
})
TOKEN_SPLIT_PATTERN = re.compile(r"[^A-Za-z0-9]+")
NAME_SEPARATOR_PATTERN = re.compile(r"[.-]")
ENV_NAME_PATTERN = re.compile(
    r"^(?:[A-Z0-9]+_)*(?:ENV|ENVIRONMENT|STAGE|MODE|PROFILE|PROFILES|PROFILES_ACTIVE|SETTINGS_MODULE|ENV_FILE)$"
)
EXPORT_COMMANDS = frozenset({"export", "declare", "typeset", "local", "readonly", "setenv"})
NON_APPLICATION_COMMANDS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "sed", "awk", "gawk", "cat",
    "less", "more", "head", "tail", "echo", "printf", "wc", "sort", "uniq",
    "cut", "tr", "jq", "yq", "find", "ls", "file", "stat", "diff", "git",
    "test", "[", "[[", "true", "false", "man", "which", "type", "whence",
    "where", "strings", "xxd", "od", "hexdump", "base64", "md5", "shasum",
    "sha256sum", "column", "nl", "tee", "touch", "mkdir", "rm", "cp", "mv",
    "ln", "chmod", "chown", "basename", "dirname", "realpath", "readlink",
    "date", "sleep", "cd", "pushd", "popd", "pwd",
})
PRODUCTION_VALUE_OPTIONS = frozenset({
    "--env",
    "--environment",
    "--mode",
    "--profile",
    "--profiles",
    "--stage",
    "--configuration",
    "--config",
    "--settings",
    "--env-file",
})
SHORT_ENVIRONMENT_OPTION = "-e"
PRODUCTION_FLAGS = frozenset({"--prod", "--production"})
JVM_PROPERTY_PREFIX = "-D"
LONG_OPTION_PREFIX = "--"
PACKAGE_MANAGERS = frozenset({"npm", "yarn", "pnpm", "bun"})
PACKAGE_RUN_WORDS = frozenset({"run", "run-script"})
INSTALL_WORDS = frozenset({
    "install", "i", "ci", "add", "prune", "update", "upgrade", "remove",
    "rm", "uninstall", "dedupe", "rebuild", "link", "audit", "outdated",
})
BUILD_PREFIX = "build"
BUILD_EXECUTABLES = frozenset({"webpack", "rollup", "esbuild", "tsc"})
TASK_RUNNERS = frozenset({"make", "gmake", "just", "task", "rake", "mage"})
SERVICE_ACTIONS = {
    "pm2": frozenset({"start", "restart", "reload", "stop", "delete"}),
    "systemctl": frozenset({"start", "restart", "reload", "stop", "enable"}),
    "service": frozenset({"start", "restart", "reload", "stop"}),
}
CONTAINER_TOOLS = frozenset({"docker", "podman"})
COMPOSE_TOOL = "docker-compose"
COMPOSE_SUBCOMMAND = "compose"
COMPOSE_FILE_OPTIONS = frozenset({"-f", "--file", "--env-file"})
COMPOSE_OPTIONS_WITH_ARGUMENT = frozenset({
    "-f",
    "--file",
    "-p",
    "--project-name",
    "--profile",
    "--env-file",
    "--project-directory",
    "--ansi",
    "--parallel",
    "--progress",
})


@dataclass
class AddArguments:
    pathspecs: list[str]
    stages_all: bool
    stages_tracked_only: bool
    includes_ignored: bool
    is_dry_run: bool
    reads_pathspec_file: bool


def main() -> None:
    payload = json.load(sys.stdin)
    command, cwd = read_tool_command(payload)
    decision = decide(command, cwd) if command else None

    if decision:
        print(json.dumps(decision))


def read_tool_command(payload: dict) -> tuple[str, str | None]:
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    cwd = payload.get("cwd")

    if payload.get("tool_name") == TERMINAL_TOOL and tool_input.get("tab_id"):
        cwd = None
    elif payload.get("tool_name") == TERMINAL_TOOL and tool_input.get("cwd"):
        cwd = join_directory(cwd, tool_input["cwd"])
    return (command if isinstance(command, str) else ""), cwd


def decide(command: str, cwd: str | None) -> dict | None:
    try:
        calls = expand_command(command, cwd)
        git_commands = find_git_commands(calls)
        deny_reasons = (
            find_commit_reasons(git_commands)
            + find_dynamic_file_reasons(calls)
            + find_credential_reasons(calls, git_commands)
        )
        push_reasons = find_push_reasons(git_commands, find_git_environment_names(calls))
        ask_reasons = push_reasons + find_unrecorded_include_reasons(calls) + find_production_reasons(calls)
    except Exception as error:
        deny_reasons = []
        ask_reasons = [ANALYSIS_FAILURE_REASON.format(error=f"{type(error).__name__}: {error}")]
    if deny_reasons:
        return make_decision(DECISION_DENY, deny_reasons)
    elif ask_reasons:
        return make_decision(DECISION_ASK, ask_reasons)
    else:
        return None


def expand_command(command: str, cwd: str | None) -> list[CommandCall]:
    config_directory = Path(os.path.expanduser(os.environ.get(CONFIG_DIRECTORY_VARIABLE) or DEFAULT_CONFIG_DIRECTORY))
    expander = CommandExpander(load_shell_definitions(config_directory), read_git_alias, read_text_file)

    return expander.expand(command, cwd)


def make_decision(decision: str, reasons: list[str]) -> dict:
    unique_reasons = list(dict.fromkeys(reasons))

    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "permissionDecision": decision,
            "permissionDecisionReason": "\n".join(unique_reasons),
        }
    }


def find_git_commands(calls: list[CommandCall]) -> list[tuple[CommandCall, GitCommand]]:
    found: list[tuple[CommandCall, GitCommand]] = []

    for call in calls:
        git_command = parse_git_command(call.argv, call.cwd) or parse_dynamic_git_command(call)

        if git_command:
            found.append((call, git_command))
    return found


def parse_dynamic_git_command(call: CommandCall) -> GitCommand | None:
    git_command = parse_git_command(["git", *call.argv[1:]], call.cwd) if call.dynamic_name else None

    if git_command and git_command.subcommand in RISKY_GIT_SUBCOMMANDS:
        return git_command
    else:
        return None


def find_commit_reasons(git_commands: list[tuple[CommandCall, GitCommand]]) -> list[str]:
    has_commit = any(git_command.subcommand == COMMIT_SUBCOMMAND for _, git_command in git_commands)

    return [COMMIT_REASON] if has_commit else []


def find_dynamic_file_reasons(calls: list[CommandCall]) -> list[str]:
    dynamic_calls = [call for call in calls if has_dynamic_wrapper_files(call)]

    return [DYNAMIC_FILES_REASON.format(command=show_command(call), origin=show_origin(call)) for call in dynamic_calls]


def has_dynamic_wrapper_files(call: CommandCall) -> bool:
    is_wrapper = find_commit_wrapper_index(call.argv) >= 0
    paths = find_commit_wrapper_files(call.argv)

    return is_wrapper and (call.unknown_arguments or any(is_dynamic_path(path) for path in paths))


def find_credential_reasons(
    calls: list[CommandCall],
    git_commands: list[tuple[CommandCall, GitCommand]],
) -> list[str]:
    findings: list[str] = []

    for call, git_command in git_commands:
        if git_command.subcommand in STAGE_SUBCOMMANDS:
            findings.extend(find_staged_credentials(call, git_command))
    for call in calls:
        findings.extend(find_commit_wrapper_credentials(call))
    if findings:
        return [CREDENTIAL_REASON.format(files=", ".join(dict.fromkeys(findings)))]
    else:
        return []


def find_staged_credentials(call: CommandCall, git_command: GitCommand) -> list[str]:
    add_arguments = parse_add_arguments(git_command.arguments)
    literal_pathspecs = [pathspec for pathspec in add_arguments.pathspecs if DYNAMIC_MARKER not in pathspec]
    has_unknown_paths = call.unknown_arguments or len(literal_pathspecs) < len(add_arguments.pathspecs)

    if add_arguments.is_dry_run:
        return []
    else:
        plain_pathspecs = [pathspec for pathspec in literal_pathspecs if not pathspec.startswith(PATHSPEC_MAGIC_PREFIX)]
        named = describe_credentials(plain_pathspecs, git_command.work_dir)
        candidates = list_add_candidates(git_command, add_arguments, literal_pathspecs, has_unknown_paths)
        return named + candidates


def parse_add_arguments(arguments: list[str]) -> AddArguments:
    flags: set[str] = set()
    pathspecs: list[str] = []
    index = 0
    reads_pathspec_file = False

    while index < len(arguments):
        argument = arguments[index]

        if argument == OPTION_END:
            pathspecs.extend(arguments[index + 1:])
            break
        elif argument.startswith("-"):
            flags.update(expand_short_flags(argument))
            reads_pathspec_file = reads_pathspec_file or argument.split("=", 1)[0] == PATHSPEC_FILE_OPTION
            index += 2 if argument in ADD_OPTIONS_WITH_ARGUMENT else 1
        else:
            pathspecs.append(argument)
            index += 1
    return AddArguments(
        pathspecs=pathspecs,
        stages_all=bool(flags & ADD_ALL_FLAGS),
        stages_tracked_only=bool(flags & ADD_UPDATE_FLAGS),
        includes_ignored=bool(flags & ADD_FORCE_FLAGS),
        is_dry_run=bool(flags & DRY_RUN_FLAGS),
        reads_pathspec_file=reads_pathspec_file,
    )


def list_add_candidates(
    git_command: GitCommand,
    add_arguments: AddArguments,
    literal_pathspecs: list[str],
    has_unknown_paths: bool,
) -> list[str]:
    stages_whole_tree = add_arguments.stages_all or add_arguments.stages_tracked_only
    uses_whole_tree = (
        has_unknown_paths or
        add_arguments.reads_pathspec_file or
        (stages_whole_tree and not literal_pathspecs)
    )
    pathspecs = [WHOLE_TREE_PATHSPEC] if uses_whole_tree else literal_pathspecs

    if pathspecs and git_command.work_dir:
        root, paths = run_status(git_command, pathspecs, add_arguments)
        return describe_credentials(paths, root)
    else:
        return []


def run_status(git_command: GitCommand, pathspecs: list[str], add_arguments: AddArguments) -> tuple[str, list[str]]:
    base = make_git_base(git_command, git_command.work_dir or "")
    ignored = ["--ignored=matching"] if add_arguments.includes_ignored else []
    status = ["status", "--porcelain=v1", "-z", "--untracked-files=all", *ignored, OPTION_END, *pathspecs]
    root = run_git_output([*base, "rev-parse", "--show-toplevel"]).strip()
    output = run_git_output([*base, *status]) if root else ""

    return root, parse_status_paths(output, add_arguments)


def make_git_base(git_command: GitCommand, work_dir: str) -> list[str]:
    return ["git", "--no-optional-locks", "-C", work_dir, *git_command.global_options]


def run_git_output(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=STATUS_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def parse_status_paths(output: str, add_arguments: AddArguments) -> list[str]:
    entries = output.split("\0")
    paths: list[str] = []
    index = 0

    while index < len(entries):
        entry = entries[index]
        status = entry[:2]

        index += 2 if status[:1] and status[0] in RENAME_OR_COPY_STATUSES else 1
        if len(entry) > 3 and is_add_candidate(status, add_arguments):
            paths.append(entry[3:])
    return paths


def is_add_candidate(status: str, add_arguments: AddArguments) -> bool:
    if status == UNTRACKED_STATUS:
        return not add_arguments.stages_tracked_only
    elif status == IGNORED_STATUS:
        return add_arguments.includes_ignored and not add_arguments.stages_tracked_only
    else:
        return status[1:2] not in UNSTAGED_UNCHANGED_OR_DELETED


def find_commit_wrapper_credentials(call: CommandCall) -> list[str]:
    return describe_credentials(find_commit_wrapper_files(call.argv), call.cwd)


def find_unrecorded_include_reasons(calls: list[CommandCall]) -> list[str]:
    reasons: list[str] = []

    for call in calls:
        arguments = read_commit_wrapper_arguments(call.argv)

        if arguments is not None and arguments.included_paths:
            files = ", ".join(arguments.included_paths)
            reasons.append(INCLUDE_UNRECORDED_REASON.format(files=files, command=show_command(call), origin=show_origin(call)))
    return reasons


def find_commit_wrapper_files(argv: list[str]) -> list[str]:
    arguments = read_commit_wrapper_arguments(argv)

    return list(arguments.files) if arguments is not None else []


def read_commit_wrapper_arguments(argv: list[str]) -> CommitArguments | None:
    script_index = find_commit_wrapper_index(argv)

    if script_index < 0:
        return None
    else:
        return parse_wrapper_arguments(argv[script_index + 1 :])


def parse_wrapper_arguments(arguments: list[str]) -> CommitArguments | None:
    try:
        return parse_commit_arguments(arguments)
    except CommitArgumentsError:
        return None


def find_commit_wrapper_index(argv: list[str]) -> int:
    names = [os.path.basename(argument) for argument in argv]

    if names[:1] == [COMMIT_WRAPPER_NAME]:
        return 0
    elif names and PYTHON_PATTERN.match(names[0]) and COMMIT_WRAPPER_NAME in names[1:]:
        position = names.index(COMMIT_WRAPPER_NAME, 1)
        return position if all(argument.startswith("-") for argument in argv[1:position]) else -1
    else:
        return -1


def describe_credentials(paths: list[str], base: str | None) -> list[str]:
    findings: list[str] = []

    for path in paths:
        full_path = join_directory(base, path) or path
        reason = get_credential_reason(path, functools.partial(read_file_head, full_path))

        if reason:
            findings.append(f"{path} ({reason})")
    return findings


def find_git_environment_names(calls: list[CommandCall]) -> list[str]:
    assignments = [assignment for call in calls for assignment in list_call_assignments(call)]

    return sorted({name for name, _ in assignments if name.startswith(GIT_VARIABLE_PREFIX)})


def list_call_assignments(call: CommandCall) -> list[tuple[str, str]]:
    name = os.path.basename(call.argv[0]) if call.argv else ""

    return call.assignments + list_command_assignments(name, call.argv[1:])


def find_push_reasons(git_commands: list[tuple[CommandCall, GitCommand]], environment_names: list[str]) -> list[str]:
    reasons: list[str] = []

    for call, git_command in git_commands:
        flags = list_push_flags(git_command.arguments)
        is_push = is_real_push(git_command, flags)
        risks = find_push_risks(call, git_command, set(flags), environment_names) if is_push else []

        if risks:
            reasons.append(describe_push(call, risks))
    return reasons


def find_push_risks(
    call: CommandCall,
    git_command: GitCommand,
    flags: set[str],
    environment_names: list[str],
) -> list[str]:
    positionals = list_positionals(git_command.arguments, PUSH_OPTIONS_WITH_ARGUMENT)
    names = ", ".join(environment_names)
    risks = [
        *find_push_option_risks(flags, positionals[1:]),
        *([GIT_ENVIRONMENT_PUSH_RISK.format(names=names)] if environment_names else []),
    ]
    texts = [*git_command.arguments, git_command.work_dir or ""]
    is_dynamic = call.dynamic_name or call.unknown_arguments or any(DYNAMIC_MARKER in text for text in texts)

    if is_dynamic:
        return [*risks, DYNAMIC_PUSH_RISK]
    elif git_command.work_dir:
        return [*risks, *find_target_push_risks(git_command, git_command.work_dir, positionals)]
    else:
        return [*risks, UNKNOWN_FOLDER_PUSH_RISK]


def list_push_flags(arguments: list[str]) -> list[str]:
    flags: list[str] = []
    index = 0

    while index < len(arguments) and arguments[index] != OPTION_END:
        argument = arguments[index]
        takes_value = argument in PUSH_OPTIONS_WITH_ARGUMENT

        if argument.startswith("-") and len(argument) > 1:
            flags.extend(sorted(expand_short_flags(argument)))
        index += 2 if takes_value else 1
    return flags


def find_push_option_risks(flags: set[str], refspecs: list[str]) -> list[str]:
    names = {flag.split("=", 1)[0] for flag in flags}
    forces = bool(names & PUSH_FORCE_FLAGS) or any(refspec.startswith(FORCE_REFSPEC_PREFIX) for refspec in refspecs)
    deletes = bool(names & PUSH_DELETE_FLAGS) or any(refspec.startswith(DELETE_REFSPEC_PREFIX) for refspec in refspecs)
    other_options = sorted(names - ROUTINE_PUSH_FLAGS - RISKY_PUSH_FLAGS)
    risks = [
        (forces, FORCE_PUSH_RISK),
        (deletes, DELETE_PUSH_RISK),
        (PUSH_MIRROR_FLAG in names, MIRROR_PUSH_RISK),
        (bool(names & PUSH_ALL_BRANCH_FLAGS), ALL_BRANCHES_PUSH_RISK),
        (bool(names & PUSH_TAG_FLAGS), TAG_PUSH_RISK),
        (bool(other_options), OTHER_OPTION_PUSH_RISK.format(options=", ".join(other_options))),
    ]

    return [risk for applies, risk in risks if applies]


def find_target_push_risks(git_command: GitCommand, work_dir: str, positionals: list[str]) -> list[str]:
    branch = read_git_output(git_command, work_dir, CURRENT_BRANCH_ARGUMENTS).strip()

    if branch:
        return find_branch_push_risks(git_command, work_dir, branch, positionals)
    else:
        return [DETACHED_PUSH_RISK]


def find_branch_push_risks(git_command: GitCommand, work_dir: str, branch: str, positionals: list[str]) -> list[str]:
    config = read_git_config(git_command, work_dir)
    remote = find_push_remote(config, branch)
    target = positionals[0] if positionals else remote
    refspecs = positionals[1:]
    other_refspecs = [refspec for refspec in refspecs if not is_branch_refspec(refspec, branch)]
    push_default = read_config_value(config, PUSH_DEFAULT_KEY)
    merge = read_config_value(config, BRANCH_MERGE_KEY.format(branch=branch))
    pushes_other_upstream = (
        push_default == UPSTREAM_PUSH_DEFAULT
        and bool(merge)
        and merge != f"{BRANCH_REF_PREFIX}{branch}"
    )
    configured_refspecs = config.get(REMOTE_PUSH_REFSPEC_KEY.format(remote=target))
    risks = [
        (target != remote, OTHER_REMOTE_PUSH_RISK.format(remote=target, expected=remote)),
        (bool(other_refspecs), OTHER_REF_PUSH_RISK.format(refs=", ".join(other_refspecs))),
        (not refspecs and push_default not in ROUTINE_PUSH_DEFAULTS, PUSH_DEFAULT_RISK.format(value=push_default)),
        (not refspecs and pushes_other_upstream, OTHER_UPSTREAM_PUSH_RISK.format(merge=merge)),
        (bool(configured_refspecs), CONFIGURED_REFSPEC_PUSH_RISK.format(remote=target)),
        (is_config_true(read_config_value(config, REMOTE_MIRROR_KEY.format(remote=target))), MIRROR_PUSH_RISK),
        (is_config_true(read_config_value(config, PUSH_FOLLOW_TAGS_KEY)), FOLLOW_TAGS_PUSH_RISK),
        (is_config_true(read_config_value(config, ASK_BEFORE_PUSH_KEY)), REPOSITORY_ASKS_PUSH_RISK),
    ]

    return [risk for applies, risk in risks if applies]


def find_push_remote(config: dict[str, list[str]], branch: str) -> str:
    keys = [
        BRANCH_PUSH_REMOTE_KEY.format(branch=branch),
        REMOTE_PUSH_DEFAULT_KEY,
        BRANCH_REMOTE_KEY.format(branch=branch),
    ]
    values = [read_config_value(config, key) for key in keys]
    remotes = [value for value in values if value]

    return remotes[0] if remotes else DEFAULT_REMOTE


def read_git_config(git_command: GitCommand, work_dir: str) -> dict[str, list[str]]:
    config: dict[str, list[str]] = {}

    for entry in read_git_output(git_command, work_dir, CONFIG_LIST_ARGUMENTS).split(CONFIG_ENTRY_SEPARATOR):
        key, separator, value = entry.partition(CONFIG_VALUE_SEPARATOR)

        if key:
            config.setdefault(key, []).append(value if separator else IMPLICIT_TRUE)
    return config


def read_config_value(config: dict[str, list[str]], key: str) -> str:
    values = config.get(key, [])

    return values[-1] if values else ""


def read_git_output(git_command: GitCommand, work_dir: str, arguments: list[str]) -> str:
    return run_git_output([*make_git_base(git_command, work_dir), *arguments])


def describe_push(call: CommandCall, risks: list[str]) -> str:
    unique_risks = RISK_SEPARATOR.join(dict.fromkeys(risks))

    return PUSH_REASON.format(risks=unique_risks, command=show_command(call), origin=show_origin(call))


def find_production_reasons(calls: list[CommandCall]) -> list[str]:
    reasons: list[str] = []

    for call in calls:
        trigger = find_production_trigger(call)

        if trigger:
            reason = PRODUCTION_REASON.format(trigger=trigger, command=show_command(call), origin=show_origin(call))
            reasons.append(reason)
    return reasons


def find_production_trigger(call: CommandCall) -> str | None:
    name = os.path.basename(call.argv[0]) if call.argv else ""
    arguments = call.argv[1:]
    assignment = find_production_assignment(call.assignments + list_command_assignments(name, arguments))

    if name and is_build_or_install(name, arguments):
        return None
    elif assignment:
        return assignment
    elif not name or name in NON_APPLICATION_COMMANDS:
        return None
    else:
        return find_production_option(arguments) or find_production_target(name, arguments)


def list_command_assignments(name: str, arguments: list[str]) -> list[tuple[str, str]]:
    if name in EXPORT_COMMANDS or name in TASK_RUNNERS:
        assignments = [argument for argument in arguments if "=" in argument and not argument.startswith("-")]
        return [split_assignment(argument) for argument in assignments]
    else:
        return []


def find_production_assignment(assignments: list[tuple[str, str]]) -> str | None:
    matches = [f"{name}={value}" for name, value in assignments if is_production_assignment(name, value)]

    return matches[0] if matches else None


def find_production_option(arguments: list[str]) -> str | None:
    for index, argument in enumerate(arguments):
        option, separator, inline_value = argument.partition("=")
        value = inline_value if separator else (arguments[index + 1] if index + 1 < len(arguments) else "")
        trigger = match_production_option(argument, option, separator, value)

        if trigger:
            return trigger
    return None


def match_production_option(argument: str, option: str, separator: str, value: str) -> str | None:
    if argument in PRODUCTION_FLAGS:
        return argument
    elif option in PRODUCTION_VALUE_OPTIONS or option == SHORT_ENVIRONMENT_OPTION:
        return match_production_value(option, value)
    elif separator and option.startswith(JVM_PROPERTY_PREFIX) and not option.startswith(LONG_OPTION_PREFIX):
        return argument if is_production_assignment(option[len(JVM_PROPERTY_PREFIX):], value) else None
    elif separator and option.startswith(LONG_OPTION_PREFIX):
        return argument if is_production_assignment(option[len(LONG_OPTION_PREFIX):], value) else None
    else:
        return None


def match_production_value(option: str, value: str) -> str | None:
    shown = f"{option} {value}"

    if "=" in value:
        return shown if is_production_assignment(*split_assignment(value)) else None
    elif option == SHORT_ENVIRONMENT_OPTION:
        return shown if value.lower() in PRODUCTION_TOKENS else None
    else:
        return shown if has_production_token(value) else None


def find_production_target(name: str, arguments: list[str]) -> str | None:
    positionals = list_positionals(arguments, frozenset())

    if name in PACKAGE_MANAGERS:
        script = find_package_script(arguments)
        return f"{name} script {script}" if has_production_token(script) else None
    elif name in TASK_RUNNERS:
        targets = [target for target in positionals if "=" not in target and has_production_token(target)]
        return f"{name} {targets[0]}" if targets else None
    elif name in SERVICE_ACTIONS and positionals[:1] and positionals[0] in SERVICE_ACTIONS[name]:
        targets = [target for target in positionals[1:] if has_production_token(target)]
        return f"{name} {positionals[0]} {targets[0]}" if targets else None
    else:
        return find_compose_file_trigger(name, arguments)


def find_compose_file_trigger(name: str, arguments: list[str]) -> str | None:
    compose_arguments = list_compose_arguments(name, arguments)
    files = find_option_values(compose_arguments or [], COMPOSE_FILE_OPTIONS)
    production_files = [file for file in files if has_production_token(file)]

    return f"compose file {production_files[0]}" if production_files else None


def is_build_or_install(name: str, arguments: list[str]) -> bool:
    action = find_action_word(name, arguments)
    positionals = list_positionals(arguments, frozenset())
    is_install = name in PACKAGE_MANAGERS and bool(positionals) and positionals[0] in INSTALL_WORDS

    return name in BUILD_EXECUTABLES or is_install or action.lower().startswith(BUILD_PREFIX)


def find_action_word(name: str, arguments: list[str]) -> str:
    compose_arguments = list_compose_arguments(name, arguments)
    positionals = list_positionals(arguments, frozenset())

    if compose_arguments is not None:
        compose_positionals = list_positionals(compose_arguments, COMPOSE_OPTIONS_WITH_ARGUMENT)
        return compose_positionals[0] if compose_positionals else ""
    elif name in PACKAGE_MANAGERS:
        return find_package_script(arguments)
    else:
        return positionals[0] if positionals else ""


def find_package_script(arguments: list[str]) -> str:
    positionals = list_positionals(arguments, frozenset())

    if positionals[:1] and positionals[0] in PACKAGE_RUN_WORDS:
        return positionals[1] if len(positionals) > 1 else ""
    else:
        return positionals[0] if positionals else ""


def list_compose_arguments(name: str, arguments: list[str]) -> list[str] | None:
    if name == COMPOSE_TOOL:
        return arguments
    elif name in CONTAINER_TOOLS and arguments[:1] == [COMPOSE_SUBCOMMAND]:
        return arguments[1:]
    else:
        return None


def list_positionals(arguments: list[str], options_with_argument: frozenset[str]) -> list[str]:
    positionals: list[str] = []
    index = 0

    while index < len(arguments):
        argument = arguments[index]

        if argument == OPTION_END:
            positionals.extend(arguments[index + 1:])
            break
        elif argument.startswith("-") and len(argument) > 1:
            index += 2 if argument in options_with_argument else 1
        else:
            positionals.append(argument)
            index += 1
    return positionals


def find_option_values(arguments: list[str], options: frozenset[str]) -> list[str]:
    values: list[str] = []

    for index, argument in enumerate(arguments):
        option, separator, inline_value = argument.partition("=")

        if option in options and separator:
            values.append(inline_value)
        elif argument in options and index + 1 < len(arguments):
            values.append(arguments[index + 1])
    return values


def collect_flags(arguments: list[str]) -> set[str]:
    flags: set[str] = set()

    for argument in arguments:
        if argument == OPTION_END:
            break
        elif argument.startswith("-"):
            flags.update(expand_short_flags(argument))
    return flags


def expand_short_flags(argument: str) -> set[str]:
    is_short_cluster = argument.startswith("-") and not argument.startswith(LONG_OPTION_PREFIX) and len(argument) > 2

    if is_short_cluster:
        return {f"-{letter}" for letter in argument[1:]}
    else:
        return {argument.split("=", 1)[0], argument}


def is_production_assignment(name: str, value: str) -> bool:
    normalized_name = NAME_SEPARATOR_PATTERN.sub("_", name).upper()

    return bool(ENV_NAME_PATTERN.match(normalized_name)) and has_production_token(value)


def has_production_token(value: str) -> bool:
    tokens = {token.lower() for token in TOKEN_SPLIT_PATTERN.split(value) if token}

    return bool(tokens & PRODUCTION_TOKENS)


def split_assignment(text: str) -> tuple[str, str]:
    name, _, value = text.partition("=")

    return name, value


def is_real_push(git_command: GitCommand, flags: list[str]) -> bool:
    return git_command.subcommand == PUSH_SUBCOMMAND and not is_dry_run(flags) and not set(flags) & HELP_FLAGS


def is_dry_run(flags: list[str]) -> bool:
    dry_run_positions = [index for index, flag in enumerate(flags) if flag in DRY_RUN_FLAGS]
    real_run_positions = [index for index, flag in enumerate(flags) if flag == NO_DRY_RUN_FLAG]

    return bool(dry_run_positions) and max(dry_run_positions) > max(real_run_positions, default=-1)


def is_dynamic_path(path: str) -> bool:
    return DYNAMIC_MARKER in path or bool(set(path) & GLOB_CHARACTERS)


def is_branch_refspec(refspec: str, branch: str) -> bool:
    source, separator, destination = refspec.partition(REFSPEC_SEPARATOR)
    names = {HEAD_REF, branch, f"{BRANCH_REF_PREFIX}{branch}"}

    return source in names and (not separator or destination in names - {HEAD_REF})


def is_config_true(value: str) -> bool:
    lowered = value.lower()
    is_nonzero_number = lowered.lstrip("-").isdigit() and int(lowered) != 0

    return lowered in GIT_TRUE_VALUES or is_nonzero_number


def show_command(call: CommandCall) -> str:
    text = " ".join(call.argv)

    if len(text) > MAX_SHOWN_COMMAND_LENGTH:
        return text[:MAX_SHOWN_COMMAND_LENGTH] + "..."
    else:
        return text


def show_origin(call: CommandCall) -> str:
    return f" (via {call.origin})" if call.origin else ""


if __name__ == "__main__":
    main()
