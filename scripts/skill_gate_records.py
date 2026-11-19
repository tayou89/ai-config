"""Where the skill gate keeps its records, and the choice record that `choice-shown.py` writes and the gate reads.

The gate's hook and the record command run as separate programs, so the folder
layout lives here once and both read it. A design session records, before it
asks, which sent file or link shows each option of a choice question, or why
the question is not about a screen. The record is the session's own
declaration: whether the user saw it is not something a file can show.
Questions and labels are compared with their spacing normalized.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
_DEFAULT_CONFIG_DIRECTORY = "~/.claude"
_RECORD_DIRECTORY_NAME = "skill-gate"
_CHOICE_RECORD_NAME = "choices.jsonl"
_UNSAFE_NAME_CHARACTERS = re.compile(r"[^\w-]")
_EMPTY_NAME = "_"
_ENCODING = "utf-8"
_LINE_END = "\n"
_QUESTION_KEY = "question"
_OPTIONS_KEY = "options"
_NOT_VISUAL_KEY = "not_visual"
_TIME_KEY = "time"


@dataclass(frozen=True)
class ChoiceRecord:
    """One question as asked, and either what shows each option label or why it is not a choice about a screen."""

    question: str
    options: dict[str, str]
    not_visual: str


def get_config_directory() -> Path:
    return Path(os.environ.get(_CONFIG_DIRECTORY_VARIABLE) or _DEFAULT_CONFIG_DIRECTORY).expanduser()


def get_record_directory() -> Path:
    return get_config_directory() / _RECORD_DIRECTORY_NAME


def to_safe_name(text: str) -> str:
    return _UNSAFE_NAME_CHARACTERS.sub(_EMPTY_NAME, text) or _EMPTY_NAME


def get_choice_record_path(session_id: str) -> Path:
    """Beside the session's skill records, so the gate's record lifetime clears it with them."""
    return get_record_directory() / to_safe_name(session_id) / _CHOICE_RECORD_NAME


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def append_choice_record(path: Path, record: ChoiceRecord) -> None:
    entry = {
        _QUESTION_KEY: record.question,
        _OPTIONS_KEY: record.options,
        _NOT_VISUAL_KEY: record.not_visual,
        _TIME_KEY: time.time(),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding=_ENCODING) as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + _LINE_END)


def read_choice_records(path: Path) -> list[ChoiceRecord]:
    """Every well-formed record line; any other line is skipped, and a missing file has none."""
    if path.exists():
        entries = [_parse_record(line) for line in path.read_text(encoding=_ENCODING, errors="replace").splitlines()]

        return [entry for entry in entries if entry]
    else:
        return []


def list_unshown_labels(records: Sequence[ChoiceRecord], question: str, labels: Sequence[str]) -> list[str]:
    """The labels of a question that no record shows, or none when a record says it is not about a screen."""
    asked = normalize_text(question)
    matching = [record for record in records if normalize_text(record.question) == asked]
    shown = {normalize_text(label) for record in matching for label in record.options}

    if any(record.not_visual for record in matching):
        return []
    else:
        return [label for label in labels if normalize_text(label) not in shown]


def _parse_record(line: str) -> ChoiceRecord | None:
    """A record line as the command writes it; nesting too deep for the decoder counts as damage, not as a failure."""
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, RecursionError):
        return None
    else:
        return _read_record_entry(entry) if isinstance(entry, dict) else None


def _read_record_entry(entry: dict[str, Any]) -> ChoiceRecord | None:
    question = entry.get(_QUESTION_KEY)
    options = entry.get(_OPTIONS_KEY)
    not_visual = entry.get(_NOT_VISUAL_KEY)

    if (
        isinstance(question, str)
        and isinstance(options, dict)
        and isinstance(not_visual, str)
        and _has_content(question, options, not_visual)
    ):
        return ChoiceRecord(question, options, not_visual.strip())
    else:
        return None


def _has_content(question: str, options: dict[Any, Any], not_visual: str) -> bool:
    """As the command writes it: a question with either filled text option pairs or a reason."""
    has_options = bool(options) and all(map(_is_filled_pair, options.items()))

    return bool(normalize_text(question)) and (has_options or bool(not_visual.strip()))


def _is_filled_pair(pair: tuple[Any, Any]) -> bool:
    return all(isinstance(part, str) and bool(part.strip()) for part in pair)
