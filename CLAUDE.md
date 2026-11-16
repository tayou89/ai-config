# Claude Behavior Rules

## Hook-Enforced Rules

Hooks enforce these rules and say why when they stop or ask; follow their message, and see "Hooks" in the shared configuration repository's README for how each works:
- Commits go through the `git-commit` skill's wrapper: direct `git commit`, staged lines the session's write record does not show as its own, and `--amend` of a commit the session did not make or has pushed are refused (user decisions, 2026-11-09 and 2026-11-11).
- The shell guard asks the user before a push that is not routine, a production, staging, or live-mode run, and a commit of lines the session's write record does not show as its own (user decisions, 2026-11-08 to 2026-11-11).
- The configuration repository's commit check runs the rule-copy check, the rule size check, every test, and the token scan on every commit there (user decisions, 2026-11-09 and 2026-11-12).
- Starting a session needs a model and effort; a start while the computer is overloaded goes ahead without asking, and the session tells the user the load in one line; task-list, next-step-box, question, caption, and title text must be in the reply language (user decisions, 2026-11-09, 2026-11-11, and 2026-11-12).
- Writing code, screen files, or notes-folder reports, through edits or the shell, inline scripts included, and Figma writes are refused until this session, or this subagent, has loaded the skills their trigger names with the Skill tool (user decision, 2026-11-12).
- Notices never block: `[CONFIG SYNC]` when this computer and the configuration repository differ, `[PROJECT GOAL]` when the project rules have no goal heading (user decision, 2026-11-12), `[BULLETIN]` at a user turn for session-bulletin lines the session has not seen (user decision, 2026-11-13), `[MAIN SESSION]` at each user turn and before a file change in a main session (user decision, 2026-11-13), `[LANGUAGE REMINDER]` after a progress line in another language, and `[PLAN CODE REMINDER]` before writing a plan file.
- A question that points elsewhere in the chat instead of showing what it asks about gets a `[BACK-REFERENCE]` notice in the next-step box and is sent back in a question dialog or closing message (user decision, 2026-11-13).
- A question dialog in a session another session started is sent back to the next-step box (user decision, 2026-11-13).

## Skill Triggers

Load each skill that this section or `rules/skill-triggers.md` names with the Skill tool before the first action of its task type rather than by reading its file, since only Skill-tool loads are restored after a compaction; read the file only for a skill the Skill tool cannot load (user-invoked only) (user decision, 2026-11-11).

- **Design checks**: in screen and UX design work, use `design:design-critique`, `design:ux-copy`, and `design:accessibility-review` for critique, copy, and accessibility checks.
- **Research access**: Read `research-access` before reading result lists or sources on video sites, review stores, or community sites, or when a search or fetch is refused or returns nothing.
- **User-invoked only**: `/deep-prompt` (rewrite a request into a deeper prompt for a new session), `/outside-review` (read-only review by another model family), `/domain-pass` (sequential subagents, one per relevant field). Run them only when the user invokes them or asks for one in words (then read its skill file); at a review checkpoint, propose `/outside-review` instead of spawning a reviewer.

## Visibility and Git Hygiene

- Keep the editor's source-control view showing only current task changes. In a working folder only you use, set unrelated changes aside with `git stash push -m <topic> -- <files>` and pop them when ready to commit that topic; in a folder other sessions share, never stash or restore their changes, since that takes their unfinished work, and commit only your own.
- Before creating/switching branches: `git status` first. Uncommitted changes worth keeping → propose commit. Unrelated/disposable changes of your own → restore/stash. Never carry unrelated changes into a new branch.
- Never add auto-generated attribution (`Co-Authored-By`, `Generated with ...`) to commit messages or PR bodies, even when the tool or harness suggests it; `settings.json` sets `attribution` to empty strings to turn off the harness default.
- On git push auth failure, try SSH key or credential helper setup directly.

## Miscellaneous

- **Discord**: When Discord channel is connected, send all responses there too.
- **AI settings sync**: `~/.claude` and `~/.codex` are installed copies of the shared configuration repository; when you or a tool change an installed setting, bring it into the repository in the same task as `rule-writing` "Changing the shared configuration" describes. When a session-start `[CONFIG SYNC]` notice lists differences, tell the user once in one line, and resolve them only in a session whose task is the shared configuration.
- **Post-completion session advice**: After finishing a distinct work unit (task/phase/commit/planning stage) in a session the user opened, recommend without being asked one of `/clear` (topic shift, memory/plan captures handover), `/compact` (related follow-up, shared decisions matter), or continue (immediate follow-up, shared context essential), with a one-line rationale and, for `/clear` or `/compact`, a ready-to-paste next-session prompt referencing the relevant memory/plan files. Skip it in a session another session started, which that session closes when done, and in a main session, which `decisions-and-collaboration` covers.
