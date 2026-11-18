#!/usr/bin/env python3
"""Notice the shape problems of a report right after it is written to the shared notes folder.

A PostToolUse hook on Edit and Write. When the written file is a Markdown
report inside a session folder of the shared notes folder (not an index), it
runs the checks in `report_check.py` and, if any finds a problem, adds a
`[REPORT CHECK]` notice with what it measured, so the session fixes the
report before handing it over (user decision, 2026-11-16). It never blocks,
and any failure leaves the write as it was.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from report_check import INDEX_FILE_NAME, check_report, format_findings, is_notes_report

_TOOL_INPUT_KEY = "tool_input"
_FILE_PATH_KEY = "file_path"
_HOOK_OUTPUT_KEY = "hookSpecificOutput"
_HOOK_EVENT_KEY = "hookEventName"
_CONTEXT_KEY = "additionalContext"
_POST_TOOL_USE = "PostToolUse"
_ENCODING = "utf-8"
_NOTICE = "[REPORT CHECK] {findings}. The report-writing skill's limits cover these; a finding that stays gets its reason in the report."


def main() -> int:
    try:
        output = make_output(json.load(sys.stdin))

        if output:
            print(json.dumps(output))
    except Exception as error:
        # The notice is advisory: a failure must not disturb the write it follows.
        print(f"report-check-hook: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


def make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    file_path = str((hook_input.get(_TOOL_INPUT_KEY) or {}).get(_FILE_PATH_KEY) or "")
    path = Path(file_path).expanduser()
    is_report = bool(file_path) and is_notes_report(file_path) and path.name != INDEX_FILE_NAME and path.is_file()
    findings = check_report(path.read_text(encoding=_ENCODING)) if is_report else []

    if findings:
        notice = _NOTICE.format(findings=format_findings(path.name, findings))
        return {_HOOK_OUTPUT_KEY: {_HOOK_EVENT_KEY: _POST_TOOL_USE, _CONTEXT_KEY: notice}}
    else:
        return None


if __name__ == "__main__":
    sys.exit(main())
