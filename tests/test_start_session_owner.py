#!/usr/bin/env python3
"""Tests for the folder-owner part of scripts/start-session-hook.py.

A session started to share a folder works where its starter works. When
that folder is a task worktree, the new session can only read there unless
it owns it, so the starter is told so, and, for its own, how to hand it over.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from owner_world import OTHER, OWNER, PASS, OwnerWorld, Session
from workflow_world import ENCODING

CALM_HOOK_RUNNER = Path(__file__).resolve().parent / "calm_start_session_hook.py"
START = {
    "initiation": "user_asked",
    "title": "시험 고치기",
    "prompt": "brief",
    "model": "claude-opus-5-5",
    "effort": "high",
    "context": "fresh",
    "background": "x" * 90,
}


class StartSessionOwnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = OwnerWorld()
        self.addCleanup(self.world.close)
        self.worktree = self.world.add_worktree_as(OWNER, "task-a")

    def test_sharing_another_live_sessions_worktree_says_it_only_reads_there(self) -> None:
        decision, context = self.start(OTHER, self.worktree, {"use_worktree": False})

        self.assertEqual(decision, PASS)
        self.assertIn("only read", context)
        self.assertIn(OWNER.title, context)

    def test_naming_another_sessions_worktree_as_the_folder_says_so_too(self) -> None:
        decision, context = self.start(OTHER, self.world.root, {"cwd": str(self.worktree)})

        self.assertEqual(decision, PASS)
        self.assertIn("only read", context)

    def test_sharing_the_starters_own_worktree_says_how_to_hand_it_over(self) -> None:
        decision, context = self.start(OWNER, self.worktree, {"use_worktree": False})

        self.assertEqual(decision, PASS)
        self.assertIn("hand-over --to", context)

    def test_a_worktree_of_its_own_needs_no_check(self) -> None:
        decision, context = self.start(OTHER, self.worktree, {"use_worktree": True})

        self.assertEqual(decision, PASS)
        self.assertNotIn("hand-over", context)

    def test_a_folder_whose_owner_left_may_be_shared(self) -> None:
        self.world.archive(OWNER)
        decision, _ = self.start(OTHER, self.worktree, {"use_worktree": False})

        self.assertEqual(decision, PASS)

    def test_a_folder_that_is_no_task_worktree_needs_no_check(self) -> None:
        decision, context = self.start(OTHER, self.world.root, {"use_worktree": False})

        self.assertEqual(decision, PASS)
        self.assertNotIn("hand-over", context)

    def test_a_worktree_link_that_cannot_be_read_checks_nothing_here(self) -> None:
        (self.worktree / ".git").write_text("nonsense\n", encoding=ENCODING)
        decision, context = self.start(OTHER, self.worktree, {"use_worktree": False})

        self.assertEqual(decision, PASS)
        self.assertNotIn("only read", context)

    def start(self, session: Session, cwd: Path, fields: dict[str, Any]) -> tuple[str, str]:
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": session.cli,
            "tool_name": "mcp__ccd_session__start_session",
            "tool_input": {**START, **fields},
            "cwd": str(cwd),
        }
        result = subprocess.run(
            [sys.executable, str(CALM_HOOK_RUNNER)],
            input=json.dumps(payload),
            env=self.world.environment(session),
            capture_output=True,
            text=True,
            encoding=ENCODING,
            check=False,
        )
        output = json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}

        self.assertEqual(result.stderr, "")
        return (
            output.get("permissionDecision", PASS),
            output.get("permissionDecisionReason") or output.get("additionalContext", ""),
        )


if __name__ == "__main__":
    unittest.main()
