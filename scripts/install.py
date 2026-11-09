#!/usr/bin/env python3
"""install.py — install or refresh the Claude Code and Codex setup from this repository.

Usage:
    install.py [--apply] [--prune] [--allow-dirty] [--first-merge {keep-local,use-repository}]

Without --apply nothing is written: the script prints what it would change.
With --apply it first copies every file it replaces into a backups folder,
then writes. Running it again changes nothing.

Files the repository owns (rules, skills, scripts, mods) are copied as they
are. settings.json is merged key by key so lines only this computer has are
kept. CLAUDE.md and the Codex AGENTS.md are merged three ways against the
copy installed last time, so lines added on this computer survive and lines
the repository removed disappear. When no earlier copy is recorded and the
installed file differs, --first-merge says whether the differences are this
computer's own lines (keep-local) or stale lines to replace (use-repository).

Plugins named in settings.json and codex/settings.json are installed with
the claude and codex commands. Sign-ins stay manual and are listed at the end.

Exit codes:
    0  done, or nothing to change
    1  the repository has uncommitted changes or is behind its upstream
    2  an instruction file could not be merged
    3  a repository entry is not registered in this script
    4  an external command failed
    5  a command the hooks need is missing
    6  this operating system is not supported
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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Mapping, Sequence, Tuple


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

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
IGNORED_NAMES = ("__pycache__", ".DS_Store")
RUNTIME_SCRIPTS = (
    "claude-commit.py",
    "shell-guard-hook.py",
    "shell_command.py",
    "secret_files.py",
    "commit_arguments.py",
)
MAINTENANCE_SCRIPTS = ("install.py", "sync-shared-rules.py")
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
MANUAL_STEPS = (
    "Sign in to the Claude app and run `claude` once to sign in there too.",
    "Sign in to the ChatGPT app and run `codex login`.",
    "Run `gh auth login` (HTTPS, store the credential in the system keychain).",
    "In a Claude session, run `/mcp` and authorize the plugin servers you use (for example Figma).",
    "Reinstall plugins a project enables only for itself (installed with --scope local or project).",
)

EXIT_DIRTY_REPOSITORY = 1
EXIT_MERGE_CONFLICT = 2
EXIT_UNREGISTERED = 3
EXIT_COMMAND_FAILED = 4
EXIT_HOOK_RUNTIME_MISSING = 5
EXIT_UNSUPPORTED_PLATFORM = 6


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


def run_command(arguments: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(arguments), capture_output=True, text=True, encoding="utf-8")


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


def set_toml_top_level(text: str, values: Mapping[str, object]) -> str:
    """Replace or insert top-level scalar keys before the first table; leave tables alone."""
    lines = text.splitlines(keepends=True)
    table_start = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    remaining = dict(values)
    head = []

    for line in lines[:table_start]:
        match = TOML_TOP_LEVEL_KEY.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            head.append(f"{key} = {format_toml_value(key, remaining.pop(key))}\n")
        else:
            head.append(line)
    inserted = [f"{key} = {format_toml_value(key, value)}\n" for key, value in remaining.items()]
    if inserted and not head and table_start < len(lines):
        inserted.append("\n")
    return "".join(inserted + head + lines[table_start:])


def format_toml_value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, int):
        return str(value)
    elif isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    else:
        raise InstallError(EXIT_UNREGISTERED, f"codex/settings.json: {key} must be text, a number, or true/false.")


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
        self._mods = find_mods(REPOSITORY_ROOT)

    def install(self) -> None:
        self._report_prerequisites()
        self._install_claude_files()
        self._install_codex_files()
        self._install_claude_plugins()
        self._install_codex_plugins()
        self._link_ego_skill()

    def print_summary(self) -> None:
        heading = "Changed" if self._options.apply else "Would change (dry run; pass --apply to write)"

        print(f"{heading}:")
        for line in self._changes or ["nothing"]:
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

    def _install_claude_files(self) -> None:
        claude_home = self._platform.claude_home

        self._install_instruction_file(
            REPOSITORY_ROOT / CLAUDE_INSTRUCTION_FILE,
            claude_home / CLAUDE_INSTRUCTION_FILE,
            claude_home,
        )
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

        self._install_instruction_file(
            CODEX_SOURCE_DIRECTORY / CODEX_INSTRUCTION_FILE,
            codex_home / CODEX_INSTRUCTION_FILE,
            codex_home,
        )
        for skill in sorted(list_entries(CODEX_SOURCE_DIRECTORY / SKILLS_DIRECTORY)):
            self._sync_directory(skill, codex_home / SKILLS_DIRECTORY / skill.name, codex_home, ())
        self._install_codex_config()

    def _install_settings(self) -> None:
        path = self._platform.claude_home / SETTINGS_FILE
        repository = load_json(REPOSITORY_ROOT / SETTINGS_FILE)
        installed = load_json(path) if path.exists() else {}
        merged = merge_settings(installed, repository, make_machine_settings(self._platform.claude_home, self._mods))
        local_keys = sorted(key for key in installed if key not in repository)

        if merged != installed:
            self._write(path, format_json(merged), self._platform.claude_home)
        if local_keys:
            self._notes.append(f"settings.json keys only this computer has (kept): {', '.join(local_keys)}")

    def _install_codex_config(self) -> None:
        path = self._platform.codex_home / CODEX_CONFIG_FILE
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = set_toml_top_level(current, load_json(CODEX_SETTINGS_PATH)["config"])

        if updated != current:
            self._write(path, updated, self._platform.codex_home)

    def _install_instruction_file(self, source: Path, destination: Path, root: Path) -> None:
        base = root / STATE_DIRECTORY / BASE_DIRECTORY / destination.name
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
            self._notes.append(f"{path} is not in the repository; pass --prune to move it to backups.")

    def _install_claude_plugins(self) -> None:
        if self._which("claude"):
            repository = load_json(REPOSITORY_ROOT / SETTINGS_FILE)
            settings = merge_settings({}, repository, make_machine_settings(self._platform.claude_home, self._mods))
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

    def _read_json_command(self, arguments: Sequence[str]) -> list:
        result = self._run(arguments)

        if result.returncode == 0:
            return json.loads(result.stdout or "[]")
        else:
            raise InstallError(EXIT_COMMAND_FAILED, f"`{' '.join(arguments)}` failed: {result.stderr.strip()}")

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
            result = self._run(arguments)
            if result.returncode != 0:
                output = (result.stderr or result.stdout).strip()
                raise InstallError(EXIT_COMMAND_FAILED, f"`{' '.join(arguments)}` failed: {output}")

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

    parser.add_argument("--apply", action="store_true", help="write the changes (default: only show them)")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="move installed files the repository no longer has to backups",
    )
    parser.add_argument("--allow-dirty", action="store_true", help="install even with uncommitted or unpulled changes")
    parser.add_argument(
        "--first-merge",
        choices=[FIRST_MERGE_KEEP_LOCAL, FIRST_MERGE_USE_REPOSITORY],
        help="how to treat differences in CLAUDE.md or AGENTS.md when no earlier install is recorded",
    )
    parsed = parser.parse_args(arguments)

    return Options(
        apply=parsed.apply,
        prune=parsed.prune,
        allow_dirty=parsed.allow_dirty,
        first_merge=parsed.first_merge or "",
    )


def main(arguments: Sequence[str]) -> int:
    try:
        options = parse_options(arguments)
        platform_paths = detect_platform(os.environ, platform.system())

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
    except InstallError as error:
        sys.stdout.flush()
        print(error, file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
