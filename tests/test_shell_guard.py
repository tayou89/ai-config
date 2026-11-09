#!/usr/bin/env python3
"""Tests for scripts/shell-guard-hook.py, secret_files.py, commit_arguments.py and claude-commit.py.

Each case feeds the hook the same JSON that Claude Code sends and checks
its decision: deny, ask, or pass (no output). Fixture repositories and a
fixture shell snapshot are created in a temporary directory, so the tests
run the same on any machine. Run with --table to print every case and
its actual decision as a Markdown table instead of running unittest.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path


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
HEAD_COMMIT_MESSAGE = "Feat: commit this session's change\n\n- one\n- two\n- three"
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
SHIM_TEXT = """#!/bin/sh
# Lands another commit after the wrapper's HEAD check and before git commit reads HEAD.
if [ "$1" = commit ] && [ ! -e "{marker}" ]; then
  touch "{marker}"
  tree=$(env -u GIT_INDEX_FILE "{real_git}" rev-parse "HEAD^{{tree}}")
  other=$(env -u GIT_INDEX_FILE "{real_git}" commit-tree "$tree" -p HEAD -m "{subject}")
  env -u GIT_INDEX_FILE "{real_git}" update-ref HEAD "$other"
fi
exec "{real_git}" "$@"
"""


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


class WrapperHeadFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repository = self.root / "repository"
        self.private_index = self.root / "private-index"
        self.other_index = self.root / "other-index"
        self.shim_directory = self.root / "bin"
        self.git_config = self.root / "gitconfig"

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
        )

        self.shim_directory.mkdir()
        shim.write_text(shim_text)
        shim.chmod(0o755)

    def environment(self, index: Path | None = None, shim: bool = False) -> dict[str, str]:
        environment = dict(os.environ)

        environment.pop(PRIVATE_INDEX_VARIABLE, None)
        environment.update({"GIT_CONFIG_GLOBAL": str(self.git_config), "GIT_CONFIG_NOSYSTEM": "1"})
        if index is not None:
            environment[PRIVATE_INDEX_VARIABLE] = str(index)
        if shim:
            environment["PATH"] = f"{self.shim_directory}{os.pathsep}{environment['PATH']}"
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

    def run_wrapper(self, *arguments: str, shim: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(COMMIT_WRAPPER), *arguments],
            env=self.environment(self.private_index, shim),
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
        result = self.fixture.run_wrapper("--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt", shim=True)

        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)
        self.assertIn("was undone", result.stderr)

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
        base = self.fixture.build_private_index("b.txt")
        amend_arguments = ["--amend", "--expect-head", base, "--no-stage", HEAD_COMMIT_MESSAGE, "b.txt"]
        result = self.fixture.run_wrapper(*amend_arguments, shim=True)

        self.assertEqual(result.returncode, HEAD_MOVED_EXIT, result.stderr)
        self.assertEqual(self.fixture.read_subject("HEAD"), OTHER_SESSION_SUBJECT)
        self.assertEqual(self.fixture.run_git("rev-parse", "HEAD^"), base)


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
