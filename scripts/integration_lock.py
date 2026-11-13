"""One integration at a time per repository.

The merge and integration tools check that a candidate contains the current
base and passed its checks, then move the base. Two sessions doing that at
once could both pass against the same base and land untested together, so
each holds this lock from the check to the moved base, as a commit queue
does. The lock file sits in the repository's common Git folder, which the
original checkout and all of its worktrees share; the operating system
releases it when the holder exits, even after a crash.
"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_FILE_NAME = "agent-integration.lock"
DEFAULT_WAIT_SECONDS = 1800
_POLL_SECONDS = 1.0
_ENCODING = "utf-8"


class LockTimeoutError(Exception):
    """Another integration held the lock for longer than this one would wait."""


@contextmanager
def hold_integration_lock(
    common_git_directory: Path,
    wait_seconds: float,
    announce_wait: Callable[[], None],
) -> Iterator[None]:
    """Hold the repository's integration lock, calling `announce_wait` once if another integration holds it."""
    with open(common_git_directory / LOCK_FILE_NAME, "a", encoding=_ENCODING) as handle:
        _acquire(handle.fileno(), time.monotonic() + wait_seconds, announce_wait)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _acquire(descriptor: int, deadline: float, announce_wait: Callable[[], None]) -> None:
    announced = False

    while not _try_lock(descriptor):
        if time.monotonic() > deadline:
            raise LockTimeoutError("another integration in this repository is still running; try again when it ends")
        elif not announced:
            announce_wait()
            announced = True
        time.sleep(_POLL_SECONDS)


def _try_lock(descriptor: int) -> bool:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        return True
