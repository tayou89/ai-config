"""A disposable repository with task worktrees, sessions, and app records for the folder-owner tests.

Each session is an app session (`local_…`) with a Claude Code id, or a
terminal session with a Claude Code id only. The app's session records are
written under a home folder of the world's own, so the hooks read them as
they read the real app's, and the sessions' variables never leak in from
the session running the tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflow_world import BASE, ENCODING, SCRIPTS_DIRECTORY, WorkflowWorld

POLICY = {"mode": "pull-request", "base": BASE, "requiredChecks": ["checks"]}
HOST_SESSION_VARIABLE = "CLAUDE_CODE_HOST_SESSION_ID"
CLI_SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
INDEX_VARIABLE = "GIT_INDEX_FILE"
RECORDS_PATH = Path("Library") / "Application Support" / "Claude" / "claude-code-sessions"
GUARD_SCRIPT = SCRIPTS_DIRECTORY / "worktree-guard-hook.py"
COMMIT_WRAPPER = SCRIPTS_DIRECTORY / "claude-commit.py"
FOLDER_OWNER_SCRIPT = SCRIPTS_DIRECTORY / "folder_owner.py"
OWNER_FILE_NAME = "claude-owner.json"
GAPS_FILE_NAME = "claude-owner-gaps.jsonl"
PASS = "pass"
DENY = "deny"
ASK = "ask"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
SESSION_START = "SessionStart"
COMMIT_MESSAGE = "Fix: keep the task's notes in one place\n\n- one\n- two\n- three"
TOOL_USE_ID = "toolu_test_call"
FOLDER_OWNER_COMMAND = f"python3 {FOLDER_OWNER_SCRIPT}"
SESSION_WRITES_COMMAND = f"python3 {SCRIPTS_DIRECTORY / 'session_writes.py'}"
NOT_OWNER_EXIT = 12
GAPS_EXIT = 13


@dataclass(frozen=True)
class Session:
    """A Claude session: `app` is its desktop-app id, None for a session run in a terminal."""

    app: str | None
    cli: str
    title: str


OWNER = Session("local_owner0001", "cli-owner-0001", "주인 세션")
OTHER = Session("local_other0002", "cli-other-0002", "다른 세션")
THIRD = Session("local_third0003", "cli-third-0003", "세 번째 세션")
FOURTH = Session("local_fourth004", "cli-fourth-004", "네 번째 세션")
TERMINAL = Session(None, "cli-terminal-0005", "")


class OwnerWorld:
    """A pull-request repository with its original checkout, a home folder holding app records, and helpers."""

    def __init__(self) -> None:
        self.world = WorkflowWorld(POLICY)
        self.root = self.world.root
        self.original = self.world.original
        self.home = self.root / "home"
        self.config_directory = self.root / "claude"
        self.config_directory.mkdir()
        (self.home / RECORDS_PATH / "group" / "folder").mkdir(parents=True)
        for session in (OWNER, OTHER, THIRD, FOURTH):
            self.write_app_record(session)

    def close(self) -> None:
        self.world.close()

    def add_worktree(self, name: str) -> Path:
        return self.world.add_worktree(name)

    def git(self, folder: Path, *arguments: str) -> str:
        return self.world.git(folder, *arguments)

    def write(self, folder: Path, name: str, text: str) -> None:
        self.world.write(folder, name, text)

    def git_directory(self, worktree: Path) -> Path:
        return Path(self.git(worktree, "rev-parse", "--absolute-git-dir"))

    def owner_file(self, worktree: Path) -> Path:
        return self.git_directory(worktree) / OWNER_FILE_NAME

    def read_owner(self, worktree: Path) -> dict[str, Any] | None:
        path = self.owner_file(worktree)

        if path.exists():
            return json.loads(path.read_text(encoding=ENCODING))
        else:
            return None

    def read_owner_cli(self, worktree: Path) -> str | None:
        owner = self.read_owner(worktree)

        return owner["owner"]["cliSession"] if owner else None

    def write_owner(self, worktree: Path, session: Session, process: dict[str, Any] | None = None) -> None:
        """An owner record as folder_owner.py writes it, for states the hooks reach only over time."""
        record = {
            "format": 1,
            "owner": {"appSession": session.app, "cliSession": session.cli, "process": process},
            "title": session.title,
            "claimedAt": 1.0,
            "method": "created",
            "previous": None,
        }

        self.owner_file(worktree).write_text(json.dumps(record), encoding=ENCODING)

    def write_app_record(self, session: Session, **fields: Any) -> None:
        if session.app is not None:
            record = {
                "sessionId": session.app,
                "cliSessionId": session.cli,
                "title": session.title,
                "isArchived": False,
                "cwd": str(self.original),
                **fields,
            }
            path = self.home / RECORDS_PATH / "group" / "folder" / f"{session.app}.json"

            path.write_text(json.dumps(record), encoding=ENCODING)

    def archive(self, session: Session) -> None:
        self.write_app_record(session, isArchived=True)

    def environment(self, session: Session | None) -> dict[str, str]:
        removed = (HOST_SESSION_VARIABLE, CLI_SESSION_VARIABLE, INDEX_VARIABLE)
        environment = {name: value for name, value in self.world.environment.items() if name not in removed}

        environment.update({"HOME": str(self.home), CONFIG_DIRECTORY_VARIABLE: str(self.config_directory)})
        if session is not None:
            environment[CLI_SESSION_VARIABLE] = session.cli
        if session is not None and session.app is not None:
            environment[HOST_SESSION_VARIABLE] = session.app
        return environment

    def run_guard(
        self,
        session: Session,
        tool_name: str,
        tool_input: dict[str, Any],
        cwd: Path,
        event: str = PRE_TOOL_USE,
        tool_response: dict[str, Any] | None = None,
        tool_use_id: str = TOOL_USE_ID,
    ) -> subprocess.CompletedProcess[str]:
        payload = {
            "hook_event_name": event,
            "session_id": session.cli,
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "cwd": str(cwd),
        }

        if tool_response is not None:
            payload["tool_response"] = tool_response
        return subprocess.run(
            [sys.executable, str(GUARD_SCRIPT)],
            input=json.dumps(payload),
            env=self.environment(session),
            capture_output=True,
            text=True,
            encoding=ENCODING,
            check=False,
        )

    def decide(self, session: Session, tool_name: str, tool_input: dict[str, Any], cwd: Path) -> str:
        """The guard's PreToolUse decision; a call it lets through runs, so its PostToolUse step runs too."""
        result = self.run_guard(session, tool_name, tool_input, cwd)
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout.strip() else PASS

        if result.stderr:
            raise AssertionError(f"the guard wrote to stderr: {result.stderr}")
        elif decision == DENY:
            return decision
        else:
            self.run_guard(session, tool_name, tool_input, cwd, POST_TOOL_USE, {})
            return decision

    def reason(self, session: Session, tool_name: str, tool_input: dict[str, Any], cwd: Path) -> str:
        result = self.run_guard(session, tool_name, tool_input, cwd)

        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]

    def bash(self, session: Session, command: str, cwd: Path) -> str:
        return self.decide(session, "Bash", {"command": command}, cwd)

    def run_commit(
        self, session: Session | None, worktree: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            cwd=worktree,
            env=self.environment(session),
            capture_output=True,
            text=True,
            encoding=ENCODING,
            timeout=120,
            check=False,
        )

    def run_folder_owner(self, session: Session, cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(FOLDER_OWNER_SCRIPT), *arguments],
            cwd=cwd,
            env=self.environment(session),
            capture_output=True,
            text=True,
            encoding=ENCODING,
            timeout=60,
            check=False,
        )

    def add_worktree_as(self, session: Session, name: str) -> Path:
        """Add a worktree the way a session does, so the guard's step after the command records its owner."""
        path = self.original / ".claude" / "worktrees" / name
        tool_input = {"command": f"git worktree add -q -b {name} {path} origin/{BASE}"}

        self.run_guard(session, "Bash", tool_input, self.original)
        self.git(self.original, "worktree", "add", "-q", "-b", name, str(path), f"origin/{BASE}")
        self.run_guard(session, "Bash", tool_input, self.original, POST_TOOL_USE, {})
        return path
