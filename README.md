# Shared AI Configuration

This repository contains the existing Claude Code configuration and a Codex adaptation of its engineering and writing rules.

## Contents

- [Claude behavior rules](CLAUDE.md) and the existing `skills/` remain the Claude configuration.
- [Shared Writing Principles](rules/writing.md) defines project-independent writing guidance for both tools.
- Claude-only user rules (Korean): [screen and UX design](rules/design.md), [decisions and collaboration](rules/decisions-and-collaboration.md), and [planning and research](rules/planning.md). They generalize feedback repeated during project work and are not yet adapted for Codex.
- [Codex global instructions](codex/AGENTS.md) contains engineering/workflow preferences, skill routing, and the full shared writing principles.
- [Codex skills](codex/skills/) contains ten task-specific instruction workflows.
- [Migration notes](codex/MIGRATION.md) records retained rules, compatibility changes, and unsupported Claude-only features.

## Apply on a Computer

| Tool | Repository source | User-level destination |
| --- | --- | --- |
| Codex global rules | `codex/AGENTS.md` | `$CODEX_HOME/AGENTS.md`, default `~/.codex/AGENTS.md` |
| Codex task skills | Each directory under `codex/skills/` | `$CODEX_HOME/skills/<name>/`, default `~/.codex/skills/<name>/` |
| Claude writing rules | `rules/writing.md` | `~/.claude/rules/writing.md` or the configured Claude directory |
| Claude design, decision, and planning rules | `rules/design.md`, `rules/decisions-and-collaboration.md`, `rules/planning.md` | `~/.claude/rules/` |

Inspect and back up existing files first. Merge unrelated instructions rather than overwriting them. For Codex, check `AGENTS.override.md`, which takes precedence over the global AGENTS.md. Keep the skills beside AGENTS.md as shown so its relative references resolve. This layout is verified against the local Codex installation; current documentation also describes `~/.agents/skills` for user skills. If choosing that location instead, update the global skill links accordingly and avoid duplicate installations.

Start a new conversation to load the complete instruction set. Codex normally detects skill changes; restart it if the skills are missing. The global defaults apply across that user's projects, together with project-specific instructions and current requests.

For Claude Code, install `rules/writing.md` in its user rules directory. A checkout directly at `~/.claude` already places it there. Installing Codex rules does not install Claude plugins, permissions, hooks, scripts, or credentials.

## Maintenance

Keep the Shared Writing Principles section of `codex/AGENTS.md` equivalent to `rules/writing.md`; only its heading levels differ because it is embedded in the larger document. They are no longer identical whole files.

Review changes to the original Claude engineering rules for corresponding updates under `codex/`; do not copy Claude-only execution settings blindly. GitHub updates do not synchronize installed local copies automatically. Refresh local files deliberately after reviewing the changes.

## References

- [Codex global instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex skill authoring and discovery](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code user-level rules](https://code.claude.com/docs/en/memory#user-level-rules)
