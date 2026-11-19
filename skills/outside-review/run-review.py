"""Run one outside review through the Codex CLI on a fixed model, effort, and read-only sandbox.

The checkpoint review runs without asking (`decisions-and-collaboration` "Independent review"), and
the app's permission rules allow this one script. The script takes only the brief and the project
root, so the allowed command can start nothing but this review: the brief goes after `--` so its
text never becomes a Codex option, and the answer and log are new files the script creates beside
the brief, refusing a name that already exists or is a link, so the command cannot overwrite any
other file. Codex's own command rules and connected tools (such as its browser tool) still apply
inside the run, as in any review.

Usage: python3 run-review.py --brief <folder>/review-brief-<topic>.md --root <project root>
The answer goes to <folder>/review-<topic>.md and Codex's progress to <folder>/review-<topic>.log.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import TextIO

CODEX_PROGRAM = "codex"
MODEL = "gpt-6-astra"
EFFORT = "xhigh"
ENCODING = "utf-8"
BRIEF_PREFIX = "review-brief-"
ANSWER_PREFIX = "review-"
MARKDOWN_SUFFIX = ".md"
LOG_SUFFIX = ".log"
STDIN_MARKER = "-"
REFUSED_EXIT = 2
EMPTY_ANSWER_EXIT = 3
NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
NEW_FILE_MODE = 0o600


def build_command(brief_text: str, root: Path, is_repository: bool) -> list[str]:
    command = [
        CODEX_PROGRAM,
        "exec",
        "-m",
        MODEL,
        "-c",
        f'model_reasoning_effort="{EFFORT}"',
        "-s",
        "read-only",
    ]

    if not is_repository:
        command.append("--skip-git-repo-check")
    command.extend(["-C", str(root), "--", brief_text])
    return command


def topic_of(brief: Path) -> str:
    return brief.name[len(BRIEF_PREFIX) : -len(MARKDOWN_SUFFIX)]


def answer_name(brief: Path) -> str:
    return f"{ANSWER_PREFIX}{topic_of(brief)}{MARKDOWN_SUFFIX}"


def log_name(brief: Path) -> str:
    return f"{ANSWER_PREFIX}{topic_of(brief)}{LOG_SUFFIX}"


def is_inside_repository(root: Path) -> bool:
    """A failed `git` call also counts as outside, since Codex then needs the repository check skipped."""
    answer = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )

    return answer.returncode == 0 and answer.stdout.strip() == "true"


def open_new_file(folder_descriptor: int, name: str) -> TextIO:
    """Create the file only if no file or link has that name, so no other file can be written through it."""
    descriptor = os.open(name, NEW_FILE_FLAGS, NEW_FILE_MODE, dir_fd=folder_descriptor)

    try:
        return os.fdopen(descriptor, "w", encoding=ENCODING)
    except Exception:
        os.close(descriptor)
        raise


def check_brief_name(brief: Path) -> None:
    is_brief_name = brief.name.startswith(BRIEF_PREFIX) and brief.suffix == MARKDOWN_SUFFIX

    if not is_brief_name or not topic_of(brief):
        raise ValueError(f"The brief must be named {BRIEF_PREFIX}<topic>{MARKDOWN_SUFFIX}: {brief.name}")


def check_brief_is_plain_file(brief: Path) -> None:
    if brief.is_symlink() or not brief.is_file():
        raise ValueError(f"The brief must be a plain file: {brief}")


def check_brief_text(brief_text: str) -> None:
    if brief_text.strip() in ("", STDIN_MARKER):
        raise ValueError("The brief is empty, so there is nothing to review.")


def check_root(root: Path) -> None:
    if not root.is_dir():
        raise ValueError(f"The project folder does not exist: {root}")


def check_arguments(brief: Path, root: Path) -> None:
    check_brief_name(brief)
    check_brief_is_plain_file(brief)
    check_root(root)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one read-only outside review through the Codex CLI.")

    parser.add_argument("--brief", required=True, type=Path, help="the review brief, review-brief-<topic>.md")
    parser.add_argument("--root", required=True, type=Path, help="the project root the reviewer reads")
    return parser.parse_args(argv)


def run_codex(command: list[str], answer: TextIO, log: TextIO) -> int:
    finished = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=answer, stderr=log, check=False)

    return finished.returncode


def report_result(returncode: int, answer_path: Path, log_path: Path) -> int:
    if returncode != 0:
        print(f"Review failed: Codex exited {returncode}; see {log_path}", file=sys.stderr)
        return returncode
    elif answer_path.stat().st_size == 0:
        print(f"Review failed: Codex wrote no answer; see {log_path}", file=sys.stderr)
        return EMPTY_ANSWER_EXIT
    else:
        print(f"exit 0; answer {answer_path}; log {log_path}")
        return 0


def run_in_new_files(folder_descriptor: int, answer_file: str, log_file: str, command: list[str]) -> int:
    """Remove the files this run created when Codex never started, so a failed start leaves no empty answer."""
    created: list[str] = []

    try:
        with open_new_file(folder_descriptor, answer_file) as answer:
            created.append(answer_file)
            with open_new_file(folder_descriptor, log_file) as log:
                created.append(log_file)
                return run_codex(command, answer, log)
    except Exception:
        for name in created:
            os.unlink(name, dir_fd=folder_descriptor)
        raise


def run_review(brief: Path, root: Path) -> int:
    brief_text = brief.read_text(encoding=ENCODING)

    check_brief_text(brief_text)
    folder = brief.parent
    answer_file = answer_name(brief)
    log_file = log_name(brief)
    command = build_command(brief_text, root.resolve(), is_inside_repository(root))
    folder_descriptor = os.open(folder, os.O_RDONLY)

    try:
        returncode = run_in_new_files(folder_descriptor, answer_file, log_file, command)
    finally:
        os.close(folder_descriptor)
    return report_result(returncode, folder / answer_file, folder / log_file)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)

    try:
        check_arguments(arguments.brief, arguments.root)
        return run_review(arguments.brief, arguments.root)
    except FileExistsError as error:
        print(f"run-review: {error.filename} already exists, so the review did not run; give it a new topic.", file=sys.stderr)
        return REFUSED_EXIT
    except FileNotFoundError as error:
        print(f"run-review: {describe_missing(error.filename)}, so the review did not run.", file=sys.stderr)
        return REFUSED_EXIT
    except UnicodeDecodeError:
        print("run-review: the brief is not UTF-8 text, so it cannot be sent.", file=sys.stderr)
        return REFUSED_EXIT
    except ValueError as error:
        print(f"run-review: {error}", file=sys.stderr)
        return REFUSED_EXIT
    except OSError as error:
        print(f"run-review: the review files could not be written ({error.strerror}: {error.filename}).", file=sys.stderr)
        return REFUSED_EXIT


def describe_missing(filename: object) -> str:
    if filename == CODEX_PROGRAM:
        return "Codex is not installed"
    else:
        return f"{filename} was not found"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
