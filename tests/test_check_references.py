"""Tests for scripts/check-references.py.

Each test copies the real script into a small repository with stand-in
instruction files, rules, skills, and hooks whose references all resolve,
breaks one of them, and runs the script the way the commit check does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_SCRIPT = REPOSITORY_ROOT / "scripts" / "check-references.py"
GENERATED_NOTICE = "<!-- Generated from shared-rules/rule-writing.md by scripts/sync-shared-rules.py -->\n\n"
FENCE = "```"
CLEAN_FILES = {
    "CLAUDE.md": "# Claude\n\n## Hooks\n\n- Notices: `[GOOD NOTICE]`.\n",
    "README.md": "# Readme\n\nSee [the rules](rules/collaboration-core.md).\n",
    "settings.json": json.dumps({"enabledPlugins": {"design@market": True}, "hooks": "[GOOD NOTICE]"}),
    "rules/collaboration-core.md": "# Core\n\n## Decisions and corrections\n\n## `check` vs `is` Naming\n",
    "skills/code-plan/SKILL.md": '# Plan\n\n## 12. After Each Commit\n\nRecord it as `collaboration-core` "Decisions and corrections" describes.\n',
    "skills/git-commit/SKILL.md": '# Commit\n\nThen follow `code-plan` "After Each Commit".\n',
    "scripts/notice-hook.py": "REASON = '(CLAUDE.md \"Hooks\")'\n",
    "codex/AGENTS.md": "# Codex\n\n## Core Collaboration Principles\n\n### Decisions and corrections\n",
    "codex/settings.json": json.dumps({"plugins": []}),
    "codex/skills/code-plan/SKILL.md": '# Plan\n\nRecord it as `collaboration-core` "Decisions and corrections" describes.\n',
    "codex/skills/only-codex/SKILL.md": "# Codex only\n",
}


class ReferenceFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.directory.name)

    def create(self) -> None:
        for relative, text in CLEAN_FILES.items():
            self.write(relative, text)
        shutil.copy2(REFERENCE_SCRIPT, self.repository / "scripts" / REFERENCE_SCRIPT.name)

    def write(self, relative: str, text: str) -> None:
        target = self.repository / relative

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def append(self, relative: str, text: str) -> None:
        target = self.repository / relative

        target.write_text(target.read_text(encoding="utf-8") + text, encoding="utf-8")

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repository / "scripts" / REFERENCE_SCRIPT.name)],
            capture_output=True,
            text=True,
            check=False,
        )


class CheckReferencesTest(unittest.TestCase):
    fixture: ReferenceFixture

    def setUp(self) -> None:
        self.fixture = ReferenceFixture()
        self.fixture.create()

    def tearDown(self) -> None:
        self.fixture.directory.cleanup()

    def assert_reported(self, expected: str) -> None:
        result = self.fixture.run()

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(expected, result.stderr)

    def assert_passes(self) -> None:
        result = self.fixture.run()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_repository_passes(self) -> None:
        self.assert_passes()

    def test_link_to_missing_file_is_reported_with_its_line(self) -> None:
        self.fixture.append("README.md", "\nSee [gone](rules/gone.md).\n")
        self.assert_reported("README.md:5: link to missing file rules/gone.md")

    def test_link_with_a_title_is_checked(self) -> None:
        self.fixture.append("README.md", '\nSee [gone](rules/gone.md "Gone").\n')
        self.assert_reported("README.md:5: link to missing file rules/gone.md")

    def test_link_definition_is_checked(self) -> None:
        self.fixture.append("README.md", "\n[gone]: rules/gone.md\n")
        self.assert_reported("README.md:5: link to missing file rules/gone.md")

    def test_percent_encoded_link_resolves(self) -> None:
        self.fixture.write("rules/two words.md", "# Two words\n")
        self.fixture.append("README.md", "\nSee [two](rules/two%20words.md).\n")
        self.assert_passes()

    def test_link_inside_fenced_code_is_ignored(self) -> None:
        self.fixture.append("README.md", f"\n{FENCE}\n[example](rules/gone.md)\n{FENCE}\n")
        self.assert_passes()

    def test_link_inside_indented_or_tilde_fence_is_ignored(self) -> None:
        indented = f"\n- Item\n\n  {FENCE}\n  [example](rules/gone.md)\n  {FENCE}\n"
        tilde = "\n~~~\n[example](rules/gone.md)\n~~~\n"

        self.fixture.append("README.md", indented + tilde)
        self.assert_passes()

    def test_missing_skill_section_is_reported(self) -> None:
        self.fixture.append("skills/git-commit/SKILL.md", 'Also see `code-plan` "Plan Location".\n')
        self.assert_reported('`code-plan` has no section "Plan Location"')

    def test_heading_inside_fenced_code_does_not_count(self) -> None:
        self.fixture.append("skills/code-plan/SKILL.md", f"\n{FENCE}bash\n# Plan Location\n{FENCE}\n")
        self.fixture.append("skills/git-commit/SKILL.md", 'Also see `code-plan` "Plan Location".\n')
        self.assert_reported('`code-plan` has no section "Plan Location"')

    def test_heading_with_code_marks_matches_plain_text(self) -> None:
        self.fixture.append(
            "skills/git-commit/SKILL.md", 'Name it as `collaboration-core` "check vs is Naming" says.\n'
        )
        self.assert_passes()

    def test_missing_instruction_section_in_a_hook_message_is_reported(self) -> None:
        self.fixture.write("scripts/notice-hook.py", "REASON = '(CLAUDE.md \"Skill Triggers\")'\n")
        self.assert_reported('scripts/notice-hook.py:1: CLAUDE.md has no section "Skill Triggers"')

    def test_escaped_quotes_in_a_hook_message_are_read(self) -> None:
        self.fixture.write("scripts/notice-hook.py", 'REASON = "(`code-plan` \\"Plan Location\\")"\n')
        self.assert_reported('scripts/notice-hook.py:1: `code-plan` has no section "Plan Location"')

    def test_codex_document_finds_rule_sections_in_its_instruction_file(self) -> None:
        self.fixture.write("codex/AGENTS.md", "# Codex\n\n## Core Collaboration Principles\n")
        self.assert_reported(
            'codex/skills/code-plan/SKILL.md:3: `collaboration-core` has no section "Decisions and corrections"'
        )

    def test_plugin_skill_section_is_not_checked(self) -> None:
        self.fixture.append("CLAUDE.md", '\nCritique it as `design` "Critique" describes.\n')
        self.assert_passes()

    def test_misspelled_skill_to_read_is_reported(self) -> None:
        self.fixture.append("CLAUDE.md", "\nRead `code-plann` first.\n")
        self.assert_reported("CLAUDE.md:7: `code-plann` names no skill")

    def test_code_word_after_use_is_not_taken_for_a_skill(self) -> None:
        self.fixture.append("CLAUDE.md", "\nUse `grep` to search.\n")
        self.assert_passes()

    def test_skill_of_the_other_tool_is_reported(self) -> None:
        self.fixture.append("skills/git-commit/SKILL.md", "Use `only-codex` first.\n")
        self.assert_reported("`only-codex` is not a skill this tool has")

    def test_phrase_matching_two_patterns_is_reported_once(self) -> None:
        self.fixture.append("skills/git-commit/SKILL.md", "Read `only-codex` skill first.\n")
        result = self.fixture.run()

        self.assertEqual(result.stderr.count("`only-codex` is not a skill this tool has"), 1, result.stderr)

    def test_generated_copy_may_name_a_skill_of_either_tool(self) -> None:
        self.fixture.write(
            "skills/rule-writing/SKILL.md",
            f"---\nname: rule-writing\n---\n\n{GENERATED_NOTICE}Read `only-codex` first.\n",
        )
        self.assert_passes()

    def test_enabled_plugin_counts_as_the_tool_s_skill(self) -> None:
        self.fixture.write("codex/skills/design/SKILL.md", "# Design\n")
        self.fixture.append("CLAUDE.md", "\nRead `design` for visual work.\n")
        self.assert_passes()

    def test_third_party_copy_is_not_checked(self) -> None:
        self.fixture.write("skills/vendor/SKILL.md", "See [their file](missing.md).\n")
        self.fixture.write("skills/vendor/LICENSE", "MIT\n")
        self.assert_passes()

    def test_missing_repository_path_is_reported(self) -> None:
        self.fixture.append("CLAUDE.md", "\nRun `scripts/gone.py --check`.\n")
        self.assert_reported("path scripts/gone.py does not exist")

    def test_listed_notice_that_no_hook_emits_is_reported(self) -> None:
        self.fixture.append("CLAUDE.md", "- Also `[OLD NOTICE]`.\n")
        self.assert_reported("CLAUDE.md:6: no hook emits [OLD NOTICE]")

    def test_emitted_notice_that_claude_instructions_omit_is_reported_where_it_is_emitted(self) -> None:
        self.fixture.append("scripts/notice-hook.py", "NOTICE = '[NEW NOTICE]'\n")
        self.assert_reported("scripts/notice-hook.py:2: emits [NEW NOTICE], which CLAUDE.md does not list")


if __name__ == "__main__":
    unittest.main()
