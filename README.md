# Shared AI Configuration

This repository holds personal AI agent configuration for Claude Code and Codex: shared rules that apply to both tools, the Claude Code setup, and its Codex adaptation. It was renamed from `claude-config` on 2026-11-04; GitHub redirects the old URLs.

## Contents

- [Shared rules](rules/) are the single tool-neutral source for project-independent principles: [writing](rules/writing.md), [screen and UX design](rules/design.md), [decisions and collaboration](rules/decisions-and-collaboration.md), and [planning and research](rules/planning.md).
- [Claude behavior rules](CLAUDE.md), `skills/`, `settings.json`, and `scripts/` are the Claude Code configuration. The repository root mirrors `~/.claude`, so a checkout there works directly.
- [Codex global instructions](codex/AGENTS.md) contains engineering/workflow preferences, skill routing, and the embedded shared writing principles.
- [Codex skills](codex/skills/) contains ten task-specific workflows adapted from the Claude skills, plus three skills generated from the shared rules.
- [Migration notes](codex/MIGRATION.md) records retained rules, compatibility changes, and unsupported Claude-only features.

## How Each Tool Reads the Shared Rules

Claude Code loads every file in its user rules directory, so the shared rules are installed there as they are.

Codex reads a single global `AGENTS.md` without imports and stops adding instruction files once their combined size reaches `project_doc_max_bytes` (32 KiB by default), which also has to leave room for project instructions. So the short, always-relevant writing principles are embedded in `codex/AGENTS.md`, and the design, decision, and planning rules become Codex skills that load only for matching tasks. [`scripts/sync-codex-rules.py`](scripts/sync-codex-rules.py) generates these copies from `rules/`.

## Apply on a Computer

| Tool | Repository source | User-level destination |
| --- | --- | --- |
| Claude shared rules | Each file in `rules/` | `~/.claude/rules/` or the configured Claude directory |
| Codex global rules | `codex/AGENTS.md` | `$CODEX_HOME/AGENTS.md`, default `~/.codex/AGENTS.md` |
| Codex task skills, including the generated rule skills | Each directory under `codex/skills/` | `$CODEX_HOME/skills/<name>/`, default `~/.codex/skills/<name>/` |

Inspect and back up existing files first. Merge unrelated instructions rather than overwriting them. For Codex, check `AGENTS.override.md`, which takes precedence over the global AGENTS.md. Keep the skills beside AGENTS.md as shown so its relative references resolve. This layout is verified against the local Codex installation; current documentation also describes `~/.agents/skills` for user skills. If choosing that location instead, update the global skill links accordingly and avoid duplicate installations. Do not place these Markdown rules in `~/.codex/rules/`, which Codex uses for command execution policies. If project instructions are long (non-Latin text takes several bytes per character), raise `project_doc_max_bytes` in `~/.codex/config.toml`, for example to `65536`, and confirm with `codex debug prompt-input` that the end of the project AGENTS.md is included. This repository does not install `config.toml`.

Start a new conversation to load the complete instruction set. Codex normally detects skill changes; restart it if the skills are missing. The global defaults apply across that user's projects, together with project-specific instructions and current requests. Installing Codex rules does not install Claude plugins, permissions, hooks, scripts, or credentials.

## Maintenance

Edit shared principles only in `rules/`, in English and without tool- or project-specific details. Then run `python3 scripts/sync-codex-rules.py` to regenerate the Codex copies, and `python3 scripts/sync-codex-rules.py --check` to confirm they match before committing. When adding a rule file, register it in the script and in the tables above.

Review changes to the original Claude engineering rules for corresponding updates under `codex/`; do not copy Claude-only execution settings blindly. GitHub updates do not synchronize installed local copies automatically. Refresh local files deliberately after reviewing the changes.

## References

- [Codex global instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex skill authoring and discovery](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code user-level rules](https://code.claude.com/docs/en/memory#user-level-rules)
