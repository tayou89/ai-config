"""Tests for scripts/skill-gate-hook.py."""

from __future__ import annotations

import importlib.util
import json
import os
import re
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
DECISIONS = "decisions-and-collaboration"
SESSIONS = "session-management"
GIT_COMMIT = "git-commit"
WRAP_UP_TOOL = "mcp__ccd_turn__wrap_up"
QUESTION_TOOL = "AskUserQuestion"
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
    ("Write", {"file_path": "/n/side-session-notes/local_1/report.md"}, ("report-writing",)),
    ("Edit", {"file_path": "/n/side-session-notes/local_1/INDEX.md"}, ("report-writing",)),
)
PASSING_FILE_CASES = (
    ("Write", {"file_path": "/a/README.md"}),
    ("Edit", {"file_path": "/a/settings.json"}),
    ("Write", {"file_path": "/a/claude-plans/plan-x.md"}),
    ("Write", {"file_path": "/n/side-session-notes/bulletin.md"}),
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
    ("cat > '/n/side-session-notes/local_1/r.md' <<'EOF'\ntext\nEOF", "`r.md`", ("report-writing",)),
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
EMIL_DESIGN_ENG = "emil-design-eng"
WEB_MOTION_TECHNIQUES = "web-motion-techniques"
MOTION_SKILLS = (UX_DESIGN, EMIL_DESIGN_ENG, WEB_MOTION_TECHNIQUES)
SCREEN_MOTION_SKILLS = (*SCREEN_SKILLS, EMIL_DESIGN_ENG, WEB_MOTION_TECHNIQUES)
SCRIPT_MOTION_SKILLS = (*TYPESCRIPT_SKILLS, *MOTION_SKILLS)
MOTION_FILE_CASES = (
    (
        "Write",
        {"file_path": "/a/a.css", "content": ".menu { transition: transform 200ms ease-out; }"},
        SCREEN_MOTION_SKILLS,
    ),
    (
        "Edit",
        {"file_path": "/a/a.css", "old_string": ".a {}", "new_string": ".a {}\n@keyframes fade { to { opacity: 1; } }"},
        SCREEN_MOTION_SKILLS,
    ),
    ("Write", {"file_path": "/a/a.scss", "content": ".a { transition-duration: var(--slow); }"}, SCREEN_MOTION_SKILLS),
    (
        "Write",
        {"file_path": "/a/b.svelte", "content": "<style>.a { animation: spin 1s linear infinite; }</style>"},
        (CODE_STYLE, *SCREEN_MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.tsx", "content": "element.animate(frames, { duration: 600 });"},
        (*TYPESCRIPT_SKILLS, *SCREEN_MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.js", "content": "document.startViewTransition(update);"},
        (*TYPESCRIPT_SKILLS, *MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.ts", "content": "box.style.transition = 'opacity 0.3s ease';"},
        (*TYPESCRIPT_SKILLS, *MOTION_SKILLS),
    ),
    (
        "Write",
        {
            "file_path": "/a/record.md",
            "content": "이 움직임은 첫 화면에서 들어갈 때만 씀(1초 안쪽, 누르면 바로 넘어감).",
        },
        MOTION_SKILLS,
    ),
    ("Write", {"file_path": "/a/notes.md", "content": "`transition: opacity 150ms ease`로 바꿈"}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/tokens.json", "content": '{"motion-duration-fast": "150ms"}'}, MOTION_SKILLS),
    ("Edit", {"file_path": "/a/a.md", "old_string": "x", "new_string": "x\nfade 0.2s"}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 0.6초 동안 이어진다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "메뉴는 페이드인 300ms로 나타난다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "The panel slides in with ease-out over 400 ms."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.txt", "content": "The intro animation lasts 2 seconds."}, MOTION_SKILLS),
    (
        "Write",
        {"file_path": "/n/side-session-notes/local_1/r.md", "content": "모션은 0.8s로 늘렸습니다."},
        ("report-writing", *MOTION_SKILLS),
    ),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 0.8 초로 한다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 1초안에 끝낸다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 .8s로 한다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 800밀리초로 한다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "등장은 0.8초로 합니다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환 easing은 ease-out으로 한다."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "animation easing: linear"}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환 길이:\n0.8초"}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "| 항목 | 값 |\n| 전환 | 0.8초 |"}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/tokens.json", "content": '{"motion": {"fast": {"duration": 150}}}'}, MOTION_SKILLS),
    (
        "Write",
        {"file_path": "/a/a.tsx", "content": '<motion.div transition={{ duration: 0.8, ease: "easeOut" }} />'},
        (*TYPESCRIPT_SKILLS, *SCREEN_MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.ts", "content": "animate(element, { opacity: 1 }, { duration: 0.8 });"},
        (*TYPESCRIPT_SKILLS, *MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.ts", "content": 'gsap.to(element, { duration: 0.8, ease: "power2.out" });'},
        (*TYPESCRIPT_SKILLS, *MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.ts", "content": "element.animate (frames, { duration: 800 });"},
        (*TYPESCRIPT_SKILLS, *MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.html", "content": '<div class="transition-opacity duration-700 ease-out">'},
        SCREEN_MOTION_SKILLS,
    ),
    (
        "Write",
        {"file_path": "/a/a.svg", "content": '<animate attributeName="opacity" dur="0.8s" />'},
        SCREEN_MOTION_SKILLS,
    ),
    (
        "Write",
        {"file_path": "/a/a.ts", "content": '{"transitionDuration": "600ms"}'},
        (*TYPESCRIPT_SKILLS, *MOTION_SKILLS),
    ),
    (
        "Write",
        {"file_path": "/a/a.json", "content": '{\n  "motion": {\n    "fast": {\n      "duration": 600\n    }\n  }\n}'},
        MOTION_SKILLS,
    ),
    ("Write", {"file_path": "/a/a.md", "content": "전환 길이: \n0.8초"}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 1초안으로 끝내세요."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 1초대로 줄이세요."}, MOTION_SKILLS),
    ("Write", {"file_path": "/a/a.md", "content": "전환은 ease로 한다."}, MOTION_SKILLS),
    (
        "Write",
        {"file_path": "/a/a.css", "content": "/* transition: opacity 200ms */\n.a { color: red; }"},
        SCREEN_MOTION_SKILLS,
    ),
)
PASSING_MOTION_FILE_CASES = (
    (
        "Edit",
        {
            "file_path": "/a/a.css",
            "old_string": ".a { transition: opacity 1s; }\n.b {}",
            "new_string": ".a { transition: opacity 1s; }\n.b { color: red; }",
        },
        SCREEN_SKILLS,
    ),
    (
        "Edit",
        {"file_path": "/a/a.css", "old_string": ".a { transition: opacity 1s; }", "new_string": ".a {}"},
        SCREEN_SKILLS,
    ),
    (
        "Write",
        {"file_path": "/a/plan.md", "content": "묶음 5 초안이 커밋되지 않은 채 남음. 전환 완료 소식을 기다림."},
        None,
    ),
    ("Write", {"file_path": "/a/plan.md", "content": "```python\ntransition: StateTransition\n```"}, None),
    (
        "Write",
        {"file_path": "/a/report.md", "content": "3장(7초, 35초)을 봤습니다. 화면은 설명용 애니메이션입니다."},
        None,
    ),
    ("Write", {"file_path": "/a/a.md", "content": "전환율은 2026-11-18에 5% 올랐다."}, None),
    ("Write", {"file_path": "/a/a.md", "content": "모드 전환 뒤 30 초과 시간은 버린다."}, None),
    ("Write", {"file_path": "/a/a.md", "content": "프로모션 배너 요청을 5초 뒤 재시도합니다."}, None),
    ("Write", {"file_path": "/a/a.ts", "content": "const transitionDuration = response.elapsed;"}, TYPESCRIPT_SKILLS),
    ("Write", {"file_path": "/a/a.json", "content": '{"name": "x", "duration": 30}'}, None),
    (
        "Write",
        {
            "file_path": "/a/a.md",
            "content": "전환 작업은 여러 단계로 나뉘며 각 단계는 따로 검토하고 기록을 남긴 뒤 다음으로 넘어가는데, 담당자가 바뀌는 경우에는 인수인계 문서와 점검표를 함께 넘기고 받은 쪽이 다시 읽어 본 다음, 마지막 확인에는 5초가 걸린다.",
        },
        None,
    ),
    ("Write", {"file_path": "/a/a.py", "content": 'delay = "transition 5s"'}, PYTHON_SKILLS),
    ("Write", {"file_path": "/a/a.ts", "content": "const transition: StateTransition = next;"}, TYPESCRIPT_SKILLS),
    ("Write", {"file_path": "/a/a.ts", "content": "const timeout = { duration: 500 };"}, TYPESCRIPT_SKILLS),
)
MOTION_MESSAGE_CASES = (
    (
        "SendMessage",
        {
            "to": "x",
            "message": "- 공통: 1초 안쪽, 누르면 바로 끝으로 넘어감, 움직임 줄이기 설정이면 짧게 사라지며 바뀜.",
        },
    ),
    ("Agent", {"description": "d", "prompt": "Make the page transition 0.8s with ease-out."}),
    ("mcp__ccd_session__start_session", {"title": "t", "prompt": "The fade-in lasts 600ms."}),
    ("mcp__ccd_session__start_session", {"title": "t", "prompt": "Fix it.", "background": "전환은 1초 안쪽으로 묶음."}),
)
PASSING_MESSAGE_CASES = (
    ("SendMessage", {"to": "x", "message": "PR을 합치고 5초 뒤 다시 확인합니다."}),
    ("SendMessage", {"to": "x", "message": {"type": "shutdown_request"}}),
    ("Agent", {"description": "d", "prompt": "Find the files that read the config."}),
    ("mcp__ccd_session__start_session", {"title": "t", "prompt": "Fix the parser."}),
)
MOTION_COMMAND_CASES = (
    ("cat > brief.md <<'EOF'\n전환은 0.4초로 한다\nEOF", "`brief.md` with a motion value", MOTION_SKILLS),
    ("cat > a.css <<'EOF'\n.a { transition: opacity 1s; }\nEOF", "`a.css` with a motion value", SCREEN_MOTION_SKILLS),
    ('codex exec -s read-only "make the menu animation 300ms"', "a `codex` prompt with a motion value", MOTION_SKILLS),
    (
        "codex exec - <<'EOF'\nThe fade lasts 0.5s.\nEOF",
        "a `codex` prompt with a motion value",
        MOTION_SKILLS,
    ),
    ("echo 'transition: opacity 800ms;' > a.css", "`a.css` with a motion value", SCREEN_MOTION_SKILLS),
    ("printf '%s' '전환은 0.4초로 한다' > brief.md", "`brief.md` with a motion value", MOTION_SKILLS),
    ("sed -i '' 's/200ms/800ms/' a.css", "`a.css` with a motion value", SCREEN_MOTION_SKILLS),
    (
        "printf 'transition: opacity 1s' | tee a.py a.css",
        "`a.css` with a motion value",
        (*PYTHON_SKILLS, *SCREEN_MOTION_SKILLS),
    ),
    ("printf 'fade 0.2s' | codex exec 'Implement the brief.'", "a `codex` prompt with a motion value", MOTION_SKILLS),
)
PASSING_MOTION_COMMANDS = (
    'codex exec --image a.png animation-300ms.png -- "run the tests"',
    "sed -i '' 's/linear/radial/' a.css",
    'codex exec -s read-only "review the parser"',
    'codex exec -o transition-300ms.log "run the tests"',
    "codex exec - < brief.md",
    "wc -l > counts.txt <<'EOF'\nfade 0.2s\nEOF",
    "grep -rn 'transition: 200ms' src",
    "echo 'fade 0.2s'",
)
MESSAGE_MOTION_REASON = (
    "Writing a message with a motion value needs `ux-design` and `emil-design-eng` and `web-motion-techniques` "
    'loaded with the Skill tool first (CLAUDE.md "Skill Triggers"), and this session has no finished Skill-tool '
    "load of them since its start or last compaction."
)
COMPACT_BOUNDARY = {"type": "system", "subtype": "compact_boundary"}
COMPACT_SUMMARY = {"type": "user", "isCompactSummary": True}
CHOICE_SHOWN_SCRIPT = SCRIPTS_DIRECTORY / "choice-shown.py"
CHOICE_ASK = "어느 방식으로 다시 만들까요?"
CHOICE_BUTTONS = [{"label": "원근 비행으로"}, {"label": "은하 구름까지"}]
CHOICE_BOX = {"recap": "r", "ask": CHOICE_ASK, "actions": CHOICE_BUTTONS}
CHOICE_REASON = 'A choice question in a session that has loaded `ux-design` needs, before it is asked, a record of what shows each option or of why it is not a choice about a screen (decisions-and-collaboration "Presenting decisions"). Not recorded: "어느 방식으로 다시 만들까요?": 원근 비행으로, 은하 구름까지. Show every option first, then record it with `python3 ~/.claude/scripts/choice-shown.py --question "<the question as asked>" --option "<label>" "<sent file path or URL>"`, one `--option` per option (one board may show several), or with `--not-visual "<why it is not a choice about a screen>"` in place of the options. Asking again without the record is refused again.'


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

    def test_figma_code_of_several_lines_also_needs_the_code_skills(self) -> None:
        program = {"code": "const page = figma.currentPage;\nreturn page.name;"}
        cases = [
            ("use_figma", program, (UX_DESIGN, *TYPESCRIPT_SKILLS)),
            ("use_figma", {"code": "return figma.currentPage.name;"}, (UX_DESIGN,)),
            ("upload_assets", program, (UX_DESIGN,)),
        ]

        for name, tool_input, skills in cases:
            requirement = skill_gate_hook.find_requirement(f"mcp__figma-server__{name}", tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, name)

    def test_questions_with_choices_need_the_decision_skill(self) -> None:
        two = [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]
        buttons = [{"label": "네, 진행"}, {"label": "고칠 곳 있음"}]
        cases = [
            ("AskUserQuestion", {"questions": [{"question": "어느 쪽?", "options": two}]}, (DECISIONS,)),
            ("AskUserQuestion", {"questions": [{"question": "어느 쪽?", "options": two[:1]}]}, None),
            ("mcp__ccd_turn__wrap_up", {"recap": "r", "ask": "할까요?", "actions": buttons}, (DECISIONS,)),
            ("mcp__ccd_turn__wrap_up", {"recap": "r", "ask": "할까요?", "actions": json.dumps(buttons)}, (DECISIONS,)),
            ("mcp__ccd_turn__wrap_up", {"recap": "r", "ask": "할까요?", "actions": buttons[:1]}, None),
            ("mcp__ccd_turn__wrap_up", {"recap": "r"}, None),
        ]

        for name, tool_input, skills in cases:
            requirement = skill_gate_hook.find_requirement(name, tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, tool_input)

    def test_questions_about_sessions_or_commits_need_the_skill_that_decides_them(self) -> None:
        one = [{"label": "네, 맡겨 주세요"}]
        two = [{"label": "새 세션에서"}, {"label": "여기서 계속"}]
        options = [{"label": "Push", "description": "a"}, {"label": "Wait", "description": "b"}]
        timing = [{"label": "Start", "description": "a"}, {"label": "Later", "description": "b"}]
        colors = [{"label": "파랑", "description": "이 세션에서 정한 색"}, {"label": "빨강", "description": "b"}]
        cases = [
            (WRAP_UP_TOOL, {"ask": "영어 답 막는 고침을 설정 관리 세션에 맡길까요?", "actions": one}, (SESSIONS,)),
            (WRAP_UP_TOOL, {"ask": "어디서 이어 갈까요?", "actions": two}, (DECISIONS, SESSIONS)),
            (WRAP_UP_TOOL, {"ask": "끝난 세션 셋을 닫을까요?", "actions": [{"label": "닫기"}]}, (SESSIONS,)),
            (WRAP_UP_TOOL, {"ask": "디자인 세션에 함께 전달할까요?", "actions": [{"label": "네"}]}, (SESSIONS,)),
            (WRAP_UP_TOOL, {"ask": "지도 카드 화면을 캡처대로 커밋할까요?", "actions": [{"label": "네, 커밋"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "검사가 끝나면 합치고 설치할까요?", "actions": json.dumps(one)}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "이대로 둘까요?", "actions": [{"action": "commit", "label": "Commit"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "끝낼까요?", "actions": [{"action": "create_pr"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "이어 갈까요?", "actions": [{"action": "new_session"}]}, (SESSIONS,)),
            (
                WRAP_UP_TOOL,
                {"ask": "Merge it and archive this session?", "actions": two},
                (DECISIONS, SESSIONS, GIT_COMMIT),
            ),
            (
                QUESTION_TOOL,
                {"questions": [{"question": "Start the review in a new session?", "options": timing}]},
                (DECISIONS, SESSIONS),
            ),
            (QUESTION_TOOL, {"questions": [{"question": "Which first?", "options": options}]}, (DECISIONS, GIT_COMMIT)),
            (WRAP_UP_TOOL, {"ask": "PR을 열까요?", "actions": [{"label": "열기"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "Review both PRs first?", "actions": [{"label": "Yes"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "Keep merging the fixes?", "actions": [{"label": "Yes"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "고친 내용을 합친 뒤 볼까요?", "actions": [{"label": "네"}]}, (GIT_COMMIT,)),
            (WRAP_UP_TOOL, {"ask": "Approve the wording?", "actions": [{"label": "Approve"}]}, None),
            (WRAP_UP_TOOL, {"ask": "Is this commitment clear?", "actions": [{"label": "Yes"}]}, None),
            (
                QUESTION_TOOL,
                {"questions": [{"question": "Push the branch now?", "options": options[:1]}]},
                (GIT_COMMIT,),
            ),
            (QUESTION_TOOL, {"questions": [{"question": "어느 색으로 할까요?", "options": colors}]}, (DECISIONS,)),
            (WRAP_UP_TOOL, {"ask": "두 표를 하나로 정리할까요?", "actions": [{"label": "정리하기"}]}, None),
            (WRAP_UP_TOOL, {"recap": "세션을 시작했고 커밋했습니다.", "ask": "표를 고칠까요?", "actions": [{"label": "고치기"}]}, None),
            (WRAP_UP_TOOL, {"recap": "세션을 시작했고 커밋했습니다."}, None),
        ]

        for name, tool_input, skills in cases:
            requirement = skill_gate_hook.find_requirement(name, tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, tool_input)

    def test_session_question_is_refused_until_the_session_skill_loads(self) -> None:
        tool_input = {"recap": "r", "ask": "이 고침을 설정 관리 세션에 맡길까요?", "actions": [{"label": "네, 맡겨 주세요"}]}

        self.assertEqual(
            self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=tool_input)),
            "Writing a question about a session needs `session-management` loaded with the Skill tool first (CLAUDE.md \"Skill Triggers\"), and this session has no finished Skill-tool load of it since its start or last compaction. The session and commit skills say which of these steps are taken without asking.",
        )
        self._load(SESSIONS)
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=tool_input))

    def test_commit_question_is_refused_until_the_commit_skill_loads(self) -> None:
        tool_input = {"recap": "r", "ask": "커밋하고 올릴까요?", "actions": [{"label": "네"}]}

        self.assertEqual(
            self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=tool_input)),
            "Writing a question about committing, pushing, or merging needs `git-commit` loaded with the Skill tool first (CLAUDE.md \"Skill Triggers\"), and this session has no finished Skill-tool load of it since its start or last compaction. The session and commit skills say which of these steps are taken without asking.",
        )
        self._load(GIT_COMMIT)
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=tool_input))

    def test_choice_question_reason_names_only_what_is_missing(self) -> None:
        tool_input = {"recap": "r", "ask": "새 세션에서 할까요?", "actions": [{"label": "새 세션"}, {"label": "여기서"}]}

        self._load(SESSIONS)
        reason = self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=tool_input))

        self.assertIn("needs `decisions-and-collaboration` loaded", reason)
        self.assertNotIn("The session and commit skills", reason)
        self._load(DECISIONS)
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=tool_input))

    def test_design_choice_is_refused_until_every_option_is_recorded(self) -> None:
        self._load_design_skills()
        self.assertEqual(self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX)), CHOICE_REASON)
        self.assertIsNotNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))
        self._record_choice(CHOICE_ASK, {"원근 비행으로": "/tmp/a.png"})
        self.assertIn(
            'Not recorded: "어느 방식으로 다시 만들까요?": 은하 구름까지.',
            self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX)),
        )
        self._record_choice(CHOICE_ASK, {"은하 구름까지": "/tmp/b.png"})
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))

    def test_renamed_option_needs_its_own_record(self) -> None:
        renamed = {**CHOICE_BOX, "actions": [CHOICE_BUTTONS[0], {"label": "은하 구름으로"}]}

        self._load_design_skills()
        self._record_choice(CHOICE_ASK, {"원근 비행으로": "/tmp/a.png", "은하 구름까지": "/tmp/a.png"})
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))
        self.assertIn(": 은하 구름으로.", self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=renamed)))

    def test_choice_recorded_as_not_about_a_screen_passes(self) -> None:
        self._load_design_skills()
        self._record_choice(CHOICE_ASK, {}, "검토 순서를 묻는 질문")
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))

    def test_design_session_counts_after_a_compaction_left_out_ux_design(self) -> None:
        self._load_design_skills()
        self._compact()
        self._append_transcript(COMPACT_BOUNDARY, make_restored_entry(DECISIONS), {"type": "assistant"})
        self.assertEqual(self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX)), CHOICE_REASON)

    def test_design_session_counts_when_only_a_restore_shows_ux_design(self) -> None:
        record = self.config_directory / "skill-gate" / SESSION / "main.jsonl"

        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"kind": "restored", "skills": [UX_DESIGN, DECISIONS]}) + "\n")
        self.assertEqual(self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX)), CHOICE_REASON)

    def test_choices_outside_design_sessions_or_with_one_option_are_not_checked(self) -> None:
        one_button = {**CHOICE_BOX, "actions": CHOICE_BUTTONS[:1]}

        self._load(DECISIONS)
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))
        self._load(UX_DESIGN)
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=one_button))

    def test_box_with_buttons_but_no_question_is_refused(self) -> None:
        self._load_design_skills()
        for ask in ({}, {"ask": "  "}):
            box = {"recap": "Choose a layout.", "actions": CHOICE_BUTTONS, **ask}

            self.assertIn(
                "Not recorded: a box with buttons but no question, which needs its question in `ask`",
                self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=box)),
                box,
            )

    def test_box_with_an_app_action_is_not_checked(self) -> None:
        actions = [{"action": "verify", "label": "확인"}, {"label": "그대로"}]
        unknown = [{"action": "reply", "label": "A"}, {"label": "B"}]

        self._load_design_skills()
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input={**CHOICE_BOX, "actions": actions}))
        self.assertIsNotNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input={**CHOICE_BOX, "actions": unknown}))

    def test_choice_wording_does_not_skip_the_check(self) -> None:
        box = {"recap": "r", "ask": "메뉴 두 개를 합칠까요?", "actions": [{"label": "합치기"}, {"label": "그대로"}]}

        self._load_design_skills()
        self._load(GIT_COMMIT)
        self.assertIn("Not recorded:", self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=box)))

    def test_each_dialog_question_is_checked_on_its_own(self) -> None:
        two = [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]
        questions = [{"question": "색은?", "options": two}, {"question": "배치는?", "options": two}]
        dialog = {"questions": questions}

        self._load_design_skills()
        self._record_choice("색은?", {"A": "/tmp/a.png", "B": "/tmp/b.png"})
        reason = self._read_reason(self._decide(tool_name=QUESTION_TOOL, tool_input=dialog))

        self.assertIn('Not recorded: "배치는?": A, B.', reason)
        self.assertNotIn("색은?", reason)

    def test_choice_list_as_text_and_extra_spaces_match_the_record(self) -> None:
        box = {"recap": "r", "ask": "  어느 방식으로\n다시  만들까요? ", "actions": json.dumps(CHOICE_BUTTONS)}

        self._load_design_skills()
        self.assertIn("Not recorded:", self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=box)))
        self._record_choice(CHOICE_ASK, {"원근 비행으로": "/tmp/a.png", "은하 구름까지": "/tmp/a.png"})
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=box))

    def test_missing_decision_skill_is_refused_first(self) -> None:
        self._load(UX_DESIGN)
        self.assertIn(
            "needs `decisions-and-collaboration` loaded",
            self._read_reason(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX)),
        )

    def test_unreadable_choice_record_lets_the_question_through(self) -> None:
        self._load_design_skills()
        with (
            mock.patch.object(skill_gate_hook, "read_choice_records", side_effect=OSError("broken")),
            mock.patch("sys.stderr") as stderr,
        ):
            self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))
        self.assertIn("choice record unchecked", "".join(call.args[0] for call in stderr.write.call_args_list))

    def test_gate_off_lets_an_unrecorded_choice_through(self) -> None:
        self._load_design_skills()
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX, gate_setting=GATE_OFF))

    def test_record_command_lets_the_same_question_through_afterwards(self) -> None:
        board = Path(self.directory.name) / "board.png"
        environment = {**os.environ, "CLAUDE_CODE_SESSION_ID": SESSION}
        command = [
            sys.executable,
            str(CHOICE_SHOWN_SCRIPT),
            "--question",
            CHOICE_ASK,
            "--option",
            "원근 비행으로",
            str(board),
            "--option",
            "은하 구름까지",
            str(board),
        ]

        board.write_bytes(b"png")
        self._load_design_skills()
        self.assertIsNotNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))
        self.assertIsNotNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))
        subprocess.run(command, env=environment, check=True, capture_output=True)
        self.assertIsNone(self._decide(tool_name=WRAP_UP_TOOL, tool_input=CHOICE_BOX))

    def test_motion_values_need_the_motion_skills(self) -> None:
        for tool_name, tool_input, skills in MOTION_FILE_CASES:
            requirement = skill_gate_hook.find_requirement(tool_name, tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, tool_input)

    def test_writes_without_new_motion_values_need_only_their_own_skills(self) -> None:
        for tool_name, tool_input, skills in PASSING_MOTION_FILE_CASES:
            requirement = skill_gate_hook.find_requirement(tool_name, tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, tool_input)

    def test_write_over_a_file_counts_only_new_motion_lines(self) -> None:
        page = Path(self.directory.name) / "page.css"
        unchanged = {"file_path": str(page), "content": ".a { transition: opacity 1s; }\n.b { color: red; }\n"}
        added = {"file_path": str(page), "content": ".a { transition: opacity 1s; }\n.b { animation: spin 2s; }\n"}

        page.write_text(".a { transition: opacity 1s; }\n.b {}\n")
        self.assertEqual(skill_gate_hook.find_requirement("Write", unchanged, "/tmp").skills, SCREEN_SKILLS)
        self.assertEqual(skill_gate_hook.find_requirement("Write", added, "/tmp").skills, SCREEN_MOTION_SKILLS)

    def test_edit_is_judged_on_the_whole_file_it_changes(self) -> None:
        page = Path(self.directory.name) / "page.css"
        record = Path(self.directory.name) / "record.md"
        script = Path(self.directory.name) / "motion.ts"
        cases = (
            (page, ".a { transition: opacity 600ms; }\n", "600ms", "800ms", SCREEN_MOTION_SKILLS),
            (page, ".a {\n  transition-duration:\n    600ms;\n}\n", "600ms", "800ms", SCREEN_MOTION_SKILLS),
            (
                page,
                ".a { transition: opacity 1s; }\n",
                ".a { transition: opacity 1s; }",
                ".a { transition: opacity 1s; }\n.b { transition: opacity 1s; }",
                SCREEN_MOTION_SKILLS,
            ),
            (page, ".a { transition: opacity 1s; }\n.b {}\n", ".b {}", ".b { color: red; }", SCREEN_SKILLS),
            (page, ".a { transition: opacity 1s; }\n", ".a {", ".card {", SCREEN_SKILLS),
            (
                page,
                ".a { transition: opacity 200ms; }\n.b { transition: opacity 800ms; }\n",
                ".a { transition: opacity 200ms; }\n.b { transition: opacity 800ms; }",
                ".a { transition: opacity 800ms; }\n.b { transition: opacity 200ms; }",
                SCREEN_MOTION_SKILLS,
            ),
            (page, ".a { transition: opacity\n 600ms ease; }\n", "600ms", "800ms", SCREEN_MOTION_SKILLS),
            (script, "gsap.to('[data-label=\"}\"]', {duration: 600});\n", "600", "800", SCRIPT_MOTION_SKILLS),
            (
                script,
                "element.animate([" + "{opacity: 0.5}, " * 45 + "], {duration: 600});\n",
                "600",
                "800",
                SCRIPT_MOTION_SKILLS,
            ),
            (script, "element.animate([], {duration: 600}), render(1);\n", "render(1)", "render(2)", TYPESCRIPT_SKILLS),
            (record, "이 움직임은 1초 안쪽으로 한다.\n", "1초", "3초", MOTION_SKILLS),
            (record, "이 움직임은 1초 안쪽으로 한다.\n", "missing", "3초", None),
        )

        for path, existing, old_string, new_string, skills in cases:
            tool_input = {"file_path": str(path), "old_string": old_string, "new_string": new_string}
            path.write_text(existing)
            requirement = skill_gate_hook.find_requirement("Edit", tool_input, "/tmp")

            self.assertEqual(requirement.skills if requirement else None, skills, (existing, old_string, new_string))

    def test_long_runs_of_digits_are_read_quickly(self) -> None:
        texts = ("transition: " + "1" * 5000, "transition = '" + "1" * 5000, "전환 " + "1" * 5000 + "초")
        started = time.monotonic()

        for text in texts:
            skill_gate_hook.find_requirement("Write", {"file_path": "/a/a.md", "content": text}, "/tmp")
        self.assertLess(time.monotonic() - started, 2)

    def test_unreadable_file_counts_all_of_the_write_as_new(self) -> None:
        folder = Path(self.directory.name) / "folder.css"
        locked = Path(self.directory.name) / "locked.css"
        content = ".a { transition: opacity 1s; }"

        folder.mkdir()
        locked.write_text(content)
        locked.chmod(0)
        for path in (folder, locked):
            tool_input = {"file_path": str(path), "content": content}

            self.assertEqual(skill_gate_hook.find_requirement("Write", tool_input, "/tmp").skills, SCREEN_MOTION_SKILLS)

    def test_in_place_edit_counts_a_time_changed_in_a_file_with_motion(self) -> None:
        brief = Path(self.directory.name) / "brief.md"
        command = f"sed -i '' 's/1초/3초/' {brief}"

        brief.write_text("전환은 1초로 한다.\n")
        self.assertEqual(skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp").skills, MOTION_SKILLS)
        brief.write_text("회의는 1초도 늦지 않는다.\n")
        self.assertIsNone(skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp"))

    def test_messages_with_motion_values_need_the_motion_skills(self) -> None:
        for tool_name, tool_input in MOTION_MESSAGE_CASES:
            requirement = skill_gate_hook.find_requirement(tool_name, tool_input, "/tmp")

            self.assertEqual(
                (requirement.target, requirement.skills) if requirement else None,
                ("a message with a motion value", MOTION_SKILLS),
                tool_input,
            )
        for tool_name, tool_input in PASSING_MESSAGE_CASES:
            self.assertIsNone(skill_gate_hook.find_requirement(tool_name, tool_input, "/tmp"), tool_input)

    def test_commands_with_motion_values_need_the_motion_skills(self) -> None:
        for command, target, skills in MOTION_COMMAND_CASES:
            requirement = skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp")

            self.assertEqual(
                (requirement.target, requirement.skills) if requirement else None, (target, skills), command
            )
        for command in PASSING_MOTION_COMMANDS:
            requirement = skill_gate_hook.find_requirement("Bash", {"command": command}, "/tmp")

            self.assertNotIn(EMIL_DESIGN_ENG, requirement.skills if requirement else (), command)

    def test_motion_message_is_refused_until_the_motion_skills_load(self) -> None:
        tool_name, tool_input = MOTION_MESSAGE_CASES[0]

        self.assertIsNone(self._decide(tool_name=tool_name, tool_input=tool_input, gate_setting=GATE_OFF))
        self.assertEqual(
            self._read_reason(self._decide(tool_name=tool_name, tool_input=tool_input)), MESSAGE_MOTION_REASON
        )
        self._load(UX_DESIGN)
        self.assertIn(
            "needs `emil-design-eng` and `web-motion-techniques` loaded",
            self._read_reason(self._decide(tool_name=tool_name, tool_input=tool_input)),
        )
        self._load(EMIL_DESIGN_ENG)
        self._load(WEB_MOTION_TECHNIQUES)
        self.assertIsNone(self._decide(tool_name=tool_name, tool_input=tool_input))

    def test_settings_send_messages_and_briefs_to_the_gate(self) -> None:
        settings = json.loads((REPOSITORY_ROOT / "settings.json").read_text())
        matchers = [
            str(entry.get("matcher") or "")
            for entry in settings["hooks"]["PreToolUse"]
            if any("skill-gate-hook.py" in str(hook.get("command")) for hook in entry.get("hooks", []))
        ]

        for tool_name in ("Edit", "Bash", "SendMessage", "Agent", "mcp__ccd_session__start_session"):
            self.assertTrue(any(re.fullmatch(matcher, tool_name) for matcher in matchers), tool_name)

    def test_edit_is_refused_until_its_skills_load(self) -> None:
        self.assertEqual(
            self._read_reason(self._decide()),
            "Writing `b.py` needs `code-style` and `python` loaded with the Skill tool first (CLAUDE.md \"Skill Triggers\"), and this session has no finished Skill-tool load of them since its start or last compaction.",
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

    def _load_design_skills(self) -> None:
        self._load(UX_DESIGN)
        self._load(DECISIONS)

    def _record_choice(self, question: str, options: dict[str, str], not_visual: str = "") -> None:
        skill_gate_records = sys.modules["skill_gate_records"]
        record = skill_gate_records.ChoiceRecord(question, options, not_visual)

        skill_gate_records.append_choice_record(skill_gate_records.get_choice_record_path(SESSION), record)

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
