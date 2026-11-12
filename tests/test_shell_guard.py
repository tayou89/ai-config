#!/usr/bin/env python3
"""Tests for scripts/shell-guard-hook.py, secret_files.py, commit_arguments.py, claude-commit.py,
own_lines.py, session_writes.py, session_commits.py and session-writes-hook.py.

Each case feeds the hook the same JSON that Claude Code sends and checks
its decision: deny, ask, or pass (no output). Fixture repositories and a
fixture shell snapshot are created in a temporary directory, so the tests
run the same on any machine. Run with --table to print every case and
its actual decision as a Markdown table instead of running unittest.
"""

from __future__ import annotations

import contextlib
import difflib
import importlib.util
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
HOOK_SCRIPT = SCRIPTS_DIRECTORY / "shell-guard-hook.py"
COMMIT_WRAPPER = SCRIPTS_DIRECTORY / "claude-commit.py"
TABLE_FLAG = "--table"
DENY = "deny"
ASK = "ask"
PASS = "pass"
BASH_TOOL = "Bash"
MONITOR_TOOL = "Monitor"
TERMINAL_TOOL = "mcp__terminal__run_in_terminal"
SNAPSHOT_TEXT = """# Snapshot file
unalias -a 2>/dev/null || true
alias -- g=git
alias -- ga='git add'
alias -- gaa='git add --all'
alias -- gc='git commit --verbose'
alias -- gcmsg='git commit --message'
alias -- gp='git push'
alias -- gpf='git push --force-with-lease --force-if-includes'
alias -- _='sudo '
ggp () {
\tif [[ $# != 0 ]]
\tthen
\t\tgit push origin "${*}"
\telse
\t\tgit push origin "$(git_current_branch)"
\tfi
}
git_current_branch () {
\tgit symbolic-ref --quiet HEAD 2> /dev/null
}
find () {
\tcommand find ${1+"$@"}
}
"""
PRIVATE_KEY_TEXT = "-----BEGIN PRIVATE KEY-----\nMIIfake\n-----END PRIVATE KEY-----\n"
CERTIFICATE_TEXT = "-----BEGIN CERTIFICATE-----\nMIIfake\n-----END CERTIFICATE-----\n"
NPM_TOKEN_TEXT = "//registry.npmjs.org/:_authToken=npm_fake\n"
COMMIT_MESSAGE = "Fix: stop git push mistakes\n\n- one\n- two\n- three"
WRAPPER_COMMAND = (
    "python3 ~/.claude/scripts/claude-commit.py \"$(cat <<'EOF'\n"
    "Fix: don't git push or add .env (yet)\n\n- one\n- two\n- three\nEOF\n)\" a.txt"
)
DEEP_NESTING = "echo " + "$(" * 400 + "x" + ")" * 400
PRIVATE_INDEX_VARIABLE = "GIT_INDEX_FILE"
PUSH_BRANCH = "main"
PUSH_LOCATIONS = ("pushing", "matching", "guarded", "detached")
SEED_FILES = ("a.txt", "b.txt", "shared.txt")
OTHER_SESSION_SUBJECT = "Feat: land the other session's change"
HEAD_COMMIT_SUBJECT = "Feat: commit this session's change"
HEAD_COMMIT_MESSAGE = f"{HEAD_COMMIT_SUBJECT}\n\n- one\n- two\n- three"
GIT_ERROR_EXIT = 1
STAGING_MISMATCH_EXIT = 3
USAGE_EXIT = 4
HEAD_MOVED_EXIT = 6
CREDENTIAL_EXIT = 5
REVIEW_MISSING_EXIT = 7
LARGE_CHANGE_LINES = 200
FAKE_GITHUB_TOKEN = "gh" + "p_" + "A" * 36
VALIDATION_EXIT = 2
INTERNAL_TERM_FAILURE = "FAIL: internal terms"
SECTION_NUMBER_MESSAGES = (
    "Chore: record the review in HANDOFF 0-16\n\n- one\n- two\n- three",
    "Chore: record the review\n\n- HANDOFF.md 0-14에 기록\n- two\n- three",
)
RANGE_AND_STANDARD_MESSAGE = (
    "Fix: keep 3-5 bullets per message\n\n"
    "- Country codes follow ISO 3166-1\n- Dated 2026-11-09\n- Step-by-step"
)
SPACED_SUBJECT_MESSAGE = "Fix: commit  this session's change\n\n- one\n- two\n- three"
HOOK_COMMIT_SUBJECT = "Chore: commit from a post-commit hook"
POST_COMMIT_HOOK = "post-commit"
POST_COMMIT_HOOK_TEXT = """#!/bin/sh
if [ ! -e "{marker}" ]; then
  touch "{marker}"
  git commit -q --allow-empty -m "{subject}"
fi
"""
PRE_COMMIT_HOOK = "pre-commit"
PRE_COMMIT_HOOK_TEXT = "#!/bin/sh\ngit reset -q --soft HEAD\n"
HOOK_RESET_REFLOG_RE = r"via claude-commit [0-9a-f]+: updating HEAD"
LANDING_VARIABLE = "SHIM_LANDING"
SHIM_TEXT = """#!/bin/sh
# Lands another session's commit around the wrapper's git commit, as {landing_variable} says:
# after the wrapper's HEAD check and before git commit reads HEAD, right after git commit returns, or both.
land_other_commit() {{
  tree=$(env -u GIT_INDEX_FILE "{real_git}" rev-parse "HEAD^{{tree}}")
  other=$(env -u GIT_INDEX_FILE "{real_git}" commit-tree "$tree" -p HEAD -m "{subject}")
  env -u GIT_INDEX_FILE -u GIT_REFLOG_ACTION "{real_git}" update-ref -m "commit: {subject}" HEAD "$other"
}}
if [ "$1" != commit ] || [ -e "{marker}" ]; then
  exec "{real_git}" "$@"
fi
touch "{marker}"
case "${landing_variable}" in *before*) land_other_commit ;; esac
"{real_git}" "$@" || exit
case "${landing_variable}" in *after*) land_other_commit ;; esac
"""

EDIT_TOOL = "Edit"
WRITE_TOOL = "Write"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
POST_TOOL_USE_FAILURE = "PostToolUseFailure"
FOREIGN_LINES_EXIT = 8
AMEND_TARGET_EXIT = 9
REMOTE_MAIN = "refs/remotes/origin/main"
REMOTE_HEAD = "refs/remotes/origin/HEAD"
COMMIT_LIST_SUFFIX = ".commits"
PARALLEL_ROOT = "/repository"
SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
RECORD_DIRECTORY_NAME = "session-writes"
RECORD_HOOK = SCRIPTS_DIRECTORY / "session-writes-hook.py"
SESSION_WRITES_SCRIPT = SCRIPTS_DIRECTORY / "session_writes.py"
RECORD_FORMAT = 2
OWN_LINES_MODULE = SCRIPTS_DIRECTORY / "own_lines.py"
OWN_SESSION = "session-own"
OTHER_SESSION = "session-other"
LOG_FILE = "log.md"
OTHER_FILE = "notes.md"
BINARY_FILE = "image.bin"
BINARY_TEXT = "\0\1binary"
MERGE_MESSAGE = "Merge: bring in the other branch\n\n- one\n- two\n- three"
CRLF_FILE = "windows.txt"
NEW_FOLDER_FILE = "new/page.md"
BROKEN_INDEX_TEXT = "not an index"
TRANSCRIPT_PROJECT = "project"
READ_TOOL = "Read"
HOUR_SECONDS = 60 * 60
PATCH_CONTEXT_LINES = 3
PERSON_TEXT = "typed by a person\n"
CREATE_TYPE = "create"
NO_NEWLINE_MARKER = "\\ No newline at end of file"
RANDOM_SEED = 20261110
RANDOM_TRIALS = 1500
RANDOM_BASE_LINES = 6
RANDOM_BASE_TEXTS = ("a\n", "b\n", "c\n", "d\n", "e\n")
RANDOM_OTHER_CHANCE = 0.6
RANDOM_CONTESTED_CHANCE = 0.3
RANDOM_OPERATIONS = ("insert", "delete", "replace")
RANDOM_REPEATED_TEXTS = ("\n", "a\n", "b\n")
TITLE_TEXT = "# log\n"
EDITED_TITLE_TEXT = "# edited log\n"
NEW_PAGE_TEXT = "new page\n"
PENDING_SUFFIX = ".pending.json"
SCRATCH_REPOSITORY = "scratch"
TRASH_FOLDER = "trash"
MISSING_ROOT = "/missing-repository"
MISSING_BLOB = "0" * 40
VANISHED_RECORD = "another session removed this record file"


@dataclass
class GuardCase:
    group: str
    command: str
    expected: str
    location: str = "plain"
    tool: str = BASH_TOOL
    tab_id: str = ""


