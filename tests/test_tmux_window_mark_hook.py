#!/usr/bin/env python3
"""Tests for scripts/tmux-window-mark-hook.py and its settings entry.

Each test runs the hook as Claude Code would, with the Notification input on
stdin and the tmux variable set or removed, so no tmux server is needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPOSITORY_ROOT / "scripts" / "tmux-window-mark-hook.py"
SETTINGS = REPOSITORY_ROOT / "settings.json"
HOOK_COMMAND = 'python3 "$HOME/.claude/scripts/tmux-window-mark-hook.py"'
TMUX_VALUE = "/private/tmp/tmux-501/default,1,0"
WAITING_TYPES = {
    "permission_prompt",
    "idle_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
    "quota_auto_resume_stale",
    "quota_auto_resume_disabled",
}


def make_input(notification_type: str = "permission_prompt") -> str:
    hook_input = {
        "hook_event_name": "Notification",
        "notification_type": notification_type,
        "message": "Claude needs your permission to use Bash",
    }

    return json.dumps(hook_input)


class TmuxWindowMarkHookTest(unittest.TestCase):
    def test_inside_tmux_returns_a_bell(self) -> None:
        result = self._run_hook(make_input(), inside_tmux=True)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"terminalSequence": "\a"})

    def test_outside_tmux_answers_nothing(self) -> None:
        result = self._run_hook(make_input(), inside_tmux=False)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_unreadable_input_is_reported_not_hidden(self) -> None:
        result = self._run_hook("not json", inside_tmux=True)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("tmux-window-mark-hook", result.stderr)

    def test_input_without_event_is_reported(self) -> None:
        result = self._run_hook(json.dumps({"message": "x"}), inside_tmux=True)

        self.assertEqual(result.returncode, 1)
        self.assertIn("tmux-window-mark-hook", result.stderr)

    def test_settings_entry_lists_the_waiting_types_and_never_blocks(self) -> None:
        entries = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["Notification"]
        matching = [entry for entry in entries if any(hook["command"] == HOOK_COMMAND for hook in entry["hooks"])]

        self.assertEqual(len(matching), 1)
        self.assertEqual(set(matching[0]["matcher"].split("|")), WAITING_TYPES)
        for hook in matching[0]["hooks"]:
            self.assertNotIn("onFailure", hook)
            self.assertNotIn("async", hook)

    def _run_hook(self, stdin: str, inside_tmux: bool) -> subprocess.CompletedProcess[str]:
        environment = {key: value for key, value in os.environ.items() if key not in ("TMUX", "TMUX_PANE")}

        if inside_tmux:
            environment["TMUX"] = TMUX_VALUE
        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=stdin,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
