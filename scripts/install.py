#!/usr/bin/env python3
"""install.py — install or refresh the Claude Code and Codex setup from this repository.

Usage:
    install.py [--apply | --check] [--prune] [--allow-dirty] [--first-merge {keep-local,use-repository}]
               [--from-upstream] [--checkout PATH]

Without --apply nothing is written: the script prints what it would change.
With --apply it first copies every file it replaces into a backups folder,
then writes. Running it again changes nothing.

With --from-upstream it installs the commit this checkout's upstream branch
points at instead of the files here: it clones that commit into a temporary
folder, runs the clone's own install.py with the same options, and removes
the clone, so work here that is not pushed stays out.

Files the repository owns (rules, skills, scripts, mods) are copied as they
are. settings.json is merged key by key so lines only this computer has are
kept. CLAUDE.md and the Codex AGENTS.md are merged three ways against the
copy installed last time, so lines added on this computer survive and lines
the repository removed disappear. When no earlier copy is recorded and the
installed file differs, --first-merge says whether the differences are this
computer's own lines (keep-local) or stale lines to replace (use-repository).

With --check it writes nothing and skips the plugin commands, which take
seconds, and lists what differs in both directions: work in this checkout
that is not committed, pushed, or pulled, repository changes not installed
here, and settings and files only this computer has. The session-start hook
config-sync-hook.py runs it.

Plugins named in settings.json and codex/settings.json are installed with
the claude and codex commands. Sign-ins stay manual and are listed at the end.

It records the working checkout in .ai-config-install/checkout inside the
Claude folder, where the session-start hook runs --check, and points that
checkout's core.hooksPath at .githooks, so every commit there runs
scripts/check-commit.py on the content being committed. A run from another
copy of the repository keeps a recorded checkout that still has
scripts/install.py; --checkout names the one to record instead.

Exit codes:
    0  done, or nothing to change
    1  the repository has uncommitted changes or is behind its upstream
    2  an instruction file could not be merged
    3  a repository entry is not registered in this script
    4  an external command failed
    5  a command the hooks need is missing
    6  this operating system is not supported
    7  --check found differences
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import filecmp
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Mapping, Sequence, Set, Tuple


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
Executor = Callable[[Sequence[str]], int]

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CODEX_SOURCE_DIRECTORY = REPOSITORY_ROOT / "codex"
CODEX_SETTINGS_PATH = CODEX_SOURCE_DIRECTORY / "settings.json"
CLAUDE_INSTRUCTION_FILE = "CLAUDE.md"
CODEX_INSTRUCTION_FILE = "AGENTS.md"
SETTINGS_FILE = "settings.json"
STATUS_LINE_FILE = "statusline-command.sh"
CODEX_CONFIG_FILE = "config.toml"
RULES_DIRECTORY = "rules"
SKILLS_DIRECTORY = "skills"
SCRIPTS_DIRECTORY = "scripts"
MODS_DIRECTORY = "mods"
BACKUPS_DIRECTORY = "backups"
STATE_DIRECTORY = ".ai-config-install"
BASE_DIRECTORY = "base"
BACKUP_PREFIX = "ai-config-install-"
MOD_MARKETPLACE_FILE = Path(".claude-plugin") / "marketplace.json"
MOD_PRESERVED_PATHS = ("claude-plans", ".claude-plugin/types")
IGNORED_NAMES = ("__pycache__", ".DS_Store", ".mypy_cache", ".ruff_cache", ".pytest_cache")
RUNTIME_SCRIPTS = (
    "back-reference-hook.py",
    "bulletin-hook.py",
    "claude-commit.py",
    "config-sync-hook.py",
    "main-session-hook.py",
    "session_lineage.py",
    "sub-session-question-hook.py",
    "project-goal-hook.py",
    "shell-guard-hook.py",
    "shell_command.py",
    "secret_files.py",
    "commit_arguments.py",
    "reply-language-hook.py",
    "own_lines.py",
    "session_writes.py",
    "session_commits.py",
    "session-writes-hook.py",
    "skill-gate-hook.py",
    "start-session-hook.py",
)
MAINTENANCE_SCRIPTS = ("install.py", "sync-shared-rules.py", "check-commit.py", "check-rule-size.py")
HOOKS_PATH_KEY = "core.hooksPath"
COMMIT_HOOKS_DIRECTORY = ".githooks"
MERGE_BY_NAME_KEYS = ("enabledPlugins", "extraKnownMarketplaces", "env")
ENABLED_PLUGINS_KEY = "enabledPlugins"
MARKETPLACES_KEY = "extraKnownMarketplaces"
ENVIRONMENT_KEY = "env"
PLUGIN_DIRECTORIES_VARIABLE = "CLAUDE_CODE_PLUGIN_DIRS"
OFFICIAL_MARKETPLACE = "claude-plugins-official"
OFFICIAL_MARKETPLACE_SOURCE = "anthropics/claude-plugins-official"
DIRECTORY_SOURCE = "directory"
SOURCE_ARGUMENT_FIELDS = {"github": "repo", "git": "url", "url": "url", DIRECTORY_SOURCE: "path", "file": "path"}
USER_SCOPE = "user"
FIRST_MERGE_KEEP_LOCAL = "keep-local"
FIRST_MERGE_USE_REPOSITORY = "use-repository"
SYSTEM_MACOS = "Darwin"
SYSTEM_WINDOWS = "Windows"
SYSTEM_LINUX = "Linux"
REQUIRED_COMMANDS = ("claude", "codex", "git", "gh", "node")
HOOK_COMMANDS = ("python3",)
WINDOWS_HOOK_COMMANDS = ("python3", "bash")
MACOS_APPLICATIONS_DIRECTORY = Path("/Applications")
MACOS_APPLICATIONS = ("Claude", "ChatGPT", "Figma", "Google Chrome", "ego lite")
EGO_SKILL_NAME = "ego-browser"
EGO_SKILL_SOURCE = Path(".local") / "share" / "ego" / "ego-skills"
CODEX_INSTALLED_STATUS = "installed"
CODEX_PLUGIN_LINE = re.compile(r"^(\S+@\S+)\s+(not installed|installed)")
TOML_TOP_LEVEL_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
DIFF_PREVIEW_LINES = 40
CODEX_RULES_DIRECTORY = "rules"
CHECKOUT_RECORD = Path(STATE_DIRECTORY) / "checkout"
INSTALL_SCRIPT = Path(SCRIPTS_DIRECTORY) / "install.py"
UPSTREAM = "@{upstream}"
UPSTREAM_COPY_PREFIX = "ai-config-upstream-"
APPLY_FLAG = "--apply"
PRUNE_FLAG = "--prune"
FIRST_MERGE_FLAG = "--first-merge"
CHECKOUT_FLAG = "--checkout"
FETCH_HEAD_FILE = "FETCH_HEAD"
FETCH_INTERVAL_SECONDS = 3600
PER_COMPUTER_SETTINGS = ("enabledPlugins.discord@claude-plugins-official", "env.KNOWLEDGE_VAULT_DIR")
ACCOUNT_SKILLS_NAME = "synced"
CODEX_SYSTEM_SKILLS_NAME = ".system"
CLAUDE_CONFIG_DIRECTORIES = (SKILLS_DIRECTORY, SCRIPTS_DIRECTORY, "agents", "commands", "output-styles")
CLAUDE_CONFIG_FILES = ("keybindings.json",)
CODEX_CONFIG_DIRECTORIES = (SKILLS_DIRECTORY, "prompts", CODEX_RULES_DIRECTORY)
CODEX_APP_KEYS = (
    "notify",
    "service_tier",
    "features",
    "shell_environment_policy",
    "desktop",
    "projects",
    "mcp_servers",
    "tui.model_availability_nux",
    "tui.screen_reader_detection_done",
)
CODEX_APP_MARKETPLACE_PREFIX = "openai-"
CODEX_MARKETPLACES_TABLE = "marketplaces"
CODEX_PLUGINS_TABLE = "plugins"
TOML_TABLE_HEADER = re.compile(r"""^\s*\[((?:[^\[\]"']|"[^"]*"|'[^']*')+)\]\s*(?:#.*)?$""")
TOML_ARRAY_TABLE_HEADER = re.compile(r"""^\s*\[\[((?:[^\[\]"']|"[^"]*"|'[^']*')+)\]\]\s*(?:#.*)?$""")
TOML_KEY = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=")
TOML_LONG_STRING_QUOTES = ('"""', "'''")
TOML_QUOTES = ('"', "'")
TOML_COMMENT = "#"
CHECKOUT_SECTION = "Repository checkout"
NOT_INSTALLED_SECTION = "Repository changes not installed here"
LOCAL_ONLY_SECTION = "Only on this computer (bring into the repository, or list as per-computer in install.py)"
IN_SYNC_MESSAGE = "This computer matches the repository."
MANUAL_STEPS = (
    "Sign in to the Claude app and run `claude` once to sign in there too.",
    "Sign in to the ChatGPT app and run `codex login`.",
    "Run `gh auth login` (HTTPS, store the credential in the system keychain).",
    "In a Claude session, run `/mcp` and authorize the plugin servers you use (for example Figma).",
    "Reinstall plugins a project enables only for itself (installed with --scope local or project).",
    "Set the Claude and Codex apps' own settings, which each computer keeps for itself.",
)

EXIT_DIRTY_REPOSITORY = 1
EXIT_MERGE_CONFLICT = 2
EXIT_UNREGISTERED = 3
EXIT_COMMAND_FAILED = 4
EXIT_HOOK_RUNTIME_MISSING = 5
EXIT_UNSUPPORTED_PLATFORM = 6
EXIT_DIFFERENCES_FOUND = 7


class InstallError(Exception):
    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code


class MergeConflictError(Exception):
    def __init__(self, path: Path, merged_text: str):
        super().__init__(f"{path} has lines changed both here and in the repository")
        self.path = path
        self.merged_text = merged_text


@dataclass(frozen=True)
class Platform:
    system_name: str
    home: Path
    claude_home: Path
    codex_home: Path
    application_paths: Tuple[Path, ...]
    hook_commands: Tuple[str, ...]
    links_ego_skill: bool


@dataclass(frozen=True)
class Mod:
    name: str
    marketplace: str


@dataclass(frozen=True)
class Options:
    apply: bool
    prune: bool
    allow_dirty: bool
    first_merge: str
    check: bool = False
    from_upstream: bool = False
    checkout: Path | None = None


@dataclass(frozen=True)
class KeyOwnership:
    """Keys one owner sets in config.toml: named keys exactly, and whole tables with everything in them."""

    keys: FrozenSet[str]
    tables: Tuple[str, ...]

    def owns(self, key: str) -> bool:
        return key in self.keys or any(key == table or key.startswith(table + ".") for table in self.tables)


def run_command(arguments: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(arguments), capture_output=True, text=True, encoding="utf-8")


def execute_command(arguments: Sequence[str]) -> int:
    """Run a command whose output goes straight to this script's own output."""
    return subprocess.run(list(arguments), check=False).returncode


def run_required(run: Runner, arguments: Sequence[str]) -> str:
    """Run a command that must succeed and return what it printed."""
    result = run(arguments)

    if result.returncode == 0:
        return result.stdout.strip()
    else:
        output = (result.stderr or result.stdout).strip()
        raise InstallError(EXIT_COMMAND_FAILED, f"`{' '.join(arguments)}` failed: {output}")


def detect_platform(environment: Mapping[str, str], system_name: str) -> Platform:
    """Return the per-OS folders and checks; the only place that branches on the OS."""
    if system_name == SYSTEM_MACOS:
        applications = tuple(MACOS_APPLICATIONS_DIRECTORY / f"{name}.app" for name in MACOS_APPLICATIONS)

        return make_platform(environment, system_name, Path(environment["HOME"]), applications, HOOK_COMMANDS, True)
    elif system_name == SYSTEM_LINUX:
        return make_platform(environment, system_name, Path(environment["HOME"]), (), HOOK_COMMANDS, False)
    elif system_name == SYSTEM_WINDOWS:
        home = Path(environment["USERPROFILE"])

        return make_platform(environment, system_name, home, (), WINDOWS_HOOK_COMMANDS, False)
    else:
        raise InstallError(
            EXIT_UNSUPPORTED_PLATFORM,
            f"This script supports macOS, Linux, and Windows, not {system_name}.",
        )


def make_platform(
    environment: Mapping[str, str],
    system_name: str,
    home: Path,
    application_paths: Tuple[Path, ...],
    hook_commands: Tuple[str, ...],
    links_ego_skill: bool,
) -> Platform:
    return Platform(
        system_name=system_name,
        home=home,
        claude_home=resolve_tool_home(environment, "CLAUDE_CONFIG_DIR", home / ".claude"),
        codex_home=resolve_tool_home(environment, "CODEX_HOME", home / ".codex"),
        application_paths=application_paths,
        hook_commands=hook_commands,
        links_ego_skill=links_ego_skill,
    )


def resolve_tool_home(environment: Mapping[str, str], variable: str, default: Path) -> Path:
    if environment.get(variable):
        return Path(environment[variable])
    else:
        return default


def merge_settings(installed: dict, repository: dict, machine_values: dict) -> dict:
    """Merge settings.json: repository values win, lines only this computer has stay."""
    merged = dict(installed)
    keys = list(repository) + [key for key in machine_values if key not in repository]

    for key in keys:
        if key in MERGE_BY_NAME_KEYS:
            merged[key] = {**installed.get(key, {}), **repository.get(key, {}), **machine_values.get(key, {})}
        else:
            merged[key] = repository[key]
    return merged


def make_machine_settings(claude_home: Path, mods: Sequence[Mod]) -> dict:
    """Settings whose values are paths on this computer, so they never live in the repository."""
    marketplaces = {}
    plugin_directories = []

    for mod in mods:
        installed_path = str(claude_home / MODS_DIRECTORY / mod.name)
        marketplaces[mod.marketplace] = {"source": {"source": DIRECTORY_SOURCE, "path": installed_path}}
        plugin_directories.append(installed_path)
    if mods:
        return {
            MARKETPLACES_KEY: marketplaces,
            ENVIRONMENT_KEY: {PLUGIN_DIRECTORIES_VARIABLE: os.pathsep.join(plugin_directories)},
        }
    else:
        return {}


def merge_instruction_file(installed: Path, base: Path, repository: Path) -> str:
    result = subprocess.run(
        ["git", "merge-file", "-p", str(installed), str(base), str(repository)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    if result.returncode == 0:
        return result.stdout
    else:
        raise MergeConflictError(installed, result.stdout)


def set_toml_values(text: str, values: Mapping[str, object]) -> str:
    """Set codex/settings.json `config` in config.toml: plain values at the top level, objects as tables."""
    plain_values = {key: value for key, value in values.items() if not isinstance(value, dict)}
    updated = set_toml_top_level(text, plain_values)

    for table, table_values in values.items():
        if isinstance(table_values, dict):
            updated = set_toml_table(updated, table, table_values)
    return updated


def set_toml_top_level(text: str, values: Mapping[str, object]) -> str:
    """Replace or insert top-level keys before the first table; leave tables alone."""
    lines = text.splitlines(keepends=True)
    table_start = find_next_toml_table(lines, 0)
    head, remaining = replace_toml_keys(lines[:table_start], values)
    inserted = format_toml_lines(remaining)

    if inserted and not head and table_start < len(lines):
        inserted.append("\n")
    return "".join(inserted + head + lines[table_start:])


def set_toml_table(text: str, table: str, values: Mapping[str, object]) -> str:
    """Replace or insert keys of one table, adding the table at the end when it is missing."""
    lines = text.splitlines(keepends=True)
    header = next((index for index, line in enumerate(lines) if read_toml_table_name(line) == table), len(lines))
    table_end = find_next_toml_table(lines, header + 1)
    body, remaining = replace_toml_keys(lines[header + 1 : table_end], values)

    if header < len(lines):
        return "".join(lines[: header + 1] + format_toml_lines(remaining) + body + lines[table_end:])
    else:
        check_toml_table_free(table, lines[: find_next_toml_table(lines, 0)])
        return "".join(lines + make_toml_table_header(lines, table) + format_toml_lines(remaining))


def replace_toml_keys(lines: Sequence[str], values: Mapping[str, object]) -> Tuple[List[str], Dict[str, object]]:
    """Return the lines with each named key's value replaced, and the values no line held."""
    remaining = dict(values)
    replaced = []

    for line in lines:
        match = TOML_TOP_LEVEL_KEY.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            check_single_line_value(key, line)
            replaced.append(format_toml_line(key, remaining.pop(key)))
        else:
            replaced.append(line)
    return replaced, remaining


def find_next_toml_table(lines: Sequence[str], start: int) -> int:
    return next((index for index in range(start, len(lines)) if is_toml_table_header(lines[index])), len(lines))


def make_toml_table_header(lines: Sequence[str], table: str) -> List[str]:
    line_end = "" if not lines or lines[-1].endswith("\n") else "\n"
    blank_line = "\n" if lines else ""

    return [f"{line_end}{blank_line}[{table}]\n"]


def read_toml_table_name(line: str) -> str:
    """Name a table header without its quotes, so [plugins."a@b"] reads as plugins.a@b; other lines read as ""."""
    match = TOML_TABLE_HEADER.match(line)

    return match.group(1).strip().replace('"', "").replace("'", "") if match else ""


def list_toml_keys(text: str) -> List[str]:
    """Name every table and every bare key in it as `table.key`; quoted keys are covered by their table."""
    keys = []
    table = ""

    for line in text.splitlines():
        table_name = read_toml_table_name(line)
        match = TOML_KEY.match(line)

        if table_name:
            table = table_name
            keys.append(table)
        elif match:
            keys.append(f"{table}.{match.group(1)}" if table else match.group(1))
    return keys


def make_codex_ownership(codex_settings: Mapping[str, Any]) -> KeyOwnership:
    config = codex_settings["config"]
    table_keys = {f"{table}.{key}" for table, values in config.items() if isinstance(values, dict) for key in values}
    marketplaces = tuple(f"{CODEX_MARKETPLACES_TABLE}.{name}" for name in codex_settings["marketplaces"])
    plugins = tuple(f"{CODEX_PLUGINS_TABLE}.{plugin}" for plugin in codex_settings["plugins"])

    return KeyOwnership(keys=frozenset(set(config) | table_keys), tables=marketplaces + plugins)


def is_codex_app_key(key: str) -> bool:
    """Whether the Codex app writes this key itself, so each computer keeps its own value."""
    is_app_marketplace = key.startswith(f"{CODEX_MARKETPLACES_TABLE}.{CODEX_APP_MARKETPLACE_PREFIX}")
    is_app_plugin = key.startswith(f"{CODEX_PLUGINS_TABLE}.") and f"@{CODEX_APP_MARKETPLACE_PREFIX}" in key

    return KeyOwnership(keys=frozenset(), tables=CODEX_APP_KEYS).owns(key) or is_app_marketplace or is_app_plugin


def check_toml_table_free(table: str, top_level_lines: Sequence[str]) -> None:
    """Refuse to add [table] where top-level keys already define it, which TOML forbids."""
    names = {match.group(1) for match in map(TOML_KEY.match, top_level_lines) if match}

    if any(name == table or name.startswith(table + ".") for name in names):
        raise InstallError(
            EXIT_MERGE_CONFLICT,
            f"config.toml already sets {table} outside a [{table}] table, so it was not changed. Move those keys into [{table}], then run this script again.",
        )


def check_single_line_value(key: str, line: str) -> None:
    if is_multi_line_value(strip_toml_comment(line.split("=", 1)[1].strip())):
        raise InstallError(
            EXIT_MERGE_CONFLICT,
            f"config.toml: {key} spans several lines, so it was not changed. Write it on one line, then run this script again.",
        )


def strip_toml_comment(value: str) -> str:
    """Drop a trailing comment, reading # inside quoted text as text."""
    quote = ""
    escaped = False

    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif quote == '"' and character == "\\":
            escaped = True
        elif quote and character == quote:
            quote = ""
        elif not quote and character in TOML_QUOTES:
            quote = character
        elif not quote and character == TOML_COMMENT:
            return value[:index].rstrip()
    return value


def is_toml_table_header(line: str) -> bool:
    return bool(TOML_TABLE_HEADER.match(line) or TOML_ARRAY_TABLE_HEADER.match(line))


def is_multi_line_value(value: str) -> bool:
    opens_collection = value.startswith(("[", "{")) and not value.endswith(("]", "}"))
    opens_long_string = any(value.startswith(quote) and value.count(quote) == 1 for quote in TOML_LONG_STRING_QUOTES)

    return opens_collection or opens_long_string


def format_toml_lines(values: Mapping[str, object]) -> List[str]:
    return [format_toml_line(key, value) for key, value in values.items()]


def format_toml_line(key: str, value: object) -> str:
    return f"{key} = {format_toml_value(key, value)}\n"


def format_toml_value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, int):
        return str(value)
    elif isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    else:
        raise InstallError(
            EXIT_UNREGISTERED,
            f"codex/settings.json: {key} must be text, a number, true/false, or a list of text.",
        )


def list_setting_names(settings: Mapping[str, Any]) -> Set[str]:
    """Name each top-level key, and each entry of the keys merged by name as `key.entry`."""
    names: Set[str] = set()

    for key, value in settings.items():
        if key in MERGE_BY_NAME_KEYS and isinstance(value, dict):
            names.update(f"{key}.{entry}" for entry in value)
        else:
            names.add(key)
    return names


def find_base_copy(root: Path, destination: Path) -> Path:
    """The copy of an instruction file installed last time, which the three-way merge compares against."""
    return root / STATE_DIRECTORY / BASE_DIRECTORY / destination.name


def find_checkout_differences(run: Runner, repository_root: Path, now: float) -> List[str]:
    """List work in the checkout that is not committed, pushed, or pulled, fetching at most once an hour."""
    repository = str(repository_root)

    fetch_if_stale(run, repository, now)
    return list_uncommitted_work(run, repository) + list_unsynchronized_commits(run, repository)


def fetch_if_stale(run: Runner, repository: str, now: float) -> None:
    git_directory = run(["git", "-C", repository, "rev-parse", "--absolute-git-dir"]).stdout.strip()
    fetch_head = Path(git_directory) / FETCH_HEAD_FILE

    if not fetch_head.exists() or now - fetch_head.stat().st_mtime > FETCH_INTERVAL_SECONDS:
        run(["git", "-C", repository, "fetch", "--quiet"])


def list_uncommitted_work(run: Runner, repository: str) -> List[str]:
    changed = run(["git", "-C", repository, "status", "--porcelain"]).stdout.splitlines()

    if changed:
        return [f"{repository} has uncommitted changes"]
    else:
        return []


def list_unsynchronized_commits(run: Runner, repository: str) -> List[str]:
    result = run(["git", "-C", repository, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
    counts = result.stdout.split()

    if result.returncode != 0 or len(counts) != 2:
        return [f"{repository} has no upstream branch to compare with"]
    else:
        ahead, behind = (int(count) for count in counts)
        messages = []

        if ahead:
            messages.append(f"{ahead} commits in {repository} are not pushed")
        if behind:
            messages.append(f"{behind} commits on the remote are not pulled into {repository}")
        return messages


def read_checkout_record(claude_home: Path) -> Path | None:
    record = claude_home / CHECKOUT_RECORD
    text = record.read_text(encoding="utf-8").strip() if record.is_file() else ""

    if text:
        return Path(text)
    else:
        return None


def resolve_checkout(named: Path | None, recorded: Path | None, current: Path) -> Path:
    """The working checkout: the one --checkout names, else the recorded one while it has install.py, else this one.

    So a run from another copy of the repository, such as a temporary clone, leaves the record alone.
    """
    if named is not None:
        return named
    elif recorded is not None and has_install_script(recorded):
        return recorded
    else:
        return current


def has_install_script(path: Path) -> bool:
    return (path / INSTALL_SCRIPT).is_file()


def read_upstream(run: Runner, repository: str) -> tuple[str, str]:
    """Name the upstream branch and the commit it pointed at when last fetched."""
    name = run(["git", "-C", repository, "rev-parse", "--abbrev-ref", "--symbolic-full-name", UPSTREAM])
    commit = run(["git", "-C", repository, "rev-parse", "--verify", "--quiet", f"{UPSTREAM}^{{commit}}"])

    if name.returncode == 0 and commit.returncode == 0:
        return name.stdout.strip(), commit.stdout.strip()
    else:
        raise InstallError(
            EXIT_COMMAND_FAILED,
            f"{repository} has no upstream branch to install from. Run this from a checkout whose branch tracks a remote branch.",
        )


def clone_commit(run: Runner, repository: str, commit: str, copy: Path) -> None:
    """Clone the checkout and check out one commit; a local clone holds every object, the fetched upstream's too."""
    run_required(run, ["git", "clone", "--quiet", "--no-checkout", repository, str(copy)])
    run_required(run, ["git", "-C", str(copy), "checkout", "--quiet", "--detach", commit])


def make_copy_arguments(options: Options, checkout: Path) -> list[str]:
    """The options for the copy's own install.py; --checkout keeps the record on the working checkout."""
    arguments = [CHECKOUT_FLAG, str(checkout)]

    if options.apply:
        arguments.append(APPLY_FLAG)
    if options.prune:
        arguments.append(PRUNE_FLAG)
    if options.first_merge:
        arguments.extend([FIRST_MERGE_FLAG, options.first_merge])
    return arguments


def list_entry_names(directory: Path) -> Set[str]:
    return {entry.name for entry in list_entries(directory)}


def count_lines(path: Path) -> Counter[str]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    return Counter(line for line in lines if line.strip())


def find_mods(repository_root: Path) -> List[Mod]:
    mods = []

    for directory in sorted(list_entries(repository_root / MODS_DIRECTORY)):
        marketplace = load_json(directory / MOD_MARKETPLACE_FILE)
        mods.append(Mod(name=directory.name, marketplace=marketplace["name"]))
    return mods


def list_entries(directory: Path) -> List[Path]:
    if directory.is_dir():
        return [entry for entry in directory.iterdir() if entry.name not in IGNORED_NAMES]
    else:
        return []


def iterate_files(directory: Path) -> Iterator[Path]:
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not set(path.relative_to(directory).parts) & set(IGNORED_NAMES):
            yield path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def format_json(value: dict) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def is_preserved(relative_path: Path, preserved: Sequence[str]) -> bool:
    text = relative_path.as_posix()

    return any(text == prefix or text.startswith(prefix + "/") for prefix in preserved)


def check_registered_entries(repository_root: Path) -> None:
    check_scripts_registered(repository_root)
    check_entries_are_directories(repository_root / SKILLS_DIRECTORY)
    check_entries_are_directories(repository_root / CODEX_SOURCE_DIRECTORY.name / SKILLS_DIRECTORY)
    check_entries_are_directories(repository_root / MODS_DIRECTORY)
    check_mods_have_marketplace(repository_root)


def check_scripts_registered(repository_root: Path) -> None:
    present = {entry.name for entry in list_entries(repository_root / SCRIPTS_DIRECTORY)}
    unregistered = sorted(present - set(RUNTIME_SCRIPTS) - set(MAINTENANCE_SCRIPTS))

    if unregistered:
        raise InstallError(
            EXIT_UNREGISTERED,
            f"Add these scripts to RUNTIME_SCRIPTS or MAINTENANCE_SCRIPTS: {', '.join(unregistered)}",
        )


def check_entries_are_directories(directory: Path) -> None:
    files = sorted(entry.name for entry in list_entries(directory) if not entry.is_dir())

    if files:
        raise InstallError(EXIT_UNREGISTERED, f"{directory} should hold only folders, but has: {', '.join(files)}")


def check_mods_have_marketplace(repository_root: Path) -> None:
    missing = sorted(
        directory.name
        for directory in list_entries(repository_root / MODS_DIRECTORY)
        if not (directory / MOD_MARKETPLACE_FILE).is_file()
    )

    if missing:
        raise InstallError(
            EXIT_UNREGISTERED,
            f"These mods need {MOD_MARKETPLACE_FILE.as_posix()}: {', '.join(missing)}",
        )


def check_repository_clean(run: Runner, repository_root: Path) -> None:
    status = run(["git", "-C", str(repository_root), "status", "--porcelain"])

    if status.stdout.strip():
        raise InstallError(
            EXIT_DIRTY_REPOSITORY,
            "The repository has uncommitted changes. Commit them first, or pass --allow-dirty to install them anyway.",
        )


def check_repository_up_to_date(run: Runner, repository_root: Path) -> None:
    behind = run(["git", "-C", str(repository_root), "rev-list", "--count", "HEAD..@{upstream}"])

    if behind.returncode == 0 and int(behind.stdout.strip() or "0") > 0:
        raise InstallError(
            EXIT_DIRTY_REPOSITORY,
            "The repository is behind its upstream. Run `git pull` first, or pass --allow-dirty.",
        )


def check_hook_commands(platform_paths: Platform, which: Callable[[str], object]) -> None:
    missing = [command for command in platform_paths.hook_commands if not which(command)]

    if missing:
        raise InstallError(
            EXIT_HOOK_RUNTIME_MISSING,
            f"The settings.json hooks need {', '.join(missing)}, which this computer does not have. Without them the shell guard cannot start and every command passes unchecked. Install them, then run this script again.",
        )


class Installer:
    def __init__(self, platform_paths: Platform, options: Options, run: Runner, which: Callable[[str], object]):
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        self._platform = platform_paths
        self._options = options
        self._run = run
        self._which = which
        self._backup_name = f"{BACKUP_PREFIX}{timestamp}"
        self._changes: List[str] = []
        self._notes: List[str] = []
        self._conflicts: List[str] = []
        self._local_only: List[str] = []
        self._mods = find_mods(REPOSITORY_ROOT)
        self._recorded_checkout = read_checkout_record(platform_paths.claude_home)
        self._checkout = resolve_checkout(options.checkout, self._recorded_checkout, REPOSITORY_ROOT)

    def install(self) -> None:
        self._report_prerequisites()
        self._set_commit_hooks_path()
        self._record_checkout()
        self._install_instruction_files()
        self._install_claude_files()
        self._install_codex_files()
        self._install_claude_plugins()
        self._install_codex_plugins()
        self._link_ego_skill()
        self._find_local_only()

    def check(self) -> Dict[str, List[str]]:
        """Collect differences without writing and without the plugin commands, which take seconds."""
        self._set_commit_hooks_path()
        self._record_checkout()
        self._install_instruction_files()
        self._install_claude_files()
        self._install_codex_files()
        self._link_ego_skill()
        self._find_local_only()
        return {
            NOT_INSTALLED_SECTION: self._changes + [conflict.splitlines()[0] for conflict in self._conflicts],
            LOCAL_ONLY_SECTION: self._local_only,
        }

    def print_summary(self) -> None:
        heading = "Changed" if self._options.apply else "Would change (dry run; pass --apply to write)"

        print(f"{heading}:")
        for line in self._changes or ["nothing"]:
            print(f"  - {line}")
        if self._local_only:
            print(f"{LOCAL_ONLY_SECTION}:")
            for line in self._local_only:
                print(f"  - {line}")
        if self._notes:
            print("Notes:")
            for line in self._notes:
                print(f"  - {line}")
        if self._options.apply and self._changes:
            print(f"Backups: {self._find_backup(self._platform.claude_home)} and {self._find_backup(self._platform.codex_home)}")
        print("Do by hand on a new computer:")
        for line in MANUAL_STEPS:
            print(f"  - {line}")

    def check_conflicts(self) -> None:
        if self._conflicts:
            details = "\n".join(self._conflicts)

            raise InstallError(EXIT_MERGE_CONFLICT, f"Not written because of merge conflicts:\n{details}")

    def _report_prerequisites(self) -> None:
        for command in REQUIRED_COMMANDS:
            if not self._which(command):
                self._notes.append(f"Command `{command}` is missing; install it, then run this script again.")
        for application in self._platform.application_paths:
            if not application.exists():
                self._notes.append(f"App {application.name} is missing.")
        if self._which("gh") and self._run(["gh", "auth", "status"]).returncode != 0:
            self._notes.append("GitHub CLI is not signed in (`gh auth login`).")

    def _set_commit_hooks_path(self) -> None:
        repository = str(self._checkout)
        current = self._run(["git", "-C", repository, "config", "--get", HOOKS_PATH_KEY]).stdout.strip()

        if current != COMMIT_HOOKS_DIRECTORY:
            self._run_action(["git", "-C", repository, "config", HOOKS_PATH_KEY, COMMIT_HOOKS_DIRECTORY])

    def _record_checkout(self) -> None:
        """Write where the working checkout is, so the session-start hook runs its --check there."""
        if self._recorded_checkout != self._checkout:
            self._write(self._platform.claude_home / CHECKOUT_RECORD, f"{self._checkout}\n", self._platform.claude_home)
        elif self._options.checkout is None and self._checkout != REPOSITORY_ROOT:
            self._notes.append(
                f"Kept the recorded checkout {self._checkout}; this run read the repository from {REPOSITORY_ROOT}. Pass --checkout to record another one.",
            )

    def _install_instruction_files(self) -> None:
        for source, destination, root in self._list_instruction_files():
            self._install_instruction_file(source, destination, root)

    def _install_claude_files(self) -> None:
        claude_home = self._platform.claude_home

        self._sync_directory(REPOSITORY_ROOT / RULES_DIRECTORY, claude_home / RULES_DIRECTORY, claude_home, ())
        for skill in sorted(list_entries(REPOSITORY_ROOT / SKILLS_DIRECTORY)):
            self._sync_directory(skill, claude_home / SKILLS_DIRECTORY / skill.name, claude_home, ())
        for script in RUNTIME_SCRIPTS:
            self._sync_file(
                REPOSITORY_ROOT / SCRIPTS_DIRECTORY / script,
                claude_home / SCRIPTS_DIRECTORY / script,
                claude_home,
            )
        self._sync_file(REPOSITORY_ROOT / STATUS_LINE_FILE, claude_home / STATUS_LINE_FILE, claude_home)
        for mod in self._mods:
            self._sync_directory(
                REPOSITORY_ROOT / MODS_DIRECTORY / mod.name,
                claude_home / MODS_DIRECTORY / mod.name,
                claude_home,
                MOD_PRESERVED_PATHS,
            )
        self._install_settings()

    def _install_codex_files(self) -> None:
        codex_home = self._platform.codex_home

        for skill in sorted(list_entries(CODEX_SOURCE_DIRECTORY / SKILLS_DIRECTORY)):
            self._sync_directory(skill, codex_home / SKILLS_DIRECTORY / skill.name, codex_home, ())
        self._install_codex_config()

    def _install_settings(self) -> None:
        path = self._platform.claude_home / SETTINGS_FILE
        repository = load_json(REPOSITORY_ROOT / SETTINGS_FILE)
        installed = load_json(path) if path.exists() else {}
        merged = merge_settings(installed, repository, make_machine_settings(self._platform.claude_home, self._mods))

        if merged != installed:
            self._write(path, format_json(merged), self._platform.claude_home)

    def _install_codex_config(self) -> None:
        try:
            path = self._platform.codex_home / CODEX_CONFIG_FILE
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            updated = set_toml_values(current, load_json(CODEX_SETTINGS_PATH)["config"])

            if updated != current:
                self._write(path, updated, self._platform.codex_home)
        except InstallError as error:
            self._conflicts.append(str(error))

    def _install_instruction_file(self, source: Path, destination: Path, root: Path) -> None:
        base = find_base_copy(root, destination)
        repository_text = source.read_text(encoding="utf-8")
        conflicts_before = len(self._conflicts)

        try:
            if not destination.exists():
                self._write(destination, repository_text, root)
            elif destination.read_text(encoding="utf-8") == repository_text:
                pass
            elif base.exists():
                self._write_if_changed(destination, merge_instruction_file(destination, base, source), root)
            elif self._options.first_merge == FIRST_MERGE_KEEP_LOCAL:
                self._notes.append(
                    f"{destination}: no earlier install recorded; kept its differences as this computer's own lines.",
                )
            elif self._options.first_merge == FIRST_MERGE_USE_REPOSITORY:
                self._write(destination, repository_text, root)
            else:
                self._conflicts.append(self._describe_unrecorded_difference(destination, source))
            if self._options.apply and len(self._conflicts) == conflicts_before:
                write_text(base, repository_text)
        except MergeConflictError as error:
            self._conflicts.append(f"{error}:\n{error.merged_text}")

    def _describe_unrecorded_difference(self, destination: Path, source: Path) -> str:
        installed_lines = destination.read_text(encoding="utf-8").splitlines()
        repository_lines = source.read_text(encoding="utf-8").splitlines()
        diff = list(difflib.unified_diff(repository_lines, installed_lines, "repository", "installed", lineterm=""))
        preview = "\n".join(diff[:DIFF_PREVIEW_LINES])

        return (
            f"{destination} differs from the repository and no earlier install is recorded. "
            f"Rerun with --first-merge keep-local (the differences are this computer's own lines) "
            f"or --first-merge use-repository (replace them):\n{preview}"
        )

    def _sync_directory(self, source: Path, destination: Path, root: Path, preserved: Sequence[str]) -> None:
        source_files = {path.relative_to(source) for path in iterate_files(source)}

        for relative_path in sorted(source_files):
            self._sync_file(source / relative_path, destination / relative_path, root)
        if destination.is_dir():
            for path in iterate_files(destination):
                relative_path = path.relative_to(destination)
                if relative_path not in source_files and not is_preserved(relative_path, preserved):
                    self._handle_extra_file(path, root)

    def _sync_file(self, source: Path, destination: Path, root: Path) -> None:
        if not (destination.is_file() and filecmp.cmp(source, destination, shallow=False)):
            self._changes.append(f"copy {source.relative_to(REPOSITORY_ROOT).as_posix()} -> {destination}")
            if self._options.apply:
                self._back_up(destination, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _handle_extra_file(self, path: Path, root: Path) -> None:
        if self._options.prune:
            self._changes.append(f"move {path} to backups (not in the repository)")
            if self._options.apply:
                self._back_up(path, root)
                path.unlink()
        else:
            self._local_only.append(f"{path} (not in the repository; --prune moves it to backups)")

    def _install_claude_plugins(self) -> None:
        if self._which("claude"):
            settings = self._make_repository_settings()
            plugins = sorted(plugin for plugin, enabled in settings.get(ENABLED_PLUGINS_KEY, {}).items() if enabled)
            marketplaces = self._read_json_command(["claude", "plugin", "marketplace", "list", "--json"])
            known = {marketplace["name"] for marketplace in marketplaces}
            listed_plugins = self._read_json_command(["claude", "plugin", "list", "--json"])
            installed = {plugin["id"] for plugin in listed_plugins if plugin.get("scope") == USER_SCOPE}

            for marketplace in sorted({plugin.split("@", 1)[1] for plugin in plugins} - known):
                source = self._find_marketplace_source(marketplace, settings)
                self._run_action(["claude", "plugin", "marketplace", "add", source, "--json"])
            for plugin in plugins:
                if plugin not in installed:
                    self._run_action(["claude", "plugin", "install", plugin, "--json"])
        else:
            self._notes.append("Skipped Claude plugins because `claude` is missing.")

    def _install_codex_plugins(self) -> None:
        if self._which("codex"):
            codex_settings = load_json(CODEX_SETTINGS_PATH)
            config_path = self._platform.codex_home / CODEX_CONFIG_FILE
            config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
            statuses = self._read_codex_plugin_statuses()
            missing = [plugin for plugin in codex_settings["plugins"] if statuses.get(plugin) != CODEX_INSTALLED_STATUS]

            for name, source in codex_settings["marketplaces"].items():
                if not self._has_codex_marketplace(config, name):
                    self._run_action(["codex", "plugin", "marketplace", "add", source])
            if missing:
                self._run_action(["codex", "plugin", "marketplace", "upgrade"])
            for plugin in missing:
                self._run_action(["codex", "plugin", "add", plugin])
        else:
            self._notes.append("Skipped Codex plugins because `codex` is missing.")

    def _link_ego_skill(self) -> None:
        link = self._platform.claude_home / SKILLS_DIRECTORY / EGO_SKILL_NAME
        source = self._platform.home / EGO_SKILL_SOURCE

        if self._platform.links_ego_skill and source.exists() and not os.path.lexists(link):
            self._changes.append(f"link {link} -> {source}")
            if self._options.apply:
                link.symlink_to(source, target_is_directory=True)
        elif self._platform.links_ego_skill and not source.exists():
            self._notes.append("ego lite is not installed, so the ego-browser skill was not linked.")

    def _find_local_only(self) -> None:
        self._find_local_settings()
        self._find_local_codex_keys()
        self._find_local_instruction_lines()
        self._find_unowned_paths()

    def _find_local_settings(self) -> None:
        path = self._platform.claude_home / SETTINGS_FILE
        installed = load_json(path) if path.exists() else {}
        expected = list_setting_names(self._make_repository_settings())
        local_names = list_setting_names(installed) - expected - set(PER_COMPUTER_SETTINGS)

        for name in sorted(local_names):
            self._local_only.append(f"{path}: {name}")

    def _find_local_codex_keys(self) -> None:
        path = self._platform.codex_home / CODEX_CONFIG_FILE
        repository = make_codex_ownership(load_json(CODEX_SETTINGS_PATH))
        keys = list_toml_keys(path.read_text(encoding="utf-8")) if path.exists() else []

        for key in keys:
            if not repository.owns(key) and not is_codex_app_key(key):
                self._local_only.append(f"{path}: {key}")

    def _find_local_instruction_lines(self) -> None:
        """Compare with the copy installed last time, so lines the repository has changed since are not taken for local ones."""
        for source, destination, root in self._list_instruction_files():
            base = find_base_copy(root, destination)
            local_lines = count_lines(destination) - count_lines(base if base.exists() else source)

            if local_lines:
                self._local_only.append(
                    f"{destination}: lines the repository does not have ({sum(local_lines.values())})",
                )

    def _find_unowned_paths(self) -> None:
        for directory, owned_names in self._list_config_owners().items():
            for entry in sorted(list_entries(directory)):
                if entry.name not in owned_names:
                    self._local_only.append(f"{entry} (not in the repository)")
        for name in CLAUDE_CONFIG_FILES:
            path = self._platform.claude_home / name
            if path.exists() and not (REPOSITORY_ROOT / name).exists():
                self._local_only.append(f"{path} (not in the repository)")

    def _list_config_owners(self) -> Dict[Path, Set[str]]:
        """Map each folder that holds configuration to the names the repository or this script puts there."""
        claude_home = self._platform.claude_home
        codex_home = self._platform.codex_home
        owners = {claude_home / name: list_entry_names(REPOSITORY_ROOT / name) for name in CLAUDE_CONFIG_DIRECTORIES}
        codex_owners = {
            codex_home / name: list_entry_names(CODEX_SOURCE_DIRECTORY / name) for name in CODEX_CONFIG_DIRECTORIES
        }

        owners[claude_home / SKILLS_DIRECTORY] |= {EGO_SKILL_NAME, ACCOUNT_SKILLS_NAME}
        owners[claude_home / SCRIPTS_DIRECTORY] = set(RUNTIME_SCRIPTS) | set(MAINTENANCE_SCRIPTS)
        codex_owners[codex_home / SKILLS_DIRECTORY] |= {CODEX_SYSTEM_SKILLS_NAME}
        return {**owners, **codex_owners}

    def _list_instruction_files(self) -> List[Tuple[Path, Path, Path]]:
        """The files merged three ways, as (repository source, installed copy, tool folder)."""
        claude_home = self._platform.claude_home
        codex_home = self._platform.codex_home
        codex_rules = sorted(list_entries(CODEX_SOURCE_DIRECTORY / CODEX_RULES_DIRECTORY))

        return [
            (REPOSITORY_ROOT / CLAUDE_INSTRUCTION_FILE, claude_home / CLAUDE_INSTRUCTION_FILE, claude_home),
            (CODEX_SOURCE_DIRECTORY / CODEX_INSTRUCTION_FILE, codex_home / CODEX_INSTRUCTION_FILE, codex_home),
            *((rules, codex_home / CODEX_RULES_DIRECTORY / rules.name, codex_home) for rules in codex_rules),
        ]

    def _make_repository_settings(self) -> dict:
        repository = load_json(REPOSITORY_ROOT / SETTINGS_FILE)

        return merge_settings({}, repository, make_machine_settings(self._platform.claude_home, self._mods))

    def _read_json_command(self, arguments: Sequence[str]) -> list:
        return json.loads(run_required(self._run, arguments) or "[]")

    def _read_codex_plugin_statuses(self) -> Dict[str, str]:
        result = self._run(["codex", "plugin", "list"])
        statuses = {}

        for line in result.stdout.splitlines():
            match = CODEX_PLUGIN_LINE.match(line)
            if match:
                statuses[match.group(1)] = match.group(2)
        return statuses

    def _run_action(self, arguments: Sequence[str]) -> None:
        self._changes.append(f"run {' '.join(arguments)}")
        if self._options.apply:
            run_required(self._run, arguments)

    def _find_marketplace_source(self, marketplace: str, settings: dict) -> str:
        entry = settings.get(MARKETPLACES_KEY, {}).get(marketplace)

        if entry:
            source = entry["source"]
            return source[SOURCE_ARGUMENT_FIELDS[source["source"]]]
        elif marketplace == OFFICIAL_MARKETPLACE:
            return OFFICIAL_MARKETPLACE_SOURCE
        else:
            raise InstallError(
                EXIT_UNREGISTERED,
                f"settings.json enables a plugin from {marketplace}, but {MARKETPLACES_KEY} has no source for it.",
            )

    def _write(self, path: Path, text: str, root: Path) -> None:
        self._changes.append(f"write {path}")
        if self._options.apply:
            self._back_up(path, root)
            write_text(path, text)

    def _write_if_changed(self, path: Path, text: str, root: Path) -> None:
        if path.read_text(encoding="utf-8") != text:
            self._write(path, text, root)

    def _find_backup(self, root: Path) -> Path:
        return root / BACKUPS_DIRECTORY / self._backup_name

    def _back_up(self, path: Path, root: Path) -> None:
        if path.exists():
            backup = self._find_backup(root) / path.relative_to(root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)

    def _has_codex_marketplace(self, config: str, name: str) -> bool:
        escaped = re.escape(name)

        return re.search(rf'^\[marketplaces\.(?:"{escaped}"|{escaped})\]', config, re.MULTILINE) is not None


def parse_options(arguments: Sequence[str]) -> Options:
    parser = argparse.ArgumentParser(
        description="Install or refresh the Claude Code and Codex setup from this repository.",
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(APPLY_FLAG, action="store_true", help="write the changes (default: only show them)")
    mode.add_argument(
        "--check",
        action="store_true",
        help="list differences in both directions quickly, write nothing, and exit 7 when there are any",
    )
    parser.add_argument(
        PRUNE_FLAG,
        action="store_true",
        help="move installed files the repository no longer has to backups",
    )
    parser.add_argument("--allow-dirty", action="store_true", help="install even with uncommitted or unpulled changes")
    parser.add_argument(
        FIRST_MERGE_FLAG,
        choices=[FIRST_MERGE_KEEP_LOCAL, FIRST_MERGE_USE_REPOSITORY],
        help="how to treat differences in CLAUDE.md or AGENTS.md when no earlier install is recorded",
    )
    parser.add_argument(
        "--from-upstream",
        action="store_true",
        help="install the commit this checkout's upstream branch points at, from a temporary clone, leaving out work here that is not pushed",
    )
    parser.add_argument(
        CHECKOUT_FLAG,
        type=parse_checkout,
        help="the working checkout to record for the session-start check and to set core.hooksPath in (default: the recorded one while it has scripts/install.py, else this one)",
    )
    parsed = parser.parse_args(arguments)

    check_mode_options(parser, parsed)
    return Options(
        apply=parsed.apply,
        prune=parsed.prune,
        allow_dirty=parsed.allow_dirty,
        first_merge=parsed.first_merge or "",
        check=parsed.check,
        from_upstream=parsed.from_upstream,
        checkout=parsed.checkout,
    )


def parse_checkout(text: str) -> Path:
    path = Path(text).expanduser().resolve()

    if has_install_script(path):
        return path
    else:
        raise argparse.ArgumentTypeError(
            f"{text} is not a checkout of this repository: it has no {INSTALL_SCRIPT.as_posix()}",
        )


def check_mode_options(parser: argparse.ArgumentParser, parsed: argparse.Namespace) -> None:
    if parsed.check and (parsed.from_upstream or parsed.checkout is not None):
        parser.error(
            "--check compares this checkout with this computer, so it takes neither --from-upstream nor --checkout",
        )


def run_from_upstream(
    repository_root: Path,
    platform_paths: Platform,
    options: Options,
    run: Runner,
    execute: Executor,
) -> int:
    """Install through the clone's own install.py, so the files and the code that installs them come from one commit."""
    repository = str(repository_root)
    fetch = run(["git", "-C", repository, "fetch", "--quiet"])
    name, commit = read_upstream(run, repository)
    checkout = resolve_checkout(options.checkout, read_checkout_record(platform_paths.claude_home), repository_root)

    print(
        f"Installing {name} at {commit[:12]} from a temporary clone; work in {repository} that is not pushed stays out.",
    )
    if fetch.returncode != 0:
        print(f"Could not fetch, so this is {name} as fetched last.")
    sys.stdout.flush()
    with tempfile.TemporaryDirectory(prefix=UPSTREAM_COPY_PREFIX) as directory:
        copy = Path(directory) / repository_root.name

        clone_commit(run, repository, commit, copy)
        return execute([sys.executable, "-I", str(copy / INSTALL_SCRIPT), *make_copy_arguments(options, checkout)])


def run_install(platform_paths: Platform, options: Options) -> int:
    check_registered_entries(REPOSITORY_ROOT)
    if not options.allow_dirty:
        run_command(["git", "-C", str(REPOSITORY_ROOT), "fetch", "--quiet"])
        check_repository_clean(run_command, REPOSITORY_ROOT)
        check_repository_up_to_date(run_command, REPOSITORY_ROOT)
    check_hook_commands(platform_paths, shutil.which)
    installer = Installer(platform_paths, options, run_command, shutil.which)
    installer.install()
    installer.print_summary()
    installer.check_conflicts()
    return 0


def run_check(platform_paths: Platform, options: Options) -> int:
    check_registered_entries(REPOSITORY_ROOT)
    checkout = find_checkout_differences(run_command, REPOSITORY_ROOT, time.time())
    installer = Installer(platform_paths, options, run_command, shutil.which)
    sections = {CHECKOUT_SECTION: checkout, **installer.check()}
    found = {heading: lines for heading, lines in sections.items() if lines}

    print_check_report(found)
    return EXIT_DIFFERENCES_FOUND if found else 0


def print_check_report(sections: Mapping[str, Sequence[str]]) -> None:
    if sections:
        for heading, lines in sections.items():
            print(f"{heading}:")
            for line in lines:
                print(f"  - {line}")
    else:
        print(IN_SYNC_MESSAGE)


def main(arguments: Sequence[str]) -> int:
    try:
        options = parse_options(arguments)
        platform_paths = detect_platform(os.environ, platform.system())

        if options.check:
            return run_check(platform_paths, options)
        elif options.from_upstream:
            return run_from_upstream(REPOSITORY_ROOT, platform_paths, options, run_command, execute_command)
        else:
            return run_install(platform_paths, options)
    except InstallError as error:
        sys.stdout.flush()
        print(error, file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
