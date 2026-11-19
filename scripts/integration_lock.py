"""One integration or commit at a time per checkout.

The merge and integration tools check that a candidate contains the current
base and passed its checks, then move the base. Two sessions doing that at
once could both pass against the same base and land untested together, so
each holds this lock from the check to the moved base, as a commit queue
does. They take the lock file in the repository's common Git folder, which
is the original checkout's own Git folder. The commit wrapper
(claude-commit.py) takes it in the Git folder of the checkout it commits
in, so commits in the original checkout queue with the integrations that
move its branch, while each linked worktree, on a branch of its own, has a
lock of its own. The operating system releases the lock when the holder
exits, even after a crash.
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
    """Another integration or commit held the lock for longer than this one would wait."""


@contextmanager
def hold_integration_lock(
    common_git_directory: Path,
    wait_seconds: float,
    announce_wait: Callable[[], None],
) -> Iterator[None]:
    """Hold the lock in this Git folder, calling `announce_wait` once if another integration or commit holds it."""
    with hold_file_lock(common_git_directory / LOCK_FILE_NAME, wait_seconds, _POLL_SECONDS, announce_wait):
        yield


@contextmanager
def hold_file_lock(
    path: Path,
    wait_seconds: float,
    poll_seconds: float,
    announce_wait: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Hold an exclusive lock on a file, which the operating system releases when the holder exits.

    `announce_wait` runs once if another process holds it; a wait of a few seconds needs none. Raises
    LockTimeoutError when another process holds it for longer than `wait_seconds`.
    """
    with open(path, "a", encoding=_ENCODING) as handle:
        _acquire(handle.fileno(), time.monotonic() + wait_seconds, poll_seconds, announce_wait)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _acquire(
    descriptor: int, deadline: float, poll_seconds: float, announce_wait: Callable[[], None] | None
) -> None:
    announced = False

    while not _try_lock(descriptor):
        if time.monotonic() > deadline:
            raise LockTimeoutError(
                "another commit or integration in this checkout is still running; try again when it ends"
            )
        elif not announced and announce_wait is not None:
            announce_wait()
            announced = True
        time.sleep(poll_seconds)


def _try_lock(descriptor: int) -> bool:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    else:
        return True
