#!/usr/bin/env python3
"""PreToolUse hook for Bash: deny direct `git commit`, point to wrapper.

Strips quoted strings and heredoc bodies before regex match so commit
messages, descriptions, or doc text mentioning the phrase are not
mistaken for command invocations.

Recognized command-position contexts:
  - start of command string or line
  - after a chain operator (;  &&  ||  |  &) or an opening ( { `
  - after shell keywords and plain wrappers (then, do, else, !, command,
    exec, env, nohup, sudo, time) and env-var assignments (VAR=value cmd)

Recognized git invocations: a path-qualified binary (/usr/bin/git) and
global options before the subcommand (-C <path>, -c <name>=<value>,
--git-dir <path>, --no-pager, ...).

Not recognized: wrapper options (sudo -u <user>), xargs/find -exec, and
commands inside quoted strings (sh -c '...'), which are stripped.
"""

import json
import re
import sys


COMMAND_START = r"(?:^|[;&|\n(`{])\s*"
COMMAND_PREFIX = (
    r"(?:(?:then|do|else|!|command|exec|env|nohup|sudo|time"
    r"|[A-Za-z_]\w*=\S*)\s+)*"
)
GIT_EXECUTABLE = r"(?:\S*/)?git"
GIT_OPTION_WITH_ARGUMENT = r"(?:-[Cc]|--(?:git-dir|work-tree|namespace|config-env))"
GIT_GLOBAL_OPTION = (
    rf"\s+(?:{GIT_OPTION_WITH_ARGUMENT}\s+\S+"
    rf"|(?!{GIT_OPTION_WITH_ARGUMENT}(?:\s|$))-\S+)"
)
GIT_COMMIT_COMMAND = re.compile(
    COMMAND_START
    + COMMAND_PREFIX
    + GIT_EXECUTABLE
    + rf"(?:{GIT_GLOBAL_OPTION})*"
    + r"\s+commit(?=\s|$|[;&|)`}])"
)


def strip_strings_and_heredocs(text: str) -> str:
    """Remove quoted strings and heredoc bodies from a shell command.

    Conservative: keeps the rest of the structure intact so chain
    operators and env prefixes outside quotes are still detectable.
    """
    text = re.sub(
        r"<<-?\s*['\"]?(\w+)['\"]?\n.*?\n\1\b",
        "\n",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    return text


def looks_like_git_commit_command(command: str) -> bool:
    cleaned = strip_strings_and_heredocs(command)
    return bool(GIT_COMMIT_COMMAND.search(cleaned))


def main() -> None:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")

    if not looks_like_git_commit_command(command):
        return

    reason = (
        "Direct git commit blocked. Use ~/.claude/scripts/claude-commit.py "
        "with message and files as args (prepend --amend if amending). "
        "The wrapper validates subject/body format (type prefix, line "
        "length, body lines, bullet count, no internal terms), runs "
        "amend check, stages exact files, prints diff --cached --stat "
        "plus verification table, then invokes git commit via subprocess "
        "so this hook does not re-fire."
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
