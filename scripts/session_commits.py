"""The commits each Claude session made through the commit wrapper.

Sessions that share a checkout commit as the same person, so git cannot
tell whose commit HEAD is. The wrapper adds each commit it makes to its
session's list, and --amend rewrites HEAD only when it is on that list, as
Perforce lets only a changelist's owner change it. The list sits beside the
session's write record and expires with it.
"""

from __future__ import annotations

import json
from pathlib import Path

from session_writes import get_record_directory

_COMMITS_SUFFIX = ".commits"
_ROOT_FIELD = "root"
_COMMIT_FIELD = "commit"
_TEXT_ENCODING = "utf-8"
_LINE_END = "\n"


def add_session_commit(session_id: str, root: str, commit: str) -> bool:
    """Add a commit this session made, saying whether it was kept; a commit not kept only cannot be amended."""
    record = _get_commits_path(session_id)
    line = json.dumps({_ROOT_FIELD: root, _COMMIT_FIELD: commit}) + _LINE_END

    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        with record.open("a", encoding=_TEXT_ENCODING) as file:
            file.write(line)
    except OSError:
        return False
    else:
        return True


def is_session_commit(session_id: str, root: str, commit: str) -> bool:
    """Whether this session made the commit through the wrapper; a list that cannot be read holds none."""
    try:
        lines = _get_commits_path(session_id).read_text(encoding=_TEXT_ENCODING).split(_LINE_END)
    except (OSError, ValueError):
        return False
    else:
        return any(_is_listed(line, root, commit) for line in lines)


def _get_commits_path(session_id: str) -> Path:
    return get_record_directory() / f"{session_id}{_COMMITS_SUFFIX}"


def _is_listed(line: str, root: str, commit: str) -> bool:
    try:
        entry = json.loads(line)
    except ValueError:
        return False
    else:
        return isinstance(entry, dict) and entry.get(_ROOT_FIELD) == root and entry.get(_COMMIT_FIELD) == commit
