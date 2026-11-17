# Claude Behavior Rules

## Hook-Enforced Rules

Hooks enforce these and say why when they stop or ask; follow their message, and see "Hooks" in the shared configuration repository's README for how each works (user decisions, 2026-11-08 to 2026-11-17):

- Commits go only through the `git-commit` wrapper; the shell guard asks before an unusual push, a production, staging, or live-mode run, and a commit of lines the session's write record does not show as its own; the configuration repository's commit check runs every check on each commit there.
- Starting a session needs a model and effort; when a start goes ahead on an overloaded computer, tell the user the load in one line.
- Writing code, screen files, notes-folder reports, or Figma content is refused until the skills its trigger names are loaded with the Skill tool.
- A question that points elsewhere in the chat, or a question dialog in a session another session started, is sent back to where the user sees it (`[BACK-REFERENCE]`, `[SUB-SESSION QUESTION]`).
- A closing message that says a recommendation changed without saying when its evidence appeared is continued once (`[RECOMMENDATION CHANGE]`).
- Notices never block: `[CONFIG SYNC]`, `[PROJECT GOAL]`, `[BULLETIN]`, `[MAIN SESSION]`, `[LANGUAGE REMINDER]`, `[PLAN CODE REMINDER]`, and `[REPORT CHECK]`.

## Skill Triggers

Load each skill that this section or `rules/skill-triggers.md` names with the Skill tool before the first action of its task type rather than by reading its file, since only Skill-tool loads are restored after a compaction; read the file only for a skill the Skill tool cannot load (user-invoked only) (user decision, 2026-11-11).

- **Research access**: Read `research-access` before reading result lists or sources on video sites, review stores, or community sites, or when a search or fetch is refused or returns nothing.
- **User-invoked only**: `/deep-prompt` (rewrite a request into a deeper prompt for a new session), `/outside-review` (read-only review by another model family), `/domain-pass` (sequential subagents, one per relevant field). Run them only when the user invokes them or asks for one in words (then read its skill file); at a review checkpoint, propose `/outside-review` instead of spawning a reviewer.

## Miscellaneous

- **AI settings sync**: when you or a tool change a setting installed in `~/.claude` or `~/.codex`, bring it into the shared configuration repository in the same task, as `rule-writing` "Changing the shared configuration" describes.
- **Post-completion session advice**: After finishing a distinct work unit (task/phase/commit/planning stage) in a session the user opened, recommend without being asked one of `/clear` (topic shift, memory/plan captures handover), `/compact` (related follow-up, shared decisions matter), or continue (immediate follow-up, shared context essential), with a one-line rationale and, for `/clear` or `/compact`, a ready-to-paste next-session prompt referencing the relevant memory/plan files. Skip it in a session another session started, which that session closes when done, and in a main session, which `session-management` covers.
