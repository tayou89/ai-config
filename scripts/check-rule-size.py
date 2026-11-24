#!/usr/bin/env python3
"""check-rule-size.py — keep always-loaded instructions within their size budgets.

Usage:
    check-rule-size.py            check every budget and the approved sizes
    check-rule-size.py --record   record the current always-loaded totals as the approved sizes

Every instruction line that loads in every session competes with all the
others for the model's attention, so the commit check measures the
always-loaded files in bytes (user decision, 2026-11-12):

- Claude loads CLAUDE.md and every file in rules/ in every session.
- Codex loads codex/AGENTS.md whole, outside any limit, so it gets a byte
  budget of its own. In the folder it runs in, it then adds one project
  instruction file per directory from the project root down to that folder
  and cuts their end at project_doc_max_bytes, a limit that covers project
  files only (codex-rs/core/src/agents_md.rs; tested with codex-cli 0.163.0
  on 2026-11-17). The folders are the working folders of the Codex sessions
  of the last 30 days, read from the first line of each session log under
  CODEX_HOME (default ~/.codex). A log that cannot be read or parsed is
  skipped, so one bad log never stops a commit.
- The Claude and Codex global totals must also equal the sizes recorded in
  always-loaded-sizes.json, the sizes the user last approved (user decision,
  2026-11-23): growth stops the commit until text is removed or the user
  approves it, and a shrink stops it until --record lowers the record, so
  freed room is not refilled without approval. --record only writes the
  measured totals; the approval is the user's review of the rule text.

Exit codes:
    0  every total is within its budget and matches its record, or --record wrote it
    1  a total is over its budget or differs from its record, the record cannot be used,
       or --record refused or failed
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NamedTuple

CODEX_HOME_VARIABLE = "CODEX_HOME"
CODEX_SESSIONS_DIRECTORY = "sessions"
CODEX_LIMIT_KEY = "project_doc_max_bytes"
CODEX_DEFAULT_LIMIT_BYTES = 32 * 1024
CODEX_FAILURE_SHARE = 0.85
CODEX_GLOBAL_BUDGET_BYTES = 32 * 1024
CLAUDE_BUDGET_BYTES = 32 * 1024
SESSION_WINDOW_SECONDS = 30 * 24 * 60 * 60
_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_INSTRUCTION_PATH = _ROOT / "CLAUDE.md"
_CLAUDE_RULES_DIRECTORY = _ROOT / "rules"
_CODEX_AGENTS_PATH = _ROOT / "codex" / "AGENTS.md"
_CODEX_SETTINGS_PATH = _ROOT / "codex" / "settings.json"
_PROJECT_ROOT_MARKER = ".git"
_PROJECT_INSTRUCTION_NAMES = ("AGENTS.override.md", "AGENTS.md")
_CLAUDE_FAILURE = (
    "Claude always-loaded rules are {total:,} bytes, over the {budget:,}-byte budget:"
    " remove or move text to a skill as the rule-writing skill describes"
)
_CODEX_GLOBAL_FAILURE = (
    "Codex global instructions are {total:,} bytes, over the {budget:,}-byte budget:"
    " remove or move text to a skill as the rule-writing skill describes"
)
_CODEX_WARNING = (
    "Codex project instructions in {root} are {total:,} bytes, over the {warning:,}-byte default"
    " project_doc_max_bytes: a computer without the raised limit cuts their end"
)
_CODEX_FAILURE = (
    "Codex project instructions in {root} are {total:,} bytes, over {failure:,} bytes"
    " ({share:.0%} of project_doc_max_bytes {limit:,}), past which Codex cuts their end:"
    " shrink that project's AGENTS.md in its own session"
)
APPROVED_SIZES_NAME = "always-loaded-sizes.json"
RECORD_OPTION = "--record"
_ENCODING = "utf-8"
_CLAUDE_KEY = "claude"
_CODEX_GLOBAL_KEY = "codex_global"
_SIZE_KEYS = (_CLAUDE_KEY, _CODEX_GLOBAL_KEY)
_APPROVED_SIZES_PATH = _ROOT / APPROVED_SIZES_NAME
_USAGE = "usage: check-rule-size.py [--record]"
_GROWTH_FAILURE = "{label} grew to {total:,} bytes, {difference:,} over the approved {approved:,} in {record}: remove or move text, or show the growth in the user's rule review and, once approved, run `python3 scripts/check-rule-size.py --record` and list the record with the rule files in the commit"
_SHRINK_FAILURE = "{label} shrank to {total:,} bytes, {difference:,} under the approved {approved:,} in {record}: run `python3 scripts/check-rule-size.py --record` and list the record with the rule files in the commit, so the freed room is not refilled without approval"
_RECORD_FAILURE = "the approved sizes in {record} cannot be used ({reason}): restore the file from the last commit, or create it with `python3 scripts/check-rule-size.py --record` only when the repository has none"
_RECORD_OVER_BUDGET = "{label} is {total:,} bytes, over its {budget:,}-byte budget"
_RECORD_UNUSABLE_REFUSAL = "{record} exists but cannot be used ({reason}): restore it from the last commit, keeping both sides' approved entries after a merge, instead of recording over it"
_RECORD_WRITE_FAILURE = "could not write {record} ({reason}); the previous record is unchanged"
_READ_PROBLEM = "cannot read it: {error}"
_NESTING_PROBLEM = "it is nested too deeply to read"
_REPEATED_KEY_PROBLEM = "repeated key {keys}"
_KEYS_PROBLEM = "it must be an object with exactly the keys {keys}"
_VALUES_PROBLEM = "no whole number from 0 to its budget for {keys}"
_RECORD_MODE = 0o644
_RECORD_DONE = "Recorded {record}. This records sizes only; run the size check and the commit check next."
_RECORD_CHANGE = "  {label}: {before} -> {total:,} bytes{note}"
_INCREASE_NOTE = " (an increase: the user's rule review must show and approve it)"
_NO_PREVIOUS_SIZE = "none"
_APPROVED_SUMMARY = "Approved sizes in {record}: {sizes}"
_UNUSABLE_SUMMARY = "Approved sizes in {record}: cannot be used"
_LABELS = {_CLAUDE_KEY: "Claude always-loaded rules", _CODEX_GLOBAL_KEY: "Codex global instructions"}
_BUDGETS = {_CLAUDE_KEY: CLAUDE_BUDGET_BYTES, _CODEX_GLOBAL_KEY: CODEX_GLOBAL_BUDGET_BYTES}
_EXIT_PASSED = 0
_EXIT_FAILED = 1


class _CodexProject(NamedTuple):
    root: Path
    project_bytes: int


class _Approval(NamedTuple):
    sizes: dict[str, int]
    problem: str


def main(arguments: list[str]) -> int:
    if arguments == [RECORD_OPTION]:
        return _record_sizes(_measure_totals())
    elif arguments:
        print(_USAGE, file=sys.stderr)
        return _EXIT_FAILED
    else:
        return _run_size_check()


def _run_size_check() -> int:
    totals = _measure_totals()
    approval = _read_approval()
    limit = _read_codex_limit()
    projects = _collect_codex_projects()
    largest = max((project.project_bytes for project in projects), default=0)
    failures = (
        _find_claude_failures(totals[_CLAUDE_KEY])
        + _find_codex_global_failures(totals[_CODEX_GLOBAL_KEY])
        + _find_approval_failures(totals, approval)
        + _find_codex_project_failures(limit, projects)
    )

    print(f"Claude always-loaded rules: {totals[_CLAUDE_KEY]:,} of {CLAUDE_BUDGET_BYTES:,} bytes")
    print(f"Codex global instructions: {totals[_CODEX_GLOBAL_KEY]:,} of {CODEX_GLOBAL_BUDGET_BYTES:,} bytes")
    print(_describe_approval(approval))
    print(f"Codex project instructions: largest {largest:,} bytes, limit {limit:,}")
    for warning in _find_codex_project_warnings(limit, projects):
        print(f"warning: {warning}")
    if failures:
        _print_failures("rule size check failed", failures)
        return _EXIT_FAILED
    else:
        return _EXIT_PASSED


def _measure_totals() -> dict[str, int]:
    return {_CLAUDE_KEY: _measure_claude_rules(), _CODEX_GLOBAL_KEY: _CODEX_AGENTS_PATH.stat().st_size}


def _record_sizes(totals: dict[str, int]) -> int:
    approval = _read_approval()
    refusals = _find_record_refusals(totals, approval)

    if refusals:
        _print_failures("rule size record refused", refusals)
        return _EXIT_FAILED
    else:
        return _write_record(totals, approval.sizes)


def _find_record_refusals(totals: dict[str, int], approval: _Approval) -> list[str]:
    over = [
        _RECORD_OVER_BUDGET.format(label=_LABELS[key], total=totals[key], budget=_BUDGETS[key])
        for key in _SIZE_KEYS
        if totals[key] > _BUDGETS[key]
    ]

    return over + _find_unusable_record(approval)


def _find_unusable_record(approval: _Approval) -> list[str]:
    if approval.problem and _APPROVED_SIZES_PATH.exists():
        return [_RECORD_UNUSABLE_REFUSAL.format(record=APPROVED_SIZES_NAME, reason=approval.problem)]
    else:
        return []


def _write_record(totals: dict[str, int], previous: dict[str, int]) -> int:
    text = json.dumps(totals, indent=2, sort_keys=True) + "\n"

    try:
        _replace_text(_APPROVED_SIZES_PATH, text)
    except OSError as error:
        failure = _RECORD_WRITE_FAILURE.format(record=APPROVED_SIZES_NAME, reason=error)

        _print_failures("rule size record failed", [failure])
        return _EXIT_FAILED
    else:
        changes = [_describe_record_change(key, totals[key], previous.get(key)) for key in _SIZE_KEYS]

        print("\n".join([_RECORD_DONE.format(record=APPROVED_SIZES_NAME), *changes]))
        return _EXIT_PASSED


def _replace_text(path: Path, text: str) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)

    os.close(descriptor)
    try:
        temporary.write_text(text, encoding=_ENCODING)
        temporary.chmod(_RECORD_MODE)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _describe_record_change(key: str, total: int, before: int | None) -> str:
    shown = f"{before:,}" if before is not None else _NO_PREVIOUS_SIZE
    note = _INCREASE_NOTE if before is not None and total > before else ""

    return _RECORD_CHANGE.format(label=_LABELS[key], before=shown, total=total, note=note)


def _describe_approval(approval: _Approval) -> str:
    if approval.sizes:
        sizes = ", ".join(f"{_LABELS[key]} {approval.sizes[key]:,} bytes" for key in _SIZE_KEYS)

        return _APPROVED_SUMMARY.format(record=APPROVED_SIZES_NAME, sizes=sizes)
    else:
        return _UNUSABLE_SUMMARY.format(record=APPROVED_SIZES_NAME)


def _find_approval_failures(totals: dict[str, int], approval: _Approval) -> list[str]:
    if approval.sizes:
        return [
            failure
            for key in _SIZE_KEYS
            for failure in _compare_with_approved(key, totals[key], approval.sizes[key])
        ]
    else:
        return [_RECORD_FAILURE.format(record=APPROVED_SIZES_NAME, reason=approval.problem)]


def _read_approval() -> _Approval:
    try:
        sizes = _read_approved_sizes()
    except ValueError as error:
        return _Approval({}, str(error))
    else:
        return _Approval(sizes, "")


def _read_approved_sizes() -> dict[str, int]:
    """Return the recorded sizes, raising ValueError when the record cannot be trusted.

    The record is UTF-8 JSON without a byte order mark: an object holding each size key
    exactly once, each a whole number from 0 to that total's budget.
    """
    try:
        record: object = json.loads(
            _APPROVED_SIZES_PATH.read_text(encoding=_ENCODING),
            object_pairs_hook=_build_unique_object,
        )
    except OSError as error:
        raise ValueError(_READ_PROBLEM.format(error=error)) from error
    except RecursionError as error:
        raise ValueError(_NESTING_PROBLEM) from error
    else:
        return _parse_approved_sizes(record)


def _build_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})

    if repeated:
        raise ValueError(_REPEATED_KEY_PROBLEM.format(keys=", ".join(repeated)))
    else:
        return dict(pairs)


def _parse_approved_sizes(record: object) -> dict[str, int]:
    if isinstance(record, dict) and set(record) == set(_SIZE_KEYS):
        return _parse_size_values(record)
    else:
        raise ValueError(_KEYS_PROBLEM.format(keys=", ".join(_SIZE_KEYS)))


def _parse_size_values(record: dict[str, object]) -> dict[str, int]:
    sizes = {
        key: value
        for key, value in record.items()
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _BUDGETS[key]
    }
    invalid = [key for key in _SIZE_KEYS if key not in sizes]

    if invalid:
        raise ValueError(_VALUES_PROBLEM.format(keys=", ".join(invalid)))
    else:
        return sizes


def _compare_with_approved(key: str, total: int, approved: int) -> list[str]:
    values = {
        "label": _LABELS[key],
        "total": total,
        "difference": abs(total - approved),
        "approved": approved,
        "record": APPROVED_SIZES_NAME,
    }

    if total > approved:
        return [_GROWTH_FAILURE.format(**values)]
    elif total < approved:
        return [_SHRINK_FAILURE.format(**values)]
    else:
        return []


def _print_failures(heading: str, failures: list[str]) -> None:
    print(f"{heading}:\n" + "\n".join(f"  - {failure}" for failure in failures), file=sys.stderr)


def _measure_claude_rules() -> int:
    rule_bytes = sum(path.stat().st_size for path in _CLAUDE_RULES_DIRECTORY.rglob("*.md"))

    return _CLAUDE_INSTRUCTION_PATH.stat().st_size + rule_bytes


def _read_codex_limit() -> int:
    settings = json.loads(_CODEX_SETTINGS_PATH.read_text(encoding="utf-8"))
    limit = settings.get("config", {}).get(CODEX_LIMIT_KEY, CODEX_DEFAULT_LIMIT_BYTES)

    return int(limit)


def _collect_codex_projects() -> list[_CodexProject]:
    largest: dict[Path, _CodexProject] = {}

    for folder in _find_codex_folders():
        root = _find_project_root(folder)
        key = root or folder
        project = _CodexProject(key, _measure_project_instructions(folder, root))

        if key not in largest or project.project_bytes > largest[key].project_bytes:
            largest[key] = project
    return sorted(largest.values(), key=lambda project: str(project.root))


def _find_codex_folders() -> set[Path]:
    sessions = _find_codex_home() / CODEX_SESSIONS_DIRECTORY
    oldest = time.time() - SESSION_WINDOW_SECONDS
    folders = (_read_session_folder(log, oldest) for log in sessions.rglob("*.jsonl"))

    return {folder for folder in folders if folder and folder.is_dir()}


def _find_codex_home() -> Path:
    if os.environ.get(CODEX_HOME_VARIABLE):
        return Path(os.environ[CODEX_HOME_VARIABLE])
    else:
        return Path.home() / ".codex"


def _read_session_folder(log: Path, oldest: float) -> Path | None:
    try:
        if log.is_file() and log.stat().st_mtime >= oldest:
            with log.open(encoding="utf-8", errors="replace") as stream:
                return _parse_session_folder(stream.readline())
        else:
            return None
    except OSError:
        return None


def _parse_session_folder(first_line: str) -> Path | None:
    try:
        record: Any = json.loads(first_line)
    except ValueError:
        record = None
    payload = record.get("payload") if isinstance(record, dict) else None
    folder = payload.get("cwd") if isinstance(payload, dict) else None

    if isinstance(folder, str) and folder:
        return Path(folder)
    else:
        return None


def _measure_project_instructions(folder: Path, root: Path | None) -> int:
    files = (_find_instruction_file(directory) for directory in _list_project_directories(folder, root))

    return sum(path.stat().st_size for path in files if path)


def _list_project_directories(folder: Path, root: Path | None) -> list[Path]:
    if root:
        return [directory for directory in [folder, *folder.parents] if directory.is_relative_to(root)]
    else:
        return [folder]


def _find_project_root(folder: Path) -> Path | None:
    roots = (directory for directory in [folder, *folder.parents] if (directory / _PROJECT_ROOT_MARKER).exists())

    return next(roots, None)


def _find_instruction_file(directory: Path) -> Path | None:
    return next((directory / name for name in _PROJECT_INSTRUCTION_NAMES if (directory / name).is_file()), None)


def _find_claude_failures(total: int) -> list[str]:
    if total > CLAUDE_BUDGET_BYTES:
        return [_CLAUDE_FAILURE.format(total=total, budget=CLAUDE_BUDGET_BYTES)]
    else:
        return []


def _find_codex_global_failures(total: int) -> list[str]:
    if total > CODEX_GLOBAL_BUDGET_BYTES:
        return [_CODEX_GLOBAL_FAILURE.format(total=total, budget=CODEX_GLOBAL_BUDGET_BYTES)]
    else:
        return []


def _find_codex_project_failures(limit: int, projects: list[_CodexProject]) -> list[str]:
    failure = int(limit * CODEX_FAILURE_SHARE)
    over = [project for project in projects if project.project_bytes > failure]

    return [
        _CODEX_FAILURE.format(
            root=project.root,
            total=project.project_bytes,
            failure=failure,
            share=CODEX_FAILURE_SHARE,
            limit=limit,
        )
        for project in over
    ]


def _find_codex_project_warnings(limit: int, projects: list[_CodexProject]) -> list[str]:
    failure = int(limit * CODEX_FAILURE_SHARE)
    warned = [project for project in projects if CODEX_DEFAULT_LIMIT_BYTES < project.project_bytes <= failure]

    return [
        _CODEX_WARNING.format(root=project.root, total=project.project_bytes, warning=CODEX_DEFAULT_LIMIT_BYTES)
        for project in warned
    ]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
