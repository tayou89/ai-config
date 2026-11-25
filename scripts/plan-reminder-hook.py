#!/usr/bin/env python3
"""Before Edit or Write of a plan in `claude-plans/`, remind that its sample code follows the code rules.

`code-plan` holds plan sample code to the rules of `code-style` and the
language skill, as implementation code is. The hook only adds that notice,
so its own error lets the call through with a hook error notice
(`hook_failure.py`).
"""

from __future__ import annotations

import os
import sys
from typing import Any

from hook_failure import OUTPUT_KEY, PRE_TOOL_USE, TOOL_INPUT_KEY, FailurePolicy, run_hook

_CONTEXT_KEY = "additionalContext"
_FILE_PATH_KEY = "file_path"
_PLAN_FOLDER = "claude-plans"
_PLAN_PREFIX = "plan-"
_PLAN_SUFFIX = ".md"
_NOTICE = "[PLAN CODE REMINDER] Plan sample code follows every rule of code-style and the language skill (typescript for .ts or .js, python for .py), as implementation code does; only Skill-tool loads of them survive a compaction."
FAILURE_POLICY = FailurePolicy.notice("plan-reminder-hook")


def main() -> int:
    return run_hook(_make_output, FAILURE_POLICY)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    file_path = str((hook_input.get(TOOL_INPUT_KEY) or {}).get(_FILE_PATH_KEY) or "")

    if _is_plan_file(file_path):
        return {OUTPUT_KEY: {"hookEventName": PRE_TOOL_USE, _CONTEXT_KEY: _NOTICE}}
    else:
        return None


def _is_plan_file(path: str) -> bool:
    name = os.path.basename(path)

    return _PLAN_FOLDER in path and name.startswith(_PLAN_PREFIX) and name.endswith(_PLAN_SUFFIX)


if __name__ == "__main__":
    sys.exit(main())
