#!/usr/bin/env python3
"""sync-shared-rules.py — generate every tool copy of the shared rules.

Usage:
    sync-shared-rules.py [--check]

shared-rules/ is the single source for both tools. Claude loads every file
in its rules directory in every session, while Codex reads one global
AGENTS.md without imports and caps the combined instruction size. So each
always-relevant rule becomes a file in Claude's rules/ and a section
embedded in codex/AGENTS.md, and each task-scoped rule becomes a Claude
skill and a Codex skill that load only when their task comes up.

With --check, nothing is written; the script lists stale copies and exits 1.

Exit codes:
    0  copies are current (or were written)
    1  stale copies found with --check
    2  codex/AGENTS.md lacks a section for an always-loaded rule
    3  a file in shared-rules/ or rules/ is not registered in this script
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "shared-rules"
CLAUDE_RULES_DIR = ROOT / "rules"
CLAUDE_SKILLS_DIR = ROOT / "skills"
CODEX_DIR = ROOT / "codex"
CODEX_SKILLS_DIR = CODEX_DIR / "skills"
AGENTS_PATH = CODEX_DIR / "AGENTS.md"
SCRIPT_PATH = "scripts/sync-shared-rules.py"

CLAUDE_SKILL_FRONTMATTER = "user-invocable: false\n"

# source file -> its section heading in codex/AGENTS.md
ALWAYS_RULES = {
    "collaboration-core.md": "## Core Collaboration Principles",
    "writing.md": "## Shared Writing Principles",
}

# source file -> (skill name, skill description)
SKILL_RULES = {
    "design.md": (
        "ux-design",
        "Apply screen and UX design principles to screen design, print and PDF document design, design review, UI copy, and design-tool work.",
    ),
    "decisions-and-collaboration.md": (
        "decisions-and-collaboration",
        "Present options for user decisions, record decision status, run checkpoint reviews, choose the model and effort for a run you start, propose, start, or close other sessions, run a main session, keep a shared notes folder, or handle design-tool comments.",
    ),
    "planning.md": (
        "product-planning",
        "Apply planning and research principles to product planning, external research and source selection, vendor or tool selection, pricing and quota research, and early-launch criteria.",
    ),
    "rule-writing.md": (
        "rule-writing",
        "Decide where a rule, skill, hook, or memory goes and how to write it, keep always-loaded rules within budget, and change the shared configuration. Apply before adding, changing, moving, or removing one, or changing a shared setting.",
    ),
}

EXIT_STALE = 1
EXIT_MISSING_SECTION = 2
EXIT_UNREGISTERED = 3


def fail(message: str, code: int) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(code)


def read_source(source_file: str) -> str:
    return (SOURCE_DIR / source_file).read_text(encoding="utf-8")


def make_generated_notice(source_file: str) -> str:
    return f"<!-- Generated from shared-rules/{source_file} by {SCRIPT_PATH}. Edit the source, not this copy. -->\n\n"


def build_skill(source_file: str, name: str, description: str, extra_frontmatter: str) -> str:
    return (
        f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}---\n\n"
        f"{make_generated_notice(source_file)}{read_source(source_file)}"
    )


def shift_headings(markdown: str) -> str:
    return "\n".join(f"#{line}" if line.startswith("#") else line for line in markdown.split("\n"))


def replace_section(agents: str, heading: str, source_file: str) -> str:
    start = agents.find(heading)
    if start == -1:
        fail(f"FAIL: {AGENTS_PATH.relative_to(ROOT)} has no '{heading}' section", EXIT_MISSING_SECTION)
    else:
        section = shift_headings(read_source(source_file))
        end = agents.find("\n## ", start + len(heading))
        if end == -1:
            return agents[:start] + section
        else:
            return agents[:start] + section + "\n" + agents[end + 1 :]


def build_agents() -> str:
    agents = AGENTS_PATH.read_text(encoding="utf-8")
    for source_file, heading in ALWAYS_RULES.items():
        agents = replace_section(agents, heading, source_file)
    return agents


def expected_copies() -> dict[Path, str]:
    copies = {AGENTS_PATH: build_agents()}
    for source_file in ALWAYS_RULES:
        copies[CLAUDE_RULES_DIR / source_file] = make_generated_notice(source_file) + read_source(source_file)
    for source_file, (name, description) in SKILL_RULES.items():
        copies[CLAUDE_SKILLS_DIR / name / "SKILL.md"] = build_skill(source_file, name, description, CLAUDE_SKILL_FRONTMATTER)
        copies[CODEX_SKILLS_DIR / name / "SKILL.md"] = build_skill(source_file, name, description, "")
    return copies


def find_unregistered() -> list[str]:
    registered = set(ALWAYS_RULES) | set(SKILL_RULES)
    unregistered_sources = [
        str(path.relative_to(ROOT)) for path in sorted(SOURCE_DIR.glob("*.md")) if path.name not in registered
    ]
    unregistered_rules = [
        str(path.relative_to(ROOT))
        for path in sorted(CLAUDE_RULES_DIR.rglob("*.md"))
        if path.relative_to(CLAUDE_RULES_DIR).as_posix() not in ALWAYS_RULES
    ]
    return unregistered_sources + unregistered_rules


def report_stale(stale: list[Path]) -> None:
    for path in stale:
        print(f"STALE: {path.relative_to(ROOT)}")
    if stale:
        sys.exit(EXIT_STALE)
    else:
        print("OK: generated copies match shared-rules/")


def write_stale(copies: dict[Path, str], stale: list[Path]) -> None:
    if stale:
        for path in stale:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(copies[path], encoding="utf-8")
            print(f"WROTE: {path.relative_to(ROOT)}")
    else:
        print("OK: generated copies already match shared-rules/")


def sync(check_only: bool) -> None:
    copies = expected_copies()
    stale = [
        path
        for path, content in copies.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if check_only:
        report_stale(stale)
    else:
        write_stale(copies, stale)


def main(argv: list[str]) -> None:
    unregistered = find_unregistered()
    if unregistered:
        fail(f"FAIL: not registered in {SCRIPT_PATH}: {', '.join(unregistered)}", EXIT_UNREGISTERED)
    else:
        sync("--check" in argv)


if __name__ == "__main__":
    main(sys.argv[1:])
