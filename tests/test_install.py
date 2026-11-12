#!/usr/bin/env python3
"""Tests for scripts/install.py.

The merge helpers are tested on their own. The installer runs against
temporary Claude and Codex folders with a stub in place of the claude,
codex, and gh commands, so the tests change nothing on this computer and
need no network.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Sequence
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPOSITORY_ROOT / "scripts" / "install.py"
LOCAL_LINE = "- A line only this computer has."
VAULT_VARIABLE = "KNOWLEDGE_VAULT_DIR"
FIRST_VAULT_FOLDER = "/Users/someone/vault"
SECOND_VAULT_FOLDER = "/home/someone/notes/vault"
SCHEDULED_TASKS_DIRECTORY = "scheduled-tasks"
GIT_REPOSITORY_NAME = re.compile(r"([^/]+?)(?:\.git)?/?$")
SPECIFICATION = importlib.util.spec_from_file_location("install", INSTALL_SCRIPT)
install = importlib.util.module_from_spec(SPECIFICATION)
sys.modules[SPECIFICATION.name] = install
SPECIFICATION.loader.exec_module(install)


class StubCommands:
    """Answers the claude, codex, and gh commands as if every plugin were installed.

    Like the real command, `codex plugin marketplace add` records the
    marketplace in config.toml (checked against codex-cli 0.163.0).
    """

    def __init__(self, codex_config: Path):
        repository_settings = json.loads((REPOSITORY_ROOT / "settings.json").read_text(encoding="utf-8"))
        codex_settings = json.loads(install.CODEX_SETTINGS_PATH.read_text(encoding="utf-8"))
        plugins = [plugin for plugin, enabled in repository_settings.get("enabledPlugins", {}).items() if enabled]

        self.calls: List[List[str]] = []
        self.git_config: dict = {}
        self._codex_config = codex_config
        self._marketplaces = [{"name": plugin.split("@", 1)[1]} for plugin in plugins]
        self._plugins = [{"id": plugin, "scope": "user"} for plugin in plugins]
        self._codex_plugins = "\n".join(f"{plugin}  installed, enabled  1.0" for plugin in codex_settings["plugins"])

    def run(self, arguments: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        command = list(arguments)

        self.calls.append(command)
        if command[:4] == ["claude", "plugin", "marketplace", "list"]:
            return self._answer(json.dumps(self._marketplaces))
        elif command[:3] == ["claude", "plugin", "list"]:
            return self._answer(json.dumps(self._plugins))
        elif command[:3] == ["codex", "plugin", "list"]:
            return self._answer(self._codex_plugins)
        elif command[:4] == ["codex", "plugin", "marketplace", "add"]:
            self._record_codex_marketplace(command[4])
            return self._answer("")
        elif command[:1] == ["git"] and command[3:5] == ["config", "--get"]:
            return self._answer(self.git_config.get(command[5], ""))
        elif command[:1] == ["git"] and command[3:4] == ["config"]:
            self.git_config[command[4]] = command[5]
            return self._answer("")
        else:
            return self._answer("")

    def _record_codex_marketplace(self, source: str) -> None:
        name = GIT_REPOSITORY_NAME.search(source).group(1)
        current = self._codex_config.read_text(encoding="utf-8") if self._codex_config.exists() else ""
        entry = f"\n[marketplaces.{name}]\nsource = {json.dumps(source)}\n"

        self._codex_config.write_text(current + entry, encoding="utf-8")

    def _answer(self, stdout: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def find_any_command(command: str) -> str:
    return f"/usr/bin/{command}"


def make_checkout(parent: Path, name: str) -> Path:
    checkout = parent / name

    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "install.py").write_text("", encoding="utf-8")
    return checkout


def write_checkout_record(claude_home: Path, checkout: Path) -> Path:
    record = claude_home / install.CHECKOUT_RECORD

    record.parent.mkdir(parents=True)
    record.write_text(f"{checkout}\n", encoding="utf-8")
    return record


def make_test_platform(home: Path) -> "install.Platform":
    return install.Platform(
        system_name="Darwin",
        home=home,
        claude_home=home / ".claude",
        codex_home=home / ".codex",
        application_paths=(),
        hook_commands=("python3",),
        links_ego_skill=True,
    )


def run_installer_on(
    platform: "install.Platform",
    commands: StubCommands,
    options: "install.Options",
) -> "install.Installer":
    installer = install.Installer(platform, options, commands.run, find_any_command)

    with redirect_stdout(io.StringIO()):
        installer.install()
    return installer


def write_settings(platform: "install.Platform", settings: dict) -> None:
    path = platform.claude_home / "settings.json"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")


def read_settings(platform: "install.Platform") -> dict:
    return json.loads((platform.claude_home / "settings.json").read_text(encoding="utf-8"))


class DetectPlatformTest(unittest.TestCase):
    def test_macos_uses_home(self):
        detected = install.detect_platform({"HOME": "/Users/someone"}, "Darwin")

        self.assertEqual(detected.claude_home, Path("/Users/someone/.claude"))
        self.assertEqual(detected.codex_home, Path("/Users/someone/.codex"))
        self.assertTrue(detected.links_ego_skill)

    def test_windows_uses_user_profile_and_needs_bash(self):
        detected = install.detect_platform({"USERPROFILE": "C:/Users/someone"}, "Windows")

        self.assertEqual(detected.claude_home, Path("C:/Users/someone/.claude"))
        self.assertIn("bash", detected.hook_commands)
        self.assertFalse(detected.links_ego_skill)

    def test_linux_uses_home_without_app_checks(self):
        detected = install.detect_platform({"HOME": "/home/someone"}, "Linux")

        self.assertEqual(detected.claude_home, Path("/home/someone/.claude"))
        self.assertEqual(detected.codex_home, Path("/home/someone/.codex"))
        self.assertEqual(detected.application_paths, ())
        self.assertNotIn("bash", detected.hook_commands)
        self.assertFalse(detected.links_ego_skill)

    def test_environment_variables_override_tool_homes(self):
        environment = {
            "HOME": "/Users/someone",
            "CLAUDE_CONFIG_DIR": "/elsewhere/claude",
            "CODEX_HOME": "/elsewhere/codex",
        }
        detected = install.detect_platform(environment, "Darwin")

        self.assertEqual(detected.claude_home, Path("/elsewhere/claude"))
        self.assertEqual(detected.codex_home, Path("/elsewhere/codex"))

    def test_unknown_system_stops(self):
        with self.assertRaises(install.InstallError) as raised:
            install.detect_platform({"HOME": "/home/someone"}, "Plan9")
        self.assertEqual(raised.exception.exit_code, install.EXIT_UNSUPPORTED_PLATFORM)


class MergeSettingsTest(unittest.TestCase):
    def test_merges_named_entries_and_keeps_local_keys(self):
        installed = {
            "enabledPlugins": {"local@market": True, "shared@market": False},
            "hooks": {"PreToolUse": ["old hook"]},
            "agentPushNotifEnabled": True,
        }
        repository = {"enabledPlugins": {"shared@market": True}, "hooks": {"PreToolUse": ["new hook"]}}
        merged = install.merge_settings(installed, repository, {})

        self.assertEqual(merged["enabledPlugins"], {"local@market": True, "shared@market": True})
        self.assertEqual(merged["hooks"], {"PreToolUse": ["new hook"]})
        self.assertTrue(merged["agentPushNotifEnabled"])

    def test_machine_values_fill_paths(self):
        mods = [install.Mod(name="usage-panel", marketplace="local-mods")]
        machine = install.make_machine_settings(Path("/Users/someone/.claude"), mods)
        merged = install.merge_settings({}, {"env": {"WATCH": "1"}}, machine)

        self.assertEqual(merged["env"]["WATCH"], "1")
        self.assertEqual(merged["env"]["CLAUDE_CODE_PLUGIN_DIRS"], "/Users/someone/.claude/mods/usage-panel")
        self.assertEqual(
            merged["extraKnownMarketplaces"]["local-mods"],
            {"source": {"source": "directory", "path": "/Users/someone/.claude/mods/usage-panel"}},
        )

    def test_no_mods_means_no_machine_values(self):
        self.assertEqual(install.make_machine_settings(Path("/Users/someone/.claude"), []), {})


class MergeInstructionFileTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def test_keeps_local_lines_and_drops_removed_lines(self):
        base = self._write("base", "one\ntwo\nthree\nfour\nfive\n")
        installed = self._write("installed", "one\ntwo\nthree\nfour\nfive\nlocal\n")
        repository = self._write("repository", "one\nthree\nfour\nfive\n")

        self.assertEqual(install.merge_instruction_file(installed, base, repository), "one\nthree\nfour\nfive\nlocal\n")

    def test_adjacent_changes_conflict(self):
        base = self._write("base", "one\ntwo\nthree\n")
        installed = self._write("installed", "one\nlocal\nthree\n")
        repository = self._write("repository", "one\nrepository\nthree\n")

        with self.assertRaises(install.MergeConflictError):
            install.merge_instruction_file(installed, base, repository)

    def _write(self, name: str, text: str) -> Path:
        path = self._root / name

        path.write_text(text, encoding="utf-8")
        return path


class SetTomlTopLevelTest(unittest.TestCase):
    def test_replaces_existing_and_inserts_missing_keys(self):
        text = 'model = "old"\nnotify = ["x"]\n\n[projects."/a"]\nmodel = "table value"\n'
        updated = install.set_toml_top_level(text, {"model": "new", "project_doc_max_bytes": 65536})

        self.assertEqual(
            updated,
            'project_doc_max_bytes = 65536\nmodel = "new"\nnotify = ["x"]\n\n[projects."/a"]\nmodel = "table value"\n',
        )

    def test_empty_file(self):
        self.assertEqual(install.set_toml_top_level("", {"model": "new"}), 'model = "new"\n')

    def test_file_starting_with_a_table_gets_a_blank_line(self):
        self.assertEqual(install.set_toml_top_level("[a]\nb = 1\n", {"model": "new"}), 'model = "new"\n\n[a]\nb = 1\n')

    def test_rejects_unsupported_values(self):
        with self.assertRaises(install.InstallError):
            install.set_toml_top_level("", {"model": 1.5})

    def test_writes_lists_of_text(self):
        self.assertEqual(install.set_toml_top_level("", {"notify": ["a", "b"]}), 'notify = ["a", "b"]\n')

    def test_refuses_to_replace_a_value_spanning_lines(self):
        with self.assertRaises(install.InstallError):
            install.set_toml_top_level('notify = [\n  "a",\n]\n', {"notify": ["b"]})


class SetTomlTableTest(unittest.TestCase):
    def test_replaces_keys_and_leaves_the_rest_of_the_table(self):
        text = 'model = "m"\n\n[tui]\nstatus_line = ["old"]\nscreen_reader_detection_done = true\n\n[desktop]\nx = 1\n'
        values = {"model": "m", "tui": {"status_line": ["new"], "terminal_title": ["t"]}}
        updated = install.set_toml_values(text, values)

        self.assertEqual(
            updated,
            'model = "m"\n\n[tui]\nterminal_title = ["t"]\nstatus_line = ["new"]\n'
            "screen_reader_detection_done = true\n\n[desktop]\nx = 1\n",
        )

    def test_adds_a_missing_table_at_the_end(self):
        updated = install.set_toml_values('model = "m"', {"tui": {"status_line": ["a"]}})

        self.assertEqual(updated, 'model = "m"\n\n[tui]\nstatus_line = ["a"]\n')

    def test_finds_a_header_with_a_comment(self):
        updated = install.set_toml_values('[tui] # mine\nstatus_line = ["old"]\n', {"tui": {"status_line": ["new"]}})

        self.assertEqual(updated, '[tui] # mine\nstatus_line = ["new"]\n')

    def test_replaces_a_value_followed_by_a_comment(self):
        updated = install.set_toml_values('[tui]\nstatus_line = ["old"] # mine\n', {"tui": {"status_line": ["new"]}})

        self.assertEqual(updated, '[tui]\nstatus_line = ["new"]\n')

    def test_refuses_a_table_defined_by_top_level_keys(self):
        for text in ("tui = { status_line = [] }\n", "tui.status_line = []\n"):
            with self.subTest(text=text), self.assertRaises(install.InstallError):
                install.set_toml_values(text, {"tui": {"status_line": ["a"]}})

    def test_nested_array_line_does_not_end_the_table(self):
        text = '[tui]\nx = [\n  [1],\n]\nstatus_line = ["old"]\n'
        updated = install.set_toml_values(text, {"tui": {"status_line": ["new"]}})

        self.assertEqual(updated.count("status_line"), 1)


class TomlKeysTest(unittest.TestCase):
    def test_names_tables_and_their_bare_keys(self):
        text = 'model = "m"\n[plugins."a@b"]\nenabled = true\n[tui.model_availability_nux]\n"gpt-x" = 1\n'

        self.assertEqual(
            install.list_toml_keys(text),
            ["model", "plugins.a@b", "plugins.a@b.enabled", "tui.model_availability_nux"],
        )

    def test_reads_quoted_headers_with_brackets_and_dotted_keys(self):
        text = '[projects."/a[b]"] # trusted\ntrust_level = "trusted"\n[x]\na.b = 1\n'

        self.assertEqual(install.list_toml_keys(text), ["projects./a[b]", "projects./a[b].trust_level", "x", "x.a.b"])

    def test_separates_repository_app_and_local_keys(self):
        codex_settings = {
            "config": {"model": "m", "tui": {"status_line": ["a"]}},
            "marketplaces": {"market": "url"},
            "plugins": ["tool@market"],
        }
        repository = install.make_codex_ownership(codex_settings)

        self.assertTrue(repository.owns("tui.status_line"))
        self.assertTrue(repository.owns("plugins.tool@market.enabled"))
        self.assertFalse(repository.owns("tui.terminal_title"))
        self.assertTrue(install.is_codex_app_key("plugins.browser@openai-bundled"))
        self.assertTrue(install.is_codex_app_key("projects./Users/someone.trust_level"))
        self.assertFalse(install.is_codex_app_key("tui.terminal_title"))


class CheckoutDifferencesTest(unittest.TestCase):
    def test_lists_uncommitted_unpushed_and_unpulled_work(self):
        answers = {"status": " M CLAUDE.md\n", "rev-list": "2\t1\n"}
        differences = install.find_checkout_differences(self._make_runner(answers, 0), Path("/repo"), 0.0)

        self.assertEqual(len(differences), 3)

    def test_clean_checkout_has_no_differences(self):
        answers = {"status": "", "rev-list": "0\t0\n"}

        self.assertEqual(install.find_checkout_differences(self._make_runner(answers, 0), Path("/repo"), 0.0), [])

    def test_missing_upstream_is_reported(self):
        answers = {"status": "", "rev-list": ""}

        self.assertEqual(len(install.find_checkout_differences(self._make_runner(answers, 128), Path("/repo"), 0.0)), 1)

    def _make_runner(self, answers: dict, rev_list_code: int):
        def run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
            command = arguments[3] if len(arguments) > 3 else ""
            code = rev_list_code if command == "rev-list" else 0

            return subprocess.CompletedProcess([], code, stdout=answers.get(command, ""), stderr="")

        return run


class RegistrationTest(unittest.TestCase):
    def test_repository_entries_are_registered(self):
        install.check_registered_entries(REPOSITORY_ROOT)

    def test_unregistered_script_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts" / "new-tool.py").write_text("", encoding="utf-8")
            with self.assertRaises(install.InstallError) as raised:
                install.check_registered_entries(root)
            self.assertEqual(raised.exception.exit_code, install.EXIT_UNREGISTERED)

    def test_tool_caches_are_not_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            for cache in (".mypy_cache", ".ruff_cache", ".pytest_cache"):
                (root / "scripts" / cache).mkdir(parents=True)
            install.check_registered_entries(root)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._platform = make_test_platform(Path(self._directory.name))
        self._commands = StubCommands(self._platform.codex_home / "config.toml")

    def tearDown(self):
        self._directory.cleanup()

    def test_second_run_changes_nothing(self):
        first = self._run_installer(apply=True)
        second = self._run_installer(apply=True)

        self.assertTrue(first._changes)
        self.assertEqual(second._changes, [])
        self.assertTrue((self._platform.claude_home / "CLAUDE.md").is_file())
        self.assertTrue((self._platform.codex_home / "config.toml").is_file())

    def test_dry_run_writes_nothing(self):
        installer = self._run_installer(apply=False)

        self.assertTrue(installer._changes)
        self.assertFalse(self._platform.claude_home.exists())

    def test_points_hooks_path_at_commit_check(self):
        dry_run = self._run_installer(apply=False)
        applied = self._run_installer(apply=True)

        self.assertTrue(any(install.HOOKS_PATH_KEY in change for change in dry_run._changes))
        self.assertEqual(self._commands.git_config, {install.HOOKS_PATH_KEY: install.COMMIT_HOOKS_DIRECTORY})
        self.assertFalse(any(install.HOOKS_PATH_KEY in change for change in self._run_installer(apply=True)._changes))
        self.assertTrue(applied._changes)

    def test_local_line_survives_a_refresh(self):
        self._run_installer(apply=True)
        instruction = self._platform.claude_home / "CLAUDE.md"
        instruction.write_text(instruction.read_text(encoding="utf-8") + LOCAL_LINE + "\n", encoding="utf-8")
        self._run_installer(apply=True)

        self.assertTrue(instruction.read_text(encoding="utf-8").endswith(LOCAL_LINE + "\n"))

    def test_unrecorded_difference_stops_until_chosen(self):
        instruction = self._platform.codex_home / "AGENTS.md"
        instruction.parent.mkdir(parents=True)
        instruction.write_text(LOCAL_LINE + "\n", encoding="utf-8")
        stopped = self._run_installer(apply=True)
        replaced = self._run_installer(apply=True, first_merge=install.FIRST_MERGE_USE_REPOSITORY)

        self.assertTrue(stopped._conflicts)
        self.assertFalse(replaced._conflicts)
        self.assertEqual(
            instruction.read_text(encoding="utf-8"),
            (install.CODEX_SOURCE_DIRECTORY / "AGENTS.md").read_text(encoding="utf-8"),
        )

    def test_settings_keep_local_keys_and_get_machine_paths(self):
        settings = self._platform.claude_home / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"agentPushNotifEnabled": True}), encoding="utf-8")
        self._run_installer(apply=True)
        merged = json.loads(settings.read_text(encoding="utf-8"))

        self.assertTrue(merged["agentPushNotifEnabled"])
        for mod in install.find_mods(REPOSITORY_ROOT):
            expected = str(self._platform.claude_home / "mods" / mod.name)
            self.assertEqual(merged["extraKnownMarketplaces"][mod.marketplace]["source"]["path"], expected)

    def test_prune_moves_extra_rules_to_backups(self):
        extra = self._platform.claude_home / "rules" / "retired.md"
        extra.parent.mkdir(parents=True)
        extra.write_text("old rule\n", encoding="utf-8")
        kept = self._run_installer(apply=True)
        pruned = self._run_installer(apply=True, prune=True)

        self.assertTrue(any("retired.md" in line for line in kept._local_only))
        self.assertFalse(extra.exists())
        self.assertTrue(list((self._platform.claude_home / "backups").rglob("retired.md")))
        self.assertTrue(any("retired.md" in change for change in pruned._changes))

    def test_check_writes_nothing_and_lists_both_directions(self):
        self._run_installer(apply=True)
        skill = self._platform.claude_home / "skills" / "made-here" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("local skill\n", encoding="utf-8")
        settings = self._platform.claude_home / "settings.json"
        merged = json.loads(settings.read_text(encoding="utf-8"))
        merged["extraKnownMarketplaces"]["added-here"] = {"source": {"source": "github", "repo": "someone/market"}}
        merged["enabledPlugins"]["discord@claude-plugins-official"] = False
        settings.write_text(json.dumps(merged), encoding="utf-8")
        instruction = self._platform.claude_home / "CLAUDE.md"
        instruction.write_text(instruction.read_text(encoding="utf-8") + LOCAL_LINE + "\n", encoding="utf-8")
        before = settings.read_text(encoding="utf-8")
        calls_before = len(self._commands.calls)
        sections = self._check()
        local_only = "\n".join(sections[install.LOCAL_ONLY_SECTION])

        self.assertIn("made-here", local_only)
        self.assertIn("extraKnownMarketplaces.added-here", local_only)
        self.assertNotIn("discord", local_only)
        self.assertNotIn("local-mods", local_only)
        self.assertIn("CLAUDE.md: lines the repository does not have (1)", local_only)
        self.assertEqual(settings.read_text(encoding="utf-8"), before)
        self.assertFalse(any(call[0] in ("claude", "codex") for call in self._commands.calls[calls_before:]))

    def test_check_after_install_finds_nothing(self):
        self._run_installer(apply=True)
        sections = self._check()

        self.assertEqual(sections, {install.NOT_INSTALLED_SECTION: [], install.LOCAL_ONLY_SECTION: []})

    def test_install_records_the_checkout(self):
        self._run_installer(apply=True)
        record = self._platform.claude_home / install.CHECKOUT_RECORD

        self.assertEqual(record.read_text(encoding="utf-8").strip(), str(REPOSITORY_ROOT))

    def test_install_from_another_copy_keeps_the_recorded_checkout(self):
        checkout = make_checkout(Path(self._directory.name), "checkout")
        record = write_checkout_record(self._platform.claude_home, checkout)
        installer = self._run_installer(apply=True)

        self.assertEqual(record.read_text(encoding="utf-8"), f"{checkout}\n")
        self.assertIn(["git", "-C", str(checkout), "config", "core.hooksPath", ".githooks"], self._commands.calls)
        self.assertTrue(any("Kept the recorded checkout" in note for note in installer._notes))
        self.assertEqual(self._check(), {install.NOT_INSTALLED_SECTION: [], install.LOCAL_ONLY_SECTION: []})

    def test_dry_run_from_another_copy_lists_no_record_change(self):
        checkout = make_checkout(Path(self._directory.name), "checkout")
        record = write_checkout_record(self._platform.claude_home, checkout)
        installer = self._run_installer(apply=False)

        self.assertFalse(any(str(record) in change for change in installer._changes))

    def test_install_replaces_a_recorded_checkout_that_is_gone(self):
        record = write_checkout_record(self._platform.claude_home, Path(self._directory.name) / "moved-away")

        self._run_installer(apply=True)
        self.assertEqual(record.read_text(encoding="utf-8"), f"{REPOSITORY_ROOT}\n")

    def test_named_checkout_replaces_the_record(self):
        record = write_checkout_record(self._platform.claude_home, make_checkout(Path(self._directory.name), "old"))
        named = make_checkout(Path(self._directory.name), "new")
        installer = self._run_installer(apply=True, checkout=named)

        self.assertEqual(record.read_text(encoding="utf-8"), f"{named}\n")
        self.assertFalse(any("Kept the recorded checkout" in note for note in installer._notes))

    def test_check_reports_codex_skills_but_not_the_app_skills(self):
        self._run_installer(apply=True)
        for name in ("made-here", ".system"):
            (self._platform.codex_home / "skills" / name).mkdir(parents=True)
        local_only = "\n".join(self._check()[install.LOCAL_ONLY_SECTION])

        self.assertIn("made-here", local_only)
        self.assertNotIn(".system", local_only)

    def test_check_reports_a_codex_config_it_cannot_change(self):
        self._run_installer(apply=True)
        config = self._platform.codex_home / "config.toml"
        config.write_text('[tui]\nstatus_line = [\n  "a",\n]\n', encoding="utf-8")
        not_installed = "\n".join(self._check()[install.NOT_INSTALLED_SECTION])

        self.assertIn("spans several lines", not_installed)

    def test_repeated_local_line_is_counted(self):
        self._run_installer(apply=True)
        instruction = self._platform.claude_home / "CLAUDE.md"
        first_line = next(line for line in instruction.read_text(encoding="utf-8").splitlines() if line.strip())
        instruction.write_text(instruction.read_text(encoding="utf-8") + first_line + "\n", encoding="utf-8")
        local_only = "\n".join(self._check()[install.LOCAL_ONLY_SECTION])

        self.assertIn("CLAUDE.md: lines the repository does not have (1)", local_only)

    def test_codex_rules_keep_local_lines(self):
        self._run_installer(apply=True)
        rules = self._platform.codex_home / "rules" / "default.rules"
        rules.write_text(rules.read_text(encoding="utf-8") + "local rule\n", encoding="utf-8")
        self._run_installer(apply=True)

        self.assertTrue(rules.read_text(encoding="utf-8").endswith("local rule\n"))
        self.assertIn(
            "default.rules: lines the repository does not have (1)",
            "\n".join(self._check()[install.LOCAL_ONLY_SECTION]),
        )

    def test_vault_folder_setting_survives_and_is_not_reported(self):
        write_settings(self._platform, {"env": {VAULT_VARIABLE: FIRST_VAULT_FOLDER}})
        self._run_installer(apply=True)
        local_only = "\n".join(self._check()[install.LOCAL_ONLY_SECTION])

        self.assertEqual(read_settings(self._platform)["env"][VAULT_VARIABLE], FIRST_VAULT_FOLDER)
        self.assertNotIn(VAULT_VARIABLE, local_only)

    def test_install_without_a_vault_folder_setting_adds_none(self):
        self._run_installer(apply=True)

        self.assertNotIn(VAULT_VARIABLE, read_settings(self._platform)["env"])
        self.assertEqual(self._check(), {install.NOT_INSTALLED_SECTION: [], install.LOCAL_ONLY_SECTION: []})

    def test_two_computers_keep_their_own_vault_folders(self):
        repository = json.loads((REPOSITORY_ROOT / "settings.json").read_text(encoding="utf-8"))
        other = make_test_platform(Path(self._directory.name) / "other")
        folders = {self._platform: FIRST_VAULT_FOLDER, other: SECOND_VAULT_FOLDER}
        options = install.Options(apply=True, prune=False, allow_dirty=True, first_merge="")

        for platform, folder in folders.items():
            write_settings(platform, {"env": {VAULT_VARIABLE: folder}})
            run_installer_on(platform, StubCommands(platform.codex_home / "config.toml"), options)
        self._run_installer(apply=True)
        for platform, folder in folders.items():
            self.assertEqual(read_settings(platform)["env"][VAULT_VARIABLE], folder)
        self.assertFalse(install.list_setting_names(repository) & set(install.PER_COMPUTER_SETTINGS))

    def test_scheduled_tasks_are_left_alone(self):
        task = self._platform.claude_home / SCHEDULED_TASKS_DIRECTORY / "weekly-review" / "SKILL.md"
        task.parent.mkdir(parents=True)
        task.write_text(LOCAL_LINE + "\n", encoding="utf-8")
        installer = self._run_installer(apply=True, prune=True)
        local_only = "\n".join(self._check()[install.LOCAL_ONLY_SECTION])

        self.assertEqual(task.read_text(encoding="utf-8"), LOCAL_LINE + "\n")
        self.assertFalse(any(SCHEDULED_TASKS_DIRECTORY in change for change in installer._changes))
        self.assertNotIn(SCHEDULED_TASKS_DIRECTORY, local_only)

    def _check(self) -> dict:
        options = install.Options(apply=False, prune=False, allow_dirty=True, first_merge="", check=True)
        installer = install.Installer(self._platform, options, self._commands.run, find_any_command)

        return installer.check()

    def _run_installer(
        self,
        apply: bool,
        prune: bool = False,
        first_merge: str = "",
        checkout: Path | None = None,
    ) -> "install.Installer":
        options = install.Options(
            apply=apply,
            prune=prune,
            allow_dirty=True,
            first_merge=first_merge,
            checkout=checkout,
        )

        return run_installer_on(self._platform, self._commands, options)


class ParseOptionsTest(unittest.TestCase):
    def test_from_upstream_keeps_the_install_options(self):
        options = install.parse_options(["--from-upstream", "--apply", "--prune"])

        self.assertTrue(options.from_upstream)
        self.assertTrue(options.apply)
        self.assertTrue(options.prune)

    def test_checkout_is_resolved(self):
        options = install.parse_options(["--checkout", str(REPOSITORY_ROOT / "scripts" / "..")])

        self.assertEqual(options.checkout, REPOSITORY_ROOT)

    def test_checkout_needs_the_install_script(self):
        with tempfile.TemporaryDirectory() as directory:
            self._assert_refused(["--checkout", directory])

    def test_check_takes_neither_from_upstream_nor_checkout(self):
        self._assert_refused(["--check", "--from-upstream"])
        self._assert_refused(["--check", "--checkout", str(REPOSITORY_ROOT)])

    def _assert_refused(self, arguments: list[str]) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            install.parse_options(arguments)


class ResolveCheckoutTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def test_named_checkout_comes_first(self):
        named = self._root / "named"
        recorded = make_checkout(self._root, "recorded")

        self.assertEqual(install.resolve_checkout(named, recorded, self._root), named)

    def test_recorded_checkout_stays_while_it_has_the_install_script(self):
        recorded = make_checkout(self._root, "recorded")

        self.assertEqual(install.resolve_checkout(None, recorded, self._root), recorded)

    def test_this_checkout_is_used_without_a_usable_record(self):
        self.assertEqual(install.resolve_checkout(None, self._root / "gone", self._root), self._root)
        self.assertEqual(install.resolve_checkout(None, None, self._root), self._root)


class FromUpstreamTest(unittest.TestCase):
    """Runs real git in temporary repositories, with this computer's git settings left out."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self._root = Path(self._directory.name)
        self._platform = install.Platform(
            system_name="Darwin",
            home=self._root,
            claude_home=self._root / ".claude",
            codex_home=self._root / ".codex",
            application_paths=(),
            hook_commands=("python3",),
            links_ego_skill=False,
        )
        self._options = install.Options(apply=True, prune=False, allow_dirty=False, first_merge="", from_upstream=True)
        self._executed: list[list[str]] = []
        self._copies: list[Path] = []
        self._copy_files: list[list[str]] = []
        self._copy_contents: list[str] = []
        self._output = io.StringIO()
        git_settings = {"GIT_CONFIG_GLOBAL": str(self._root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        environment = mock.patch.dict(os.environ, git_settings)

        environment.start()
        self.addCleanup(environment.stop)

    def tearDown(self):
        self._directory.cleanup()

    def test_installs_the_pushed_commit_and_leaves_out_the_work_here(self):
        checkout = self._make_checkout_behind_its_upstream()
        code = self._install_from_upstream(checkout)

        self.assertEqual(code, 7)
        self.assertEqual(self._copy_contents, ["second\n"])
        self.assertEqual(self._copy_files, [["pushed.txt", "scripts"]])
        self.assertFalse(self._copies[0].exists())
        self.assertEqual(self._executed[0][:2], [sys.executable, "-I"])
        self.assertEqual(self._executed[0][3:], ["--checkout", str(checkout), "--apply"])

    def test_the_copy_keeps_the_recorded_checkout(self):
        recorded = make_checkout(self._root, "recorded")

        write_checkout_record(self._platform.claude_home, recorded)
        self._install_from_upstream(self._make_checkout_behind_its_upstream())
        self.assertEqual(self._executed[0][3:5], ["--checkout", str(recorded)])

    def test_a_failed_fetch_installs_the_upstream_as_fetched_last(self):
        checkout = self._make_checkout_behind_its_upstream()

        self._git(checkout, "remote", "set-url", "origin", str(self._root / "missing.git"))
        code = self._install_from_upstream(checkout)

        self.assertEqual(code, 7)
        self.assertIn("Could not fetch", self._output.getvalue())
        self.assertEqual(self._copy_contents, ["second\n"])

    def test_a_checkout_without_upstream_stops(self):
        checkout = self._root / "alone"

        self._git(self._root, "init", "-q", "-b", "main", str(checkout))
        with self.assertRaises(install.InstallError) as raised:
            self._install_from_upstream(checkout)
        self.assertEqual(raised.exception.exit_code, install.EXIT_COMMAND_FAILED)
        self.assertEqual(self._executed, [])

    def test_a_failed_clone_leaves_no_copy(self):
        calls: list[list[str]] = []

        def run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
            is_clone = list(arguments[:2]) == ["git", "clone"]

            calls.append(list(arguments))
            return subprocess.CompletedProcess(arguments, 128 if is_clone else 0, stdout="origin/main\n", stderr="")

        with redirect_stdout(io.StringIO()), self.assertRaises(install.InstallError):
            install.run_from_upstream(self._root, self._platform, self._options, run, self._execute)
        self.assertFalse(Path(calls[-1][-1]).parent.exists())
        self.assertEqual(self._executed, [])

    def test_copy_arguments_carry_the_install_options(self):
        every_option = install.Options(apply=True, prune=True, allow_dirty=True, first_merge="keep-local")
        dry_run = install.Options(apply=False, prune=False, allow_dirty=False, first_merge="")
        checkout = Path("/checkout")

        self.assertEqual(
            install.make_copy_arguments(every_option, checkout),
            ["--checkout", str(checkout), "--apply", "--prune", "--first-merge", "keep-local"],
        )
        self.assertEqual(install.make_copy_arguments(dry_run, checkout), ["--checkout", str(checkout)])

    def _install_from_upstream(self, checkout: Path) -> int:
        with redirect_stdout(self._output):
            return install.run_from_upstream(
                checkout,
                self._platform,
                self._options,
                install.run_command,
                self._execute,
            )

    def _execute(self, arguments: Sequence[str]) -> int:
        copy = Path(arguments[2]).parent.parent

        self._executed.append(list(arguments))
        self._copies.append(copy)
        self._copy_files.append(sorted(path.name for path in copy.iterdir() if path.name != ".git"))
        self._copy_contents.append((copy / "pushed.txt").read_text(encoding="utf-8"))
        return 7

    def _make_checkout_behind_its_upstream(self) -> Path:
        """A checkout one commit behind its upstream, with an unpushed commit and an uncommitted file of its own."""
        remote = self._root / "remote.git"
        other = self._root / "other"
        checkout = self._root / "checkout"

        self._git(self._root, "init", "-q", "--bare", "-b", "main", str(remote))
        self._git(self._root, "clone", "-q", str(remote), str(other))
        make_checkout(self._root, other.name)
        self._commit(other, "first")
        self._git(other, "push", "-q", "origin", "main")
        self._git(self._root, "clone", "-q", str(remote), str(checkout))
        self._commit(other, "second")
        self._git(other, "push", "-q", "origin", "main")
        self._commit(checkout, "unpushed")
        self._git(checkout, "fetch", "-q")
        (checkout / "uncommitted.txt").write_text("not committed\n", encoding="utf-8")
        return checkout

    def _commit(self, repository: Path, text: str) -> None:
        (repository / "pushed.txt").write_text(f"{text}\n", encoding="utf-8")
        self._git(repository, "add", ".")
        self._git(repository, "commit", "-q", "-m", text)

    def _git(self, directory: Path, *arguments: str) -> None:
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]

        subprocess.run(["git", *identity, "-C", str(directory), *arguments], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1] + sys.argv[1:])
