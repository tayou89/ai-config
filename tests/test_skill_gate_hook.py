"""Tests for scripts/skill-gate-hook.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
HOOK_SCRIPT = SCRIPTS_DIRECTORY / "skill-gate-hook.py"
HOOK_MODULE_NAME = "skill_gate_hook"


def load_hook() -> ModuleType:
    """Load the hook by path, since its file name is not importable; dataclasses need it in sys.modules first."""
    specification = importlib.util.spec_from_file_location(HOOK_MODULE_NAME, HOOK_SCRIPT)

    if specification and specification.loader:
        module = importlib.util.module_from_spec(specification)

        sys.path.insert(0, str(SCRIPTS_DIRECTORY))
        sys.modules[HOOK_MODULE_NAME] = module
        specification.loader.exec_module(module)
        return module
    else:
        raise ImportError(f"Cannot load {HOOK_SCRIPT}")


skill_gate_hook = load_hook()
SESSION = "session-1"
GATE_ON = "on"
GATE_OFF = "off"
DENY = "deny"
CODE_STYLE = "code-style"
PYTHON = "python"
TYPESCRIPT = "typescript"
UX_DESIGN = "ux-design"
FRONTEND_DESIGN = "frontend-design"
PYTHON_SKILLS = (CODE_STYLE, PYTHON)
TYPESCRIPT_SKILLS = (CODE_STYLE, TYPESCRIPT)
SCREEN_SKILLS = (UX_DESIGN, FRONTEND_DESIGN)
FIGMA_TOOL = "mcp__8cca5a5c-c20e-4d43-a1ba-f1af393422aa__use_figma"
GENERAL_AGENT = {"agent_id": "a1", "agent_type": "general-purpose"}
OTHER_AGENT = {"agent_id": "a2", "agent_type": "general-purpose"}
FORK_AGENT = {"agent_id": "f1", "agent_type": "fork"}
GUIDE_AGENT = {"agent_id": "g1", "agent_type": "claude-code-guide"}
FILE_CASES = (
    ("Edit", {"file_path": "/a/b.py"}, PYTHON_SKILLS),
    ("Write", {"file_path": "/a/b.pyi"}, PYTHON_SKILLS),
    ("NotebookEdit", {"notebook_path": "/a/n.ipynb"}, PYTHON_SKILLS),
    ("Write", {"file_path": "/a/b.ts"}, TYPESCRIPT_SKILLS),
    ("Edit", {"file_path": "/a/b.mjs"}, TYPESCRIPT_SKILLS),
    ("Edit", {"file_path": "/a/b.js"}, TYPESCRIPT_SKILLS),
    ("Write", {"file_path": "/a/b.tsx"}, (*TYPESCRIPT_SKILLS, *SCREEN_SKILLS)),
    ("Write", {"file_path": "/a/b.jsx"}, (*TYPESCRIPT_SKILLS, *SCREEN_SKILLS)),
    ("Write", {"file_path": "/a/b.svelte"}, (CODE_STYLE, *SCREEN_SKILLS)),
    ("Write", {"file_path": "/a/B.CSS"}, SCREEN_SKILLS),
    ("Write", {"file_path": "/a/b.html"}, SCREEN_SKILLS),
    ("Write", {"file_path": "/a/b.go"}, (CODE_STYLE,)),
    ("Write", {"file_path": "/a/b.sh"}, (CODE_STYLE,)),
    ("Write", {"file_path": "/private/tmp/scratchpad/x.py"}, PYTHON_SKILLS),
)
PASSING_FILE_CASES = (
    ("Write", {"file_path": "/a/README.md"}),
    ("Edit", {"file_path": "/a/settings.json"}),
    ("Write", {"file_path": "/a/claude-plans/plan-x.md"}),
    ("Write", {"file_path": "/a/Makefile"}),
    ("Read", {"file_path": "/a/b.py"}),
)
COMMAND_CASES = (
    ("cat > /tmp/x.py <<'EOF'\nprint(1)\nEOF", "`x.py`", PYTHON_SKILLS),
    ("cat <<'EOF' > src/app.ts\nconst a = 1;\nEOF", "`app.ts`", TYPESCRIPT_SKILLS),
    ("echo body | tee page.css", "`page.css`", SCREEN_SKILLS),
    ("sed -i '' 's/a/b/' src/x.ts", "`x.ts`", TYPESCRIPT_SKILLS),
    ("perl -pi -e 's/a/b/' tool.py", "`tool.py`", PYTHON_SKILLS),
    ("sed -i.bak -e 's/a/b/' tool.py", "`tool.py`", PYTHON_SKILLS),
    ("sed --in-place 's/a/b/' tool.py", "`tool.py`", PYTHON_SKILLS),
    ("python3 - <<'EOF'\nimport os\nprint(os.sep)\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("python3 <<'EOF'\nx = 1\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("python3 -c 'import os\nprint(os.sep)'", "this command's inline program", PYTHON_SKILLS),
    ("echo 'print(1)' | python3", "this command's inline program", PYTHON_SKILLS),
    ("python3.12 -I - arg <<'EOF'\nx\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("cd /tmp && python3 - <<'EOF'\nx\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("timeout 60 python3 - <<'EOF'\nx\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("env A=1 python3 - <<'EOF'\nx\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("uv run --with rich python - <<'EOF'\nx\nEOF", "this command's inline program", PYTHON_SKILLS),
    ("node -e 'const a = 1;\nconsole.log(a)'", "this command's inline program", TYPESCRIPT_SKILLS),
    ("node - <<'EOF'\nconsole.log(1)\nEOF", "this command's inline program", TYPESCRIPT_SKILLS),
    ("bash -c \"cat > a.sh <<'X'\necho hi\nX\"", "`a.sh`", (CODE_STYLE,)),
    ("bash -c 'echo x > a.py' > out.log", "`a.py`", PYTHON_SKILLS),
    ("python3 -Sc 'import os\nprint(os.sep)'", "this command's inline program", PYTHON_SKILLS),
    ("uv run -q python - <<'EOF'\nx\nEOF", "this command's inline program", PYTHON_SKILLS),
)
PASSING_COMMANDS = (
    "python3 -c 'print(1)'",
    "python3 -c \"import json,sys; print(json.load(sys.stdin)['a'])\" < data.json",
    "python3 tool.py <<'EOF'\ninput data\nEOF",
    "python3 -m json.tool < data.json",
    "python3 -W ignore tool.py",
    "node -e 'console.log(1)'",
    "node script.js",
    "git commit -F - <<'EOF'\nmessage\nEOF",
    "cat <<'EOF' > notes.md\ntext\nEOF",
    "echo x > out.txt",
    "grep -n x a.py && cat a.py",
    "sed -n '1,5p' a.py",
    "perl -Mstrict -ne 'print' a.py",
    "python3 -Im json.tool < data.json",
    "echo x | uv run ruff check python",
    "uv run --python 3.12 tool.py <<'EOF'\ninput\nEOF",
    "ls > /dev/null 2>&1",
    "",
)
COMPACT_BOUNDARY = {"type": "system", "subtype": "compact_boundary"}
COMPACT_SUMMARY = {"type": "user", "isCompactSummary": True}


def make_restored_entry(*skills: str) -> dict[str, Any]:
    restored = [{"name": skill} for skill in skills]

    return {"type": "attachment", "attachment": {"type": "invoked_skills", "skills": restored}}


class SkillGateHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_directory = Path(self.directory.name) / "config"
        self.transcript = Path(self.directory.name) / f"{SESSION}.jsonl"
        self.transcript.write_text(json.dumps({"type": "user"}) + "\n")
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config_directory)})

        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_code_and_screen_files_need_their_skills(self) -> None:
        for tool_name, tool_input, skills in FILE_CASES:
            requirement = skill_gate_hook.find_requirement(tool_name, tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, tool_input)

    def test_other_files_and_tools_pass(self) -> None:
        for tool_name, tool_input in PASSING_FILE_CASES:
            self.assertIsNone(skill_gate_hook.find_requirement(tool_name, tool_input, "/tmp"), tool_input)

    def test_shell_writes_and_inline_programs_need_their_skills(self) -> None:
        for command, target, skills in COMMAND_CASES:
            requirement = skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp")

            self.assertEqual(
                (requirement.target, requirement.skills) if requirement else None, (target, skills), command
            )

    def test_reading_commands_and_one_line_expressions_pass(self) -> None:
        for command in PASSING_COMMANDS:
            self.assertIsNone(skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp"), command)

    def test_monitor_and_terminal_commands_are_judged_like_bash(self) -> None:
        for tool_name in ("Monitor", "mcp__terminal__run_in_terminal"):
            requirement = skill_gate_hook.find_requirement(tool_name, {"command": "cat > a.py <<'EOF'\nx\nEOF"}, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, PYTHON_SKILLS, tool_name)

    def test_figma_writes_need_ux_design(self) -> None:
        for name in ("use_figma", "upload_assets", "create_new_file", "send_code_to_figma"):
            requirement = skill_gate_hook.find_requirement(f"mcp__figma-server__{name}", {}, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, (UX_DESIGN,), name)
        self.assertIsNone(skill_gate_hook.find_requirement("mcp__figma-server__get_screenshot", {}, "/tmp"))

    def test_edit_is_refused_until_its_skills_load(self) -> None:
        self.assertEqual(
            self._read_reason(self._decide()),
            "Writing `b.py` needs `code-style` and `python` loaded with the Skill tool first (CLAUDE.md Skill Triggers), and this session has no finished Skill-tool load of them since its start or last compaction.",
        )
        self._load(CODE_STYLE)
        self.assertIn("needs `python` loaded", self._read_reason(self._decide()))
        self.assertIn("load of it since", self._read_reason(self._decide()))
        self._load(PYTHON)
        self.assertIsNone(self._decide())

    def test_plugin_and_slash_names_count(self) -> None:
        self._load("frontend-design:frontend-design")
        self._load(f"/{UX_DESIGN}")
        self.assertIsNone(self._decide(tool_input={"file_path": "/a/page.css"}))

    def test_typed_slash_command_counts_for_the_main_thread(self) -> None:
        expansion = {
            "hook_event_name": "UserPromptExpansion",
            "session_id": SESSION,
            "expansion_type": "slash_command",
            "command_name": UX_DESIGN,
        }

        skill_gate_hook.handle_event(expansion, GATE_ON)
        self.assertIsNone(self._decide(tool_name=FIGMA_TOOL, tool_input={"code": "x"}))

    def test_subagent_loads_are_its_own(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self.assertIn("this agent has no finished", self._read_reason(self._decide(GENERAL_AGENT)))
        self._load(CODE_STYLE, GENERAL_AGENT)
        self._load(PYTHON, GENERAL_AGENT)
        self.assertIsNone(self._decide(GENERAL_AGENT))
        self.assertIsNotNone(self._decide(OTHER_AGENT))

    def test_subagent_loads_do_not_count_for_the_session(self) -> None:
        self._load(CODE_STYLE, GENERAL_AGENT)
        self._load(PYTHON, GENERAL_AGENT)
        self.assertIsNotNone(self._decide())

    def test_fork_counts_its_parent_loads(self) -> None:
        self.assertIn("this session has no finished", self._read_reason(self._decide(FORK_AGENT)))
        self._load(CODE_STYLE)
        self._load(PYTHON, FORK_AGENT)
        self.assertIsNone(self._decide(FORK_AGENT))
        self.assertIsNotNone(self._decide())

    def test_agents_without_the_skill_tool_pass(self) -> None:
        self.assertIsNone(self._decide(GUIDE_AGENT))
        self.assertIsNone(self._decide({"agent_id": "x1"}))

    def test_gate_off_passes_and_still_records(self) -> None:
        self.assertIsNone(self._decide(gate_setting=GATE_OFF))
        self._load(CODE_STYLE, gate_setting=GATE_OFF)
        self._load(PYTHON, gate_setting=GATE_OFF)
        self.assertIsNone(self._decide())

    def test_compaction_keeps_only_restored_skills(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self.assertIsNotNone(self._decide())
        self._append_transcript(
            COMPACT_BOUNDARY,
            {"type": "attachment", "attachment": {"type": "instructions"}},
            COMPACT_SUMMARY,
            make_restored_entry(PYTHON),
        )
        self.assertIn("needs `code-style` loaded", self._read_reason(self._decide()))
        self._load(CODE_STYLE)
        self.assertIsNone(self._decide())
        self.assertEqual([event["kind"] for event in self._read_record()].count("restored"), 1)

    def test_compaction_without_restored_skills_keeps_none(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self._append_transcript(
            COMPACT_BOUNDARY,
            COMPACT_SUMMARY,
            {"type": "assistant", "message": {"content": []}},
            make_restored_entry(PYTHON),
        )
        self.assertIn("needs `code-style` and `python`", self._read_reason(self._decide()))

    def test_manual_compaction_restores_after_its_command_lines(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self._append_transcript(
            COMPACT_BOUNDARY,
            COMPACT_SUMMARY,
            {"type": "user", "isMeta": True, "message": {"content": "<local-command-caveat>"}},
            {"type": "user", "message": {"content": "<command-name>/compact</command-name>"}},
            {"type": "user", "message": {"content": "<local-command-stdout>Compacted</local-command-stdout>"}},
            {"type": "attachment", "attachment": {"type": "skill_listing"}},
            make_restored_entry(CODE_STYLE, PYTHON),
        )
        self.assertIsNone(self._decide())

    def test_compaction_without_a_reply_yet_is_not_settled(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self._append_transcript(COMPACT_BOUNDARY, COMPACT_SUMMARY)
        self.assertIsNotNone(self._decide())
        self.assertNotIn("restored", [event["kind"] for event in self._read_record()])
        self._append_transcript({"type": "assistant", "message": {"content": []}})
        self.assertIsNotNone(self._decide())
        self.assertIn("restored", [event["kind"] for event in self._read_record()])

    def test_unwritten_compaction_is_not_settled_yet(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self.assertIsNotNone(self._decide())
        self.assertNotIn("restored", [event["kind"] for event in self._read_record()])
        self._append_transcript(COMPACT_BOUNDARY, make_restored_entry(CODE_STYLE, PYTHON))
        self.assertIsNone(self._decide())

    def test_second_compaction_uses_its_own_restore(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self._append_transcript(COMPACT_BOUNDARY, make_restored_entry(CODE_STYLE, PYTHON))
        self.assertIsNone(self._decide())
        self._compact()
        self._append_transcript(COMPACT_BOUNDARY, make_restored_entry(PYTHON))
        self.assertIn("needs `code-style` loaded", self._read_reason(self._decide()))

    def test_missing_transcript_leaves_compaction_unsettled(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self.transcript.unlink()
        self.assertIsNotNone(self._decide())

    def test_damaged_record_lines_are_skipped(self) -> None:
        self._load(CODE_STYLE)
        with self._get_record().open("a") as record:
            record.write("not json\n[1, 2]\n")
        self._load(PYTHON)
        self.assertIsNone(self._decide())

    def test_expired_records_are_removed_when_a_session_starts(self) -> None:
        expired_time = time.time() - 15 * 24 * 60 * 60
        old = self._make_old_record("old-session", "main.jsonl", expired_time)
        active = self._make_old_record("active-session", "main.jsonl", time.time())
        partly_old = self._make_old_record("mixed-session", "a1.jsonl", expired_time)
        fresh_sibling = partly_old.parent / "main.jsonl"

        fresh_sibling.write_text("{}\n")
        os.utime(active.parent, (expired_time,) * 2)
        self._load(CODE_STYLE)
        self.assertFalse(old.parent.exists())
        self.assertTrue(active.exists())
        self.assertFalse(partly_old.exists())
        self.assertTrue(fresh_sibling.exists())

    def test_running_an_existing_script_does_not_count_as_writing(self) -> None:
        script = Path(self.directory.name) / "generate.sh"

        script.write_text("cat > gen.ts <<'EOF'\nx\nEOF\npython3 - <<'EOF'\nprint(1)\nEOF\n")
        for command in (f"bash {script}", f"source {script}", f"sh {script} > out.log"):
            self.assertIsNone(skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp"), command)

    def test_compaction_that_never_happened_keeps_earlier_loads(self) -> None:
        self._load(CODE_STYLE)
        self._load(PYTHON)
        self._compact()
        self._append_transcript({"type": "assistant", "message": {"content": []}}, COMPACT_BOUNDARY)
        self.assertIsNone(self._decide())
        self.assertIsNone(self._decide())
        self.assertEqual(self._read_record()[-1]["skills"], [CODE_STYLE, PYTHON])

    def test_hook_script_refuses_and_survives_bad_input(self) -> None:
        refused = self._run_script({**self._make_edit_input(), "agent_type": None})
        broken = self._run_script("not json")
        missing_session = self._run_script({"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": None})

        self.assertEqual(json.loads(refused.stdout)["hookSpecificOutput"]["permissionDecision"], DENY)
        self.assertEqual((broken.returncode, broken.stdout), (0, ""))
        self.assertIn("skill-gate-hook:", broken.stderr)
        self.assertEqual((missing_session.returncode, missing_session.stdout), (0, ""))

    def _decide(
        self,
        agent: dict[str, Any] | None = None,
        tool_name: str = "Edit",
        tool_input: dict[str, Any] | None = None,
        gate_setting: str = GATE_ON,
    ) -> dict[str, Any] | None:
        hook_input = {**self._make_edit_input(), **(agent or {}), "tool_name": tool_name}

        if tool_input is not None:
            hook_input["tool_input"] = tool_input
        output: dict[str, Any] | None = skill_gate_hook.handle_event(hook_input, gate_setting)

        return output

    def _load(self, skill: str, agent: dict[str, Any] | None = None, gate_setting: str = GATE_ON) -> None:
        post = {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION,
            "tool_name": "Skill",
            "tool_input": {"skill": skill},
            **(agent or {}),
        }

        skill_gate_hook.handle_event(post, gate_setting)

    def _compact(self) -> None:
        compaction = {
            "hook_event_name": "PreCompact",
            "session_id": SESSION,
            "transcript_path": str(self.transcript),
            "trigger": "auto",
        }

        skill_gate_hook.handle_event(compaction, GATE_ON)

    def _append_transcript(self, *entries: dict[str, Any]) -> None:
        with self.transcript.open("a") as transcript:
            for entry in entries:
                transcript.write(json.dumps(entry) + "\n")

    def _make_edit_input(self) -> dict[str, Any]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": SESSION,
            "transcript_path": str(self.transcript),
            "cwd": "/tmp",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/a/b.py", "old_string": "a", "new_string": "b"},
        }

    def _make_old_record(self, session: str, name: str, modified: float) -> Path:
        record = self.config_directory / "skill-gate" / session / name

        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{}\n")
        os.utime(record, (modified, modified))
        return record

    def _get_record(self) -> Path:
        return self.config_directory / "skill-gate" / SESSION / "main.jsonl"

    def _read_record(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._get_record().read_text().splitlines()]

    def _read_reason(self, output: dict[str, Any] | None) -> str:
        return str((output or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", ""))

    def _run_script(self, hook_input: dict[str, Any] | str) -> subprocess.CompletedProcess[str]:
        text = hook_input if isinstance(hook_input, str) else json.dumps(hook_input)
        environment = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.config_directory), "SKILL_GATE": GATE_ON}

        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=text,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
