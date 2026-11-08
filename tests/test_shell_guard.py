#!/usr/bin/env python3
"""Tests for scripts/shell-guard-hook.py, secret_files.py and claude-commit.py.

Each case feeds the hook the same JSON that Claude Code sends and checks
its decision: deny, ask, or pass (no output). Fixture repositories and a
fixture shell snapshot are created in a temporary directory, so the tests
run the same on any machine. Run with --table to print every case and
its actual decision as a Markdown table instead of running unittest.
"""

from __future__ import annotations

import json
import os
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


@dataclass
class GuardCase:
    group: str
    command: str
    expected: str
    location: str = "plain"
    tool: str = BASH_TOOL


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
    GuardCase("push", "git push", ASK),
    GuardCase("push", "gp", ASK),
    GuardCase("push", "ggp", ASK),
    GuardCase("push", "gpf", ASK),
    GuardCase("push", "cd {plain} && git push origin main", ASK),
    GuardCase("push", "git -C {plain} push", ASK),
    GuardCase("push", "sh -c 'git push'", ASK),
    GuardCase("push", "git push --force origin main", ASK),
    GuardCase("push", "git push origin :old-branch", ASK),
    GuardCase("push", "G=git; $G push", ASK),
    GuardCase("push", "$(which git) push", ASK),
    GuardCase("push", "echo 'git push' | sh", ASK),
    GuardCase("push", "bash <<EOF\ngit push\nEOF", ASK),
    GuardCase("push", "gp", ASK, tool=MONITOR_TOOL),
    GuardCase("push", 'echo "$(git push)"', ASK),
    GuardCase("push", "cat <<EOF\n$(git push)\nEOF", ASK),
    GuardCase("push", "for r in a b; do git -C $r push; done", ASK),
    GuardCase("push", "alias p='git push'; p", ASK),
    GuardCase("push", "git push --dry-run", PASS),
    GuardCase("push", "cat <<'EOF'\n$(git push)\nEOF", PASS),
    GuardCase("push", "ls # git push", PASS),
    GuardCase("push", "git log origin/main..HEAD", PASS),
    GuardCase("push", 'echo "git push"', PASS),
    GuardCase("push", "git status", PASS),
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
        }

    def create(self) -> None:
        snapshot_directory = self.config_directory / "shell-snapshots"

        snapshot_directory.mkdir(parents=True)
        (snapshot_directory / "snapshot-zsh-test.sh").write_text(SNAPSHOT_TEXT)
        self.git_config.write_text("[user]\n\tname = Guard Test\n\temail = guard@example.com\n")
        for location in self.locations.values():
            location.mkdir()
            self.run_git(location, "init", "-q")
            (location / "a.txt").write_text("a\n")
        self.create_secret_files(self.locations["secrets"])
        self.create_secret_files(self.locations["ignored"])
        (self.locations["ignored"] / ".gitignore").write_text(".env\n.npmrc\nkey.pem\nconfig/\n")
        (self.locations["plain"] / "commit.sh").write_text("#!/bin/sh\ngit commit -m scripted\n")
        (self.locations["plain"] / "commit.sh").chmod(0o755)

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
        payload = {
            "tool_name": case.tool,
            "tool_input": {"command": command, "description": "test"},
            "cwd": cwd,
        }
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
