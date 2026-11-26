#!/usr/bin/env python3
"""Inside tmux, mark the window of a Claude Code session that waits on the user or has finished its reply.

A Notification hook. With `allow-passthrough all` in the shared tmux settings
(`terminal/tmux.conf`), Claude Code's own desktop notification reaches the
terminal even from a tmux window not being viewed (hooks documentation, "Get
terminal notifications inside tmux or GNU screen", read 2026-11-24), but it
does not say which window to look at. So the hook answers with a bare bell in
`terminalSequence`, which Claude Code writes through its own terminal output,
since a hook has no terminal of its own (hooks documentation, "Emit terminal
notifications"). tmux then flags that window in its status bar until it is
viewed, and with `bell-action none` it sends the terminal no second alert.
Outside tmux the hook answers nothing, and Claude Code ignores the field in
the desktop app and in `-p` runs. Its own error exits 1 under the notice
policy of `hook_failure.py`; Claude Code ignores a Notification hook's exit
code, so the error never stops a turn.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from hook_failure import FailurePolicy, run_hook

_TMUX_VARIABLE = "TMUX"
_SEQUENCE_KEY = "terminalSequence"
_BELL = "\a"
FAILURE_POLICY = FailurePolicy.notice("tmux-window-mark-hook")


def main() -> int:
    return run_hook(_make_output, FAILURE_POLICY)


def _make_output(hook_input: dict[str, Any]) -> dict[str, Any] | None:
    """Every Notification type the settings entry lists gets the same mark, so the input needs no further reading."""
    if os.environ.get(_TMUX_VARIABLE):
        return {_SEQUENCE_KEY: _BELL}
    else:
        return None


if __name__ == "__main__":
    sys.exit(main())
