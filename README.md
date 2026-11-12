# Shared AI Configuration

This repository holds personal AI agent configuration for Claude Code and Codex: shared rules that apply to both tools, the Claude Code setup, and its Codex adaptation. It was renamed from `claude-config` on 2026-11-04; GitHub redirects the old URLs.

## Contents

- [Shared rules](shared-rules/) are the single tool-neutral source for project-independent principles: [writing](shared-rules/writing.md) and [core collaboration](shared-rules/collaboration-core.md), which apply to every task, and [screen and UX design](shared-rules/design.md), [decisions and collaboration](shared-rules/decisions-and-collaboration.md), and [planning and research](shared-rules/planning.md), which apply to their tasks.
- [Claude behavior rules](CLAUDE.md), `rules/`, `skills/`, `settings.json`, `scripts/`, and `mods/` (the usage panel shown beside the chat) are the Claude Code configuration, and [`scripts/install.py`](scripts/install.py) installs them. The repository root mirrors `~/.claude`, so a checkout there works directly. `rules/` and the `ux-design`, `decisions-and-collaboration`, and `product-planning` skills are generated from `shared-rules/`; `emil-design-eng`, `review-animations`, and `apple-design` are unchanged MIT copies from `emilkowalski/skills` (commit `5206a95`), `grilling` is an unchanged MIT copy from `mattpocock/skills` (commit `4ae2317`), `humanizer-ko` is an unchanged MIT copy from `IsaacEryn/humanizer-ko` (commit `b1562c2`, version 1.5.0), `web-motion-techniques` and `web-interface-guidelines` wrap unchanged MIT references from `Motionsites-AI/claude-skill` (commit `faa30d5`) and `vercel-labs/web-interface-guidelines` (commit `434b7f9`); the other skills are written by hand, and `python` adapts a few tool lines from Astral's MIT `uv` and `ruff` skills (`astral-sh/claude-code-plugins` commit `f3ce88a`).
- [Codex global instructions](codex/AGENTS.md) contains engineering/workflow preferences, skill routing, and the embedded core collaboration and writing principles. [`codex/settings.json`](codex/settings.json) holds the Codex model, reasoning effort, instruction size limit, and plugins that the install script applies.
- [Codex skills](codex/skills/) contains eleven task-specific workflows adapted from the Claude skills, three skills generated from the shared rules, and unchanged copies of third-party design and planning skills, each with its license beside it: Anthropic's `frontend-design` (version 1.1.1 of the Claude plugin, Apache 2.0), since Codex cannot load Claude plugins, Emil Kowalski's `emil-design-eng`, `review-animations`, and `apple-design` (`emilkowalski/skills` commit `5206a95`, MIT), Matt Pocock's `grilling` (`mattpocock/skills` commit `4ae2317`, MIT), `humanizer-ko` (`IsaacEryn/humanizer-ko` commit `b1562c2`, MIT), and `web-motion-techniques`, whose `references/` are the MIT technique, craft, spec, and review files of `Motionsites-AI/claude-skill` (commit `faa30d5`) under a skill file written here, without that skill's example prompts and media, and `web-interface-guidelines`, whose `guidelines.md` is the MIT `command.md` of `vercel-labs/web-interface-guidelines` (commit `434b7f9`) under a skill file written here. Refresh a copy when its source updates.
- [Migration notes](codex/MIGRATION.md) records retained rules, compatibility changes, and unsupported Claude-only features.

## How Each Tool Reads the Shared Rules

Claude Code loads every Markdown file in its user rules directory, including subdirectories, in every session. So only the always-relevant writing and core collaboration principles are generated into `rules/`, and the design, decision, and planning rules become Claude skills that load only for matching tasks.

Codex reads a single global `AGENTS.md` without imports and stops adding instruction files once their combined size reaches `project_doc_max_bytes` (32 KiB by default), which also has to leave room for project instructions. So the same two always-relevant files are embedded in `codex/AGENTS.md`, and the design, decision, and planning rules become Codex skills. [`scripts/sync-shared-rules.py`](scripts/sync-shared-rules.py) generates all of these copies from `shared-rules/`.

