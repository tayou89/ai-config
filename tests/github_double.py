#!/usr/bin/env python3
"""A stand-in for the few `gh` commands the merge and cleanup tools run, backed by a local bare repository.

Tests put a `gh` shim that runs this file first on PATH and point
GITHUB_DOUBLE_STATE at a folder holding state.json:

    {"remote": "<bare repository>",
     "pulls": {"1": {"head": "task-a", "base": "main", "state": "OPEN", "isDraft": false,
                     "mergeable": "MERGEABLE", "mergeCommit": null, "mergedHead": null}},
     "checks": {"<head commit>": [<statusCheckRollup entries>]},
     "defaultChecks": [<entries for any other head>],
     "mergeDelaySeconds": 0}

Branch tips and the compare answer come from the bare repository, and a
merge really squashes the head onto the base there, so a test sees the base
move exactly as GitHub would. Each call is appended to calls.jsonl. The
response shapes follow gh 2.103.3 (`gh pr view --json`, the REST compare and
ref endpoints) as read on 2026-11-13.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

STATE_VARIABLE = "GITHUB_DOUBLE_STATE"
STATE_FILE = "state.json"
CALLS_FILE = "calls.jsonl"
LOCK_FILE = "state.lock"
REF_PATH = "repos/{owner}/{repo}/git/ref/heads/"
COMPARE_PATH = "repos/{owner}/{repo}/compare/"
URL_PREFIX = "https://github.com/owner/repo/pull/"
ENCODING = "utf-8"
EXIT_FAILED = 1


def main(arguments: Sequence[str]) -> int:
    folder = Path(os.environ[STATE_VARIABLE])

    with open(folder / CALLS_FILE, "a", encoding=ENCODING) as calls:
        calls.write(json.dumps({"argv": list(arguments), "cwd": os.getcwd()}) + "\n")
    with open(folder / LOCK_FILE, "a", encoding=ENCODING) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = json.loads((folder / STATE_FILE).read_text(encoding=ENCODING))
        code = dispatch(state, list(arguments))
        (folder / STATE_FILE).write_text(json.dumps(state, indent=2), encoding=ENCODING)
        return code


def dispatch(state: dict[str, Any], arguments: list[str]) -> int:
    if arguments[:2] == ["pr", "view"]:
        return view_pull_request(state, arguments[2:])
    elif arguments[:2] == ["pr", "list"]:
        return list_pull_requests(state, arguments[2:])
    elif arguments[:2] == ["pr", "merge"]:
        return merge_pull_request(state, arguments[2:])
    elif arguments[:1] == ["api"]:
        return answer_api(state, arguments[1])
    else:
        print(f"github_double: unsupported command {arguments}", file=sys.stderr)
        return EXIT_FAILED


def view_pull_request(state: dict[str, Any], arguments: list[str]) -> int:
    selector = arguments[0] if arguments and not arguments[0].startswith("-") else None
    number = find_pull_number(state, selector)
    fields = arguments[arguments.index("--json") + 1].split(",")

    if number is None:
        print("no pull requests found for branch", file=sys.stderr)
        return EXIT_FAILED
    else:
        record = describe_pull_request(state, number)
        print(json.dumps({field: record[field] for field in fields}))
        return 0


def list_pull_requests(state: dict[str, Any], arguments: list[str]) -> int:
    head = arguments[arguments.index("--head") + 1]
    wanted_state = arguments[arguments.index("--state") + 1].upper()
    fields = arguments[arguments.index("--json") + 1].split(",")
    records = [describe_pull_request(state, number) for number in state["pulls"]]
    matching = [record for record in records if record["headRefName"] == head and record["state"] == wanted_state]

    print(json.dumps([{field: record[field] for field in fields} for record in matching]))
    return 0


def merge_pull_request(state: dict[str, Any], arguments: list[str]) -> int:
    number = arguments[0]
    pull = state["pulls"][number]
    expected_head = arguments[arguments.index("--match-head-commit") + 1]
    head = read_branch_tip(state, pull["head"])

    time.sleep(float(state.get("mergeDelaySeconds", 0)))
    if pull["state"] != "OPEN" or head != expected_head:
        print("Pull request head has changed or the pull request is not open", file=sys.stderr)
        return EXIT_FAILED
    else:
        pull["mergeCommit"] = squash_onto_base(state["remote"], pull["base"], pull["head"])
        pull["mergedHead"] = head
        pull["state"] = "MERGED"
        return 0


def answer_api(state: dict[str, Any], path: str) -> int:
    if path.startswith(REF_PATH):
        print(read_branch_tip(state, path[len(REF_PATH) :]))
        return 0
    elif path.startswith(COMPARE_PATH):
        base, head = path[len(COMPARE_PATH) :].split("...")
        print(run_git(state["remote"], ["rev-list", "--count", f"{head}..{base}"]))
        return 0
    else:
        print(f"github_double: unsupported API path {path}", file=sys.stderr)
        return EXIT_FAILED


def find_pull_number(state: dict[str, Any], selector: str | None) -> str | None:
    branch = selector or run_git(os.getcwd(), ["symbolic-ref", "--short", "HEAD"])
    numbers = [number for number, pull in state["pulls"].items() if number == branch or pull["head"] == branch]

    return numbers[0] if numbers else None


def describe_pull_request(state: dict[str, Any], number: str) -> dict[str, Any]:
    pull = state["pulls"][number]
    head = pull["mergedHead"] if pull["state"] == "MERGED" else read_branch_tip(state, pull["head"])
    checks = state.get("checks", {}).get(head, state.get("defaultChecks", []))
    merge_commit = {"oid": pull["mergeCommit"]} if pull["mergeCommit"] else None

    return {
        "number": int(number),
        "url": URL_PREFIX + number,
        "state": pull["state"],
        "isDraft": pull.get("isDraft", False),
        "mergeable": pull.get("mergeable", "MERGEABLE"),
        "baseRefName": pull["base"],
        "headRefName": pull["head"],
        "headRefOid": head,
        "statusCheckRollup": checks,
        "mergeCommit": merge_commit,
    }


def squash_onto_base(remote: str, base: str, head: str) -> str:
    with tempfile.TemporaryDirectory() as folder:
        run_git(folder, ["clone", "--quiet", "--branch", base, remote, "."])
        run_git(folder, ["merge", "--squash", "--quiet", f"origin/{head}"])
        run_git(folder, ["commit", "--quiet", "--no-verify", "-m", f"Squash {head}"])
        run_git(folder, ["push", "--quiet", "origin", f"HEAD:{base}"])
        return run_git(folder, ["rev-parse", "HEAD"])


def read_branch_tip(state: dict[str, Any], branch: str) -> str:
    return run_git(state["remote"], ["rev-parse", f"refs/heads/{branch}"])


def run_git(folder: str, arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", folder, *arguments],
        capture_output=True,
        text=True,
        encoding=ENCODING,
        check=True,
    )

    return result.stdout.strip()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
