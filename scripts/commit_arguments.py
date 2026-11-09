"""Parse the command line of claude-commit.py.

Shared by claude-commit.py and the shell guard hook, which reads the
file list from the same command line to refuse credential files before
the wrapper runs. One parser keeps the two from reading an option's
value as the message or the message as a file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

AMEND_OPTION = "--amend"
NO_STAGE_OPTION = "--no-stage"
EXPECT_HEAD_OPTION = "--expect-head"
REVIEWED_OPTION = "--reviewed"
OPTION_PREFIX = "--"
USAGE = f'Usage: claude-commit.py [{AMEND_OPTION}] [{NO_STAGE_OPTION}] [{EXPECT_HEAD_OPTION} <sha>] [{REVIEWED_OPTION}] "<message>" [<file1> ...]'


class CommitArgumentsError(ValueError):
    pass


@dataclass(frozen=True)
class CommitArguments:
    amend: bool
    no_stage: bool
    reviewed: bool
    expected_head: str | None
    message: str
    files: tuple[str, ...]


def parse_commit_arguments(argv: Sequence[str]) -> CommitArguments:
    amend = False
    no_stage = False
    reviewed = False
    expected_head: str | None = None
    index = 0

    while index < len(argv) and argv[index].startswith(OPTION_PREFIX):
        option = argv[index]

        if option == AMEND_OPTION:
            amend = True
            index += 1
        elif option == NO_STAGE_OPTION:
            no_stage = True
            index += 1
        elif option == REVIEWED_OPTION:
            reviewed = True
            index += 1
        elif option == EXPECT_HEAD_OPTION and index + 1 < len(argv):
            expected_head = argv[index + 1]
            index += 2
        else:
            raise CommitArgumentsError(f"Unknown option: {option}")
    check_positional_count(len(argv) - index, no_stage)
    return CommitArguments(
        amend=amend,
        no_stage=no_stage,
        reviewed=reviewed,
        expected_head=expected_head,
        message=argv[index],
        files=tuple(argv[index + 1 :]),
    )


def check_positional_count(count: int, no_stage: bool) -> None:
    minimum = 1 if no_stage else 2

    if count < minimum:
        raise CommitArgumentsError(USAGE)
