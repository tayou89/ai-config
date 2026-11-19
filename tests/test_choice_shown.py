"""Tests for scripts/choice-shown.py and the choice record in scripts/skill_gate_records.py."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
COMMAND_SCRIPT = SCRIPTS_DIRECTORY / "choice-shown.py"


def load_records() -> ModuleType:
    """Import the record module from the scripts folder, which the hooks run from."""
    sys.path.insert(0, str(SCRIPTS_DIRECTORY))
    return importlib.import_module("skill_gate_records")


skill_gate_records = load_records()
ChoiceRecord = skill_gate_records.ChoiceRecord
get_choice_record_path = skill_gate_records.get_choice_record_path
list_unshown_labels = skill_gate_records.list_unshown_labels
read_choice_records = skill_gate_records.read_choice_records
SESSION = "session-1"
QUESTION = "어느 전환으로 할까요?"
URL = "https://www.figma.com/design/abc?node-id=1-2"
NOT_FOUND = "is neither a file on this computer nor an http(s) link with a host"


class ChoiceShownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_directory = Path(self.directory.name) / "config"
        self.board = Path(self.directory.name) / "board.png"
        self.board.write_bytes(b"png")
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config_directory)})

        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_options_are_recorded_with_what_shows_them(self) -> None:
        result = self._run(
            "--question",
            f"  {QUESTION}\n",
            "--option",
            "원근  비행",
            str(self.board),
            "--option",
            "Width = height",
            URL,
        )
        records = read_choice_records(get_choice_record_path(SESSION))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(records, [ChoiceRecord(QUESTION, {"원근 비행": str(self.board), "Width = height": URL}, "")])

    def test_file_urls_and_relative_paths_count_as_files(self) -> None:
        file_url = "file://" + str(self.board).replace(" ", "%20")
        result = self._run("--question", QUESTION, "--option", "A", file_url, "--option", "B", "board.png")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            read_choice_records(get_choice_record_path(SESSION))[0].options,
            {"A": file_url, "B": str(self.board.resolve())},
        )

    def test_not_visual_reason_is_recorded(self) -> None:
        result = self._run("--question", QUESTION, "--not-visual", "검토 순서를 묻는 질문")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_choice_records(get_choice_record_path(SESSION))[0].not_visual, "검토 순서를 묻는 질문")

    def test_wrong_input_records_nothing(self) -> None:
        cases = [
            (("--question", QUESTION, "--option", "A", "/missing/board.png"), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", self.directory.name), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", "ftp://example.com/a"), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", "https://"), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", "https://user@"), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", "https://example.com:bad/path"), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", "https://["), NOT_FOUND),
            (("--question", QUESTION, "--option", "A", f"file://remote.invalid{self.board}"), NOT_FOUND),
            (("--question", QUESTION, "--option", " ", str(self.board)), "label is empty"),
            (("--question", QUESTION, "--not-visual", "  "), "why"),
            (("--question", " ", "--option", "A", str(self.board)), "question"),
        ]

        for arguments, message in cases:
            result = self._run(*arguments)

            self.assertEqual((result.returncode, "Traceback" in result.stderr), (1, False), arguments)
            self.assertIn(message, result.stderr, arguments)
        self.assertFalse(get_choice_record_path(SESSION).exists())

    def test_missing_session_records_nothing(self) -> None:
        result = self._run("--question", QUESTION, "--not-visual", "reason", session="")

        self.assertEqual(result.returncode, 1)
        self.assertIn("session", result.stderr)
        self.assertFalse(get_choice_record_path("_").exists())

    def test_options_and_reason_cannot_be_mixed_or_left_out(self) -> None:
        for arguments in (
            ("--question", QUESTION),
            ("--question", QUESTION, "--option", f"A={self.board}"),
            ("--question", QUESTION, "--option", "A", str(self.board), "--not-visual", "reason"),
        ):
            self.assertEqual(self._run(*arguments).returncode, 2, arguments)

    def test_unshown_labels_follow_the_records_of_that_question(self) -> None:
        records = [
            ChoiceRecord(QUESTION, {"A": "board.png"}, ""),
            ChoiceRecord(QUESTION, {"B  C": "board.png"}, ""),
            ChoiceRecord("다른 질문", {"D": "x.png"}, ""),
            ChoiceRecord("순서를 정할까요?", {}, "화면이 아님"),
        ]

        self.assertEqual(list_unshown_labels(records, f" {QUESTION} ", ["A", "B C", "D"]), ["D"])
        self.assertEqual(list_unshown_labels(records, "순서를  정할까요?", ["X", "Y"]), [])
        self.assertEqual(list_unshown_labels([], QUESTION, ["A", "B"]), ["A", "B"])

    def test_damaged_or_malformed_record_lines_are_skipped(self) -> None:
        path = get_choice_record_path(SESSION)
        malformed = [
            {"question": QUESTION, "options": []},
            {"question": QUESTION, "options": {"A": None, "B": ""}, "not_visual": ""},
            {"question": QUESTION, "options": {}, "not_visual": "   "},
            {"question": " ", "options": {}, "not_visual": "reason"},
            {"question": QUESTION, "options": {"A": "a.png"}, "not_visual": None},
        ]
        well_formed = {"question": QUESTION, "options": {"A": "a.png"}, "not_visual": ""}
        lines = ["not json", "[1]", "[" * 100_000, *map(json.dumps, malformed), json.dumps(well_formed)]

        path.parent.mkdir(parents=True)
        path.write_text("\n".join(lines) + "\n")
        self.assertEqual(read_choice_records(path), [ChoiceRecord(QUESTION, {"A": "a.png"}, "")])

    def test_settings_let_the_command_run_without_asking(self) -> None:
        settings = json.loads((REPOSITORY_ROOT / "settings.json").read_text())

        self.assertIn("Bash(python3 ~/.claude/scripts/choice-shown.py:*)", settings["permissions"]["allow"])

    def _run(self, *arguments: str, session: str = SESSION) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "CLAUDE_CODE_SESSION_ID": session}

        return subprocess.run(
            [sys.executable, str(COMMAND_SCRIPT), *arguments],
            capture_output=True,
            text=True,
            env=environment,
            cwd=self.directory.name,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
