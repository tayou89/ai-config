---
name: rule-writing
description: Decide where a rule, skill, hook, or memory goes and how to write it, keep always-loaded rules within budget, and change the shared configuration. Apply before adding, changing, moving, or removing one, or changing a shared setting.
---

<!-- Generated from shared-rules/rule-writing.md by scripts/sync-shared-rules.py. Edit the source, not this copy. -->

# Rule Writing Principles

Apply before adding, changing, moving, or removing a rule, skill, hook, or memory, in the global rules or a project's, and before changing a shared setting. Every always-loaded line lowers the chance that each other line is followed, so a rule earns its place by what it prevents. Follow project rules first.

## Where a rule goes

- Record a user correction or decision at once, since one correction is enough, in the narrowest place that fits: a skill when it applies to one task type, language, or tool; the project rules when it names that project's files, screens, copy, or numbers; memory when it is a fact about the user or ongoing work (user decision, 2026-11-12).
- Put a rule into an always-loaded global file (the global instruction file, the always-loaded rules, the Codex global instructions) only when all three hold: it applies to every task, no code, tool, or hook can work it out or enforce it, and the model would not do it anyway. Otherwise use a skill and give that skill a one-line trigger in the global instruction file.
- In the shared source, put principles every task needs (tool use, checks before showing work, handing over work, shared files and sessions) in `collaboration-core.md`; screens, copy, and design tools in `design.md`; decisions, reviews, and running sessions in `decisions-and-collaboration.md`; planning, research, and vendor selection in `planning.md`; writing in `writing.md`; rules about rules in this file. If no file fits, create a new file by topic.
- Keep project-specific names, colors, numbers, and decisions in project rules, and record the generalized principle alongside. Global rules and skills use generic terms ("test simulators", "live backend", "message brokers") and generic examples, never project names, server addresses, or project copy. A language- or tool-specific rule belongs in that language's or tool's skill, which is not project-specific.
- Treat planning, research, development, tool use, and writing the same way, not only design.

## How to write it

- Keep each rule to one clear sentence where possible, and do not enumerate what a general statement already covers. Make it specific enough to check, and give a reason in one clause when the rule would otherwise look arbitrary.
- When adding a rule, look in the same change for what to remove: a line it makes redundant, an older decision it replaces, or a rule nobody follows. When a new rule conflicts with an old one, change or remove the old one in the same edit; never leave two rules that ask for different actions in the same situation.
- Keep only the current rule in rule text, with its decision date so newer decisions can be told from older ones; leave how it changed ("replacing …") to the commit history (user decision, 2026-11-12).
- Do not stack emphasis (capitals, "IMPORTANT", bold) to make a rule stick. When creating a skill, and when a skill is forgotten repeatedly, consider enforcing it with a hook (a script the tool runs before or after an action) or the commit check instead, when it has a specific triggering action and skipping it costs more than a reminder: the hook blocks the action and the skill performs it through a wrapper the hook does not intercept. Propose such a hook to the user before adding it, and remove hooks that only enforce taste.
- Write a hook's added context as one or two short factual sentences, not as a system command, since notices that read like commands can be taken for prompt injection (text pretending to be instructions).
- Write global rules in English and keep them tool-neutral so every AI tool can use them; user-facing replies and project documents follow the user's language.

## Size

- The repository's commit check measures always-loaded rules in bytes and stops a commit over budget; when it does, make room by removing or moving text rather than raising the budget, unless the user decides otherwise.
- Keep a skill under 500 lines and put its most important rules first: after a compaction only the first part of each loaded skill (about 20,000 characters) is restored. Move long examples and rarely needed procedures into a reference file one level deep that the skill names.

## Changing the shared configuration

- The source of the global rules is `shared-rules/` in the public shared configuration repository `tayou89/ai-config`, which also holds every setting that should follow the user to another computer. Installed local copies are not linked to it, so edit a pulled checkout (locate it by its remote, or use the installed Claude folder when it is itself a checkout), never only an installed copy. Regenerate the tool-specific copies with its sync script, re-check for project-specific terms, and commit and push. When adding a file, register it in the sync script, whose check fails on unregistered files, and add it to the README.
- When a tool or app changes an installed setting itself (a plugin or marketplace install, an app setting, a file created in an installed folder), bring it into the repository in the same task, or list it as per-computer in the install script when each computer should decide it. Keep auto memory and session notes out of this public repository.
- After pushing, refresh the installed copies with the checkout's `scripts/install.py --from-upstream`, which installs only pushed commits so other sessions' unfinished work stays out: a dry run, `--apply`, then a second dry run (it merges local-only lines instead of overwriting them). Finish when `install.py --check` reports nothing, before moving to other work.
- When a rule change needs the user's review, show the changed text and ask whether it is right, never whether to commit, push, or install: those follow the review without asking, and a question about them reads as the commit approval the user dropped (user correction, 2026-11-12).
- Tell the user in one line where a rule was recorded.
