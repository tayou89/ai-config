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
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import List, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPOSITORY_ROOT / "scripts" / "install.py"
LOCAL_LINE = "- A line only this computer has."
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
            install.set_toml_top_level("", {"model": ["list"]})


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


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        home = Path(self._directory.name)

        self._platform = install.Platform(
            system_name="Darwin",
            home=home,
            claude_home=home / ".claude",
            codex_home=home / ".codex",
            application_paths=(),
            hook_commands=("python3",),
            links_ego_skill=True,
        )

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

        self.assertTrue(any("retired.md" in note for note in kept._notes))
        self.assertFalse(extra.exists())
        self.assertTrue(list((self._platform.claude_home / "backups").rglob("retired.md")))
        self.assertTrue(any("retired.md" in change for change in pruned._changes))

    def _run_installer(self, apply: bool, prune: bool = False, first_merge: str = "") -> "install.Installer":
        options = install.Options(apply=apply, prune=prune, allow_dirty=True, first_merge=first_merge)
        commands = StubCommands(self._platform.codex_home / "config.toml")
        installer = install.Installer(self._platform, options, commands.run, find_any_command)

        with redirect_stdout(io.StringIO()):
            installer.install()
        return installer


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1] + sys.argv[1:])
