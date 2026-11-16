"""Tests for session-writes-hook.py --around, which records a project formatter's rewrite as the session's write.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from test_shell_guard import (
    FOREIGN_LINES_EXIT,
    HEAD_COMMIT_MESSAGE,
    HOOK_REWRITTEN_CALL,
    HOOKED_CALL,
    OTHER_SESSION,
    OWN_SESSION,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    RECORD_HOOK,
    SessionWritesFixture,
    make_edit_response,
)

MODULE_FILE = "orders.py"
MODULE_BASE = "def send():\n    pass\n"
FORMATTED_MODULE = MODULE_BASE + HOOK_REWRITTEN_CALL
PERSON_LINE = "# a person's note\n"
HOOK_OUTPUT = "formatter says hello"
HOOK_ERROR = "formatter could not parse the file"
FEEDBACK_EXIT = 2
USAGE_EXIT = 1
AROUND_OPTION = "--around"
RECORD_WAIT_SECONDS = 0.5
PAYLOAD_FILE = "payload.json"
FORMATTER_SOURCE = """\
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
file = Path(payload["tool_input"]["file_path"])
file.write_text(file.read_text().replace({hooked!r}, {rewritten!r}))
print({output!r})
print({error!r}, file=sys.stderr)
sys.exit(int(sys.argv[1]))
"""


class AroundHookTest(unittest.TestCase):
    fixture: SessionWritesFixture
    formatter: Path

    def setUp(self) -> None:
        self.fixture = SessionWritesFixture()
        self.fixture.create()
        self.addCleanup(self.fixture.directory.cleanup)
        (self.fixture.repository / MODULE_FILE).write_text(MODULE_BASE)
        self.fixture.run_git("add", MODULE_FILE)
        self.fixture.run_git("commit", "-q", "-m", "Chore: seed module")
        self.formatter = self.fixture.root / "format.py"
        self.formatter.write_text(
            FORMATTER_SOURCE.format(
                hooked=HOOKED_CALL,
                rewritten=HOOK_REWRITTEN_CALL,
                output=HOOK_OUTPUT,
                error=HOOK_ERROR,
            )
        )

    def test_formatter_rewrite_of_my_edit_is_committed_as_mine(self) -> None:
        """The formatter's lines go into the commit with no dialog, as the session's own."""
        self.fixture.edit(OWN_SESSION, MODULE_FILE, MODULE_BASE, MODULE_BASE + HOOKED_CALL)
        self._run_around(self._make_post_payload(OWN_SESSION))
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, MODULE_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(MODULE_FILE).decode(), FORMATTED_MODULE)

    def test_formatter_waits_for_the_call_record(self) -> None:
        """A formatter started before the call's after snapshot still leaves the Edit exact and the chain unbroken."""
        edit_input, response = self._start_edit(OWN_SESSION)
        payload = self._make_post_payload(OWN_SESSION)
        around = self._start_around(payload)

        time.sleep(RECORD_WAIT_SECONDS)
        self.assertEqual((self.fixture.repository / MODULE_FILE).read_text(), MODULE_BASE + HOOKED_CALL)
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE, self._current_tool_use(), edit_input, response)
        _, errors = around.communicate(timeout=30)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, MODULE_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + errors)
        self.assertEqual(self.fixture.read_committed_bytes(MODULE_FILE).decode(), FORMATTED_MODULE)

    def test_person_edit_after_the_formatter_is_asked(self) -> None:
        self.fixture.edit(OWN_SESSION, MODULE_FILE, MODULE_BASE, MODULE_BASE + HOOKED_CALL)
        self._run_around(self._make_post_payload(OWN_SESSION))
        self.fixture.append(MODULE_FILE, PERSON_LINE)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, MODULE_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn(PERSON_LINE.strip(), result.stderr)

    def test_formatter_beside_another_session_command_is_asked(self) -> None:
        """Another session's call running while the formatter ran may have written its change."""
        head = self.fixture.run_git("rev-parse", "HEAD")

        self.fixture.edit(OWN_SESSION, MODULE_FILE, MODULE_BASE, MODULE_BASE + HOOKED_CALL)
        started = self.fixture.start_command(OTHER_SESSION)
        self._run_around(self._make_post_payload(OWN_SESSION))
        self.fixture.finish_command(started)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, MODULE_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("another session's command was running", result.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)

    def test_project_hook_output_and_exit_code_pass_through(self) -> None:
        self.fixture.edit(OWN_SESSION, MODULE_FILE, MODULE_BASE, MODULE_BASE + HOOKED_CALL)
        result = self._run_around(self._make_post_payload(OWN_SESSION), FEEDBACK_EXIT)

        self.assertEqual(result.returncode, FEEDBACK_EXIT)
        self.assertEqual(result.stdout.strip(), HOOK_OUTPUT)
        self.assertIn(HOOK_ERROR, result.stderr)

    def test_project_hook_runs_for_a_payload_without_a_session(self) -> None:
        """A payload the record cannot use still reaches the project hook, which formats the file."""
        self.fixture.edit(OWN_SESSION, MODULE_FILE, MODULE_BASE, MODULE_BASE + HOOKED_CALL)
        payload = self._make_post_payload(OWN_SESSION)

        del payload["session_id"]
        result = self._run_around(payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.fixture.repository / MODULE_FILE).read_text(), FORMATTED_MODULE)

    def test_around_without_a_command_prints_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RECORD_HOOK), AROUND_OPTION],
            input="{}",
            env=self.fixture.environment(None),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, USAGE_EXIT)
        self.assertIn("usage:", result.stderr)

    def _start_edit(self, session: str) -> tuple[dict[str, str], dict[str, object]]:
        """Make an Edit whose PostToolUse record has not run yet; return its input and response."""
        self.fixture.tool_uses += 1
        file = self.fixture.repository / MODULE_FILE
        edit_input = {"file_path": str(file)}
        edited = MODULE_BASE + HOOKED_CALL

        self.fixture.run_record_hook(session, PRE_TOOL_USE, self._current_tool_use(), edit_input)
        file.write_text(edited)
        return edit_input, make_edit_response(MODULE_BASE, edited, MODULE_BASE, edited, False)

    def _current_tool_use(self) -> str:
        return f"call-{self.fixture.tool_uses}"

    def _make_post_payload(self, session: str) -> dict[str, object]:
        edit_input = {"file_path": str(self.fixture.repository / MODULE_FILE)}

        return self.fixture.make_hook_payload(session, POST_TOOL_USE, self._current_tool_use(), edit_input, {})

    def _make_around_command(self, exit_code: int) -> list[str]:
        return [sys.executable, str(RECORD_HOOK), AROUND_OPTION, sys.executable, str(self.formatter), str(exit_code)]

    def _run_around(self, payload: dict[str, object], exit_code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._make_around_command(exit_code),
            input=json.dumps(payload),
            env=self.fixture.environment(None),
            capture_output=True,
            text=True,
            check=False,
        )

    def _start_around(self, payload: dict[str, object]) -> subprocess.Popen[str]:
        """Start the hook in the background, reading its payload from a file as Claude Code pipes it in."""
        payload_file = self.fixture.root / PAYLOAD_FILE

        payload_file.write_text(json.dumps(payload))
        with payload_file.open() as stdin:
            return subprocess.Popen(
                self._make_around_command(0),
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.fixture.environment(None),
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
