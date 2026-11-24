"""Tests for scripts/check-references.py.

Each test copies the real script into a small repository with stand-in
instruction files, rules, skills, and hooks whose references all resolve,
breaks one of them, and runs the script the way the commit check does:
in a plain folder, as on a copy of the index, unless the test makes it a
Git work tree.
"""

from __future__ import annotations

import json
import os
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
GIT_PREFIX = "GIT_"
GIT_SETTINGS = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
BROKEN_LINK = "link to missing file gone.md"
BROKEN_LINK_LINE = "See [gone](gone.md).\n"
MISSING_IMPORT_LINE = "@gone.md\n"
CODEX_ONLY_READ_LINE = "\nRead `only-codex` first.\n"
PROJECT_CLAUDE_PATH = ".claude/CLAUDE.md"
IGNORED_FOLDER_RULE = "worktrees/\n"
IGNORED_README_PATH = "worktrees/other/README.md"
QUIET = "-q"
COMMIT = "commit"
CLEAN_FILES = {
    "CLAUDE.md": "# Claude\n\n## Hooks\n\n- Notices: `[GOOD NOTICE]`.\n",
    "AGENTS.md": "# Project\n\nCommit as `git-commit` describes.\n",
    PROJECT_CLAUDE_PATH: "@../AGENTS.md\n",
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
        self.environment = {name: value for name, value in os.environ.items() if not name.startswith(GIT_PREFIX)}

        self.environment.update(GIT_SETTINGS)

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

    def initialize_git(self) -> None:
        self.run_git("init", QUIET)

    def run_git(self, *arguments: str) -> int:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            check=False,
        )

        return result.returncode

    def run(self, **variables: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repository / "scripts" / REFERENCE_SCRIPT.name)],
            capture_output=True,
            text=True,
            env={**self.environment, **variables},
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

    def write_import_chain(self, length: int) -> None:
        for hop in range(1, length):
            self.fixture.write(f"hop{hop}.md", f"@hop{hop + 1}.md\n")
        self.fixture.write(f"hop{length}.md", "# Last\n")
        self.fixture.append("CLAUDE.md", "\n@hop1.md\n")

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

    def test_project_instruction_file_is_checked(self) -> None:
        self.fixture.append("AGENTS.md", "\nSee [gone](rules/gone.md).\n")
        self.assert_reported("AGENTS.md:5: link to missing file rules/gone.md")

    def test_markdown_file_in_a_new_place_is_checked(self) -> None:
        self.fixture.write("docs/guide.md", "# Guide\n\nRun `scripts/gone.py` first.\n")
        self.assert_reported("docs/guide.md:3: path scripts/gone.py does not exist")

    def test_shared_rule_source_is_left_to_its_copies(self) -> None:
        self.fixture.write("shared-rules/core.md", "See [the copy's neighbour](only-beside-the-copy.md).\n")
        self.assert_passes()

    def test_file_git_ignores_is_not_checked(self) -> None:
        self.fixture.initialize_git()
        self.fixture.write(".gitignore", IGNORED_FOLDER_RULE)
        self.fixture.write(IGNORED_README_PATH, BROKEN_LINK_LINE)
        self.assert_passes()

    def test_untracked_file_git_would_commit_is_checked(self) -> None:
        self.fixture.initialize_git()
        self.fixture.write("notes.md", BROKEN_LINK_LINE)
        self.assert_reported("notes.md:1: link to missing file gone.md")

    def test_project_instruction_file_may_name_a_skill_of_either_tool(self) -> None:
        self.fixture.append("AGENTS.md", CODEX_ONLY_READ_LINE)
        self.assert_passes()

    def test_project_instruction_file_claude_does_not_import_is_read_by_codex(self) -> None:
        self.fixture.write("mods/tool/AGENTS.md", "# Tool\n\nRead `git-commit` first.\n")
        self.assert_reported("mods/tool/AGENTS.md:3: `git-commit` is not a skill this tool has")

    def test_markdown_file_directly_in_the_skills_folder_is_checked(self) -> None:
        self.fixture.write("skills/README.md", BROKEN_LINK_LINE)
        self.assert_reported(f"skills/README.md:1: {BROKEN_LINK}")

    def test_nested_repository_is_left_out_of_a_plain_copy(self) -> None:
        self.fixture.write("vendor/inner/.git", "gitdir: /elsewhere\n")
        self.fixture.write("vendor/inner/README.md", BROKEN_LINK_LINE)
        self.assert_passes()

    def test_git_variables_from_the_caller_are_ignored(self) -> None:
        self.fixture.initialize_git()
        self.fixture.write(".gitignore", IGNORED_FOLDER_RULE)
        self.fixture.write(IGNORED_README_PATH, BROKEN_LINK_LINE)
        result = self.fixture.run(GIT_DIR=str(self.fixture.repository / "missing"))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_file_in_a_merge_conflict_is_reported_once(self) -> None:
        self.fixture.initialize_git()
        self.fixture.write("notes.md", "base\n")
        self.fixture.run_git("add", "-A")
        self.fixture.run_git(COMMIT, QUIET, "-m", "base")
        self.fixture.run_git("checkout", QUIET, "-b", "other")
        self.fixture.write("notes.md", "theirs\n")
        self.fixture.run_git(COMMIT, QUIET, "-am", "theirs")
        self.fixture.run_git("checkout", QUIET, "-")
        self.fixture.write("notes.md", BROKEN_LINK_LINE)
        self.fixture.run_git(COMMIT, QUIET, "-am", "ours")
        merge = self.fixture.run_git("merge", QUIET, "other")
        result = self.fixture.run()

        self.assertNotEqual(merge, 0)
        self.assertEqual(result.stderr.count(BROKEN_LINK), 1, result.stderr)

    def test_claude_project_file_may_not_name_a_codex_skill(self) -> None:
        self.fixture.append(PROJECT_CLAUDE_PATH, CODEX_ONLY_READ_LINE)
        self.assert_reported(".claude/CLAUDE.md:3: `only-codex` is not a skill this tool has")

    def test_import_of_missing_file_is_reported(self) -> None:
        self.fixture.write(PROJECT_CLAUDE_PATH, "@../AGENT.md\n")
        self.assert_reported(".claude/CLAUDE.md:1: import of missing file `../AGENT.md`")

    def test_import_in_a_rules_file_is_checked(self) -> None:
        self.fixture.append("rules/collaboration-core.md", "\nAlso @gone.md.\n")
        self.assert_reported("rules/collaboration-core.md:7: import of missing file `gone.md.`")

    def test_import_in_project_rules_and_local_instructions_is_checked(self) -> None:
        self.fixture.write(".claude/rules/extra.md", MISSING_IMPORT_LINE)
        self.fixture.write("CLAUDE.local.md", "@also-gone.md\n")
        self.assert_reported(".claude/rules/extra.md:1: import of missing file `gone.md`")
        self.assert_reported("CLAUDE.local.md:1: import of missing file `also-gone.md`")

    def test_import_inside_an_imported_file_is_checked(self) -> None:
        self.fixture.append("AGENTS.md", "\n@docs/more.md\n")
        self.fixture.write("docs/more.md", "# More\n\n@gone.md\n")
        self.assert_reported("docs/more.md:3: import of missing file `gone.md`")

    def test_import_four_hops_deep_is_loaded(self) -> None:
        self.write_import_chain(4)
        self.assert_passes()

    def test_import_more_than_four_hops_deep_is_reported(self) -> None:
        self.write_import_chain(5)
        self.assert_reported("hop4.md:1: import of `hop5.md` is more than 4 hops deep, so Claude skips it")

    def test_import_ends_before_a_heading_anchor(self) -> None:
        self.fixture.append("CLAUDE.md", "\n@rules/collaboration-core.md#decisions-and-corrections\n")
        self.assert_passes()

    def test_import_in_html_comment_or_front_matter_is_not_read(self) -> None:
        self.fixture.write("rules/scoped.md", "---\ndescription: ask @owner\n---\n\n# Scoped\n")
        self.fixture.append("CLAUDE.md", "\n<!-- old: @gone.md -->\n")
        self.assert_passes()

    def test_import_leading_out_of_the_repository_is_not_checked(self) -> None:
        self.fixture.append("CLAUDE.md", "\n@../../outside.md\n")
        self.assert_passes()

    def test_import_with_an_escaped_space_resolves(self) -> None:
        self.fixture.write("two words.md", "# Two words\n")
        self.fixture.append("CLAUDE.md", "\n@two\\ words.md\n")
        self.assert_passes()

    def test_at_signs_claude_does_not_import_are_not_checked(self) -> None:
        words = 'Mail name@example.com, keep `@gone.md`, skip @"quoted.md", @~/home.md, @/absolute.md, and @@twice.\n'

        self.fixture.append("CLAUDE.md", f"\n{words}\n{FENCE}\n@gone.md\n{FENCE}\n")
        self.assert_passes()

    def test_at_sign_outside_an_instruction_file_is_not_an_import(self) -> None:
        self.fixture.append("skills/code-plan/SKILL.md", "\n### Animate enter states with @starting-style\n")
        self.assert_passes()

    def test_import_through_a_text_file_of_any_name_is_followed(self) -> None:
        self.fixture.write("policy.txt", MISSING_IMPORT_LINE)
        self.fixture.append("CLAUDE.md", "\n@policy.txt\n")
        self.assert_reported("policy.txt:1: import of missing file `gone.md`")

    def test_import_of_a_file_claude_does_not_read_as_text_is_reported(self) -> None:
        (self.fixture.repository / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR")
        self.fixture.append("CLAUDE.md", "\n@image.png\n")
        self.assert_reported("CLAUDE.md:7: import of `image.png` names a file Claude does not read as text")

    def test_import_in_a_nested_project_rules_folder_is_checked(self) -> None:
        self.fixture.write("feature/.claude/rules/local.md", MISSING_IMPORT_LINE)
        self.assert_reported("feature/.claude/rules/local.md:1: import of missing file `gone.md`")

    def test_code_spans_and_comments_are_read_in_one_pass(self) -> None:
        self.fixture.append("CLAUDE.md", "\n`<!--` @gone.md `-->`\n")
        self.assert_reported("CLAUDE.md:7: import of missing file `gone.md`")

    def test_import_inside_a_double_or_multi_line_code_span_is_not_read(self) -> None:
        self.fixture.append("CLAUDE.md", "\nKeep ``@gone.md`` and `a\n@also-gone.md` as text.\n")
        self.assert_passes()

    def test_file_first_reached_deep_keeps_its_depth(self) -> None:
        for name, target in (("a", "b"), ("b", "c"), ("c", "shared"), ("shared", "leaf")):
            self.fixture.write(f"{name}.md", f"@{target}.md\n")
        self.fixture.write("leaf.md", "# Leaf\n")
        self.fixture.append("CLAUDE.md", "\n@a.md\n@shared.md\n")
        self.assert_reported("shared.md:1: import of `leaf.md` is more than 4 hops deep, so Claude skips it")

    def test_import_counts_from_where_a_linked_file_really_sits(self) -> None:
        self.fixture.write("docs/instructions.md", "@more.md\n")
        self.fixture.write("docs/more.md", "# More\n")
        (self.fixture.repository / PROJECT_CLAUDE_PATH).unlink()
        (self.fixture.repository / PROJECT_CLAUDE_PATH).symlink_to("../docs/instructions.md")
        self.assert_passes()

    def test_imported_project_instruction_file_may_quote_a_codex_skill_section(self) -> None:
        self.fixture.append("AGENTS.md", '\nFollow `only-codex` "Codex only".\n')
        self.assert_passes()
        self.fixture.append("AGENTS.md", 'Then follow `only-codex` "Missing part".\n')
        self.assert_reported('AGENTS.md:6: `only-codex` has no section "Missing part"')

    def test_skill_folder_with_a_license_folder_is_still_checked(self) -> None:
        self.fixture.write("skills/local-copy/SKILL.md", BROKEN_LINK_LINE)
        self.fixture.write("skills/local-copy/LICENSE-data/notes.txt", "data\n")
        self.assert_reported(f"skills/local-copy/SKILL.md:1: {BROKEN_LINK}")


if __name__ == "__main__":
    unittest.main()
