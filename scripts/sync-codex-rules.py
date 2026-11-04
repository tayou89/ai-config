#!/usr/bin/env python3
"""sync-codex-rules.py — generate the Codex copies of the shared rules.

Usage:
    sync-codex-rules.py [--check]

rules/ is the single source for both tools. Claude reads every file in its
rules directory, but Codex reads one global AGENTS.md without imports and
caps the combined instruction size. So the writing rules are embedded in
codex/AGENTS.md, and the task-scoped rules become Codex skills that load
only when their task comes up.

With --check, nothing is written; the script lists stale copies and exits 1.

Exit codes:
    0  copies are current (or were written)
    1  stale copies found with --check
    2  codex/AGENTS.md has no shared writing section
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"
CODEX_DIR = ROOT / "codex"
AGENTS_PATH = CODEX_DIR / "AGENTS.md"

WRITING_RULE = "writing.md"
WRITING_HEADING = "## Shared Writing Principles"

# rule file -> (skill name, skill description)
SKILL_RULES = {
    "design.md": (
        "ux-design",
        "Apply screen and UX design principles to screen design, design review, UI copy, and design-tool work.",
    ),
    "decisions-and-collaboration.md": (
        "decisions-and-collaboration",
        "Present options for user decisions, record decision status, handle design-tool comments, coordinate with concurrent sessions, or update the global rules.",
    ),
    "planning.md": (
        "product-planning",
        "Apply planning and research principles to product planning, vendor or tool selection, pricing and quota research, and early-launch criteria.",
    ),
}

EXIT_STALE = 1
EXIT_MISSING_SECTION = 2


def fail(message: str, code: int) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def build_skill(rule_file: str, name: str, description: str) -> str:
    body = (RULES_DIR / rule_file).read_text(encoding="utf-8")
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"<!-- Generated from rules/{rule_file} by scripts/sync-codex-rules.py. Edit the source, not this copy. -->\n\n"
        f"{body}"
    )


def shift_headings(markdown: str) -> str:
    return "\n".join(f"#{line}" if line.startswith("#") else line for line in markdown.split("\n"))


def build_agents() -> str:
    agents = AGENTS_PATH.read_text(encoding="utf-8")
    start = agents.find(WRITING_HEADING)
    if start == -1:
        fail(f"FAIL: {AGENTS_PATH.relative_to(ROOT)} has no '{WRITING_HEADING}' section", EXIT_MISSING_SECTION)
    writing = shift_headings((RULES_DIR / WRITING_RULE).read_text(encoding="utf-8"))
    end = agents.find("\n## ", start + len(WRITING_HEADING))
    if end == -1:
        return agents[:start] + writing
    return agents[:start] + writing + "\n" + agents[end + 1 :]


def expected_copies() -> dict[Path, str]:
    copies = {AGENTS_PATH: build_agents()}
    for rule_file, (name, description) in SKILL_RULES.items():
        copies[CODEX_DIR / "skills" / name / "SKILL.md"] = build_skill(rule_file, name, description)
    return copies


def main(argv: list[str]) -> None:
    check_only = "--check" in argv
    copies = expected_copies()
    stale = [
        path
        for path, content in copies.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if check_only:
        for path in stale:
            print(f"STALE: {path.relative_to(ROOT)}")
        if stale:
            sys.exit(EXIT_STALE)
        print("OK: Codex copies match rules/")
        return
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(copies[path], encoding="utf-8")
        print(f"WROTE: {path.relative_to(ROOT)}")
    if not stale:
        print("OK: Codex copies already match rules/")


if __name__ == "__main__":
    main(sys.argv[1:])