CASES = [
    GuardCase("commit", "git commit -m x", DENY),
    GuardCase("commit", "cd {plain} && git commit -m x", DENY),
    GuardCase("commit", "git -C /tmp/r commit -m x", DENY),
    GuardCase("commit", "git -c user.name=a commit -m x", DENY),
    GuardCase("commit", "/usr/bin/git commit -m x", DENY),
    GuardCase("commit", "gc -m x", DENY),
    GuardCase("commit", "gcmsg x", DENY),
    GuardCase("commit", "g commit -m x", DENY),
    GuardCase("commit", "_ git commit -m x", DENY),
    GuardCase("commit", "sh -c 'git commit -m x'", DENY),
    GuardCase("commit", 'bash -lc "git commit -m x"', DENY),
    GuardCase("commit", "eval 'git commit -m x'", DENY),
    GuardCase("commit", "echo x | xargs git commit -m", DENY),
    GuardCase("commit", "G=git; $G commit -m x", DENY),
    GuardCase("commit", "sudo -u me git commit -m x", DENY),
    GuardCase("commit", "f() { git commit -m x; }; f", DENY),
    GuardCase("commit", "git -c alias.ci=commit ci -m x", DENY),
    GuardCase("commit", "git 'commit' -m x", DENY),
    GuardCase("commit", "\\git commit -m x", DENY),
    GuardCase("commit", "bash commit.sh", DENY),
    GuardCase("commit", "./commit.sh", DENY),
    GuardCase("commit", "git commit -m x", DENY, tool=TERMINAL_TOOL),
    GuardCase("commit", WRAPPER_COMMAND, PASS),
    GuardCase("commit", 'F=(a.txt b.txt) && ~/.claude/scripts/claude-commit.py "Fix: x" "${F[@]}"', DENY),
    GuardCase("commit", 'files=$(git status --porcelain | cut -c4-) && ~/.claude/scripts/claude-commit.py "Fix: x" $files', DENY),
    GuardCase("commit", '~/.claude/scripts/claude-commit.py --expect-head $(git rev-parse HEAD) "Fix: x" a.txt', PASS),
    GuardCase("commit", '~/.claude/scripts/claude-commit.py "Fix: x" *.py', DENY),
    GuardCase("own lines", "python3 ~/.claude/scripts/claude-commit.py --own-lines --expect-head 0123abc 'Fix: x' a.txt", PASS),
    GuardCase("own lines", "python3 ~/.claude/scripts/claude-commit.py --include-unrecorded a.txt 'Fix: x' a.txt", ASK),
    GuardCase("own lines", "python3 ~/.claude/scripts/claude-commit.py --own-lines --include-unrecorded .env 'Fix: x' .env", DENY, "secrets"),
    GuardCase("own lines", "python3 ~/.claude/scripts/claude-commit.py --include-unrecorded key.pem 'Fix: x' key.pem a.txt", DENY, "secrets"),
    GuardCase("commit", 'git ls-files -m | xargs ~/.claude/scripts/claude-commit.py "Fix: x"', DENY),
    GuardCase("commit", "git log --grep 'git commit'", PASS),
    GuardCase("commit", 'echo "git commit -m x"', PASS),
    GuardCase("commit", "command -v git", PASS),
    GuardCase("credential", "git add .env", DENY, "secrets"),
    GuardCase("credential", "ga .env", DENY, "secrets"),
    GuardCase("credential", "git add -f .env.local", DENY, "secrets"),
    GuardCase("credential", "git add config/id_rsa", DENY, "secrets"),
    GuardCase("credential", "git add -A", DENY, "secrets"),
    GuardCase("credential", "cd {secrets} && git add .", DENY, "plain"),
    GuardCase("credential", "gaa", DENY, "secrets"),
    GuardCase("credential", "git add key.pem", DENY, "secrets"),
    GuardCase("credential", "git add .npmrc", DENY, "secrets"),
    GuardCase("credential", "git ls-files -o | xargs git add", DENY, "secrets"),
    GuardCase("credential", "python3 ~/.claude/scripts/claude-commit.py 'Fix: x' .env", DENY, "secrets"),
    GuardCase(
        "credential",
        "python3 ~/.claude/scripts/claude-commit.py --expect-head 0123abc 'Fix: x' .env",
        DENY,
        "secrets",
    ),
    GuardCase(
        "commit",
        "python3 ~/.claude/scripts/claude-commit.py --expect-head 0123abc key.pem a.txt",
        PASS,
        "secrets",
    ),
    GuardCase("credential", "git add .", DENY, "secrets", TERMINAL_TOOL),
    GuardCase("credential", "find . -name '.env*' -exec git add {} +", DENY, "secrets"),
    GuardCase("credential", "git add -- .env", DENY, "secrets"),
    GuardCase("credential", "REPO={secrets}; git -C \"$REPO\" add .", DENY, "plain"),
    GuardCase(
        "credential",
        "GIT_INDEX_FILE=/tmp/i ~/.claude/scripts/claude-commit.py 'Fix: x' key.pem",
        DENY,
        "secrets",
    ),
    GuardCase("credential", "git add .env.example", PASS, "secrets"),
    GuardCase("credential", "git add . ':!.env' ':!.npmrc' ':!key.pem' ':!config'", PASS, "secrets"),
    GuardCase("credential", "git add cert.pem", PASS, "secrets"),
    GuardCase("credential", "git add a.txt", PASS, "secrets"),
    GuardCase("credential", "git rm --cached .env", PASS, "secrets"),
    GuardCase("credential", "git add -n .env", PASS, "secrets"),
    GuardCase("credential", "cat .env", PASS, "secrets"),
    GuardCase("credential", "find . -name '*.txt' -exec wc -l {} +", PASS, "secrets"),
    GuardCase("credential", "git add -A", PASS, "ignored"),
    GuardCase("credential", "git add .", PASS, "ignored"),
    GuardCase("push", "git push", PASS, "pushing"),
    GuardCase("push", "gp", PASS, "pushing"),
    GuardCase("push", "git push -u origin main", PASS, "pushing"),
    GuardCase("push", "git push origin HEAD", PASS, "pushing"),
    GuardCase("push", "git push origin main:main", PASS, "pushing"),
    GuardCase("push", "git push -q", PASS, "pushing"),
    GuardCase("push", "cd {pushing} && git push origin main", PASS),
    GuardCase("push", "git -C {pushing} push", PASS),
    GuardCase("push", "sh -c 'git push'", PASS, "pushing"),
    GuardCase("push", "G=git; $G push", PASS, "pushing"),
    GuardCase("push", "echo 'git push' | sh", PASS, "pushing"),
    GuardCase("push", "bash <<EOF\ngit push\nEOF", PASS, "pushing"),
    GuardCase("push", "gp", PASS, "pushing", tool=MONITOR_TOOL),
    GuardCase("push", 'echo "$(git push)"', PASS, "pushing"),
    GuardCase("push", "cat <<EOF\n$(git push)\nEOF", PASS, "pushing"),
    GuardCase("push", "alias p='git push'; p", PASS, "pushing"),
    GuardCase("push", "git push --dry-run --force", PASS, "pushing"),
    GuardCase("push", "cat <<'EOF'\n$(git push)\nEOF", PASS, "pushing"),
    GuardCase("push", "ls # git push", PASS, "pushing"),
    GuardCase("push", "git log origin/main..HEAD", PASS, "pushing"),
    GuardCase("push", 'echo "git push"', PASS, "pushing"),
    GuardCase("push", "git status", PASS, "pushing"),
    GuardCase("push", "gpf", ASK, "pushing"),
    GuardCase("push", "ggp", ASK, "pushing"),
    GuardCase("push", "git push --force origin main", ASK, "pushing"),
    GuardCase("push", "git push origin +main", ASK, "pushing"),
    GuardCase("push", "git push origin :old-branch", ASK, "pushing"),
    GuardCase("push", "git push --delete origin old-branch", ASK, "pushing"),
    GuardCase("push", "git push --mirror", ASK, "pushing"),
    GuardCase("push", "git push --all", ASK, "pushing"),
    GuardCase("push", "git push --tags", ASK, "pushing"),
    GuardCase("push", "git push origin v1.0", ASK, "pushing"),
    GuardCase("push", "git push upstream main", ASK, "pushing"),
    GuardCase("push", "git push https://example.com/x.git main", ASK, "pushing"),
    GuardCase("push", "git push --no-verify", ASK, "pushing"),
    GuardCase("push", "git push -o ci.skip", ASK, "pushing"),
    GuardCase("push", "git -c push.default=matching push", ASK, "pushing"),
    GuardCase("push", "git push", ASK, "matching"),
    GuardCase("push", "git push", ASK, "guarded"),
    GuardCase("push", "git push origin HEAD", ASK, "detached"),
    GuardCase("push", "$(which git) push", ASK, "pushing"),
    GuardCase("push", "for r in a b; do git -C $r push; done", ASK, "pushing"),
    GuardCase("push", "git push", ASK, "pushing", tool=TERMINAL_TOOL, tab_id="1"),
    GuardCase("push", "git push -fu origin main", ASK, "pushing"),
    GuardCase("push", "git push --force-with-lease=main:abc", ASK, "pushing"),
    GuardCase("push", "git push --repo=other", ASK, "pushing"),
    GuardCase("push", "git push origin HEAD:refs/heads/other", ASK, "pushing"),
    GuardCase("push", "git push origin refs/tags/v1", ASK, "pushing"),
    GuardCase("push", "git push -o -n --force origin main", ASK, "pushing"),
    GuardCase("push", "git push -vn --no-dry-run origin main:other", ASK, "pushing"),
    GuardCase("push", "git push --no-dry-run -n --force", PASS, "pushing"),
    GuardCase("push", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=push.default GIT_CONFIG_VALUE_0=matching git push", ASK, "pushing"),
    GuardCase("push", "env GIT_DIR=../x/.git git push", ASK, "pushing"),
    GuardCase("push", "export GIT_WORK_TREE=/x; git push", ASK, "pushing"),
    GuardCase("push", "git -c push.followTags=true push", ASK, "pushing"),
    GuardCase("push", "git -c shellGuard.askBeforePush=2 push", ASK, "pushing"),
    GuardCase("push", "git -c push.default=upstream -c branch.main.merge=refs/heads/other push", ASK, "pushing"),
    GuardCase("push", "git -c push.default=upstream push", PASS, "pushing"),
    GuardCase("push", "git -c branch.main.pushRemote=upstream push upstream main", PASS, "pushing"),
    GuardCase("push", "git -c remote.pushDefault=upstream push origin main", ASK, "pushing"),
    GuardCase("push", "git -c remote.origin.mirror=true push", ASK, "pushing"),
    GuardCase("push", "git -c remote.origin.push=refs/heads/*:refs/heads/* push", ASK, "pushing"),
    GuardCase("push", "git push -- origin main", PASS, "pushing"),
    GuardCase("production", "NODE_ENV=production node server.js", ASK),
    GuardCase("production", "export NODE_ENV=production && npm start", ASK),
    GuardCase("production", "env RAILS_ENV=production rails server", ASK),
    GuardCase("production", "rails server -e production", ASK),
    GuardCase("production", "npm run start:prod", ASK),
    GuardCase("production", "pm2 start app.js --env production", ASK),
    GuardCase("production", "docker compose -f docker-compose.prod.yml up -d", ASK),
    GuardCase("production", "docker run -e NODE_ENV=production my-image", ASK),
    GuardCase("production", "java -Dspring.profiles.active=prod -jar app.jar", ASK),
    GuardCase("production", "ASPNETCORE_ENVIRONMENT=Production dotnet run", ASK),
    GuardCase("production", "vercel --prod", ASK),
    GuardCase("production", "sh -c 'NODE_ENV=production node app.js'", ASK),
    GuardCase("production", "python3 manage.py runserver --settings=mysite.settings.production", ASK),
    GuardCase("production", "make deploy-prod", ASK),
    GuardCase("production", "NODE_ENV=preprod node server.js", ASK),
    GuardCase("production", "next build && NODE_ENV=production next start", ASK),
    GuardCase("production", "NODE_ENV=nonprod node server.js", PASS),
    GuardCase("production", "NODE_ENV=production npm run build", PASS),
    GuardCase("production", "vite build --mode production", PASS),
    GuardCase("production", "npm install --production", PASS),
    GuardCase("production", "NODE_ENV=development npm start", PASS),
    GuardCase("production", "grep -r NODE_ENV=production .", PASS),
    GuardCase("production", "cat .env.production", PASS),
    GuardCase("production", "npm test", PASS),
    GuardCase("combined", "git add .env && git push", DENY, "secrets"),
    GuardCase("combined", DEEP_NESTING, ASK),
]


class GuardFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_directory = self.root / "config"
        self.git_config = self.root / "gitconfig"
        self.locations = {
            "plain": self.root / "plain",
            "secrets": self.root / "secrets",
            "ignored": self.root / "ignored",
            "pushing": self.root / "pushing",
            "matching": self.root / "matching",
            "guarded": self.root / "guarded",
            "detached": self.root / "detached",
        }

    def create(self) -> None:
        snapshot_directory = self.config_directory / "shell-snapshots"

        snapshot_directory.mkdir(parents=True)
        (snapshot_directory / "snapshot-zsh-test.sh").write_text(SNAPSHOT_TEXT)
        self.git_config.write_text("[user]\n\tname = Guard Test\n\temail = guard@example.com\n")
        for location in self.locations.values():
            location.mkdir()
            self.run_git(location, "init", "-q", "-b", PUSH_BRANCH)
            (location / "a.txt").write_text("a\n")
        for name in PUSH_LOCATIONS:
            self.create_push_repository(self.locations[name])
        self.run_git(self.locations["matching"], "config", "push.default", "matching")
        self.run_git(self.locations["guarded"], "config", "shellGuard.askBeforePush", "true")
        self.run_git(self.locations["detached"], "add", "a.txt")
        self.run_git(self.locations["detached"], "commit", "-q", "-m", "Chore: seed")
        self.run_git(self.locations["detached"], "checkout", "-q", "--detach")
        self.create_secret_files(self.locations["secrets"])
        self.create_secret_files(self.locations["ignored"])
        (self.locations["ignored"] / ".gitignore").write_text(".env\n.npmrc\nkey.pem\nconfig/\n")
        (self.locations["plain"] / "commit.sh").write_text("#!/bin/sh\ngit commit -m scripted\n")
        (self.locations["plain"] / "commit.sh").chmod(0o755)

    def create_push_repository(self, location: Path) -> None:
        self.run_git(location, "remote", "add", "origin", str(self.root / "remote.git"))
        self.run_git(location, "remote", "add", "upstream", str(self.root / "upstream.git"))
        self.run_git(location, "config", f"branch.{PUSH_BRANCH}.remote", "origin")
        self.run_git(location, "config", f"branch.{PUSH_BRANCH}.merge", f"refs/heads/{PUSH_BRANCH}")

    def create_secret_files(self, location: Path) -> None:
        (location / ".env").write_text("TOKEN=fake\n")
        (location / ".env.example").write_text("TOKEN=\n")
        (location / "key.pem").write_text(PRIVATE_KEY_TEXT)
        (location / "cert.pem").write_text(CERTIFICATE_TEXT)
        (location / ".npmrc").write_text(NPM_TOKEN_TEXT)
        (location / "config").mkdir()
        (location / "config" / "id_rsa").write_text(PRIVATE_KEY_TEXT)

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)

        environment.pop(SESSION_VARIABLE, None)
        environment.update({
            "CLAUDE_CONFIG_DIR": str(self.config_directory),
            "GIT_CONFIG_GLOBAL": str(self.git_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        })
        return environment

    def run_git(self, location: Path, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(location), *arguments],
            env=self.environment(),
            capture_output=True,
            text=True,
            check=True,
        )

    def run_hook(self, case: GuardCase) -> tuple[str, str]:
        cwd = str(self.locations[case.location])
        command = case.command

        for name, path in self.locations.items():
            command = command.replace(f"{{{name}}}", str(path))
        tool_input = {"command": command, "description": "test", **({"tab_id": case.tab_id} if case.tab_id else {})}
        payload = {"tool_name": case.tool, "tool_input": tool_input, "cwd": cwd}
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            env=self.environment(),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return f"error {result.returncode}", result.stderr.strip()
        elif result.stdout.strip():
            output = json.loads(result.stdout)["hookSpecificOutput"]
            return output["permissionDecision"], output["permissionDecisionReason"]
        else:
            return PASS, ""

    def run_wrapper(self, location: Path, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            env=self.environment(),
            cwd=location,
            capture_output=True,
            text=True,
            check=False,
        )


class ShellGuardHookTest(unittest.TestCase):
    fixture: GuardFixture

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = GuardFixture()
        cls.fixture.create()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.directory.cleanup()

    def test_cases(self) -> None:
        for case in CASES:
            with self.subTest(group=case.group, command=case.command, tool=case.tool):
                decision, reason = self.fixture.run_hook(case)
                self.assertEqual(decision, case.expected, reason)

    def test_wrapper_refuses_staged_credential_file(self) -> None:
        location = self.fixture.locations["secrets"]

        self.fixture.run_git(location, "add", "-f", ".env")
        result = self.fixture.run_wrapper(location, "--no-stage", COMMIT_MESSAGE)
        self.fixture.run_git(location, "reset", "-q")
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn(".env (environment file)", result.stderr)

    def test_wrapper_commits_clean_files(self) -> None:
        location = self.fixture.locations["plain"]
        result = self.fixture.run_wrapper(location, COMMIT_MESSAGE, "a.txt")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No credential files", result.stdout)

    def test_wrapper_refuses_document_section_number(self) -> None:
        location = self.fixture.locations["plain"]

        for message in SECTION_NUMBER_MESSAGES:
            with self.subTest(message=message):
                result = self.fixture.run_wrapper(location, "--no-stage", message)
                self.assertEqual(result.returncode, VALIDATION_EXIT, result.stderr)
                self.assertIn(INTERNAL_TERM_FAILURE, result.stderr)

    def test_wrapper_allows_ranges_dates_and_standards(self) -> None:
        location = self.fixture.locations["plain"]
        result = self.fixture.run_wrapper(location, "--no-stage", RANGE_AND_STANDARD_MESSAGE)

        self.assertNotEqual(result.returncode, VALIDATION_EXIT, result.stderr)
        self.assertNotIn(INTERNAL_TERM_FAILURE, result.stderr)


class Landing(str, Enum):
    """When the git shim lands another session's commit around the wrapper's git commit."""

    BEFORE_COMMIT = "before"
    AFTER_COMMIT = "after"
    AROUND_COMMIT = "before after"


class WrapperHeadFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repository = self.root / "repository"
        self.private_index = self.root / "private-index"
        self.other_index = self.root / "other-index"
        self.shim_directory = self.root / "bin"
        self.git_config = self.root / "gitconfig"
        self.config_directory = self.root / "config"

    def create(self) -> None:
        self.git_config.write_text("[user]\n\tname = Wrapper Test\n\temail = wrapper@example.com\n")
        self.repository.mkdir()
        self.run_git("init", "-q")
        for path in SEED_FILES:
            (self.repository / path).write_text(f"{path}\n")
        self.run_git("add", *SEED_FILES)
        seed = self.run_git("commit-tree", self.run_git("write-tree"), "-m", "Chore: seed")
        self.run_git("update-ref", "HEAD", seed)
        self.create_shim()

    def create_shim(self) -> None:
        shim = self.shim_directory / "git"
        shim_text = SHIM_TEXT.format(
            marker=self.root / "shim-fired",
            real_git=shutil.which("git"),
            subject=OTHER_SESSION_SUBJECT,
            landing_variable=LANDING_VARIABLE,
        )

        self.shim_directory.mkdir()
        shim.write_text(shim_text)
        shim.chmod(0o755)

    def add_post_commit_hook(self) -> None:
        hook_text = POST_COMMIT_HOOK_TEXT.format(marker=self.root / "hook-fired", subject=HOOK_COMMIT_SUBJECT)

        self.add_hook(POST_COMMIT_HOOK, hook_text)

    def add_hook(self, name: str, hook_text: str) -> None:
        hook = self.repository / ".git" / "hooks" / name

        hook.write_text(hook_text)
        hook.chmod(0o755)

    def environment(
        self, index: Path | None = None, landing: Landing | None = None, session: str | None = None
    ) -> dict[str, str]:
        environment = dict(os.environ)
        settings = {
            "GIT_CONFIG_GLOBAL": str(self.git_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            CONFIG_DIRECTORY_VARIABLE: str(self.config_directory),
        }

        environment.pop(PRIVATE_INDEX_VARIABLE, None)
        environment.pop(SESSION_VARIABLE, None)
        environment.update(settings)
        if index is not None:
            environment[PRIVATE_INDEX_VARIABLE] = str(index)
        if landing is not None:
            environment["PATH"] = f"{self.shim_directory}{os.pathsep}{environment['PATH']}"
            environment[LANDING_VARIABLE] = landing.value
        if session is not None:
            environment[SESSION_VARIABLE] = session
        return environment

    def run_git(self, *arguments: str, index: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *arguments],
            env=self.environment(index),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout.strip()

    def run_wrapper(
        self, *arguments: str, landing: Landing | None = None, session: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            env=self.environment(self.private_index, landing, session),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )

    def read_head(self) -> str:
        return self.run_git("rev-parse", "HEAD")

    def read_subject(self, revision: str) -> str:
        return self.run_git("log", "-1", "--format=%s", revision)

    def edit(self, path: str, text: str) -> None:
        with (self.repository / path).open("a") as file:
            file.write(text)

    def read_listed_commits(self, session: str) -> list[str]:
        commit_list = self.config_directory / RECORD_DIRECTORY_NAME / f"{session}{COMMIT_LIST_SUFFIX}"

        return [json.loads(line)["commit"] for line in commit_list.read_text().splitlines()]

    def build_private_index(self, *paths: str) -> str:
        base = self.read_head()

        self.run_git("read-tree", base, index=self.private_index)
        self.run_git("update-index", "--add", "--", *paths, index=self.private_index)
        return base

    def commit_other_session(self, path: str) -> str:
        parent = self.read_head()

        self.edit(path, "the other session's line\n")
        self.run_git("read-tree", parent, index=self.other_index)
        self.run_git("update-index", "--add", "--", path, index=self.other_index)
        tree = self.run_git("write-tree", index=self.other_index)
        commit = self.run_git("commit-tree", tree, "-p", parent, "-m", OTHER_SESSION_SUBJECT)
        self.run_git("update-ref", "HEAD", commit, parent)
        return commit


class CommitWrapperHeadTest(unittest.TestCase):
    fixture: WrapperHeadFixture

    def setUp(self) -> None:
        self.fixture = WrapperHeadFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_private_index_requires_expected_head(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, USAGE_EXIT, result.stderr)
        self.assertIn("needs --expect-head", result.stderr)

    def test_expected_head_takes_a_sha(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--expect-head", "HEAD", "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, USAGE_EXIT, result.stderr)

    def test_stale_private_index_is_refused(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        other = self.fixture.commit_other_session("a.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_head(), other)
        self.assertIn(OTHER_SESSION_SUBJECT, result.stderr)
        self.assertIn("also commit: none", result.stderr)

    def test_stale_private_index_names_shared_file(self) -> None:
        self.fixture.edit("shared.txt", "mine\n")
        base = self.fixture.build_private_index("shared.txt")
        other = self.fixture.commit_other_session("shared.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "shared.txt")

        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_head(), other)
        self.assertIn("also commit: shared.txt", result.stderr)

    def test_current_private_index_commits_listed_files(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)
        self.assertEqual(self.fixture.run_git("diff", "--name-only", "HEAD^", "HEAD"), "b.txt")

    def test_no_stage_still_checks_listed_files(self) -> None:
        self.fixture.edit("a.txt", "mine\n")
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("a.txt", "b.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, STAGING_MISMATCH_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_head(), base)

    def test_head_moving_as_commit_starts_is_undone(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        arguments = ["--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*arguments, landing=Landing.BEFORE_COMMIT)

        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)
        self.assertIn("was undone", result.stderr)

    def test_commit_landing_right_after_mine_is_kept(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        arguments = ["--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*arguments, landing=Landing.AFTER_COMMIT, session=OWN_SESSION)
        mine = self.fixture.run_git("rev-parse", "HEAD^")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.read_subject(mine), HEAD_COMMIT_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^^"), base)
        self.assertEqual(self.fixture.read_listed_commits(OWN_SESSION), [mine])

    def test_my_moved_commit_under_another_is_left_in_place(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        arguments = ["--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*arguments, landing=Landing.AROUND_COMMIT)

        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.read_subject("HEAD^"), HEAD_COMMIT_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^^^"), base)
        self.assertIn("was not undone", result.stderr)

    def test_commit_by_a_post_commit_hook_is_not_taken_for_mine(self) -> None:
        self.fixture.add_post_commit_hook()
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        arguments = ["--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*arguments, session=OWN_SESSION)
        mine = self.fixture.run_git("rev-parse", "HEAD^")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), HOOK_COMMIT_SUBJECT)
        self.assertEqual(self.fixture.read_subject(mine), HEAD_COMMIT_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^^"), base)
        self.assertEqual(self.fixture.read_listed_commits(OWN_SESSION), [mine])

    def test_head_entry_from_a_hook_command_is_not_taken_for_my_commit(self) -> None:
        self.fixture.add_hook(PRE_COMMIT_HOOK, PRE_COMMIT_HOOK_TEXT)
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        arguments = ["--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*arguments, session=OWN_SESSION)
        mine = self.fixture.read_head()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(self.fixture.run_git("log", "--walk-reflogs", "--format=%gs", "HEAD"), HOOK_RESET_REFLOG_RE)
        self.assertEqual(self.fixture.read_subject(mine), HEAD_COMMIT_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)
        self.assertEqual(self.fixture.read_listed_commits(OWN_SESSION), [mine])

    def test_subject_with_a_run_of_spaces_is_found(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        arguments = ["--expect-head", base, "--no-stage", SPACED_SUBJECT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*arguments, session=OWN_SESSION)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_listed_commits(OWN_SESSION), [self.fixture.read_head()])

    def test_commit_without_a_reflog_is_reported_and_left_in_place(self) -> None:
        self.fixture.run_git("config", "core.logAllRefUpdates", "false")
        shutil.rmtree(self.fixture.repository / ".git" / "logs")
        self.fixture.edit("b.txt", "mine\n")
        base = self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, GIT_ERROR_EXIT, result.stderr)
        self.assertIn("no HEAD reflog entry carries its mark", result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), HEAD_COMMIT_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)

    def test_added_token_is_refused_without_showing_it(self) -> None:
        self.fixture.edit("b.txt", f"key = {FAKE_GITHUB_TOKEN}\n")
        base = self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, CREDENTIAL_EXIT, result.stderr)
        self.assertIn("b.txt: GitHub token (ghp_AA…)", result.stderr)
        self.assertNotIn(FAKE_GITHUB_TOKEN, result.stderr)
        self.assertEqual(self.fixture.read_head(), base)

    def test_token_placeholder_passes(self) -> None:
        self.fixture.edit("b.txt", "key = ghp_xxxx (set GITHUB_TOKEN)\n")
        base = self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_large_code_change_needs_review(self) -> None:
        self.fixture.edit("tool.py", "value = 1\n" * LARGE_CHANGE_LINES)
        base = self.fixture.build_private_index("tool.py")
        refused = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "tool.py")
        head_after_refusal = self.fixture.read_head()
        reviewed_arguments = ["--expect-head", base, "--no-stage", "--reviewed", HEAD_COMMIT_MESSAGE, "tool.py"]
        reviewed = self.fixture.run_wrapper(*reviewed_arguments)

        self.assertEqual(refused.returncode, REVIEW_MISSING_EXIT, refused.stderr)
        self.assertIn("checklist review", refused.stderr)
        self.assertEqual(head_after_refusal, base)
        self.assertEqual(reviewed.returncode, 0, reviewed.stdout + reviewed.stderr)

    def test_change_below_the_limit_needs_no_review(self) -> None:
        self.fixture.edit("tool.py", "value = 1\n" * (LARGE_CHANGE_LINES - 1))
        base = self.fixture.build_private_index("tool.py")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "tool.py")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lock_file_change_needs_no_review(self) -> None:
        self.fixture.edit("go.sum", "module v1.0.0 h1:x\n" * LARGE_CHANGE_LINES)
        base = self.fixture.build_private_index("go.sum")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "go.sum")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_token_on_a_line_starting_with_plus_signs_is_refused(self) -> None:
        self.fixture.edit("notes.txt", f"++ {FAKE_GITHUB_TOKEN}\n")
        base = self.fixture.build_private_index("notes.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "notes.txt")

        self.assertEqual(result.returncode, CREDENTIAL_EXIT, result.stderr)
        self.assertIn("notes.txt: GitHub token", result.stderr)

    def test_slug_that_looks_like_a_key_passes(self) -> None:
        self.fixture.edit("b.txt", "name = sk-user-management-service-configuration-x\n")
        base = self.fixture.build_private_index("b.txt")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_large_document_change_needs_no_review(self) -> None:
        self.fixture.edit("notes.md", "line\n" * LARGE_CHANGE_LINES)
        base = self.fixture.build_private_index("notes.md")
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "notes.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_amend_as_head_moves_is_undone(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        first_base = self.fixture.build_private_index("b.txt")
        first_arguments = ["--expect-head", first_base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        first = self.fixture.run_wrapper(*first_arguments, session=OWN_SESSION)
        self.fixture.edit("b.txt", "more of mine\n")
        base = self.fixture.build_private_index("b.txt")
        amend_arguments = ["--amend", "--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*amend_arguments, landing=Landing.BEFORE_COMMIT, session=OWN_SESSION)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)
        self.assertEqual(self.fixture.read_listed_commits(OWN_SESSION), [base])

    def test_commit_landing_right_after_my_amend_is_kept(self) -> None:
        self.fixture.edit("b.txt", "mine\n")
        first_base = self.fixture.build_private_index("b.txt")
        first_arguments = ["--expect-head", first_base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        first = self.fixture.run_wrapper(*first_arguments, session=OWN_SESSION)
        self.fixture.edit("b.txt", "more of mine\n")
        base = self.fixture.build_private_index("b.txt")
        amend_arguments = ["--amend", "--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*amend_arguments, landing=Landing.AFTER_COMMIT, session=OWN_SESSION)
        amended = self.fixture.run_git("rev-parse", "HEAD^")

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^^"), first_base)
        self.assertEqual(self.fixture.read_listed_commits(OWN_SESSION), [base, amended])


@dataclass(frozen=True)
class OwnLinesCase:
    name: str
    first_before: str
    writes: tuple[tuple[str, str], ...]
    working_tree: str
    base: str
    expected: str
    entangled: tuple[str, ...] = ()
    contested_writes: tuple[int, ...] = ()
    expected_contested: tuple[tuple[str, str], ...] = ()


LOG_BASE = "# log\n\n## base\ntext\n"
MINE_1 = "\n## mine 1\nmy text\n"
THEIRS = "\n## theirs\ntheir text\n"
MINE_2 = "\n## mine 2\nmore\n"
MINE_3 = "\n## mine 3\nlast\n"
MY_TEXT = "my text\n"
MY_EDITED_TEXT = "my edited text\n"
BASE_TEXT = "\ntext\n"
DRAFT_TEXT = "\ndraft text\n"
EDITED_TEXT = "\nedited text\n"
LOG_TITLE = "# log\n"
THEIR_TITLE = "# their log\n"
BASE_HEADING = "\n## base\n"
FIRST_NOTE = "first note\n"
REWORDED_NOTE = "first note, reworded\n"
SECOND_NOTE = "second note\n"
OWN_LINES_CASES = (
    OwnLinesCase(
        "another session's section between two of mine",
        LOG_BASE,
        ((LOG_BASE, LOG_BASE + MINE_1), (LOG_BASE + MINE_1 + THEIRS, LOG_BASE + MINE_1 + THEIRS + MINE_2)),
        LOG_BASE + MINE_1 + THEIRS + MINE_2,
        LOG_BASE,
        LOG_BASE + MINE_1 + MINE_2,
    ),
    OwnLinesCase(
        "that section committed before mine",
        LOG_BASE,
        ((LOG_BASE, LOG_BASE + MINE_1), (LOG_BASE + MINE_1 + THEIRS, LOG_BASE + MINE_1 + THEIRS + MINE_2)),
        LOG_BASE + MINE_1 + THEIRS + MINE_2,
        LOG_BASE + THEIRS,
        LOG_BASE + MINE_1 + THEIRS + MINE_2,
    ),
    OwnLinesCase(
        "another session's later edit of one line",
        "a\nrow old\nb\nstep old\nc\n",
        (("a\nrow old\nb\nstep old\nc\n", "a\nrow new\nb\nstep old\nc\n"),),
        "a\nrow new\nb\nstep new\nc\n",
        "a\nrow old\nb\nstep old\nc\n",
        "a\nrow new\nb\nstep old\nc\n",
    ),
    OwnLinesCase(
        "their deletion restored",
        "a\nb\nc\n",
        (("a\nb\nc\n", "a\nb\nc\nmine\n"),),
        "a\nc\nmine\n",
        "a\nb\nc\n",
        "a\nb\nc\nmine\n",
    ),
    OwnLinesCase(
        "my deletion kept, their insert dropped",
        "a\nb\nc\n",
        (("a\nb\nc\n", "a\nc\n"), ("a\nc\ntheirs\n", "a\nc\ntheirs\nmine\n")),
        "a\nc\ntheirs\nmine\n",
        "a\nb\nc\n",
        "a\nc\nmine\n",
    ),
    OwnLinesCase(
        "dirty before my first write",
        "a\ntheirs\nb\n",
        (("a\ntheirs\nb\n", "a\ntheirs\nb\nmine\n"),),
        "a\ntheirs\nb\nmine\n",
        "a\nb\n",
        "a\nb\nmine\n",
    ),
    OwnLinesCase(
        "my committed line not repeated",
        "a\nb\n",
        (("a\nb\n", "a\nm1\nb\n"), ("a\nm1\nb\n", "a\nm1\nb\n\nm2\n")),
        "a\nm1\nb\n\nm2\n",
        "a\nm1\nb\n",
        "a\nm1\nb\n\nm2\n",
    ),
    OwnLinesCase(
        "my line between base lines",
        "a\nb\nc\n",
        (("a\nb\nc\n", "a\nmine\nb\nc\n"),),
        "a\nmine\nb\ntheirs\nc\n",
        "a\nb\nc\n",
        "a\nmine\nb\nc\n",
    ),
    OwnLinesCase(
        "my deletion of a line they committed",
        "a\nb\n",
        (("a\nx\nb\n", "a\nb\nmine\n"),),
        "a\nb\nmine\n",
        "a\nx\nb\n",
        "a\nb\nmine\n",
    ),
    OwnLinesCase(
        "their deletion beside my insert",
        "a\nb\nc\n",
        (("a\nb\nc\n", "a\nm\nb\nc\n"),),
        "a\nm\nc\n",
        "a\nb\nc\n",
        "a\nm\nb\nc\n",
    ),
    OwnLinesCase(
        "overlapping own commands",
        "a\n",
        (("a\n", "a\nfrom-a\n"), ("a\n", "a\nfrom-a\nfrom-b\n")),
        "a\nfrom-a\nfrom-b\n",
        "a\n",
        "a\nfrom-a\nfrom-b\n",
    ),
    OwnLinesCase("new file", "", (("", "x\ny\n"),), "x\ny\n", "", "x\ny\n"),
    OwnLinesCase(
        "lines written while another session also wrote are asked about",
        "a\n",
        (("a\n", "a\nmine\ntheirs\n"),),
        "a\nmine\ntheirs\n",
        "a\n",
        "a\n",
        contested_writes=(0,),
        expected_contested=(("+", "mine\n"), ("+", "theirs\n")),
    ),
    OwnLinesCase(
        "my line inside their block",
        "a\n",
        (("a\nX1\nX2\n", "a\nX1\nmine\nX2\n"),),
        "a\nX1\nmine\nX2\n",
        "a\n",
        "a\nmine\n",
        entangled=("mine\n",),
    ),
    OwnLinesCase(
        "my edit of their uncommitted line",
        "a\nL\n",
        (("a\nL2\n", "a\nL3\n"),),
        "a\nL3\n",
        "a\nL\n",
        "a\nL\nL3\n",
        entangled=("L3\n",),
    ),
    OwnLinesCase(
        "my edit of a line dirty before my first write",
        "a\nX\n",
        (("a\nX\n", "a\nX2\n"),),
        "a\nX2\n",
        "a\n",
        "a\nX2\n",
        entangled=("X2\n",),
    ),
    OwnLinesCase("my edit of a committed line", "a\nL\n", (("a\nL\n", "a\nL2\n"),), "a\nL2\n", "a\nL\n", "a\nL2\n"),
    OwnLinesCase(
        "three sessions taking turns appending",
        "a\n",
        (("a\n", "a\nm1\n"), ("a\nm1\nt1\n", "a\nm1\nt1\nm2\n"), ("a\nm1\nt1\nm2\nt2\n", "a\nm1\nt1\nm2\nt2\nm3\n")),
        "a\nm1\nt1\nm2\nt2\nm3\n",
        "a\n",
        "a\nm1\nm2\nm3\n",
    ),
    OwnLinesCase(
        "my committed line edited again",
        "a\nb\n",
        (("a\nb\n", "a\nR\nb\n"), ("a\nR\nb\n", "a\nR2\nb\n")),
        "a\nR2\nb\n",
        "a\nR\nb\n",
        "a\nR2\nb\n",
    ),
    OwnLinesCase(
        "my committed line deleted again",
        "a\nb\n",
        (("a\nb\n", "a\nR\nb\n"), ("a\nR\nb\n", "a\nb\n")),
        "a\nb\n",
        "a\nR\nb\n",
        "a\nb\n",
    ),
    OwnLinesCase(
        "my edit after restoring the committed file",
        "a\nN\nb\n",
        (("a\nN\nb\n", "a\nO\nb\n"), ("a\nO\nb\n", "a\nN2\nb\n")),
        "a\nN2\nb\n",
        "a\nO\nb\n",
        "a\nN2\nb\n",
    ),
    OwnLinesCase(
        "their deletion of my committed line restored",
        "a\nb\n",
        (("a\nb\n", "a\nR\nb\n"),),
        "a\nb\n",
        "a\nR\nb\n",
        "a\nR\nb\n",
    ),
    OwnLinesCase(
        "my committed edit of a base line, then more",
        "a\nL\nb\n",
        (("a\nL\nb\n", "a\nL2\nb\n"), ("a\nL2\nb\n", "a\nL2\nb\nM\n")),
        "a\nL2\nb\nM\n",
        "a\nL2\nb\n",
        "a\nL2\nb\nM\n",
    ),
    OwnLinesCase(
        "a contested replacement is asked about, not halved",
        "a\nb\nc\n",
        (("a\nb\nc\n", "a\nB\nc\n"),),
        "a\nB\nc\n",
        "a\nb\nc\n",
        "a\nb\nc\n",
        contested_writes=(0,),
        expected_contested=(("-", "b\n"), ("+", "B\n")),
    ),
    OwnLinesCase(
        "a contested line I deleted later",
        "a\n",
        (("a\n", "a\nX\n"), ("a\nX\n", "a\n")),
        "a\n",
        "a\n",
        "a\n",
        contested_writes=(0,),
    ),
    OwnLinesCase(
        "my edit of a contested line",
        "a\n",
        (("a\n", "a\nX\n"), ("a\nX\n", "a\nY\n")),
        "a\nY\n",
        "a\n",
        "a\nY\n",
        entangled=("Y\n",),
        contested_writes=(0,),
    ),
    OwnLinesCase(
        "my replacement replaced again by them",
        "a\nb\n",
        (("a\nb\n", "a2\nb\n"),),
        "a3\nb\n",
        "a\nb\n",
        "b\n",
        entangled=("a\n",),
    ),
    OwnLinesCase(
        "my replacement replaced again by me",
        "a\nb\n",
        (("a\nb\n", "a2\nb\n"), ("a2\nb\n", "a3\nb\n")),
        "a3\nb\n",
        "a\nb\n",
        "a3\nb\n",
    ),
    OwnLinesCase(
        "my removal hidden by their re-added line is asked about",
        "a\nb\nc\n",
        (("a\nb\nc\n", ""),),
        "a\n",
        "a\nb\nc\n",
        "",
        expected_contested=(("-", "a\n"),),
    ),
    OwnLinesCase(
        "my removal written again by them between my writes is asked about",
        "a\nb\n",
        (("a\nb\n", "a\n"), ("a\nb\n", "a\nb\nc\n")),
        "a\nb\nc\n",
        "a\nb\n",
        "a\nc\n",
        expected_contested=(("+", "b\n"), ("-", "b\n")),
    ),
    OwnLinesCase(
        "a base that two versions of different lines have is asked about",
        "\nb\n",
        (("\nb\n", "b\n"), ("", "b\n")),
        "b\n",
        "b\n",
        "b\nb\n",
        expected_contested=(("-", "b\n"),),
    ),
    OwnLinesCase(
        "my removal of their committed line out of order is asked about",
        "n1\nn2\nn3\n",
        (("n1\nn2\nn3\n", "n1\nn2\nn3\nn4\n"), ("n6\nn2\nn5\nn7\n", "n2\nn5\nn7\n")),
        "n2\nn5\nn7\n",
        "n6\nn1\nn2\nn5\nn7\n",
        "n6\nn1\nn2\nn5\nn7\n",
        expected_contested=(("-", "n6\n"),),
    ),
    OwnLinesCase(
        "my committed line in a base that is no version is not added twice",
        "a\n",
        (("a\n", "a\nX\n"),),
        "a\nX\n",
        "a\nX\nQ\n",
        "a\nX\nQ\n",
    ),
    OwnLinesCase(
        "my removal of my committed line in a base that is no version",
        "a\n",
        (("a\n", "a\nX\n"), ("a\nX\n", "a\n")),
        "a\n",
        "a\nX\nQ\n",
        "a\nQ\n",
    ),
    OwnLinesCase(
        "my replacement of the first of two same lines",
        "b\nb\n",
        (("b\nb\n", "X\nb\n"),),
        "X\nb\n",
        "b\nb\n",
        "X\nb\n",
    ),
    OwnLinesCase(
        "my removal of one blank line among blank lines, while they add below",
        "a\n\nb\n\nc\n",
        (("a\n\nb\n\nc\n", "a\n\nb\nc\n"),),
        "a\n\nb\nc\n\nd\n",
        "a\n\nb\n\nc\n",
        "a\n\nb\nc\n",
    ),
    OwnLinesCase(
        "contested removals hidden by a contested re-added line are asked about",
        "a\nb\nc\n",
        (("a\nb\nc\n", ""), ("", "a\n")),
        "a\n",
        "a\nb\nc\n",
        "a\nb\nc\n",
        contested_writes=(0, 1),
        expected_contested=(("-", "b\n"), ("-", "c\n")),
    ),
    OwnLinesCase(
        "a line in a CRLF file",
        "a\r\nb\r\n",
        (("a\r\nb\r\n", "a\r\nB\r\nmine\r\n"),),
        "a\r\nB\r\nmine\r\n",
        "a\r\nb\r\n",
        "a\r\nB\r\nmine\r\n",
    ),
)
OWN_LINES_SPECIFICATION = importlib.util.spec_from_file_location("own_lines", OWN_LINES_MODULE)
own_lines = importlib.util.module_from_spec(OWN_LINES_SPECIFICATION)
sys.modules[OWN_LINES_SPECIFICATION.name] = own_lines
OWN_LINES_SPECIFICATION.loader.exec_module(own_lines)


def make_random_history(generator: random.Random) -> tuple[own_lines.FileHistory, str, str, bool]:
    """A history of own writes, some contested, between other sessions' writes that keep base lines.

    Returns it with the base, the right commit when nothing is contested, and
    whether any contested change is still left in the working tree.
    """
    texts = [generator.choice(RANDOM_BASE_TEXTS) for _ in range(RANDOM_BASE_LINES)]
    base_ids = list(range(RANDOM_BASE_LINES))
    ids = list(base_ids)
    writes: list[own_lines.RecordedWrite] = []
    own_ids: dict[bool, set[int]] = {True: set(), False: set()}
    unsure_ids: set[int] = set()

    for _ in range(generator.randint(1, 3)):
        if generator.random() < RANDOM_OTHER_CHANCE:
            ids, _ = change_random_lines(ids, texts, generator, set(base_ids))
        before = "".join(texts[index] for index in ids)
        contested = generator.random() < RANDOM_CONTESTED_CHANCE
        ids, changes = change_random_lines(ids, texts, generator, set())

        for is_added, index in changes:
            (unsure_ids if contested else own_ids[is_added]).add(index)
        writes.append(own_lines.RecordedWrite(before, "".join(texts[index] for index in ids), contested))
    if generator.random() < RANDOM_OTHER_CHANCE:
        ids, _ = change_random_lines(ids, texts, generator, set(base_ids))
    removed = own_ids[False]
    expected = "".join(
        texts[index] for index in ids if (index in base_ids and index not in removed) or index in own_ids[True]
    )
    unsure = any(index in ids or index in base_ids for index in unsure_ids - removed)
    history = own_lines.FileHistory(writes[0].before, tuple(writes), "".join(texts[index] for index in ids))

    return history, "".join(texts[index] for index in base_ids), expected, unsure


def change_random_lines(
    ids: list[int],
    texts: list[str],
    generator: random.Random,
    kept: set[int],
) -> tuple[list[int], list[tuple[bool, int]]]:
    """Insert, delete or replace one or two lines (each new line has its own text), never removing kept lines."""
    ids = list(ids)
    changes: list[tuple[bool, int]] = []

    for _ in range(generator.randint(1, 2)):
        removable = [position for position, index in enumerate(ids) if index not in kept]
        operation = generator.choice(RANDOM_OPERATIONS) if removable else RANDOM_OPERATIONS[0]
        position = generator.choice(removable) if operation != RANDOM_OPERATIONS[0] else generator.randint(0, len(ids))

        if operation != RANDOM_OPERATIONS[0]:
            changes.append((False, ids.pop(position)))
        if operation != RANDOM_OPERATIONS[1]:
            texts.append(f"new {len(texts)}\n")
            ids.insert(position, len(texts) - 1)
            changes.append((True, len(texts) - 1))
    return ids, changes


def make_random_own_history(generator: random.Random) -> tuple[own_lines.FileHistory, str]:
    """Own writes only, of lines whose text repeats, with a base from before them or after one (an own commit)."""
    lines = [generator.choice(RANDOM_REPEATED_TEXTS) for _ in range(RANDOM_BASE_LINES)]
    versions = ["".join(lines)]

    for _ in range(generator.randint(1, 3)):
        lines = change_repeated_lines(lines, generator)

        versions.append("".join(lines))
    writes = tuple(own_lines.RecordedWrite(before, after, False) for before, after in zip(versions, versions[1:]))

    return own_lines.FileHistory(versions[0], writes, versions[-1]), generator.choice(versions)


def make_random_shared_history(generator: random.Random) -> own_lines.FileHistory:
    """Own writes with another writer's changes between and after them, all of lines whose text repeats."""
    lines = [generator.choice(RANDOM_REPEATED_TEXTS) for _ in range(RANDOM_BASE_LINES)]
    first_before = "".join(lines)
    writes: list[own_lines.RecordedWrite] = []

    for _ in range(generator.randint(1, 3)):
        if generator.random() < RANDOM_OTHER_CHANCE:
            lines = change_repeated_lines(lines, generator)
        before = "".join(lines)
        lines = change_repeated_lines(lines, generator)

        writes.append(own_lines.RecordedWrite(before, "".join(lines), False))
    if generator.random() < RANDOM_OTHER_CHANCE:
        lines = change_repeated_lines(lines, generator)
    return own_lines.FileHistory(first_before, tuple(writes), "".join(lines))


def change_repeated_lines(lines: list[str], generator: random.Random) -> list[str]:
    """Insert, delete or replace one or two lines, each new line repeating a text the file may already have."""
    lines = list(lines)

    for _ in range(generator.randint(1, 2)):
        operation = generator.choice(RANDOM_OPERATIONS) if lines else RANDOM_OPERATIONS[0]
        position = (
            generator.randint(0, len(lines)) if operation == RANDOM_OPERATIONS[0] else generator.randrange(len(lines))
        )

        if operation != RANDOM_OPERATIONS[0]:
            del lines[position]
        if operation != RANDOM_OPERATIONS[1]:
            lines.insert(position, generator.choice(RANDOM_REPEATED_TEXTS))
    return lines


def make_history(case: OwnLinesCase) -> own_lines.FileHistory:
    writes = tuple(
        own_lines.RecordedWrite(before, after, index in case.contested_writes)
        for index, (before, after) in enumerate(case.writes)
    )

    return own_lines.FileHistory(case.first_before, writes, case.working_tree)


class OwnLinesTest(unittest.TestCase):
    def test_cases(self) -> None:
        for case in OWN_LINES_CASES:
            with self.subTest(case=case.name):
                content = own_lines.build_own_content(case.base, make_history(case))
                contested = tuple((line.kind.value, line.text) for line in content.contested)
                self.assertEqual(content.text, case.expected)
                self.assertEqual(content.entangled, case.entangled)
                self.assertEqual(contested, case.expected_contested)

    def test_random_histories_never_commit_wrong_content(self) -> None:
        """Random interleavings with known authorship: a commit that goes through holds exactly the own changes.

        Other sessions keep base lines here, so the right commit is the working
        tree's own and base lines in order; contested writes may be anything.
        """
        generator = random.Random(RANDOM_SEED)

        for trial in range(RANDOM_TRIALS):
            with self.subTest(trial=trial):
                history, base, expected, unsure = make_random_history(generator)
                content = own_lines.build_own_content(base, history)
                is_asked = bool(content.entangled or content.contested)

                self.assertTrue(is_asked or (content.text == expected and not unsure))

    def test_random_histories_of_one_session_commit_whole(self) -> None:
        """With no other writer, the commit is the working tree exactly, however often line texts repeat."""
        generator = random.Random(RANDOM_SEED)

        for trial in range(RANDOM_TRIALS):
            with self.subTest(trial=trial):
                history, base = make_random_own_history(generator)
                content = own_lines.build_own_content(base, history)

                self.assertEqual((content.text, content.entangled, content.contested), (history.working_tree, (), ()))

    def test_random_histories_already_committed_stay_as_they_are(self) -> None:
        """When base is the working tree, nothing is left to commit: the commit is base, or it asks."""
        generator = random.Random(RANDOM_SEED)

        for trial in range(RANDOM_TRIALS):
            with self.subTest(trial=trial):
                history = make_random_shared_history(generator)
                content = own_lines.build_own_content(history.working_tree, history)
                is_asked = bool(content.entangled or content.contested)

                self.assertTrue(is_asked or content.text == history.working_tree)

    def test_earlier_record_types_are_gone(self) -> None:
        """A process that loads an older sibling module must fail on import, not read the new records."""
        self.assertFalse(hasattr(own_lines, "OwnWrite"))
        self.assertFalse(hasattr(own_lines, "OwnContent"))

    def test_foreign_lines_of_a_whole_file(self) -> None:
        case = OWN_LINES_CASES[0]
        own = own_lines.build_own_content(case.base, make_history(case)).text
        foreign = own_lines.list_foreign_lines(case.base, case.working_tree, own)

        self.assertEqual([line.text for line in foreign if line.text], ["## theirs", "their text"])
        self.assertEqual({line.kind for line in foreign}, {own_lines.ChangeKind.ADDED})
        self.assertEqual(own_lines.list_foreign_lines(case.base, own, own), [])
        self.assertEqual(own_lines.list_foreign_lines(case.base, case.base + MINE_1, own), [])


sys.path.insert(0, str(SCRIPTS_DIRECTORY))
RECORD_HOOK_SPECIFICATION = importlib.util.spec_from_file_location("session_writes_hook", RECORD_HOOK)
record_hook = importlib.util.module_from_spec(RECORD_HOOK_SPECIFICATION)
RECORD_HOOK_SPECIFICATION.loader.exec_module(record_hook)
session_writes = importlib.import_module("session_writes")
CRLF_PATCH = [
    {"oldStart": 1, "oldLines": 3, "newStart": 1, "newLines": 4, "lines": [" one", "-two", "+TWO", "+added", " three"]}
]
QUOTE_PATCH = [
    {"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": ["-say “hi”", "+say “bye”", " end"]}
]
NO_NEWLINE_PATCH = [
    {
        "oldStart": 1,
        "oldLines": 1,
        "newStart": 1,
        "newLines": 2,
        "lines": ["-last", NO_NEWLINE_MARKER, "+last", "+second", NO_NEWLINE_MARKER],
    },
]


def make_parallel_write(start: float, end: float, before: str, after: str) -> session_writes.WriteEvent:
    return session_writes.WriteEvent(PARALLEL_ROOT, LOG_FILE, before, after, start, end, BASH_TOOL)


class ParallelWritesTest(unittest.TestCase):
    """How one session's calls that ran side by side are chained before their changes are attributed."""

    def test_side_by_side_calls_are_chained(self) -> None:
        cases = (
            (
                "a call beside an edit starts where the edit left the file",
                [(1, 3, "a", "b"), (2, 4, "a", "b")],
                ["a", "b"],
            ),
            (
                "a call beside two chained edits starts after both",
                [(1, 2, "a", "b"), (2.5, 3, "b", "c"), (1.5, 4, "a", "d")],
                ["a", "b", "c"],
            ),
            ("a later call from the same file is left as recorded", [(1, 2, "a", "b"), (3, 4, "a", "c")], ["a", "a"]),
            (
                "a chain someone else broke is left as recorded",
                [(1, 2, "a", "b"), (2.2, 2.5, "x", "c"), (1.5, 3, "a", "d")],
                ["a", "x", "a"],
            ),
        )

        for name, writes, befores in cases:
            with self.subTest(name):
                aligned = session_writes._align_parallel_writes(make_parallel_write(*write) for write in writes)

                self.assertEqual([write.before for write in aligned], befores)


class RecordHookExactnessTest(unittest.TestCase):
    """Payload shapes captured from Claude Code's Edit and Write responses on 2026-11-10."""

    def test_edit_results(self) -> None:
        cases = (
            ("CRLF file keeps its endings", CRLF_PATCH, "one\r\ntwo\r\nthree\r\n", "one\r\nTWO\r\nadded\r\nthree\r\n"),
            ("curly quotes as written", QUOTE_PATCH, "say “hi”\nend\n", "say “bye”\nend\n"),
            ("no newline at the end", NO_NEWLINE_PATCH, "last", "last\nsecond"),
            ("another writer swapped lines in reach", CRLF_PATCH, "two\none\nthree\n", None),
            ("a file that mixes endings", CRLF_PATCH, "one\r\ntwo\nthree\r\n", None),
            ("no structured patch", None, "one\n", None),
        )

        for name, hunks, before, expected in cases:
            with self.subTest(case=name):
                response = {} if hunks is None else {"structuredPatch": hunks}
                self.assertEqual(record_hook._expect_edit(response, before), expected)

    def test_write_results(self) -> None:
        requested = {"tool_input": {"content": "new\n"}}

        self.assertEqual(record_hook._expect_write(requested, {"type": CREATE_TYPE}), "new\n")
        self.assertEqual(record_hook._expect_write({}, {"content": "reported\n"}), "reported\n")
        self.assertIsNone(record_hook._expect_write({}, {"type": CREATE_TYPE}))

    def test_failures_and_other_tools(self) -> None:
        failed = {"tool_name": EDIT_TOOL, "hook_event_name": POST_TOOL_USE_FAILURE, "tool_response": {}}
        unknown = {"tool_name": WRITE_TOOL, "hook_event_name": POST_TOOL_USE, "tool_response": "done"}
        command = {"tool_name": BASH_TOOL, "hook_event_name": POST_TOOL_USE, "tool_response": {}}

        self.assertIs(record_hook._judge_exactness(failed, "/", None, None), False)
        self.assertIs(record_hook._judge_exactness(unknown, "/", None, None), False)
        self.assertIsNone(record_hook._judge_exactness(command, "/", None, None))

    def test_contents_that_cannot_be_read_back_are_not_exact(self) -> None:
        """Like a folder moved away after its after snapshot: raising here would leave the pending file behind."""
        edit = {"tool_name": EDIT_TOOL, "hook_event_name": POST_TOOL_USE, "tool_response": {"structuredPatch": []}}

        self.assertIs(record_hook._judge_exactness(edit, MISSING_ROOT, MISSING_BLOB, MISSING_BLOB), False)


def make_edit_response(original: str, edited: str) -> dict:
    """The Edit tool's response: the file it read and a jsdiff structured patch, both with line endings in LF."""
    old_lines = split_keeping_ends(original.replace("\r\n", "\n"))
    new_lines = split_keeping_ends(edited.replace("\r\n", "\n"))
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks = [make_hunk(group, old_lines, new_lines) for group in matcher.get_grouped_opcodes(PATCH_CONTEXT_LINES)]

    return {"originalFile": "".join(old_lines), "structuredPatch": hunks}


def split_keeping_ends(text: str) -> list[str]:
    pieces = text.split("\n")

    return [f"{piece}\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def make_hunk(group: list[tuple[str, int, int, int, int]], old_lines: list[str], new_lines: list[str]) -> dict:
    """One jsdiff hunk: lines without their LF, and a marker after a line that has none."""
    signed = [
        (sign, line)
        for tag, i1, i2, j1, j2 in group
        for sign, line in (
            [(" ", line) for line in old_lines[i1:i2]]
            if tag == "equal"
            else [*(("-", line) for line in old_lines[i1:i2]), *(("+", line) for line in new_lines[j1:j2])]
        )
    ]
    lines = [
        entry
        for sign, line in signed
        for entry in ([f"{sign}{line[:-1]}"] if line.endswith("\n") else [f"{sign}{line}", NO_NEWLINE_MARKER])
    ]
    first, last = group[0], group[-1]
    old_count = last[2] - first[1]

    return {
        "oldStart": first[1] + 1 if old_count else first[1],
        "oldLines": old_count,
        "newStart": first[3] + 1,
        "newLines": last[4] - first[3],
        "lines": lines,
    }


class SessionWritesFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.directory.name))
        self.repository = self.root / "repository"
        self.config_directory = self.root / "config"
        self.private_index = self.root / "private-index"
        self.git_config = self.root / "gitconfig"
        self.tool_uses = 0

    def create(self) -> None:
        self.git_config.write_text("[user]\n\tname = Record Test\n\temail = record@example.com\n")
        self.repository.mkdir()
        self.run_git("init", "-q")
        (self.repository / LOG_FILE).write_text(LOG_BASE)
        self.run_git("add", LOG_FILE)
        self.run_git("commit", "-q", "-m", "Chore: seed")

    def environment(self, session: str | None, index: Path | None = None) -> dict[str, str]:
        environment = {
            name: value for name, value in os.environ.items() if name not in (SESSION_VARIABLE, PRIVATE_INDEX_VARIABLE)
        }

        environment.update(
            {
                CONFIG_DIRECTORY_VARIABLE: str(self.config_directory),
                "GIT_CONFIG_GLOBAL": str(self.git_config),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        if session is not None:
            environment[SESSION_VARIABLE] = session
        if index is not None:
            environment[PRIVATE_INDEX_VARIABLE] = str(index)
        return environment

    def run_git(self, *arguments: str, index: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *arguments],
            env=self.environment(None, index),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout.strip()

    def run_record_hook(
        self,
        session: str,
        event: str,
        tool_use: str,
        tool_input: dict,
        response: dict | None = None,
        broken_index: Path | None = None,
        tool: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        payload = self.make_hook_payload(session, event, tool_use, tool_input, response, tool)

        return subprocess.run(
            [sys.executable, str(RECORD_HOOK)],
            input=json.dumps(payload),
            env=self.environment(None, broken_index),
            capture_output=True,
            text=True,
            check=False,
        )

    def run_record_hook_in_process(self, payload: dict[str, Any]) -> str:
        """Run the hook's entry point here, where a test can patch what it calls; returns its error output."""
        errors = io.StringIO()

        with (
            mock.patch.dict(os.environ, self.environment(None)),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
            contextlib.redirect_stderr(errors),
        ):
            record_hook.main()
        return errors.getvalue()

    def make_hook_payload(
        self,
        session: str,
        event: str,
        tool_use: str,
        tool_input: dict[str, Any],
        response: dict[str, Any] | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        tool = tool or (EDIT_TOOL if "file_path" in tool_input else BASH_TOOL)
        payload = {
            "session_id": session,
            "hook_event_name": event,
            "tool_use_id": tool_use,
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": str(self.repository),
        }

        if response is not None:
            payload["tool_response"] = response
        return payload

    def write(self, session: str, path: str, text: str, mode: str = "a") -> None:
        self.tool_uses += 1
        tool_use = f"call-{self.tool_uses}"
        command = {"command": f"python3 write.py {path}"}

        self.run_record_hook(session, PRE_TOOL_USE, tool_use, command)
        with (self.repository / path).open(mode, newline="") as file:
            file.write(text)
        self.run_record_hook(session, POST_TOOL_USE, tool_use, command, {})

    def edit(self, session: str, path: str, old: str, new: str, reported: bool = True, during: str = "") -> None:
        """Replace text as the Edit tool does, which reports the file it started from and its patch with LF endings.

        reported=False sends only the file it started from, as records made before patches were kept.
        during is text someone else appends to the file before the hook looks at it again.
        """
        self.tool_uses += 1
        tool_use = f"call-{self.tool_uses}"
        file = self.repository / path
        edit_input = {"file_path": str(file)}

        self.run_record_hook(session, PRE_TOOL_USE, tool_use, edit_input)
        original = file.read_bytes().decode()
        edited = original.replace(old, new, 1)

        file.write_bytes(edited.encode())
        self.append(path, during)
        response = make_edit_response(original, edited) if reported else {"originalFile": original}

        self.run_record_hook(session, POST_TOOL_USE, tool_use, edit_input, response)

    def create_file(self, session: str, path: str, text: str) -> None:
        """Create a file as the Write tool does, which reports the whole content as added."""
        self.tool_uses += 1
        tool_use = f"call-{self.tool_uses}"
        write_input = {"file_path": str(self.repository / path)}
        response = {"type": CREATE_TYPE, "content": text, "structuredPatch": [], "originalFile": None}

        self.run_record_hook(session, PRE_TOOL_USE, tool_use, write_input, tool=WRITE_TOOL)
        (self.repository / path).write_text(text)
        self.run_record_hook(session, POST_TOOL_USE, tool_use, write_input, response, tool=WRITE_TOOL)

    def start_command(self, session: str) -> tuple[str, str, dict]:
        """Begin a command that is still running, or waiting for approval, while other calls happen."""
        self.tool_uses += 1
        tool_use = f"call-{self.tool_uses}"
        command = {"command": "python3 long-running.py"}

        self.run_record_hook(session, PRE_TOOL_USE, tool_use, command)
        return session, tool_use, command

    def finish_command(self, started: tuple[str, str, dict]) -> None:
        session, tool_use, command = started

        self.run_record_hook(session, POST_TOOL_USE, tool_use, command, {})

    def append(self, path: str, text: str) -> None:
        """Write to a file without any hook, as a running command or a person does."""
        with (self.repository / path).open("a", newline="") as file:
            file.write(text)

    def report(self, session: str, path: str) -> str:
        result = subprocess.run(
            [sys.executable, str(SESSION_WRITES_SCRIPT), "report", path],
            env=self.environment(session),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )

        return result.stdout + result.stderr

    def run_wrapper(
        self, *arguments: str, session: str | None = OWN_SESSION, index: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            env=self.environment(session, index),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_own_lines_commit(
        self, *arguments: str, session: str | None = OWN_SESSION
    ) -> subprocess.CompletedProcess[str]:
        base = self.run_git("rev-parse", "HEAD")

        self.run_git("read-tree", base, index=self.private_index)
        return self.run_wrapper("--expect-head", base, *arguments, session=session, index=self.private_index)

    def read_committed_bytes(self, path: str) -> bytes:
        result = subprocess.run(
            ["git", "cat-file", "blob", f"HEAD:{path}"],
            env=self.environment(None),
            cwd=self.repository,
            capture_output=True,
            check=True,
        )

        return result.stdout

    def write_transcript(self, session: str, tool: str, seconds_ago: int) -> None:
        """A session transcript whose first call of the given tool ended some time ago."""
        transcript = self.config_directory / "projects" / TRANSCRIPT_PROJECT / f"{session}.jsonl"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds_ago))
        use = {"type": "assistant", "timestamp": stamp, "message": {"content": [{"type": "tool_use", "id": "t0", "name": tool}]}}
        result = {"type": "user", "timestamp": stamp, "message": {"content": [{"type": "tool_result", "tool_use_id": "t0"}]}}

        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps(use) + "\n" + json.dumps(result) + "\n")

    def read_events(self, session: str) -> list[dict]:
        record = self.config_directory / RECORD_DIRECTORY_NAME / f"{session}.jsonl"

        return [json.loads(line) for line in record.read_text().splitlines()]

    def end_without_hook(
        self,
        session: str,
        tool_use: str,
        started_ago: float,
        ended_ago: float,
        folder: str = "",
        text: str = "PreToolUse:Bash hook error: Direct git commit blocked.",
    ) -> None:
        """Move a pending call's start back and give it a result in its transcript, as a call stopped before it ran.

        folder names a subagent transcript of the session to hold the result instead of its own.
        """
        pending = self.config_directory / RECORD_DIRECTORY_NAME / session / f"{tool_use}{PENDING_SUFFIX}"
        payload = json.loads(pending.read_text())
        project = self.config_directory / "projects" / TRANSCRIPT_PROJECT
        transcript = project / session / "subagents" / f"{folder}.jsonl" if folder else project / f"{session}.jsonl"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - ended_ago))
        block = {"type": "tool_result", "tool_use_id": tool_use, "is_error": True, "content": text}
        result = {"type": "user", "timestamp": stamp, "message": {"content": [block]}}

        payload["start"] = time.time() - started_ago
        pending.write_text(json.dumps(payload))
        transcript.parent.mkdir(parents=True, exist_ok=True)
        with transcript.open("a") as file:
            file.write(json.dumps(result) + "\n")

    def run_approved_wrapper(self, session: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        """A wrapper call the user approved in the shell guard's dialog, after the record hook saw it start."""
        self.tool_uses += 1
        self.run_record_hook(session, PRE_TOOL_USE, f"call-{self.tool_uses}", {"command": "claude-commit.py"})

        return self.run_wrapper(*arguments, session=session)

    def run_guard(self, session: str, event: str, command: str) -> subprocess.CompletedProcess[str]:
        payload = {
            "session_id": session,
            "hook_event_name": event,
            "tool_name": BASH_TOOL,
            "tool_input": {"command": command},
            "cwd": str(self.repository),
        }

        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            env=self.environment(None),
            capture_output=True,
            text=True,
            check=False,
        )

    def count_approvals(self, session: str) -> int:
        approvals = self.config_directory / RECORD_DIRECTORY_NAME / session / "approved-includes.jsonl"

        return len(approvals.read_text().splitlines()) if approvals.exists() else 0

    def age_approvals(self, session: str, seconds: float) -> None:
        """Move every kept approval of the session back in time."""
        path = self.config_directory / RECORD_DIRECTORY_NAME / session / "approved-includes.jsonl"
        approvals = [json.loads(line) for line in path.read_text().splitlines()]
        aged = [{**approval, "time": approval["time"] - seconds} for approval in approvals]

        path.write_text("".join(json.dumps(approval) + "\n" for approval in aged))

    def has_events(self, session: str) -> bool:
        record = self.config_directory / RECORD_DIRECTORY_NAME / f"{session}.jsonl"

        return record.exists() and bool(record.read_text().strip())

    def list_pending_files(self) -> list[str]:
        return sorted(
            pending.name for pending in (self.config_directory / RECORD_DIRECTORY_NAME).glob(f"*/*{PENDING_SUFFIX}")
        )

    def create_side_repository(self, name: str) -> Path:
        """Another repository beside the fixture's, such as a session's scratch repository."""
        repository = self.root / name

        repository.mkdir()
        for arguments in (("init", "-q"), ("commit", "-q", "--allow-empty", "-m", "Chore: seed")):
            subprocess.run(
                ["git", *arguments], env=self.environment(None), cwd=repository, capture_output=True, check=True
            )
        return repository


class SessionWritesTest(unittest.TestCase):
    fixture: SessionWritesFixture

    def setUp(self) -> None:
        self.fixture = SessionWritesFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def write_mixed_log(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.write(OTHER_SESSION, LOG_FILE, THEIRS)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_2)

    def test_hook_records_commands_and_edits(self) -> None:
        log = self.fixture.repository / LOG_FILE
        edit_input = {"file_path": str(log)}

        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        original = log.read_text()
        pre = self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "edit-1", edit_input)
        log.write_text(original.replace("my text", "my edited text"))
        post = self.fixture.run_record_hook(
            OWN_SESSION, POST_TOOL_USE, "edit-1", edit_input, {"originalFile": original}
        )
        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "fail-1", {"command": "false"})
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE_FAILURE, "fail-1", {"command": "false"})
        self.assertEqual([event["tool"] for event in self.fixture.read_events(OWN_SESSION)], [BASH_TOOL, EDIT_TOOL])
        self.assertEqual((pre.returncode, pre.stdout, post.returncode, post.stdout), (0, "", 0, ""))

    def test_hook_never_blocks_on_bad_input(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RECORD_HOOK)],
            input="not json",
            env=self.fixture.environment(None),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_whole_mixed_file_is_refused(self) -> None:
        self.write_mixed_log()
        head = self.fixture.run_git("rev-parse", "HEAD")
        refused = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(refused.returncode, FOREIGN_LINES_EXIT, refused.stderr)
        self.assertIn("## theirs", refused.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)

    def test_own_lines_commits_only_own_lines(self) -> None:
        self.write_mixed_log()
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.run_git("show", f"HEAD:{LOG_FILE}") + "\n", LOG_BASE + MINE_1 + MINE_2)
        self.assertIn(THEIRS, (self.fixture.repository / LOG_FILE).read_text())
        self.assertIn("Only own lines staged", result.stdout)

    def restore_then_edit(self) -> None:
        """Restore a drafted file to its commit with a recorded command, then edit the restored line."""
        log = self.fixture.repository / LOG_FILE
        restore = {"command": f"git checkout -- {LOG_FILE}"}

        log.write_text(LOG_BASE.replace(BASE_TEXT, DRAFT_TEXT))
        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "restore-1", restore)
        self.fixture.run_git("checkout", "--", LOG_FILE)
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE, "restore-1", restore, {})
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)

    def test_own_lines_replaces_my_committed_line(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        first = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        self.fixture.edit(OWN_SESSION, LOG_FILE, MY_TEXT, MY_EDITED_TEXT)
        second = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())

    def test_own_lines_after_restoring_the_file(self) -> None:
        self.restore_then_edit()
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE).decode(), LOG_BASE.replace(BASE_TEXT, EDITED_TEXT))

    def test_whole_file_after_restoring_the_file_passes(self) -> None:
        self.restore_then_edit()
        result = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_report_names_the_lines_left_out(self) -> None:
        self.write_mixed_log()
        report = subprocess.run(
            [sys.executable, str(SESSION_WRITES_SCRIPT), "report", LOG_FILE],
            env=self.fixture.environment(OWN_SESSION),
            cwd=self.fixture.repository,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertIn("## theirs", report.stdout)
        self.assertNotIn("## mine", report.stdout)

    def test_unrecorded_file_needs_include(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.write(OTHER_SESSION, OTHER_FILE, "theirs only\n", "w")
        refused = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, OTHER_FILE)
        included = self.fixture.run_own_lines_commit(
            "--own-lines", "--include-unrecorded", OTHER_FILE, HEAD_COMMIT_MESSAGE, OTHER_FILE
        )

        self.assertEqual(refused.returncode, FOREIGN_LINES_EXIT, refused.stderr)
        self.assertIn("no recorded writes", refused.stderr)
        self.assertEqual(included.returncode, 0, included.stdout + included.stderr)

    def test_check_is_skipped_without_a_record(self) -> None:
        (self.fixture.repository / OTHER_FILE).write_text("typed by a person\n")
        outside = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, OTHER_FILE)

        self.assertEqual(outside.returncode, 0, outside.stdout + outside.stderr)
        self.assertIn("not checked (no write record", outside.stdout)

    def test_check_is_skipped_outside_a_session(self) -> None:
        (self.fixture.repository / OTHER_FILE).write_text("typed by a person\n")
        base = self.fixture.run_git("rev-parse", "HEAD")
        self.fixture.run_git("read-tree", base, index=self.fixture.private_index)
        outside = self.fixture.run_wrapper(
            "--expect-head", base, HEAD_COMMIT_MESSAGE, OTHER_FILE, session=None, index=self.fixture.private_index
        )

        self.assertEqual(outside.returncode, 0, outside.stdout + outside.stderr)
        self.assertIn("not checked (not a Claude session)", outside.stdout)

    def test_binary_file_commits_whole_only_when_all_own(self) -> None:
        self.fixture.write(OWN_SESSION, BINARY_FILE, BINARY_TEXT, "w")
        own = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, BINARY_FILE)
        self.fixture.write(OTHER_SESSION, BINARY_FILE, BINARY_TEXT)
        mixed = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, BINARY_FILE)

        self.assertEqual(own.returncode, 0, own.stdout + own.stderr)
        self.assertEqual(mixed.returncode, FOREIGN_LINES_EXIT, mixed.stdout + mixed.stderr)

    def test_entangled_lines_are_refused(self) -> None:
        log = self.fixture.repository / LOG_FILE

        self.fixture.write(OTHER_SESSION, LOG_FILE, "- their first\n- their second\n")
        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "split-1", {"command": "python3 split.py"})
        log.write_text(log.read_text().replace("- their first\n", "- their first\n- mine inside\n"))
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE, "split-1", {"command": "python3 split.py"}, {})
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stderr)
        self.assertIn("cannot be committed apart", result.stderr)

    def test_entangled_unrecorded_lines_are_not_called_another_sessions(self) -> None:
        (self.fixture.repository / LOG_FILE).write_text(PERSON_TEXT)
        self.fixture.edit(OWN_SESSION, LOG_FILE, PERSON_TEXT, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stderr)
        self.assertIn("cannot be committed apart", result.stderr)
        self.assertIn("does not show as its own", result.stderr)
        self.assertNotIn("another session has not committed", result.stderr)

    def test_merge_in_progress_is_not_checked(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        (self.fixture.repository / OTHER_FILE).write_text("from the merged branch\n")
        self.fixture.run_git("add", OTHER_FILE)
        tree = self.fixture.run_git("write-tree")
        merged = self.fixture.run_git("commit-tree", tree, "-m", "Chore: other branch")
        (self.fixture.repository / ".git" / "MERGE_HEAD").write_text(merged + "\n")
        result = self.fixture.run_wrapper("--no-stage", MERGE_MESSAGE, OTHER_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not checked (merge in progress)", result.stdout)

    def test_failed_snapshot_claims_nothing(self) -> None:
        broken_index = self.fixture.root / "broken-index"
        command = {"command": "python3 write.py log.md"}

        broken_index.write_text(BROKEN_INDEX_TEXT)
        self.fixture.write(OWN_SESSION, OTHER_FILE, "mine\n", "w")
        self.fixture.write(OTHER_SESSION, LOG_FILE, THEIRS)
        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "broken-1", command, broken_index=broken_index)
        with (self.fixture.repository / LOG_FILE).open("a") as file:
            file.write(MINE_1)
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE, "broken-1", command, {})
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertNotIn(b"their text", self.fixture.read_committed_bytes(LOG_FILE))

    def test_call_that_moves_a_repository_away_holds_up_no_one(self) -> None:
        """A call moved a scratch repository it covered to the Trash (2026-11-11); its pending file stayed an hour."""
        scratch = self.fixture.create_side_repository(SCRATCH_REPOSITORY)
        command = {"command": f"mv {scratch} {self.fixture.root / TRASH_FOLDER}"}

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "move-1", command)
        scratch.rename(self.fixture.root / TRASH_FOLDER)
        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "move-1", command, {})
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())
        self.assertEqual(self.fixture.list_pending_files(), [])

    def test_change_a_failed_after_snapshot_hides_is_asked_only_while_the_call_ran(self) -> None:
        broken_index = self.fixture.root / "broken-index"
        command = {"command": "python3 write.py log.md"}

        broken_index.write_text(BROKEN_INDEX_TEXT)
        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "hidden-1", command)
        own = self.fixture.start_command(OWN_SESSION)
        self.fixture.append(LOG_FILE, THEIRS)
        self.fixture.append(LOG_FILE, MINE_1)
        self.fixture.finish_command(own)
        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "hidden-1", command, {}, broken_index=broken_index)
        during = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.fixture.write(OWN_SESSION, OTHER_FILE, MINE_2, "w")
        after = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, OTHER_FILE)

        self.assertEqual(during.returncode, FOREIGN_LINES_EXIT, during.stdout + during.stderr)
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(OTHER_FILE).decode(), MINE_2)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE).decode(), LOG_BASE)

    def test_call_whose_before_snapshot_fails_is_seen_by_others(self) -> None:
        broken_index = self.fixture.root / "broken-index"
        command = {"command": "python3 write.py log.md"}
        head = self.fixture.run_git("rev-parse", "HEAD")

        broken_index.write_text(BROKEN_INDEX_TEXT)
        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "blind-1", command, broken_index=broken_index)
        own = self.fixture.start_command(OWN_SESSION)
        self.fixture.append(LOG_FILE, THEIRS)
        self.fixture.append(LOG_FILE, MINE_1)
        self.fixture.finish_command(own)
        running = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "blind-1", command, {})
        ended = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(running.returncode, FOREIGN_LINES_EXIT, running.stdout + running.stderr)
        self.assertEqual(ended.returncode, FOREIGN_LINES_EXIT, ended.stdout + ended.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)

    def test_commands_after_moving_a_written_repository_away_are_recorded(self) -> None:
        scratch = self.fixture.create_side_repository(SCRATCH_REPOSITORY)
        scratch_command = {"command": f"python3 write.py {scratch / OTHER_FILE}"}
        log_command = {"command": "python3 write.py log.md"}
        expected = [(str(scratch), OTHER_FILE), (str(self.fixture.repository), LOG_FILE)]

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "scratch-1", scratch_command)
        (scratch / OTHER_FILE).write_text(THEIRS)
        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "scratch-1", scratch_command, {})
        scratch.rename(self.fixture.root / TRASH_FOLDER)
        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "log-1", log_command)
        self.fixture.append(LOG_FILE, THEIRS)
        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "log-1", log_command, {})
        recorded = [(event["root"], event["path"]) for event in self.fixture.read_events(OTHER_SESSION)]

        self.assertEqual(recorded, expected)

    def test_pending_file_from_before_unknown_scopes_is_read(self) -> None:
        """A call that starts under the previous hook and ends under this one, as the call installing it does."""
        command = {"command": "python3 write.py log.md"}
        pending = self.fixture.config_directory / RECORD_DIRECTORY_NAME / OTHER_SESSION / f"old-1{PENDING_SUFFIX}"

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "old-1", command)
        previous = {key: value for key, value in json.loads(pending.read_text()).items() if key != "unknown"}

        pending.write_text(json.dumps(previous))
        self.fixture.append(LOG_FILE, THEIRS)
        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "old-1", command, {})

        self.assertEqual([event["path"] for event in self.fixture.read_events(OTHER_SESSION)], [LOG_FILE])
        self.assertEqual(self.fixture.list_pending_files(), [])

    @mock.patch.object(record_hook, "remove_expired_records", side_effect=FileNotFoundError(VANISHED_RECORD))
    def test_failed_cleanup_of_old_records_keeps_the_call_seen(self, cleanup: mock.MagicMock) -> None:
        """A session's first call drops old records while other sessions remove their own files, so that can fail."""
        command = {"command": "python3 write.py log.md"}
        payload = self.fixture.make_hook_payload(OTHER_SESSION, PRE_TOOL_USE, "first-1", command)
        errors = self.fixture.run_record_hook_in_process(payload)

        cleanup.assert_called_once_with()
        self.assertIn(VANISHED_RECORD, errors)
        self.assertEqual(self.fixture.list_pending_files(), [f"first-1{PENDING_SUFFIX}"])

    def test_line_endings_are_kept(self) -> None:
        (self.fixture.repository / CRLF_FILE).write_bytes(b"a\r\nb\r\n")
        self.fixture.run_git("add", CRLF_FILE)
        self.fixture.run_git("commit", "-q", "-m", "Chore: add a file with CRLF line endings")
        self.fixture.write(OWN_SESSION, CRLF_FILE, "mine\r\n")
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, CRLF_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(CRLF_FILE), b"a\r\nb\r\nmine\r\n")

    def test_edit_keeps_crlf_line_endings(self) -> None:
        (self.fixture.repository / CRLF_FILE).write_bytes(b"a\r\nb\r\n")
        self.fixture.run_git("add", CRLF_FILE)
        self.fixture.run_git("commit", "-q", "-m", "Chore: add a file with CRLF line endings")
        self.fixture.edit(OWN_SESSION, CRLF_FILE, "b\r\n", "B\r\nmine\r\n")
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, CRLF_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(CRLF_FILE), b"a\r\nB\r\nmine\r\n")

    def test_moved_line_in_my_edit_stays_mine(self) -> None:
        log = self.fixture.repository / LOG_FILE
        edit_input = {"file_path": str(log)}
        hunk = {"oldStart": 5, "oldLines": 2, "newStart": 5, "newLines": 2, "lines": ["-first", " second", "+first"]}
        moved_the_other_way = {"structuredPatch": [hunk]}

        self.fixture.append(LOG_FILE, "first\nsecond\n")
        self.fixture.run_git("commit", "-q", "-am", "Chore: add two lines")
        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "move-1", edit_input)
        log.write_text(LOG_BASE + "second\nfirst\n")
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE, "move-1", edit_input, moved_the_other_way)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE).decode(), LOG_BASE + "second\nfirst\n")

    def test_edit_record_marks_exact_edits(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.edit(OWN_SESSION, LOG_FILE, MY_TEXT, MY_EDITED_TEXT, during=PERSON_TEXT)
        command, exact, mixed = self.fixture.read_events(OWN_SESSION)

        self.assertEqual((command["exact"], exact["exact"], mixed["exact"]), (None, True, False))
        self.assertEqual({event["record_format"] for event in (command, exact, mixed)}, {RECORD_FORMAT})

    def test_edit_inside_another_sessions_command_stays_mine(self) -> None:
        started = self.fixture.start_command(OTHER_SESSION)
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.finish_command(started)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE).decode(), LOG_BASE.replace(BASE_TEXT, EDITED_TEXT))

    def test_unexplained_or_old_edit_inside_another_command_is_asked(self) -> None:
        record = self.fixture.config_directory / RECORD_DIRECTORY_NAME / f"{OWN_SESSION}.jsonl"
        started = self.fixture.start_command(OTHER_SESSION)
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT, reported=False)
        self.fixture.finish_command(started)
        unexplained = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        old_format = [
            {key: value for key, value in event.items() if key not in ("exact", "record_format")}
            for event in self.fixture.read_events(OWN_SESSION)
        ]
        record.write_text("".join(json.dumps(event) + "\n" for event in old_format))
        old = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(unexplained.returncode, FOREIGN_LINES_EXIT, unexplained.stdout + unexplained.stderr)
        self.assertEqual(old.returncode, FOREIGN_LINES_EXIT, old.stdout + old.stderr)

    def test_command_around_another_sessions_edit_is_asked(self) -> None:
        head = self.fixture.run_git("rev-parse", "HEAD")
        started = self.fixture.start_command(OWN_SESSION)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.append(LOG_FILE, MINE_1)
        self.fixture.finish_command(started)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)

    def test_waiting_command_does_not_hold_up_the_editor(self) -> None:
        started = self.fixture.start_command(OWN_SESSION)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, TITLE_TEXT, EDITED_TITLE_TEXT)
        self.fixture.finish_command(started)
        report = self.fixture.report(OWN_SESSION, LOG_FILE)
        other = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE, session=OTHER_SESSION)

        self.assertIn("may be this session's", report)
        self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
        self.assertEqual(
            self.fixture.read_committed_bytes(LOG_FILE).decode(),
            LOG_BASE.replace(BASE_TEXT, EDITED_TEXT).replace(TITLE_TEXT, EDITED_TITLE_TEXT),
        )

    def test_command_inside_another_command_is_neither_sessions(self) -> None:
        started = self.fixture.start_command(OTHER_SESSION)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.finish_command(started)
        own = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, LOG_FILE)
        other = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, LOG_FILE, session=OTHER_SESSION)

        self.assertEqual(own.returncode, FOREIGN_LINES_EXIT, own.stdout + own.stderr)
        self.assertEqual(other.returncode, FOREIGN_LINES_EXIT, other.stdout + other.stderr)

    def test_replacement_inside_another_command_is_asked_not_halved(self) -> None:
        head = self.fixture.run_git("rev-parse", "HEAD")
        started = self.fixture.start_command(OTHER_SESSION)
        self.fixture.write(OWN_SESSION, LOG_FILE, LOG_BASE.replace(BASE_TEXT, EDITED_TEXT), "w")
        self.fixture.finish_command(started)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        report = self.fixture.report(OWN_SESSION, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("another session also wrote this file", result.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)
        self.assertIn("may be this session's", report)

    def test_running_command_of_another_session_is_asked(self) -> None:
        self.fixture.start_command(OTHER_SESSION)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_line_written_and_undone_by_others_in_my_command_is_asked(self) -> None:
        started = self.fixture.start_command(OWN_SESSION)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, BASE_TEXT, BASE_TEXT + MINE_2)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, BASE_TEXT + MINE_2, BASE_TEXT)
        self.fixture.append(LOG_FILE, MINE_2 + MINE_1)
        self.fixture.finish_command(started)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_my_replacement_replaced_by_them_is_asked(self) -> None:
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, EDITED_TEXT, DRAFT_TEXT)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("they changed your replacement", result.stderr)

    def test_edit_finishing_as_my_command_starts_is_asked(self) -> None:
        log = self.fixture.repository / LOG_FILE
        edit_input = {"file_path": str(log)}
        original = log.read_text()

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "edit-early", edit_input)
        log.write_text(original + THEIRS)
        started = self.fixture.start_command(OWN_SESSION)
        self.fixture.run_record_hook(
            OTHER_SESSION, POST_TOOL_USE, "edit-early", edit_input, make_edit_response(original, original + THEIRS)
        )
        self.fixture.append(LOG_FILE, MINE_1)
        self.fixture.finish_command(started)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_broken_record_line_stops_the_commit(self) -> None:
        record = self.fixture.config_directory / RECORD_DIRECTORY_NAME / f"{OTHER_SESSION}.jsonl"
        own_record = self.fixture.config_directory / RECORD_DIRECTORY_NAME / f"{OWN_SESSION}.jsonl"
        cut_in_a_character = '{"root": "한'.encode()[:-1]

        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        record.write_bytes(b'{"root": "half written\n' + cut_in_a_character)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        with own_record.open("ab") as file:
            file.write(cut_in_a_character + b"\n")
        followed = self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "after-broken", {"command": "true"})

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("cannot be read back", result.stderr)
        self.assertEqual((followed.returncode, followed.stderr), (0, ""))

    def test_overlapping_record_refuses_whole_file(self) -> None:
        record = self.fixture.config_directory / RECORD_DIRECTORY_NAME / f"{OTHER_SESSION}.jsonl"
        root = str(self.fixture.repository)
        missing = "0" * 40

        self.fixture.write(OWN_SESSION, BINARY_FILE, BINARY_TEXT, "w")
        event = {**self.fixture.read_events(OWN_SESSION)[-1], "before": missing, "after": missing[:-1] + "1"}
        record.write_text(json.dumps({**event, "root": root}) + "\n")
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, BINARY_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_line_typed_during_my_edit_is_asked(self) -> None:
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT, during=PERSON_TEXT)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn(PERSON_TEXT, (self.fixture.repository / LOG_FILE).read_text())

    def test_change_during_a_failed_edit_is_asked(self) -> None:
        edit_input = {"file_path": str(self.fixture.repository / LOG_FILE)}

        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "edit-failed", edit_input)
        self.fixture.append(LOG_FILE, THEIRS)
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE_FAILURE, "edit-failed", edit_input)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_created_file_is_mine(self) -> None:
        self.fixture.create_file(OWN_SESSION, OTHER_FILE, NEW_PAGE_TEXT)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, OTHER_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(OTHER_FILE).decode(), NEW_PAGE_TEXT)

    def test_committed_edit_of_a_base_line_then_more(self) -> None:
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        first = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_2)
        second = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())

    def test_my_committed_paragraph_is_not_added_again(self) -> None:
        """Paragraphs end in blank lines as the base's do, so lining up the base by text put one in twice."""
        self.fixture.edit(OWN_SESSION, LOG_FILE, LOG_TITLE, f"{FIRST_NOTE}\n{LOG_TITLE}")
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_HEADING, f"\n{SECOND_NOTE}{BASE_HEADING}")
        first = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        self.fixture.edit(OWN_SESSION, LOG_FILE, FIRST_NOTE, REWORDED_NOTE)
        second = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())

    def test_command_beside_my_edit_stays_mine(self) -> None:
        """A command run beside this session's Edit starts from the same file and records the Edit's change again."""
        command = self.fixture.start_command(OWN_SESSION)
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.finish_command(command)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())

    def test_command_writing_after_my_parallel_edit_stays_mine(self) -> None:
        command = self.fixture.start_command(OWN_SESSION)
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.append(LOG_FILE, MINE_2)
        self.fixture.finish_command(command)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())

    def test_other_edit_between_my_parallel_calls_is_asked(self) -> None:
        head = self.fixture.run_git("rev-parse", "HEAD")
        command = self.fixture.start_command(OWN_SESSION)
        self.fixture.edit(OWN_SESSION, LOG_FILE, BASE_TEXT, EDITED_TEXT)
        self.fixture.edit(OTHER_SESSION, LOG_FILE, LOG_TITLE, THEIR_TITLE)
        self.fixture.finish_command(command)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn(THEIR_TITLE.strip(), result.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)

    def test_new_file_in_new_folder_is_recorded(self) -> None:
        path = self.fixture.repository / NEW_FOLDER_FILE
        write_input = {"file_path": str(path), "content": NEW_PAGE_TEXT}
        response = {"type": CREATE_TYPE, "filePath": str(path)}

        self.fixture.run_record_hook(OWN_SESSION, PRE_TOOL_USE, "write-1", write_input, tool=WRITE_TOOL)
        path.parent.mkdir()
        path.write_text(NEW_PAGE_TEXT)
        self.fixture.run_record_hook(OWN_SESSION, POST_TOOL_USE, "write-1", write_input, response, tool=WRITE_TOOL)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, NEW_FOLDER_FILE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_own_lines_without_a_record_explains_the_fallback(self) -> None:
        (self.fixture.repository / OTHER_FILE).write_text("typed by a person\n")
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, OTHER_FILE)

        self.assertEqual(result.returncode, USAGE_EXIT, result.stderr)
        self.assertIn("commit without --own-lines", result.stderr)

    def test_record_started_mid_session_is_not_checked(self) -> None:
        self.write_mixed_log()
        self.fixture.write_transcript(OWN_SESSION, BASH_TOOL, HOUR_SECONDS)
        own_lines = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)
        whole = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(own_lines.returncode, USAGE_EXIT, own_lines.stderr)
        self.assertEqual(whole.returncode, 0, whole.stdout + whole.stderr)
        self.assertIn("not checked (write record started mid-session", whole.stdout)

    def test_record_from_session_start_is_checked(self) -> None:
        self.write_mixed_log()
        self.fixture.write_transcript(OWN_SESSION, READ_TOOL, HOUR_SECONDS)
        result = self.fixture.run_own_lines_commit(HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_option_errors(self) -> None:
        both = self.fixture.run_wrapper("--no-stage", "--own-lines", HEAD_COMMIT_MESSAGE)
        unlisted = self.fixture.run_wrapper("--include-unrecorded", OTHER_FILE, HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(both.returncode, USAGE_EXIT, both.stderr)
        self.assertEqual(unlisted.returncode, USAGE_EXIT, unlisted.stderr)


shell_command = importlib.import_module("shell_command")
FILE_WRITING_COMMANDS = (
    "echo a > f",
    "echo a >> f",
    "> f",
    "(echo a) > f",
    "{ echo a; } > f",
    "echo a &> f",
    "cat <> f",
)
FILE_KEEPING_COMMANDS = ("cat f 2>&1 | head", "cat f 2>/dev/null", "echo a >&2", "git status --short")
WRITE_TARGET_CASES = (
    ("echo a > f.py", [["f.py"]]),
    ("cat > /tmp/a.ts <<'EOF'\nx\nEOF", [["/tmp/a.ts"]]),
    ("ls > out.txt 2>&1", [["out.txt"]]),
    ("echo a > f 2>/dev/null >> g", [["f", "g"]]),
    ("cat f 2>/dev/null", [[]]),
    ("cd /tmp && echo a > b", [[], ["b"]]),
    ("bash -c 'echo x > a.py' > out.log", [["out.log"], ["a.py", "out.log"]]),
)
STDIN_TEXT_CASES = (
    ("python3 - <<'EOF'\nimport os\nEOF", ["python3", "-"], ["import os"]),
    ("timeout 60 python3 <<EOF\nx = 1\nEOF", ["python3"], ["x = 1"]),
    ("env A=1 nohup python3 - <<< 'print(1)'", ["python3", "-"], ["print(1)"]),
    ("echo 'print(1)' | python3", ["python3"], ["print(1)"]),
    ("python3 tool.py", ["python3", "tool.py"], []),
)


class CommandCallContentTest(unittest.TestCase):
    """A call carries the files its redirections write and the text that reaches its standard input."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_redirections_list_their_targets(self) -> None:
        for command, expected in WRITE_TARGET_CASES:
            calls = shell_command.expand_shell_command(command, "/tmp", Path(self.directory.name))

            self.assertEqual([call.write_targets for call in calls], expected, command)

    def test_input_text_reaches_the_program_through_wrappers(self) -> None:
        for command, argv, expected in STDIN_TEXT_CASES:
            calls = shell_command.expand_shell_command(command, "/tmp", Path(self.directory.name))
            program_calls = [call for call in calls if call.argv == argv]

            self.assertEqual([call.stdin_texts for call in program_calls], [expected], command)

    def test_calls_read_from_a_script_file_are_marked(self) -> None:
        script = Path(self.directory.name) / "run.sh"
        commands = (f"bash {script}", f"source {script}", f"sh -c 'bash {script}'")

        script.write_text("cat > gen.ts <<'EOF'\nx\nEOF\n")
        for command in commands:
            calls = shell_command.expand_shell_command(command, "/tmp", Path(self.directory.name))

            self.assertEqual([call.is_from_script_file for call in calls if call.write_targets], [True], command)
        inline = shell_command.expand_shell_command("sh -c 'echo > a.py'", "/tmp", Path(self.directory.name))

        self.assertEqual([call.is_from_script_file for call in inline], [False, False])


class CommandScopeTest(unittest.TestCase):
    """Commands that leave working trees alone take no snapshot, and calls that ended without their end hook stop covering."""

    fixture: SessionWritesFixture

    def setUp(self) -> None:
        self.fixture = SessionWritesFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_file_redirections_mark_the_call_as_writing(self) -> None:
        for command in FILE_WRITING_COMMANDS:
            calls = shell_command.expand_shell_command(
                command, str(self.fixture.repository), self.fixture.config_directory
            )

            self.assertTrue(any(call.writes_file for call in calls), command)
        for command in FILE_KEEPING_COMMANDS:
            calls = shell_command.expand_shell_command(
                command, str(self.fixture.repository), self.fixture.config_directory
            )

            self.assertFalse(any(call.writes_file for call in calls), command)

    def test_reading_command_of_another_session_holds_up_no_one(self) -> None:
        command = {"command": f"git status --short && cat {LOG_FILE} | head -1"}

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "read-1", command)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_record_hook(OTHER_SESSION, POST_TOOL_USE, "read-1", command, {})
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.fixture.has_events(OTHER_SESSION))

    def test_command_writing_a_file_still_covers_the_repository(self) -> None:
        command = {"command": f"cat {LOG_FILE} > copy.md"}

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "copy-1", command)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_commit_waiting_for_approval_holds_up_no_one(self) -> None:
        command = {"command": self.make_commit_command()}

        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "commit-1", command)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_commit_in_a_repository_with_commit_hooks_still_covers_it(self) -> None:
        hook = self.fixture.repository / ".git" / "hooks" / "pre-commit"
        command = {"command": self.make_commit_command()}

        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        self.fixture.run_record_hook(OTHER_SESSION, PRE_TOOL_USE, "commit-1", command)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_call_stopped_before_it_ran_holds_up_no_one_after_its_result(self) -> None:
        self.check_stopped_call_holds_up_no_one("")

    def test_subagent_call_stopped_before_it_ran_holds_up_no_one_after_its_result(self) -> None:
        self.check_stopped_call_holds_up_no_one("agent-1")

    def test_call_stopped_before_it_ran_still_covers_until_its_result(self) -> None:
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        started = self.fixture.start_command(OTHER_SESSION)
        self.fixture.end_without_hook(OTHER_SESSION, started[1], started_ago=30, ended_ago=0)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_private_index_option_commits_own_lines_and_cleans_up(self) -> None:
        temporary = self.fixture.root / "tmp"
        base = self.fixture.run_git("rev-parse", "HEAD")
        environment = {**self.fixture.environment(OWN_SESSION), "TMPDIR": str(temporary)}

        temporary.mkdir()
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        self.fixture.append(LOG_FILE, MINE_2)
        result = subprocess.run(
            [
                sys.executable,
                str(COMMIT_WRAPPER),
                "--private-index",
                "--expect-head",
                base,
                "--own-lines",
                HEAD_COMMIT_MESSAGE,
                LOG_FILE,
            ],
            env=environment,
            cwd=self.fixture.repository,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE).decode(), LOG_BASE + MINE_1)
        self.assertEqual(list(temporary.iterdir()), [])
        self.assertEqual(self.fixture.run_git("diff", "--cached", "--name-only"), "")

    def test_ran_call_without_end_hook_keeps_covering(self) -> None:
        started = self.fixture.start_command(OTHER_SESSION)

        self.fixture.end_without_hook(OTHER_SESSION, started[1], started_ago=30, ended_ago=20, text="Exit code 1")
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)

    def test_programs_count_only_by_their_bare_names_or_installed_paths(self) -> None:
        repository = str(self.fixture.repository)
        recorded = [
            "./test",
            "scripts/diff a b",
            "python3 ./head f",
            f"{self.fixture.root}/claude-commit.py m f",
            "/bin/cat f",
        ]
        kept = [f"cat {LOG_FILE}", f'{COMMIT_WRAPPER} --own-lines "m" f', f'python3 {COMMIT_WRAPPER} --own-lines "m" f']

        self.assertEqual(
            {command: self.find_roots(command) for command in recorded}, dict.fromkeys(recorded, [repository])
        )
        self.assertEqual({command: self.find_roots(command) for command in kept}, dict.fromkeys(kept, []))

    def test_git_options_that_touch_files_or_run_programs_are_recorded(self) -> None:
        repository = str(self.fixture.repository)
        recorded = [
            "git read-tree -mu HEAD",
            "git reset --har HEAD~",
            "git -c core.fsmonitor=x status",
            "GIT_EXTERNAL_DIFF=x git diff",
            "git diff --ext-diff",
            "export GIT_DIR=/x; git status",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=x git status",
        ]
        kept = [
            "git -c core.quotepath=false status",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.quotepath GIT_CONFIG_VALUE_0=false git diff",
            "git reset -q -- a",
            "git read-tree HEAD",
        ]

        self.assertEqual(
            {command: self.find_roots(command) for command in recorded}, dict.fromkeys(recorded, [repository])
        )
        self.assertEqual({command: self.find_roots(command) for command in kept}, dict.fromkeys(kept, []))

    def test_private_index_option_cleans_up_when_a_check_fails(self) -> None:
        temporary = self.fixture.root / "tmp"
        base = self.fixture.run_git("rev-parse", "HEAD")
        environment = {**self.fixture.environment(OWN_SESSION), "TMPDIR": str(temporary)}
        arguments = ["--private-index", "--expect-head", base, "--own-lines", HEAD_COMMIT_MESSAGE, "big.py"]

        temporary.mkdir()
        self.fixture.write(OWN_SESSION, "big.py", "x = 1\n" * 250)
        result = subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            env=environment,
            cwd=self.fixture.repository,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, REVIEW_MISSING_EXIT, result.stdout + result.stderr)
        self.assertEqual(list(temporary.iterdir()), [])

    def test_private_index_option_needs_a_base(self) -> None:
        result = self.fixture.run_wrapper("--private-index", "--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, USAGE_EXIT, result.stdout + result.stderr)

    def check_stopped_call_holds_up_no_one(self, folder: str) -> None:
        started = self.fixture.start_command(OTHER_SESSION)

        self.fixture.end_without_hook(OTHER_SESSION, started[1], started_ago=30, ended_ago=20, folder=folder)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def find_roots(self, command: str) -> list[str]:
        with mock.patch.dict(os.environ, {CONFIG_DIRECTORY_VARIABLE: str(self.fixture.config_directory)}):
            roots: list[str] = session_writes.find_command_roots("unit-session", command, str(self.fixture.repository))

        return roots

    def make_commit_command(self) -> str:
        return f'cd {self.fixture.repository} && {COMMIT_WRAPPER} --private-index --expect-head abc --own-lines "{HEAD_COMMIT_SUBJECT}" other.md'


class IncludeDialogTest(unittest.TestCase):
    """The shell guard's dialog is the one question about unrecorded lines: it lists them and is not repeated on a retry."""

    fixture: SessionWritesFixture

    def setUp(self) -> None:
        self.fixture = SessionWritesFixture()
        self.fixture.create()
        self.wrapper_arguments = (
            "--expect-head",
            "abc",
            "--include-unrecorded",
            LOG_FILE,
            HEAD_COMMIT_MESSAGE,
            LOG_FILE,
        )
        self.command = f'cd {self.fixture.repository} && ~/.claude/scripts/claude-commit.py --expect-head abc --include-unrecorded {LOG_FILE} "{HEAD_COMMIT_SUBJECT}" {LOG_FILE}'

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_wrapper_says_not_to_ask_in_chat(self) -> None:
        self.fixture.start_command(OTHER_SESSION)
        self.fixture.write(OWN_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_own_lines_commit("--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE)

        self.assertEqual(result.returncode, FOREIGN_LINES_EXIT, result.stdout + result.stderr)
        self.assertIn("without asking in chat first", result.stderr)

    def test_dialog_lists_the_lines_it_commits(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)
        decision = json.loads(result.stdout)["hookSpecificOutput"]

        self.assertEqual(decision["permissionDecision"], ASK, result.stderr)
        self.assertIn("+ ## mine 1", decision["permissionDecisionReason"])

    def test_declined_call_is_asked_again(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], ASK, result.stderr)

    def test_approved_retry_is_not_asked_again(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_approved_wrapper(OWN_SESSION, *self.wrapper_arguments)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(result.stdout, "", result.stderr)

    def test_change_no_record_shows_is_asked_again(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_approved_wrapper(OWN_SESSION, *self.wrapper_arguments)
        self.fixture.append(LOG_FILE, MINE_2)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], ASK, result.stderr)

    def test_retry_keeps_the_first_approval_time(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_approved_wrapper(OWN_SESSION, *self.wrapper_arguments)
        self.fixture.run_approved_wrapper(OWN_SESSION, *self.wrapper_arguments)

        self.assertEqual(self.fixture.count_approvals(OWN_SESSION), 1)

    def test_approval_expires_after_half_an_hour(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_approved_wrapper(OWN_SESSION, *self.wrapper_arguments)
        self.fixture.age_approvals(OWN_SESSION, 31 * 60)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], ASK, result.stderr)

    def test_retry_after_a_new_write_is_asked_again(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_approved_wrapper(OWN_SESSION, *self.wrapper_arguments)
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_2)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], ASK, result.stderr)

    def test_approval_of_another_session_does_not_count(self) -> None:
        self.fixture.write(OTHER_SESSION, LOG_FILE, MINE_1)
        self.fixture.run_approved_wrapper(OTHER_SESSION, *self.wrapper_arguments)
        result = self.fixture.run_guard(OWN_SESSION, PRE_TOOL_USE, self.command)

        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], ASK, result.stderr)


