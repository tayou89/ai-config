"""What a tool call names: the file it writes and the commands its shell line runs, for the hooks that judge calls.

The skill gate and the notes-place hook both read these, so they agree on which
tools write files and which run shell lines.
"""

from __future__ import annotations

import os
from typing import Any

from shell_command import CommandCall, expand_shell_command
from skill_gate_records import get_config_directory

FILE_PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
SHELL_TOOLS = frozenset({"Bash", "Monitor", "mcp__terminal__run_in_terminal"})
COMMAND_KEY = "command"


def list_line_calls(command: str, cwd: str | None) -> list[CommandCall]:
    """The calls the command line makes itself, not what a script file on disk runs; a line too large raises."""
    return [call for call in expand_shell_command(command, cwd, get_config_directory()) if not call.is_from_script_file]


def list_written_files(tool_name: str, tool_input: dict[str, Any], cwd: str | None) -> list[str]:
    file_path = tool_input.get(FILE_PATH_KEYS.get(tool_name, ""))

    if isinstance(file_path, str) and file_path:
        return [os.path.expanduser(file_path)]
    elif tool_name in SHELL_TOOLS:
        return _list_command_written_files(str(tool_input.get(COMMAND_KEY) or ""), cwd)
    else:
        return []


def _list_command_written_files(command: str, cwd: str | None) -> list[str]:
    """Files the command line itself writes, relative ones resolved from the folder each call runs in."""
    return [
        os.path.join(call.cwd or cwd or "", os.path.expanduser(path))
        for call in list_line_calls(command, cwd)
        for path in call.list_written_files()
    ]
