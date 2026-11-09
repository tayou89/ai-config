# Shared AI Configuration

This repository holds personal AI agent configuration for Claude Code and Codex: shared rules that apply to both tools, the Claude Code setup, and its Codex adaptation. It was renamed from `claude-config` on 2026-11-04; GitHub redirects the old URLs.

## Contents

- [Shared rules](shared-rules/) are the single tool-neutral source for project-independent principles: [writing](shared-rules/writing.md) and [core collaboration](shared-rules/collaboration-core.md), which apply to every task, and [screen and UX design](shared-rules/design.md), [decisions and collaboration](shared-rules/decisions-and-collaboration.md), and [planning and research](shared-rules/planning.md), which apply to their tasks.
- [Claude behavior rules](CLAUDE.md), `rules/`, `skills/`, `settings.json`, and `scripts/` are the Claude Code configuration. The repository root mirrors `~/.claude`, so a checkout there works directly. `rules/` and the `ux-design`, `decisions-and-collaboration`, and `product-planning` skills are generated from `shared-rules/`; `emil-design-eng`, `review-animations`, and `apple-design` are unchanged MIT copies from `emilkowalski/skills` (commit `5206a95`), `web-motion-techniques` wraps unchanged MIT references from `Motionsites-AI/claude-skill` (commit `faa30d5`); the other skills are written by hand.
- [Codex global instructions](codex/AGENTS.md) contains engineering/workflow preferences, skill routing, and the embedded core collaboration and writing principles.
- [Codex skills](codex/skills/) contains ten task-specific workflows adapted from the Claude skills, three skills generated from the shared rules, and unchanged copies of third-party design skills, each with its license beside it: Anthropic's `frontend-design` (version 1.1.1 of the Claude plugin, Apache 2.0), since Codex cannot load Claude plugins, Emil Kowalski's `emil-design-eng`, `review-animations`, and `apple-design` (`emilkowalski/skills` commit `5206a95`, MIT), and `web-motion-techniques`, whose `references/` are the MIT technique, craft, spec, and review files of `Motionsites-AI/claude-skill` (commit `faa30d5`) under a skill file written here, without that skill's example prompts and media. Refresh a copy when its source updates.
- [Migration notes](codex/MIGRATION.md) records retained rules, compatibility changes, and unsupported Claude-only features.

## How Each Tool Reads the Shared Rules

Claude Code loads every Markdown file in its user rules directory, including subdirectories, in every session. So only the always-relevant writing and core collaboration principles are generated into `rules/`, and the design, decision, and planning rules become Claude skills that load only for matching tasks.

Codex reads a single global `AGENTS.md` without imports and stops adding instruction files once their combined size reaches `project_doc_max_bytes` (32 KiB by default), which also has to leave room for project instructions. So the same two always-relevant files are embedded in `codex/AGENTS.md`, and the design, decision, and planning rules become Codex skills. [`scripts/sync-shared-rules.py`](scripts/sync-shared-rules.py) generates all of these copies from `shared-rules/`.

## Apply on a Computer

| Tool | Repository source | User-level destination |
| --- | --- | --- |
| Claude global rules | `CLAUDE.md` | `~/.claude/CLAUDE.md` or the configured Claude directory |
| Claude shared rules (generated) | Each file in `rules/` | `~/.claude/rules/` |
| Claude skills, including the generated rule skills | Each directory under `skills/` | `~/.claude/skills/<name>/` |
| Codex global rules | `codex/AGENTS.md` | `$CODEX_HOME/AGENTS.md`, default `~/.codex/AGENTS.md` |
| Codex task skills, including the generated rule skills | Each directory under `codex/skills/` | `$CODEX_HOME/skills/<name>/`, default `~/.codex/skills/<name>/` |

When updating Claude copies installed before 2026-11-06, delete `design.md`, `planning.md`, and `decisions-and-collaboration.md` from `~/.claude/rules/` after checking they hold no local edits; otherwise Claude keeps loading them in every session next to the new skills. Then confirm that `~/.claude/rules/` holds only the files in `rules/`.

Inspect and back up existing files first. Merge unrelated instructions rather than overwriting them. For Codex, check `AGENTS.override.md`, which takes precedence over the global AGENTS.md. Keep the skills beside AGENTS.md as shown so its relative references resolve. This layout is verified against the local Codex installation; current documentation also describes `~/.agents/skills` for user skills. If choosing that location instead, update the global skill links accordingly and avoid duplicate installations. Do not place these Markdown rules in `~/.codex/rules/`, which Codex uses for command execution policies. If project instructions are long (non-Latin text takes several bytes per character), raise `project_doc_max_bytes` in `~/.codex/config.toml`, for example to `65536`, and confirm with `codex debug prompt-input` that the end of the project AGENTS.md is included. This repository does not install `config.toml`.

Start a new conversation to load the complete instruction set. Codex normally detects skill changes; restart it if the skills are missing. The global defaults apply across that user's projects, together with project-specific instructions and current requests. Installing Codex rules does not install Claude plugins, permissions, hooks, scripts, or credentials.

## Maintenance

Edit shared principles only in `shared-rules/`, in English and without tool- or project-specific details. Then run `python3 scripts/sync-shared-rules.py` to regenerate the Claude and Codex copies, and `python3 scripts/sync-shared-rules.py --check` to confirm they match before committing. When adding a rule file, register it in the script as always-loaded or task-scoped (the check fails on unregistered files in `shared-rules/` or `rules/`) and update the contents above.

Install `scripts/` as a whole: the shell guard hook (`shell-guard-hook.py`) and the commit wrapper (`claude-commit.py`) import `shell_command.py` and `secret_files.py` from beside them, and a hook that cannot start lets every command through. After changing any of these four scripts, run `python3 tests/test_shell_guard.py`; add `--table` to print every case with its decision.

Review changes to the original Claude engineering rules for corresponding updates under `codex/`; do not copy Claude-only execution settings blindly. GitHub updates do not synchronize installed local copies automatically. Refresh local files deliberately after reviewing the changes.

## References

- [Codex global instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex skill authoring and discovery](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code user-level rules](https://code.claude.com/docs/en/memory#user-level-rules)