class CommitWrapperAmendTest(unittest.TestCase):
    """--amend rewrites only a commit this session made through the wrapper and has not pushed."""

    fixture: SessionWritesFixture

    def setUp(self) -> None:
        self.fixture = SessionWritesFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def commit_section(self, session: str, text: str, *options: str) -> subprocess.CompletedProcess[str]:
        """Append a section as the session and commit its lines, taking HEAD as the base in the same call."""
        self.fixture.write(session, LOG_FILE, text)
        return self.fixture.run_own_lines_commit(
            *options, "--own-lines", HEAD_COMMIT_MESSAGE, LOG_FILE, session=session
        )

    def test_amend_of_another_sessions_commit_is_refused(self) -> None:
        mine = self.commit_section(OWN_SESSION, MINE_1)
        theirs = self.commit_section(OTHER_SESSION, THEIRS)
        their_commit = self.fixture.run_git("rev-parse", "HEAD")
        amend = self.commit_section(OWN_SESSION, MINE_2, "--amend")

        self.assertEqual((mine.returncode, theirs.returncode), (0, 0), mine.stderr + theirs.stderr)
        self.assertEqual(amend.returncode, AMEND_TARGET_EXIT, amend.stdout + amend.stderr)
        self.assertIn("this session did not make", amend.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), their_commit)

    def test_amend_of_my_commit_passes_again_and_again(self) -> None:
        first = self.commit_section(OWN_SESSION, MINE_1)
        parent = self.fixture.run_git("rev-parse", "HEAD^")
        amend = self.commit_section(OWN_SESSION, MINE_2, "--amend")
        again = self.commit_section(OWN_SESSION, MINE_3, "--amend")

        self.assertEqual([first.returncode, amend.returncode, again.returncode], [0, 0, 0], amend.stderr + again.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), parent)
        self.assertEqual(self.fixture.read_committed_bytes(LOG_FILE), (self.fixture.repository / LOG_FILE).read_bytes())

    def test_amend_of_an_unlisted_commit_is_refused(self) -> None:
        head = self.fixture.run_git("rev-parse", "HEAD")
        unlisted = self.commit_section(OWN_SESSION, MINE_1, "--amend")
        outside = self.fixture.run_own_lines_commit(
            "--amend", "--no-stage", HEAD_COMMIT_MESSAGE, LOG_FILE, session=None
        )

        self.assertEqual(unlisted.returncode, AMEND_TARGET_EXIT, unlisted.stdout + unlisted.stderr)
        self.assertIn("this session did not make", unlisted.stderr)
        self.assertEqual(outside.returncode, AMEND_TARGET_EXIT, outside.stdout + outside.stderr)
        self.assertIn("only a Claude session", outside.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), head)

    def test_amend_of_a_pushed_commit_is_refused(self) -> None:
        mine = self.commit_section(OWN_SESSION, MINE_1)
        self.fixture.run_git("update-ref", REMOTE_MAIN, "HEAD")
        self.fixture.run_git("symbolic-ref", REMOTE_HEAD, REMOTE_MAIN)
        pushed = self.fixture.run_git("rev-parse", "HEAD")
        amend = self.commit_section(OWN_SESSION, MINE_2, "--amend")

        self.assertEqual(mine.returncode, 0, mine.stdout + mine.stderr)
        self.assertEqual(amend.returncode, AMEND_TARGET_EXIT, amend.stdout + amend.stderr)
        self.assertIn("is already on origin/main, so", amend.stderr)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD"), pushed)