## Apply on a Computer

Run [`scripts/install.py`](scripts/install.py) from a clone of this repository. It installs everything below into the Claude and Codex folders and refreshes them later; a second run changes nothing.

```bash
python3 scripts/install.py
```

```bash
python3 scripts/install.py --apply
```

The first command only shows what would change. The second writes it, after copying every file it replaces into `backups/ai-config-install-<time>/` inside the Claude or Codex folder. The script stops when the repository has uncommitted changes or is behind its upstream (`--allow-dirty` overrides this, and `--from-upstream` below avoids it), and when the `python3` (and on Windows `bash`) that the hooks call is missing, because a hook that cannot start lets every command through. Other flags:

- `--check` writes nothing and skips the plugin commands, so it takes under a second. It lists work in this checkout that is not committed, pushed, or pulled (fetching at most once an hour), repository changes not installed here, and what only this computer has: `settings.json` entries, Codex `config.toml` keys, lines in the merged instruction and rule files, and files in the configuration folders (`skills/`, `scripts/`, `agents/`, `commands/`, `output-styles/`, and the Codex `skills/`, `prompts/`, and `rules/`). It exits 7 when it finds any. Entries each computer decides for itself are listed in `PER_COMPUTER_SETTINGS` and keys the Codex app writes in `CODEX_APP_KEYS`; extend those lists rather than leave a difference unexplained.
- `--prune` moves installed files the repository no longer has (for example a retired file in `rules/`) to the backups folder; without it they are only listed.
- `--from-upstream` installs the commit the checkout's upstream branch points at instead of the files in the checkout: it clones that commit into a temporary folder, runs that clone's own `install.py` with the same `--apply`, `--prune`, and `--first-merge` flags, and removes the clone. Use it when the checkout holds work that is not pushed, such as another session's unfinished edits; the record below stays on the checkout, so a second dry run shows nothing.
- `--checkout <path>` names the working checkout to record in `.ai-config-install/checkout` inside the Claude folder and to set `core.hooksPath` in. Without it, a run keeps a recorded checkout that still has `scripts/install.py`, so installing from another copy of the repository does not move the record; otherwise it records the checkout it runs from.
- `--first-merge keep-local` or `--first-merge use-repository` answers the one question the script cannot: when `CLAUDE.md` or the Codex `AGENTS.md` differs from the repository and no earlier install is recorded, are the differences this computer's own lines or stale lines to replace? Later runs merge three ways against the copy installed last time (kept in `.ai-config-install/base/`), so lines added on one computer survive and lines the repository removed disappear.

| Repository source | Destination in the Claude folder (`~/.claude`, or `CLAUDE_CONFIG_DIR`) | How |
| --- | --- | --- |
| `CLAUDE.md` | `CLAUDE.md` | Three-way merge |
| Each file in `rules/` | `rules/` | Copied |
| Each directory under `skills/` | `skills/<name>/` | Copied; skills not in the repository, such as a linked `ego-browser`, are left alone |
| `claude-commit.py`, `shell-guard-hook.py`, `shell_command.py`, `secret_files.py`, `commit_arguments.py`, `own_lines.py`, `session_writes.py`, `session_commits.py`, `session-writes-hook.py`, `reply-language-hook.py`, `config-sync-hook.py` | `scripts/` | Copied (`check-commit.py`, `install.py`, and `sync-shared-rules.py` stay in the repository) |
| `statusline-command.sh` | `statusline-command.sh` | Copied |
| Each directory under `mods/` | `mods/<name>/` | Copied; `claude-plans/` and generated `.claude-plugin/types/` there are left alone |
| `settings.json` | `settings.json` | Merged: `enabledPlugins`, `extraKnownMarketplaces`, and `env` by name, other repository keys replaced, keys only this computer has kept. Paths to `mods/` are filled in for this computer |

