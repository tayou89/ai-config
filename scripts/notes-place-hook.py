#!/usr/bin/env python3
"""Refuse a write that would create a new file in a session folder of the desktop app's notes folder.

The app deletes those folders, skipping the Trash, once the session group
closes, and the copy the session notes hook makes at each turn's end misses a
session closed from the sidebar after an interrupted turn, so new notes go in
the project's notes folder `~/Documents/session-notes/<project>/`
(`report-writing` "Notes folders", user decision, 2026-11-23). It checks the
Edit, Write, NotebookEdit, and shell calls of every agent, gated or not; edits
to files already there pass, and so does every call while `SKILL_GATE` is off
in the settings env, as when this check was part of the skill gate. A call it
cannot judge, such as a command line too large to expand, is refused, and the
settings entry blocks the call when the hook cannot answer at all
(`hook_failure.py`, user decision, 2026-11-24), since a missed refusal can
lose the note.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from hook_failure import TOOL_INPUT_KEY, TOOL_NAME_KEY, Decision, FailurePolicy, make_decision, run_hook
from report_check import is_app_session_folder_file
from skill_gate_records import GATE_OFF, read_gate_setting
from tool_calls import list_written_files

_CWD_KEY = "cwd"
_NOTES_PLACE_REASON = "`{name}` would be a new file in a session folder of the desktop app's notes folder, which the app deletes without the Trash once that session group closes. Write new notes in the project's notes folder `~/Documents/session-notes/<project>/` (report-writing \"Notes folders\"); editing a file already there stays allowed."
FAILURE_POLICY = FailurePolicy.blocking(
    "notes-place-hook",
    Decision.DENY,
    "The notes-place hook could not check whether this call creates a file in a session folder of the desktop app's notes folder ({error}), so it refuses it. Write notes in the project's notes folder `~/Documents/session-notes/<project>/` and tell the user; README \"Hooks\" (failure handling) says how to repair the hook from a terminal.",
)


def main() -> int:
    return run_hook(_make_output, FAILURE_POLICY)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    reason = _find_notes_place_reason(hook_input) if read_gate_setting() != GATE_OFF else None

    if reason:
        return make_decision(Decision.DENY, [reason])
    else:
        return None


def _find_notes_place_reason(hook_input: dict[str, Any]) -> str | None:
    tool_name = str(hook_input.get(TOOL_NAME_KEY) or "")
    tool_input = hook_input.get(TOOL_INPUT_KEY) or {}
    written = list_written_files(tool_name, tool_input, hook_input.get(_CWD_KEY))
    new_files = [path for path in written if is_app_session_folder_file(path) and not os.path.lexists(path)]

    return _NOTES_PLACE_REASON.format(name=os.path.basename(new_files[0])) if new_files else None


if __name__ == "__main__":
    sys.exit(main())
