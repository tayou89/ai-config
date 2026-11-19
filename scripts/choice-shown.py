#!/usr/bin/env python3
"""Record what shows each option of a choice question, before a design session asks it.

    choice-shown.py --question "<question as asked>" --option "<label>" "<sent file or link>" [--option ...]
    choice-shown.py --question "<question as asked>" --not-visual "<why it is not a choice about a screen>"

The skill gate refuses a choice question from a session that has loaded
`ux-design` until every option of that question is recorded here, or the
question is recorded as not about a screen. A label and what shows it are
separate arguments, so a label may hold any character. A file must exist on
this computer and a link must name a web host; nothing records whether the
user saw it. The session comes from CLAUDE_CODE_SESSION_ID, which Claude Code
sets for the commands it runs.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

from skill_gate_records import ChoiceRecord, append_choice_record, get_choice_record_path, normalize_text

_SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
_WEB_SCHEMES = frozenset({"http", "https"})
_FILE_SCHEME = "file"
_LOCAL_HOSTS = frozenset({"", "localhost"})
_EXIT_RECORDED = 0
_EXIT_INVALID = 1
_MESSAGE_PREFIX = "choice-shown: "


class _ChoiceInputError(Exception):
    pass


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parse_arguments(arguments)
    options = [(label, shown_by) for label, shown_by in parsed.option or []]

    try:
        session_id = os.environ.get(_SESSION_VARIABLE, "")

        _check_session(session_id)
        _check_record_input(parsed.question, options, parsed.not_visual)
        record = _make_record(parsed.question, options, parsed.not_visual)

        append_choice_record(get_choice_record_path(session_id), record)
    except (_ChoiceInputError, OSError) as error:
        print(f"{_MESSAGE_PREFIX}{error}", file=sys.stderr)
        return _EXIT_INVALID
    else:
        print(f'{_MESSAGE_PREFIX}recorded {_describe_record(record)} for "{record.question}"')
        return _EXIT_RECORDED


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record what shows each option of a choice question.")
    shown = parser.add_mutually_exclusive_group(required=True)

    parser.add_argument("--question", required=True, help="the question exactly as it will be asked")
    shown.add_argument(
        "--option",
        action="append",
        nargs=2,
        metavar=("LABEL", "SHOWN_BY"),
        help="an option label and the sent file or web link that shows it, once per option",
    )
    shown.add_argument("--not-visual", help="why the choice is not about a screen")
    return parser.parse_args(arguments)


def _make_record(question: str, options: Sequence[tuple[str, str]], not_visual: str | None) -> ChoiceRecord:
    shown = {normalize_text(label): _resolve_target(target.strip()) for label, target in options}

    return ChoiceRecord(normalize_text(question), shown, (not_visual or "").strip())


def _resolve_target(target: str) -> str:
    """A link as given, so the user can open what the record names; a path made absolute."""
    if urlparse(target).scheme:
        return target
    else:
        return str(Path(target).expanduser().absolute())


def _describe_record(record: ChoiceRecord) -> str:
    if record.not_visual:
        return "that it is not a choice about a screen"
    else:
        return f"{len(record.options)} option(s)"


def _is_found_target(target: str) -> bool:
    """A web link with a valid host and port, or a file on this computer named by a path or a local file URL."""
    try:
        parts = urlparse(target)
        is_web_link = parts.scheme in _WEB_SCHEMES and bool(parts.hostname) and (parts.port is None or parts.port > 0)
    except ValueError:
        return False
    else:
        is_file_url = parts.scheme == _FILE_SCHEME and parts.netloc in _LOCAL_HOSTS
        is_local_file = is_file_url and Path(unquote(parts.path)).is_file()
        is_path = not parts.scheme and Path(target).expanduser().is_file()

        return is_web_link or is_local_file or is_path


def _check_session(session_id: str) -> None:
    if not session_id:
        raise _ChoiceInputError(
            f"No session to record for: run this from a Claude Code session, which sets {_SESSION_VARIABLE}."
        )


def _check_question(question: str) -> None:
    if not normalize_text(question):
        raise _ChoiceInputError("The question is empty: give it exactly as it will be asked.")


def _check_label(label: str) -> None:
    if not normalize_text(label):
        raise _ChoiceInputError("An option label is empty: give each option's label exactly as the question shows it.")


def _check_target(target: str) -> None:
    if not _is_found_target(target.strip()):
        raise _ChoiceInputError(
            f"{target} is neither a file on this computer nor an http(s) link with a host: give the file you sent or a link to what shows the option."
        )


def _check_reason(reason: str | None) -> None:
    if reason is not None and not reason.strip():
        raise _ChoiceInputError("Say why the choice is not about a screen.")


def _check_record_input(question: str, options: Sequence[tuple[str, str]], not_visual: str | None) -> None:
    _check_question(question)
    for label, target in options:
        _check_label(label)
        _check_target(target)
    _check_reason(not_visual)


if __name__ == "__main__":
    sys.exit(main())
