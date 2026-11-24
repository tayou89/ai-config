#!/usr/bin/env python3
"""Tests for scripts/session-notes-hook.py.

Each test runs the hook as Claude Code would, with the hook input on stdin and
the home folder pointed at a temporary folder, so nothing on this computer is
read or written.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
HOOK_SCRIPT = SCRIPTS_DIRECTORY / "session-notes-hook.py"
SETTINGS_FILE = REPOSITORY_ROOT / "settings.json"
APP_NOTES_PATH = Path("Library") / "Application Support" / "Claude" / "side-session-notes"
BACKUP_PATH = Path("Documents") / "session-notes-backup"
COPY_PATH = BACKUP_PATH / "app-notes"
PARTIAL_PATH = BACKUP_PATH / ".app-notes-partial"
LOCK_PATH = BACKUP_PATH / ".app-notes.lock"
STATUS_PATH = BACKUP_PATH / "app-notes-status.json"
NOTES_PATH = Path("Documents") / "session-notes"
ARCHIVE_TOOL = "mcp__ccd_session_mgmt__archive_session"
CLOSING_TOOLS = (ARCHIVE_TOOL, "mcp__ccd_session_mgmt__delete_session", "mcp__ccd_session__move_to_cloud")
HOUR_SECONDS = 60 * 60


def load_hook() -> ModuleType:
    """Load the hook by path, since its file name is not importable; it imports report_check from its own folder."""
    sys.path.insert(0, str(SCRIPTS_DIRECTORY))
    specification = importlib.util.spec_from_file_location("session_notes_hook", HOOK_SCRIPT)

    if specification and specification.loader:
        module = importlib.util.module_from_spec(specification)

        sys.modules[specification.name] = module
        specification.loader.exec_module(module)
        return module
    else:
        raise ImportError(f"cannot load {HOOK_SCRIPT}")


class SessionNotesHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._home = Path(self._directory.name).resolve()
        self._source = self._home / APP_NOTES_PATH
        self._target = self._home / COPY_PATH

    def tearDown(self) -> None:
        for path in [self._home, *self._home.rglob("*")]:
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        self._directory.cleanup()

    def test_a_missing_app_notes_folder_does_nothing_and_makes_the_notes_folder(self) -> None:
        output = self._run_stop()

        self.assertEqual(output, "")
        self.assertFalse(self._target.exists())
        self.assertTrue((self._home / NOTES_PATH).is_dir())

    def test_new_files_are_copied_with_their_modification_time(self) -> None:
        report = self._write_note("local_1/report.md", "보고서")
        old_time = time.time() - 3 * HOUR_SECONDS

        os.utime(report, (old_time, old_time))
        self._write_note("bulletin.md", "알림판")
        self._run_stop()
        copied = self._target / "local_1" / "report.md"

        self.assertEqual(copied.read_text(encoding="utf-8"), "보고서")
        self.assertEqual(copied.stat().st_mtime_ns, report.stat().st_mtime_ns)
        self.assertTrue((self._target / "bulletin.md").is_file())

    def test_changed_files_are_replaced_and_unchanged_files_are_left_alone(self) -> None:
        report = self._write_note("local_1/report.md", "첫 판")
        index = self._write_note("local_1/INDEX.md", "목록")

        self._run_stop()
        copied_index = self._target / "local_1" / "INDEX.md"
        copied_index_inode = copied_index.stat().st_ino

        report.write_text("둘째 판이 더 깁니다", encoding="utf-8")
        self._run_stop()
        self.assertEqual((self._target / "local_1" / "report.md").read_text(encoding="utf-8"), "둘째 판이 더 깁니다")
        self.assertEqual(copied_index.stat().st_ino, copied_index_inode)
        self.assertEqual(copied_index.read_text(encoding="utf-8"), index.read_text(encoding="utf-8"))

    def test_files_removed_from_the_app_folder_stay_in_the_copy(self) -> None:
        report = self._write_note("local_1/report.md", "보고서")

        self._run_stop()
        report.unlink()
        report.parent.rmdir()
        self._run_stop()
        self.assertTrue((self._target / "local_1" / "report.md").is_file())

    def test_symbolic_links_and_finder_files_are_skipped(self) -> None:
        outside = self._home / "outside.md"

        outside.write_text("밖", encoding="utf-8")
        self._write_note("local_1/.DS_Store", "finder")
        (self._source / "local_1" / "link.md").symlink_to(outside)
        self._run_stop()
        self.assertFalse((self._target / "local_1" / ".DS_Store").exists())
        self.assertFalse((self._target / "local_1" / "link.md").exists())

    def test_closing_tools_copy_first_and_allow_the_call(self) -> None:
        for tool in CLOSING_TOOLS:
            with self.subTest(tool=tool):
                self._write_note(f"local_{tool[-5:]}/report.md", "보고서")
                output = self._run_closing(tool)

                self.assertEqual(output, "")
                self.assertTrue((self._target / f"local_{tool[-5:]}" / "report.md").is_file())

    def test_an_unreadable_file_denies_every_closing_tool_but_not_the_turn_end(self) -> None:
        self._skip_as_root()
        self._write_note("local_1/report.md", "보고서")
        self._write_note("local_1/locked.md", "잠김").chmod(0o000)
        for tool in CLOSING_TOOLS:
            with self.subTest(tool=tool):
                decision = json.loads(self._run_closing(tool))["hookSpecificOutput"]

                self.assertEqual(decision["permissionDecision"], "deny")
                self.assertIn("locked.md", decision["permissionDecisionReason"])
        self.assertTrue((self._target / "local_1" / "report.md").is_file())
        self.assertEqual(self._run_stop(), "")
        self.assertEqual(list(self._target.rglob("*.partial")), [])

    def test_many_failures_are_shown_as_a_few_and_a_count(self) -> None:
        self._skip_as_root()
        for number in range(5):
            self._write_note(f"local_1/locked-{number}.md", "잠김").chmod(0o000)
        reason = json.loads(self._run_closing(ARCHIVE_TOOL))["hookSpecificOutput"]["permissionDecisionReason"]

        self.assertIn("and 2 more", reason)

    def test_an_unreadable_folder_denies_closing(self) -> None:
        self._skip_as_root()
        self._write_note("local_1/report.md", "보고서")
        (self._source / "local_1").chmod(0o000)
        decision = json.loads(self._run_closing(ARCHIVE_TOOL))["hookSpecificOutput"]

        self.assertEqual(decision["permissionDecision"], "deny")

    def test_an_unexpected_error_denies_closing_and_only_logs_at_the_turn_end(self) -> None:
        self._skip_as_root()
        self._write_note("local_1/report.md", "보고서")
        self._source.parent.chmod(0o000)
        decision = json.loads(self._run_closing(ARCHIVE_TOOL))["hookSpecificOutput"]
        stop = self._run(json.dumps({"hook_event_name": "Stop"}))

        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("PermissionError", decision["permissionDecisionReason"])
        self.assertEqual((stop.returncode, stop.stdout), (0, ""))
        self.assertIn("session-notes-hook:", stop.stderr)

    def test_a_copy_that_cannot_write_denies_closing(self) -> None:
        self._write_note("local_1/report.md", "보고서")
        self._target.parent.mkdir(parents=True)
        self._target.write_text("자리를 막는 파일", encoding="utf-8")
        decision = json.loads(self._run_closing(ARCHIVE_TOOL))["hookSpecificOutput"]

        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertEqual(self._run_stop(), "")

    def test_a_notes_folder_that_cannot_be_made_does_not_stop_the_copy(self) -> None:
        self._write_note("local_1/report.md", "보고서")
        (self._home / NOTES_PATH).parent.mkdir(parents=True, exist_ok=True)
        (self._home / NOTES_PATH).write_text("폴더 자리를 막는 파일", encoding="utf-8")
        output = self._run_closing(ARCHIVE_TOOL)

        self.assertEqual(output, "")
        self.assertTrue((self._target / "local_1" / "report.md").is_file())

    def test_leftover_temporary_files_are_cleared_and_notes_named_like_them_are_kept(self) -> None:
        note_named_partial = self._write_note("local_1/.draft.partial", "진짜 노트")
        old_time = time.time() - 3 * HOUR_SECONDS
        leftover = self._home / PARTIAL_PATH / "999-0"

        os.utime(note_named_partial, (old_time, old_time))
        leftover.parent.mkdir(parents=True)
        leftover.write_text("끊긴 실행의 조각", encoding="utf-8")
        self._run_stop()
        self._run_stop()
        self.assertFalse(leftover.exists())
        self.assertEqual((self._target / "local_1" / ".draft.partial").read_text(encoding="utf-8"), "진짜 노트")

    def test_each_run_records_its_time_and_failures(self) -> None:
        self._write_note("local_1/report.md", "보고서")
        self._run_stop()
        first = json.loads((self._home / STATUS_PATH).read_text(encoding="utf-8"))

        self.assertEqual(first["failure_count"], 0)
        self.assertIn("last_run", first)
        self._skip_as_root()
        self._write_note("local_1/locked.md", "잠김").chmod(0o000)
        self._run_stop()
        second = json.loads((self._home / STATUS_PATH).read_text(encoding="utf-8"))

        self.assertEqual(second["failure_count"], 1)
        self.assertIn("locked.md", second["failures"][0])

    def test_a_copy_waits_for_another_copy_to_finish(self) -> None:
        self._write_note("local_1/report.md", "보고서")
        lock_path = self._home / LOCK_PATH

        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            process = subprocess.Popen(
                [sys.executable, str(HOOK_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                env={**os.environ, "HOME": str(self._home)},
            )
            time.sleep(1)
            is_copied_while_locked = (self._target / "local_1" / "report.md").exists()
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        output, _ = process.communicate(json.dumps({"hook_event_name": "PreToolUse", "tool_name": ARCHIVE_TOOL}))

        self.assertFalse(is_copied_while_locked)
        self.assertEqual(output, "")
        self.assertTrue((self._target / "local_1" / "report.md").is_file())

    def test_other_tools_and_broken_input_never_block(self) -> None:
        self._write_note("local_1/report.md", "보고서")
        inputs = (
            "",
            "not json",
            "[1, 2]",
            json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash"}),
            json.dumps({"hook_event_name": "PreToolUse", "tool_name": [ARCHIVE_TOOL]}),
        )

        for stdin in inputs:
            with self.subTest(stdin=stdin):
                result = self._run(stdin)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_copy_now_reports_success_and_failure(self) -> None:
        self._write_note("local_1/report.md", "보고서")
        done = self._run("", arguments=["--now"])

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Copied", done.stdout)
        self.assertTrue((self._target / "local_1" / "report.md").is_file())
        self._skip_as_root()
        self._write_note("local_1/locked.md", "잠김").chmod(0o000)
        failed = self._run("", arguments=["--now"])

        self.assertEqual(failed.returncode, 1)
        self.assertIn("locked.md", failed.stdout)

    def test_settings_send_the_closing_tools_and_turn_ends_to_the_hook(self) -> None:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        hooks = settings["hooks"]
        command_name = HOOK_SCRIPT.name
        matchers = [
            entry["matcher"]
            for entry in hooks["PreToolUse"]
            if any(command_name in hook["command"] for hook in entry["hooks"])
        ]
        stop_commands = [hook["command"] for entry in hooks["Stop"] for hook in entry["hooks"]]

        self.assertEqual([set(matcher.split("|")) for matcher in matchers], [set(load_hook().CLOSING_TOOLS)])
        self.assertTrue(any(command_name in command for command in stop_commands))
        self.assertIn("~/Documents/session-notes", settings["permissions"]["additionalDirectories"])

    def _skip_as_root(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("permissions do not stop the root user")

    def _write_note(self, relative: str, text: str) -> Path:
        path = self._source / relative

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _run_stop(self) -> str:
        return self._run_hook({"hook_event_name": "Stop", "session_id": "session-a"})

    def _run_closing(self, tool: str) -> str:
        return self._run_hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {}})

    def _run_hook(self, hook_input: dict[str, object]) -> str:
        result = self._run(json.dumps(hook_input))

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _run(self, stdin: str, arguments: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "HOME": str(self._home)}

        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT), *(arguments or [])],
            input=stdin,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