| Repository source | Destination in the Codex folder (`~/.codex`, or `CODEX_HOME`) | How |
| --- | --- | --- |
| `codex/AGENTS.md` | `AGENTS.md` | Three-way merge |
| Each directory under `codex/skills/` | `skills/<name>/` | Copied |
| Each file in `codex/rules/` | `rules/<name>` | Three-way merge (command rules; this computer's own lines are kept) |
| `codex/settings.json` `config` | `config.toml` | Top-level keys, and the keys of tables it names (such as `tui`), set; other tables and lines the Codex app writes are left alone |

The script then installs the Claude plugins that `settings.json` enables with `claude plugin marketplace add` and `claude plugin install`, and the Codex plugins in `codex/settings.json` with `codex plugin marketplace add` and `codex plugin add`. Claude Code would also fetch user-enabled plugins in the background after the first session starts, but installing them here makes the result immediate and checkable with `claude plugin list`. Plugins and connectors turned on for your claude.ai account (in the desktop app's Customize) arrive with the sign-in, not from this repository.

Keep the Codex skills beside AGENTS.md as installed so its relative references resolve. Check `AGENTS.override.md`, which takes precedence over the global AGENTS.md. Do not place these Markdown rules in `~/.codex/rules/`, which Codex uses for command execution policies. After an install, confirm with `codex debug prompt-input` that the end of a long project AGENTS.md is included (non-Latin text takes several bytes per character, which is why `project_doc_max_bytes` is raised). Start a new conversation to load the complete instruction set.

### A new computer

The script does not install apps or sign in. On a new computer, in this order:

1. Install the apps and command-line tools.
   - macOS: Command Line Tools (`xcode-select --install`, which brings `git` and `python3`), the Claude app, the ChatGPT app, Node.js, the GitHub CLI (`gh`), Claude Code (official installer), and the Codex CLI; also Figma, Chrome, and ego lite if you use them.
   - Windows: Git for Windows (it provides Git Bash, which Claude Code uses to run the hooks), Python 3 with `python3` available in Git Bash, the Claude app, the ChatGPT app, Node.js, the GitHub CLI, Claude Code, and the Codex CLI. This setup has not been run on Windows yet, so run the dry run first and read its notes.
   - Linux: `git`, `python3`, Node.js, and the GitHub CLI from the distribution's packages, Claude Code (official installer), and the Codex CLI; the Claude desktop app has a Linux beta installed with apt. This setup has not been run on Linux yet either, so run the dry run first and read its notes.
2. Sign in yourself: the Claude app and `claude`, the ChatGPT app and `codex login`, and `gh auth login` (HTTPS, credential in the system keychain). Set `git config --global user.name` and `user.email`.
3. Clone this repository and run the dry run, then `--apply` (with `--first-merge` if it asks).
4. In a Claude session, run `/mcp` and authorize the plugin servers you use. Plugins a project enables only for itself (`--scope local` or `project`) are not installed by this script; install them again in that project.
5. Set what each computer keeps for itself: the Claude app's own settings (Settings → Claude Code: notifications, keep awake, browser, the Remote Control default; the branch prefix and auto-archive on pull request close follow the account), the Codex app's display settings, and, on a computer that holds the knowledge vault, `KNOWLEDGE_VAULT_DIR` in the `env` of the installed `settings.json`, the folder the `knowledge-vault` skill reads (the repository's `settings.json` leaves it out so each computer keeps its own path). The `code-plan` skill adds `claude-plans/` to the global gitignore the first time it runs.

Project data outside git (Claude's auto memory and transcripts under `~/.claude/projects/`, the desktop app's scheduled tasks under `~/.claude/scheduled-tasks/`, the desktop app's session notes under `~/Library/Application Support/Claude/side-session-notes/`, review material) is not part of this repository. Auto memory is kept per computer, in a folder named after the project's path, so keep the same user name and project path when copying it.

## Maintenance

Edit shared principles only in `shared-rules/`, in English and without tool- or project-specific details. Then run `python3 scripts/sync-shared-rules.py` to regenerate the Claude and Codex copies, and `python3 scripts/sync-shared-rules.py --check` to confirm they match before committing. When adding a rule file, register it in the script as always-loaded or task-scoped (the check fails on unregistered files in `shared-rules/` or `rules/`) and update the contents above.

Install `scripts/` as a whole: the shell guard hook (`shell-guard-hook.py`) and the commit wrapper (`claude-commit.py`) import `shell_command.py`, `secret_files.py`, `commit_arguments.py`, and `session_writes.py` (which imports `own_lines.py` and `shell_command.py`) from beside them, the wrapper also imports `session_commits.py`, the write-record hook (`session-writes-hook.py`) imports `session_writes.py`, and a hook that cannot start lets every command through. The write-record hook keeps each session's record in `session-writes/` inside the Claude folder and deletes records older than 14 days, the age after which Git may prune the file contents they name. A call whose snapshot cannot be taken, such as one that moves a repository away, is recorded as an unknown write over what that snapshot covered while the call ran, so the wrapper refuses another session's lines only when they were written while that call ran. A command made only of reading programs and git index work, such as a commit through the wrapper, takes no snapshot, and a call a hook or the user stopped before it ran stops covering at its result in the transcript. The commit wrapper keeps each `--include-unrecorded` call it runs as approved, since the shell guard asks before such a call and a declined call never runs, so a retry of it with nothing written since is not asked again. The wrapper lists each commit it makes beside the session's record, so `--amend` rewrites only that session's commits. After changing any of these nine scripts, run `python3 tests/test_shell_guard.py`; add `--table` to print every case with its decision.

The reply-language hook (`reply-language-hook.py`) imports nothing from beside it. It reads the language the user reads in from `REPLY_LANGUAGE` in the `settings.json` `env` and does nothing when that is unset; before a tool draws a task list, next-step box, question dialog, file caption, or session title, it denies text in another language, and after each tool call it reminds the model when the transcript shows a progress line in another language. After changing it, run `python3 -m unittest discover -s tests -p test_reply_language.py`.

Every commit in this checkout runs `.githooks/pre-commit` (`install.py` sets `core.hooksPath`), which copies out the index being committed and runs the sync check, every test, and the token scan in that copy. Commits need no approval (user decision, 2026-11-09), so this check is where a broken rule copy or script is stopped; do not bypass it with `--no-verify`. The hook runs the working tree's copy of `check-commit.py`, so a half-finished edit to that script by another session can stop commits until it is finished. After changing `scripts/check-commit.py` or the hook, run `python3 -m unittest tests.test_check_commit`.

The session-start hook (`config-sync-hook.py`) runs `install.py --check` from the working checkout that `--apply` recorded in `.ai-config-install/checkout` inside the Claude folder (see `--checkout` above), and adds its report to the session as a `[CONFIG SYNC]` notice; the same report repeats only after a day. A check that cannot run (an error exit, more than 25 seconds, or a recorded checkout without `install.py`) adds a notice saying so, because a silent failure would hide every later difference; a computer with no recorded checkout gets nothing. After changing it, run `python3 tests/test_config_sync_hook.py`.

Review changes to the original Claude engineering rules for corresponding updates under `codex/`; do not copy Claude-only execution settings blindly. GitHub updates do not synchronize installed local copies automatically. After pulling and reviewing the changes, refresh them with `python3 scripts/install.py`, then `--apply`, and confirm with `--check`. After changing the install script, run `python3 tests/test_install.py`; when adding a script to `scripts/`, register it in the install script as a runtime or maintenance script (the test fails otherwise).

## References

- [Codex global instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex skill authoring and discovery](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code user-level rules](https://code.claude.com/docs/en/memory#user-level-rules)
