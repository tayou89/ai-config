"""Listing record files without hiding a folder that cannot be read.

The guards, the write record, and the command parser read records that other runs leave in folders, and a record
left out unseen reads as no record. pathlib's glob skips a folder it may not read, so the listing here reads each
level with os.scandir and raises instead (user decision, 2026-11-24). It imports nothing of this repository, so any
script may use it.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

_PATTERN_SEPARATOR = "/"


def list_matching_paths(folder: Path, pattern: str) -> list[Path]:
    """The paths under a folder that match a slash-separated pattern of shell wildcards, such as `worktrees/*/gitdir`.

    Names match case for case on every system. A folder that is missing, gone, or not a folder matches nothing, and
    any other failure raises OSError.
    """
    matches = [folder]

    for part in pattern.split(_PATTERN_SEPARATOR):
        matches = [child for parent in matches for child in _list_matching_children(parent, part)]
    return matches


def _list_matching_children(folder: Path, part: str) -> list[Path]:
    try:
        with os.scandir(folder) as entries:
            names = [entry.name for entry in entries]
    except (FileNotFoundError, NotADirectoryError):
        return []
    else:
        return [folder / name for name in sorted(names) if fnmatch.fnmatchcase(name, part)]