def print_table() -> None:
    fixture = GuardFixture()
    rows: list[str] = []
    failures = 0
    durations: list[float] = []

    fixture.create()
    for case in CASES:
        started = time.perf_counter()
        decision, _ = fixture.run_hook(case)
        durations.append(time.perf_counter() - started)
        failures += decision != case.expected
        shown = case.command.replace("\n", "⏎").replace("|", "\\|")
        shown = shown if len(shown) <= 70 else shown[:67] + "..."
        tool = "" if case.tool == BASH_TOOL else f" ({case.tool.split('__')[-1]})"
        mark = "OK" if decision == case.expected else "**FAIL**"
        rows.append(f"| {case.group} | `{shown}`{tool} | {case.location} | {case.expected} | {decision} | {mark} |")
    fixture.directory.cleanup()
    print("| Group | Command | Repository | Expected | Actual | Result |")
    print("| --- | --- | --- | --- | --- | --- |")
    print("\n".join(rows))
    print(f"\n{len(CASES) - failures}/{len(CASES)} cases match. Hook run time per call: "
          f"median {sorted(durations)[len(durations) // 2] * 1000:.0f} ms, max {max(durations) * 1000:.0f} ms.")


if __name__ == "__main__":
    if TABLE_FLAG in sys.argv:
        print_table()
    else:
        unittest.main()
