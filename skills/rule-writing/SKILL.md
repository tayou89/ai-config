---
name: rule-writing
description: Decide where a rule, skill, hook, or memory goes and how to write it, keep always-loaded rules within budget, and change the shared configuration. Apply when recording a user correction or decision and before adding, changing, moving, or removing one, or changing a shared setting.
user-invocable: false
---

<!-- Generated from shared-rules/rule-writing.md by scripts/sync-shared-rules.py. Edit the source, not this copy. -->

# Rule Writing Principles

Apply before adding, changing, moving, or removing a rule, skill, hook, or memory, in the global rules or a project's, and before changing a shared setting. Every always-loaded line lowers the chance that each other line is followed, so a rule earns its place by what it prevents. Follow project rules first.

## Where a rule goes

- Record a user correction or decision at once, since one correction is enough, in the narrowest place that fits, judged by the work a statement governs rather than the project where it came up or the names in its example: a way of working, language, or tool goes in the skill that already covers it; a project's own choices, values, policies, and approved deviations stay in that project's rules or a project skill; a fact about the user or ongoing work goes to memory. Widen a project's rule to every project only when the user's stated scope supports it (user decision, 2026-11-12).
- When the user approves or adopts a way of working in one project (a method, review step, or process that worked, not one of the project's values), record it there and, in the same task, write it as a lesson for the knowledge vault as `knowledge-vault` "Writing" describes, so other projects and tasks start from it instead of repeating the same trial and error (user decision, 2026-11-13). It enters a shared rule or skill only when the user's stated scope covers every project, through the review under "Changing the shared configuration".
- Put a rule into an always-loaded global file (the global instruction file, the always-loaded rules, the Codex global instructions) only when all three hold: it applies to every task, no code, tool, or hook can work it out or enforce it, and the model would not do it anyway. Otherwise put it in the skill that already covers its task, and create a new skill with a one-line trigger in the global instruction file only when none fits.
- In the shared source, put principles every task needs (tool use, checks before showing work, handing over work, shared files and sessions) in `collaboration-core.md`; screens, copy, and design tools in `design.md`; decisions, reviews, and running sessions in `decisions-and-collaboration.md`; planning, research, and vendor selection in `planning.md`; writing in `writing.md`; rules about rules in this file. If no file fits, create a new file by topic.
- When a statement mixes a shared method with one project's details, split it: the skill holds the method, and the project rules hold only a link to it, where it applies, that project's values, and the deviations the user approved, as a project extends a shared config with its own settings (user decision, 2026-11-12). Global rules and skills use generic terms ("test simulators", "live backend", "message brokers") and generic examples, never project names, server addresses, project copy, or identifiers taken from a project's code, though a public library or interface name may stay where the example needs it. A language- or tool-specific rule belongs in that language's or tool's skill, which is not project-specific.
- Treat planning, research, development, tool use, and writing the same way, not only design.

## How to write it

- Keep each rule to one clear sentence where possible, and do not enumerate what a general statement already covers. Make it specific enough to check, and give a reason in one clause when the rule would otherwise look arbitrary.
- When adding a rule, look in the same change for what to remove: a line it makes redundant, an older decision it replaces, or a rule nobody follows. When a new rule conflicts with an old one, change or remove the old one in the same edit; never leave two rules that ask for different actions in the same situation. When you move a rule into a shared skill, first check that every agent that follows it can read that skill, then delete only the superseded rule text where it was, keeping that place's own values and links and leaving decision records and evidence alone.
- When a new rule is added, audit all current project deliverables for violations, report and fix them, and propose agents (with approval) when the scope is large.
- Keep only the current rule in rule text, with its decision date so newer decisions can be told from older ones; leave how it changed ("replacing …") to the commit history (user decision, 2026-11-12).
- Do not stack emphasis (capitals, "IMPORTANT", bold) to make a rule stick. When creating a skill, and when a skill is forgotten repeatedly, consider enforcing it with a hook (a script the tool runs before or after an action) or the commit check instead, when it has a specific triggering action and skipping it costs more than a reminder: the hook blocks the action and the skill performs it through a wrapper the hook does not intercept. Propose such a hook to the user before adding it, and remove hooks that only enforce taste.
- Write a hook's added context as one or two short factual sentences, not as a system command, since notices that read like commands can be taken for prompt injection (text pretending to be instructions).
- Write global rules in English and keep them tool-neutral so every AI tool can use them; user-facing replies and project documents follow the user's language.

## When a rule is not followed

- When a rule that applied was not followed, find the cause from the transcript and the rule text of that time before proposing a fix, and say which it was with the evidence (user decision, 2026-11-13): no rule covered the case; the wording was vague or named a narrower case; the rule was not loaded at that moment (a skill not loaded, a reference not read, text cut by a compaction or a size limit); another instruction conflicted with it; or the model's habit overrode a clear rule that was loaded.
- Propose the fix that the cause calls for and show it to the user like any rule change: write or sharpen the sentence; move it where it loads when needed (a trigger, an always-loaded file, the top of a skill); remove the conflict; restore text lost in a cut; or, for a habit that recurs against a clear, loaded rule, a hook or check on the triggering action or a question for the independent reviewer. A promise to be more careful is not a fix.

## Size

- The repository's commit check measures always-loaded rules in bytes and stops a commit over budget; when it does, make room by removing or moving text rather than raising the budget, unless the user decides otherwise.
- Keep a skill under 500 lines and put its most important rules first: after a compaction only the first part of each loaded skill (about 20,000 characters) is restored. Move long examples and rarely needed procedures into a reference file one level deep that the skill names.

## Changing the shared configuration

- The source of the global rules is `shared-rules/` in the public shared configuration repository `tayou89/ai-config`, which also holds every setting that should follow the user to another computer. Installed local copies are not linked to it, so edit a checkout of it, a task worktree when its rules use worktrees (locate it by its remote, or use the installed Claude folder when it is itself a checkout), never only an installed copy. Regenerate the tool-specific copies with its sync script, re-check for project-specific terms, and commit and push. When adding a file, register it in the sync script, whose check fails on unregistered files, and add it to the README.
- When a tool or app changes an installed setting itself (a plugin or marketplace install, an app setting, a file created in an installed folder), bring it into the repository in the same task, or list it as per-computer in the install script when each computer should decide it. Keep auto memory and session notes out of this public repository.
- Once the change is on the repository's default branch on the remote (pushed, or merged through its pull request), refresh the installed copies with the checkout's `scripts/install.py --from-upstream`, which installs that branch's commit from any checkout or worktree and leaves out other work: a dry run, `--apply`, then a second dry run (it merges local-only lines instead of overwriting them). Finish when `install.py --check` reports nothing, before moving to other work.
- When a rule change needs the user's review, show the changed text, say in the user's language what each change does, and ask whether it is right, never whether to commit, push, or install: those follow the review without asking, and a question about them reads as the commit approval the user dropped (user corrections, 2026-11-12 and 2026-11-13).
- Tell the user in one line where a rule was recorded.
