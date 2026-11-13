"""Disposable repositories for the merge, integration, and cleanup tool tests.

A world holds a bare repository standing in for the remote, the original
checkout cloned from it, worktrees for tasks, and a `gh` shim on PATH that
runs github_double.py against the bare repository. Git runs with a config
of its own, so the user's global settings and hooks never apply.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
DOUBLE_SCRIPT = Path(__file__).resolve().parent / "github_double.py"
POLICY_FILE = ".agent-workflow.json"
BASE = "main"
PASSED_CHECK = {"__typename": "CheckRun", "name": "checks", "status": "COMPLETED", "conclusion": "SUCCESS"}
ENCODING = "utf-8"


def load_script(name: str) -> ModuleType:
    """Import a script whose file name has hyphens, with the scripts folder importable as the hooks have it."""
    if str(SCRIPTS_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIRECTORY))
    specification = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS_DIRECTORY / f"{name}.py")
    module = importlib.util.module_from_spec(specification)

    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class WorkflowWorld:
    def __init__(self, policy: dict[str, Any] | None):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name).resolve()
        self.remote = self.root / "remote.git"
        self.original = self.root / "original"
        self.state_folder = self.root / "github"
        self.environment = self._make_environment()
        self.state_folder.mkdir()
        self._write_shim()
        self.git(self.root, "init", "-q", "--bare", "-b", BASE, str(self.remote))
        self.git(self.root, "clone", "-q", str(self.remote), str(self.original))
        self.write(self.original, "README.md", "seed\n")
        self.write(self.original, ".gitignore", ".claude/worktrees/\n")
        if policy is not None:
            self.write(self.original, POLICY_FILE, json.dumps(policy))
        self.git(self.original, "add", "-A")
        self.git(self.original, "commit", "-q", "-m", "Chore: seed")
        self.git(self.original, "push", "-q", "origin", BASE)
        self.git(self.original, "remote", "set-head", "origin", BASE)
        self.save_state({"remote": str(self.remote), "pulls": {}, "checks": {}, "defaultChecks": [PASSED_CHECK]})

    def close(self) -> None:
        self._directory.cleanup()

    def add_worktree(self, branch: str) -> Path:
        path = self.original / ".claude" / "worktrees" / branch

        self.git(self.original, "worktree", "add", "-q", "-b", branch, str(path), f"origin/{BASE}")
        return path

    def commit_file(self, checkout: Path, name: str, text: str, message: str) -> str:
        self.write(checkout, name, text)
        self.git(checkout, "add", name)
        self.git(checkout, "commit", "-q", "-m", message)
        return self.git(checkout, "rev-parse", "HEAD")

    def push_base_commit(self, name: str, text: str) -> str:
        """Land a commit on the remote base branch directly, as another merge would."""
        helper = self.root / "helper"

        if not helper.exists():
            self.git(self.root, "clone", "-q", str(self.remote), str(helper))
        self.git(helper, "pull", "-q", "origin", BASE)
        commit = self.commit_file(helper, name, text, f"Feat: add {name}")
        self.git(helper, "push", "-q", "origin", BASE)
        return commit

    def open_pull_request(self, number: int, branch: str, checkout: Path) -> None:
        self.git(checkout, "push", "-q", "-u", "origin", branch)
        state = self.load_state()
        state["pulls"][str(number)] = {
            "head": branch,
            "base": BASE,
            "state": "OPEN",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeCommit": None,
            "mergedHead": None,
        }
        self.save_state(state)

    def set_checks(self, commit: str, checks: list[dict[str, Any]]) -> None:
        state = self.load_state()

        state["checks"][commit] = checks
        self.save_state(state)

    def update_state(self, **values: Any) -> None:
        state = self.load_state()

        state.update(values)
        self.save_state(state)

    def load_state(self) -> dict[str, Any]:
        return json.loads((self.state_folder / "state.json").read_text(encoding=ENCODING))

    def save_state(self, state: dict[str, Any]) -> None:
        (self.state_folder / "state.json").write_text(json.dumps(state, indent=2), encoding=ENCODING)

    def list_gh_calls(self) -> list[list[str]]:
        path = self.state_folder / "calls.jsonl"
        lines = path.read_text(encoding=ENCODING).splitlines() if path.exists() else []

        return [json.loads(line)["argv"] for line in lines]

    def run_script(self, name: str, arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIRECTORY / f"{name}.py"), *arguments],
            cwd=cwd,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding=ENCODING,
            timeout=120,
            check=False,
        )

    def start_script(self, name: str, arguments: list[str], cwd: Path) -> subprocess.Popen[str]:
        """Start a tool in a process group of its own, so a test can stop it together with the checks it runs."""
        return subprocess.Popen(
            [sys.executable, str(SCRIPTS_DIRECTORY / f"{name}.py"), *arguments],
            cwd=cwd,
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=ENCODING,
            start_new_session=True,
        )

    def remote_tip(self) -> str:
        return self.git(self.remote, "rev-parse", f"refs/heads/{BASE}")

    def git(self, folder: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(folder), *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            encoding=ENCODING,
            check=False,
        )

        if result.returncode == 0:
            return result.stdout.strip()
        else:
            raise AssertionError(f"git {' '.join(arguments)} failed: {result.stderr}")

    def write(self, folder: Path, name: str, text: str) -> None:
        path = folder / name

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=ENCODING)

    def _make_environment(self) -> dict[str, str]:
        git_config = self.root / "gitconfig"
        environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}

        git_config.write_text("[user]\n\tname = Test\n\temail = test@example.com\n", encoding=ENCODING)
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": str(git_config),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GITHUB_DOUBLE_STATE": str(self.state_folder),
                "PATH": f"{self.root / 'bin'}{os.pathsep}{environment.get('PATH', '')}",
            },
        )
        return environment

    def _write_shim(self) -> None:
        shim = self.root / "bin" / "gh"

        shim.parent.mkdir()
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{DOUBLE_SCRIPT}" "$@"\n', encoding=ENCODING)
        shim.chmod(0o755)
