"""Tests for scripts/check-commit.py and .githooks/pre-commit.

Each test builds a small repository with the real check script, the real
hook, a stand-in sync script, and a one-file test suite that reads
value.txt from the copied index. Commits go through plain git with
core.hooksPath set, as they do in the shared checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
COPIED_FILES = ("scripts/check-commit.py", "scripts/secret_files.py", ".githooks/pre-commit")
PRIVATE_INDEX_VARIABLE = "GIT_INDEX_FILE"
GOOD_VALUE = "good\n"
BAD_VALUE = "bad\n"
FAKE_GITHUB_TOKEN = "gh" + "p_" + "B" * 36
PASSING_STAND_IN = "import sys\nsys.exit(0)\n"
FAILING_STAND_IN = "import sys\nsys.exit(1)\n"
STAND_IN_SCRIPTS = ("sync-shared-rules.py", "check-rule-size.py")
SAMPLE_TEST = '''import os
import unittest
from pathlib import Path


class SampleTest(unittest.TestCase):
    def test_value_in_the_copied_index(self):
        self.assertEqual(Path("value.txt").read_text(), "good\\n")
        self.assertNotIn("GIT_INDEX_FILE", os.environ)
        self.assertNotIn("GIT_DIR", os.environ)
'''


class CheckCommitFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repository = self.root / "repository"
        self.private_index = self.root / "private-index"
        self.git_config = self.root / "gitconfig"

    def create(self) -> None:
        self.git_config.write_text("[user]\n\tname = Check Test\n\temail = check@example.com\n")
        for relative in COPIED_FILES:
            target = self.repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY_ROOT / relative, target)
        for stand_in in STAND_IN_SCRIPTS:
            (self.repository / "scripts" / stand_in).write_text(PASSING_STAND_IN)
        (self.repository / "tests").mkdir()
        (self.repository / "tests" / "test_sample.py").write_text(SAMPLE_TEST)
        (self.repository / "value.txt").write_text(GOOD_VALUE)
        self.run_git("init", "-q", "-b", "main")
        self.run_git("config", "core.hooksPath", ".githooks")
        self.run_git("add", ".")
        self.run_git("commit", "-q", "-m", "Chore: seed")

    def environment(self, index: Path | None = None) -> dict[str, str]:
        environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}

        environment.update({"GIT_CONFIG_GLOBAL": str(self.git_config), "GIT_CONFIG_NOSYSTEM": "1"})
        if index is not None:
            environment[PRIVATE_INDEX_VARIABLE] = str(index)
        return environment

    def run_git(
        self,
        *arguments: str,
        index: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            env=self.environment(index),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=check,
        )

    def write(self, path: str, text: str) -> None:
        (self.repository / path).write_text(text)

    def read_head(self) -> str:
        return self.run_git("rev-parse", "HEAD").stdout.strip()

    def commit_private_index(self, path: str, text: str) -> subprocess.CompletedProcess[str]:
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            input=text,
            env=self.environment(),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        self.run_git("read-tree", "HEAD", index=self.private_index)
        self.run_git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", index=self.private_index)
        return self.run_git("commit", "-q", "-m", "Fix: change the value", index=self.private_index, check=False)


class CheckCommitTest(unittest.TestCase):
    fixture: CheckCommitFixture

    def setUp(self) -> None:
        self.fixture = CheckCommitFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def test_checks_the_index_not_the_working_tree(self) -> None:
        self.fixture.write("value.txt", BAD_VALUE)
        result = self.fixture.commit_private_index("other.txt", "another session's line\n")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failing_content_stops_the_commit(self) -> None:
        base = self.fixture.read_head()
        result = self.fixture.commit_private_index("value.txt", BAD_VALUE)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-commit check failed", result.stderr)
        self.assertIn("tests", result.stderr)
        self.assertEqual(self.fixture.read_head(), base)

    def test_default_index_commit_is_checked(self) -> None:
        self.fixture.write("value.txt", BAD_VALUE)
        self.fixture.run_git("add", "value.txt")
        result = self.fixture.run_git("commit", "-q", "-m", "Fix: change the value", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tests", result.stderr)

    def test_failing_sync_check_stops_the_commit(self) -> None:
        result = self.fixture.commit_private_index("scripts/sync-shared-rules.py", FAILING_STAND_IN)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generated rule copies", result.stderr)

    def test_failing_size_check_stops_the_commit(self) -> None:
        result = self.fixture.commit_private_index("scripts/check-rule-size.py", FAILING_STAND_IN)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size budgets", result.stderr)

    def test_added_token_stops_the_commit(self) -> None:
        base = self.fixture.read_head()
        result = self.fixture.commit_private_index("notes.txt", f"token {FAKE_GITHUB_TOKEN}\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("notes.txt: GitHub token", result.stderr)
        self.assertNotIn(FAKE_GITHUB_TOKEN, result.stderr)
        self.assertEqual(self.fixture.read_head(), base)


if __name__ == "__main__":
    unittest.main()
