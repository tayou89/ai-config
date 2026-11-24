"""Run scripts/start-session-hook.py as a hook process on a calm computer.

The hook reads this computer's load and memory pressure, and asks the user
before a start when memory is short. Tests that check its other answers run
it through this file, so a busy test run cannot turn their answer into that
question.
"""

from __future__ import annotations

import os
from unittest import mock

from workflow_world import load_script

CALM_PRESSURE = 1


def read_calm_load() -> tuple[float, int]:
    return 0.0, os.cpu_count() or 1


def read_calm_pressure() -> int:
    return CALM_PRESSURE


def main() -> None:
    hook = load_script("start-session-hook")

    with (
        mock.patch.object(hook, "_read_load", read_calm_load),
        mock.patch.object(hook, "_read_pressure", read_calm_pressure),
    ):
        hook.main()


if __name__ == "__main__":
    main()
