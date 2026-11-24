"""Tests for scripts/check-codex-skills.py.

Each test copies the real script into a small Git repository with a stand-in
Claude skill and Codex copy for every adapted skill, then runs it the way the
commit check does.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPOSITORY_ROOT / "scripts" / "check-codex-skills.py"
SYNC_SCRIPT = REPOSITORY_ROOT / "scripts" / "sync-shared-rules.py"
CHECK_SPECIFICATION = importlib.util.spec_from_file_location("check_codex_skills", CHECK_SCRIPT)
assert CHECK_SPECIFICATION is not None and CHECK_SPECIFICATION.loader is not None
check_codex_skills = importlib.util.module_from_spec(CHECK_SPECIFICATION)
CHECK_SPECIFICATION.loader.exec_module(check_codex_skills)
ENCODING = "utf-8"
ACCEPT_OPTION = "--accept"
SHOW_OPTION = "--show"
CHANGED_SKILL = "python"
CLAUDE_SKILL_PATH = f"skills/{CHANGED_SKILL}/SKILL.md"
CLAUDE_REFERENCE_PATH = f"skills/{CHANGED_SKILL}/references/examples.md"
CODEX_SKILL_PATH = f"codex/skills/{CHANGED_SKILL}/SKILL.md"
CHANGED_LINE = "- A rule the Claude copy gained.\n"
GENERATED_NOTICE = "<!-- Generated from shared-rules/rule-writing.md by scripts/sync-shared-rules.py -->\n\n"
UNLISTED_MESSAGE = "is adapted by hand"
REFERENCE_LINE = "- An example the Claude copy keeps apart.\n"
GIT_PREFIX = "GIT_"
GIT_SETTINGS = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
EXIT_PASSED = 0
EXIT_STALE = 1
EXIT_UNUSABLE = 2


class CodexSkillFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.directory.name) / "repository"
        self.environment = {name: value for name, value in os.environ.items() if not name.startswith(GIT_PREFIX)}
        self.environment.update(GIT_SETTINGS)

    def create(self) -> None:
        (self.repository / "scripts").mkdir(parents=True)
        shutil.copy2(CHECK_SCRIPT, self.repository / "scripts" / CHECK_SCRIPT.name)
        shutil.copy2(SYNC_SCRIPT, self.repository / "scripts" / SYNC_SCRIPT.name)
        for name in check_codex_skills.ADAPTED_SKILLS:
            self.write(f"skills/{name}/SKILL.md", f"---\nname: {name}\n---\n\n# {name}\n\n- A shared rule.\n")
            self.write(f"codex/skills/{name}/SKILL.md", f"---\nname: {name}\n---\n\n# {name}\n\n- A Codex rule.\n")
        self.write(CLAUDE_REFERENCE_PATH, "# Examples\n\n" + REFERENCE_LINE)
        self.run_git("init", "-q")

    def write(self, relative: str, text: str) -> None:
        target = self.repository / relative

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding=ENCODING)

    def append(self, relative: str, text: str) -> None:
        self.write(relative, self.read(relative) + text)

    def read(self, relative: str) -> str:
        return (self.repository / relative).read_text(encoding=ENCODING)

    def remove(self, relative: str) -> None:
        (self.repository / relative).unlink()

    def accept(self, name: str) -> subprocess.CompletedProcess[str]:
        return self.run(ACCEPT_OPTION, name)

    def accept_all(self) -> list[int]:
        codes = [self.accept(name).returncode for name in check_codex_skills.ADAPTED_SKILLS]

        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", "adapted")
        return codes

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repository / "scripts" / CHECK_SCRIPT.name), *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout


class CheckCodexSkillsTest(unittest.TestCase):
    fixture: CodexSkillFixture

    def setUp(self) -> None:
        self.fixture = CodexSkillFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def accept_all(self) -> None:
        codes = self.fixture.accept_all()

        self.assertEqual(set(codes), {EXIT_PASSED})

    def test_copy_without_comment_is_unusable(self) -> None:
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_UNUSABLE)
        self.assertIn("has no adaptation comment", result.stderr)

    def test_passes_after_every_copy_is_accepted(self) -> None:
        self.accept_all()
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_PASSED, result.stderr)
        self.assertIn("OK:", result.stdout)

    def test_blob_matches_git(self) -> None:
        expected = self.fixture.run_git("hash-object", CLAUDE_SKILL_PATH).strip()

        self.assertEqual(check_codex_skills._compute_blob(self.fixture.repository / CLAUDE_SKILL_PATH), expected)

    def test_accept_places_one_comment_under_the_front_matter(self) -> None:
        self.accept_all()
        result = self.fixture.accept(CHANGED_SKILL)
        text = self.fixture.read(CODEX_SKILL_PATH)

        self.assertEqual(result.returncode, EXIT_PASSED, result.stderr)
        self.assertEqual(text.count("<!-- Adapted from "), 1)
        self.assertTrue(
            text.startswith(f"---\nname: {CHANGED_SKILL}\n---\n\n<!-- Adapted from {CLAUDE_SKILL_PATH} at ")
        )
        self.assertIn(f", {CLAUDE_REFERENCE_PATH} at ", text)

    def test_accept_refuses_a_copy_without_front_matter(self) -> None:
        self.fixture.write(CODEX_SKILL_PATH, "# python\n")
        result = self.fixture.accept(CHANGED_SKILL)

        self.assertEqual(result.returncode, EXIT_UNUSABLE)
        self.assertEqual(self.fixture.read(CODEX_SKILL_PATH), "# python\n")

    def test_copy_adapted_by_hand_must_be_listed(self) -> None:
        self.accept_all()
        self.fixture.write("skills/new-copy/SKILL.md", "# new-copy\n")
        self.fixture.write("codex/skills/new-copy/SKILL.md", "# new-copy\n")
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_STALE)
        self.assertIn(f"codex/skills/new-copy/SKILL.md {UNLISTED_MESSAGE}", result.stderr)

    def test_generated_and_licensed_copies_need_no_listing(self) -> None:
        self.accept_all()
        self.fixture.write("skills/rule-writing/SKILL.md", f"{GENERATED_NOTICE}# rule-writing\n")
        self.fixture.write("codex/skills/rule-writing/SKILL.md", f"{GENERATED_NOTICE}# rule-writing\n")
        self.fixture.write("skills/vendor/SKILL.md", "# vendor\n")
        self.fixture.write("codex/skills/vendor/SKILL.md", "# vendor\n")
        self.fixture.write("codex/skills/vendor/LICENSE.txt", "MIT\n")
        self.fixture.write("codex/skills/codex-only/SKILL.md", "# codex-only\n")
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_PASSED, result.stderr)

    def test_carried_over_notice_licence_folder_or_renamed_source_still_need_listing(self) -> None:
        self.accept_all()
        self.fixture.write("skills/copied/SKILL.md", "# copied\n")
        self.fixture.write("codex/skills/copied/SKILL.md", f"{GENERATED_NOTICE}# copied\n")
        self.fixture.write("skills/data-copy/SKILL.md", "# data-copy\n")
        self.fixture.write("codex/skills/data-copy/SKILL.md", "# data-copy\n")
        self.fixture.write("codex/skills/data-copy/LICENSE-data/notes.txt", "data\n")
        self.fixture.write(
            "codex/skills/renamed/SKILL.md", f"<!-- Adapted from skills/python/SKILL.md at {'0' * 40} -->\n"
        )
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_STALE)
        for name in ("copied", "data-copy", "renamed"):
            self.assertIn(f"codex/skills/{name}/SKILL.md {UNLISTED_MESSAGE}", result.stderr)

    def test_changed_claude_source_fails_until_accepted(self) -> None:
        self.accept_all()
        self.fixture.append(CLAUDE_SKILL_PATH, CHANGED_LINE)
        stale = self.fixture.run()
        accept = self.fixture.accept(CHANGED_SKILL)
        accepted = self.fixture.run()

        self.assertEqual(stale.returncode, EXIT_STALE)
        self.assertIn(f"{CODEX_SKILL_PATH} was adapted from an older {CLAUDE_SKILL_PATH}", stale.stderr)
        self.assertIn("record in the copy what it leaves out or changes on purpose", stale.stderr)
        self.assertEqual(accept.returncode, EXIT_PASSED, accept.stderr)
        self.assertEqual(accepted.returncode, EXIT_PASSED, accepted.stderr)

    def test_show_prints_the_source_change(self) -> None:
        self.accept_all()
        self.fixture.append(CLAUDE_SKILL_PATH, CHANGED_LINE)
        result = self.fixture.run(SHOW_OPTION, CHANGED_SKILL)

        self.assertEqual(result.returncode, EXIT_PASSED, result.stderr)
        self.assertIn("+" + CHANGED_LINE, result.stdout)

    def test_show_without_changes_says_so(self) -> None:
        self.accept_all()
        result = self.fixture.run(SHOW_OPTION, CHANGED_SKILL)

        self.assertEqual(result.returncode, EXIT_PASSED, result.stderr)
        self.assertIn("already names its current Claude sources", result.stdout)

    def test_added_reference_file_counts_as_a_change(self) -> None:
        added = f"skills/{CHANGED_SKILL}/references/more.md"

        self.accept_all()
        self.fixture.write(added, "# More\n")
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_STALE)
        self.assertIn(added, result.stderr)

    def test_removed_reference_file_counts_as_a_change(self) -> None:
        self.accept_all()
        self.fixture.remove(CLAUDE_REFERENCE_PATH)
        result = self.fixture.run()
        shown = self.fixture.run(SHOW_OPTION, CHANGED_SKILL)

        self.assertEqual(result.returncode, EXIT_STALE)
        self.assertIn(CLAUDE_REFERENCE_PATH, result.stderr)
        self.assertIn("-" + REFERENCE_LINE, shown.stdout)

    def test_removed_skill_file_counts_as_a_change(self) -> None:
        self.accept_all()
        self.fixture.remove(CLAUDE_SKILL_PATH)
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_STALE, result.stderr)
        self.assertIn(CLAUDE_SKILL_PATH, result.stderr)

    def test_malformed_entry_is_unusable(self) -> None:
        self.accept_all()
        self.fixture.write(CODEX_SKILL_PATH, f"---\nname: x\n---\n\n<!-- Adapted from {CLAUDE_SKILL_PATH} at ZZ -->\n")
        result = self.fixture.run()

        self.assertEqual(result.returncode, EXIT_UNUSABLE)
        self.assertIn("unreadable adaptation entry", result.stderr)


if __name__ == "__main__":
    unittest.main()
