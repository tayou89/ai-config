# Report Writing Principles

Apply when writing or revising a report, plan, review, decision document, or any other document that the user or another session will read later, in a notes folder or a project, and when keeping a notes folder. The prose principles come first and apply to any document others will read; the rest sets the shape of reports and plans. Follow project rules first.

## Prose

Apply these principles when writing or revising documents, plans, reports, and explanations. Adapt to the requested format and the reader's purpose; do not impose one template on every task.

### Structure and headings

- Give each heading one central topic, and keep its content within that scope.
- Use subheadings to develop the parent topic; place independent topics at the same level.
- Use a consistent classification basis for headings at the same level.
- Do not broaden headings merely to combine unrelated content or fragment short passages unnecessarily.

### Explanation and presentation

- Lead with the main point, then connect reasons, evidence, and conditions in a logical order.
- Use paragraphs for connected explanations, lists for independent items, numbered lists for sequences, and tables for comparisons.
- Do not turn every sentence into a list item or split content mechanically at punctuation.
- Use clear subjects, concrete actions, familiar wording, and consistent terms for the same concepts.
- Use emphasis and nested lists only when they clarify importance or genuine relationships.

### Revision and preservation

- Wording and organization may change, but preserve meaning, premises, conditions, exceptions, quantities, and sources.
- Distinguish facts, assumptions, proposals, agreements, unresolved matters, and completion status; do not change their status during editing.
- Merge repetition only after confirming that meaning and scope match, and keep qualifications near the statements they constrain.

### Final review

- Check that the headings alone communicate a coherent structure.
- Read the body in order to confirm that the explanation flows naturally.
- Compare revisions with the source to catch omitted content or altered meaning.
- Judge readability by whether readers can understand the content and find what they need, not merely by how neatly it is divided.

## What done means

- A report is done when the user can tell from its first screen alone what was found, what is asked of them, and what is recommended, and can reach any supporting detail from a heading: the plain-language principles of ISO 24495-1:2023 (relevant, findable, understandable, usable) are the acceptance criteria for documents (user decision, 2026-11-16). Passing the check below does not show this, so read the first screen as the user would before handing it over.

## First screen

- Fit what the reader needs to act into about the first 1,500 characters, one screen in the note app: a title that states the finding, then a summary callout (`> [!abstract]`) (user decision, 2026-11-16).
- In that callout, give the conclusion in two or three sentences, the status (waiting for a decision, applied, on hold), and what the user must decide or do, describing each option in its own words; for a decision, add the five-axis table with a few words per cell and the recommendation with its reason (user decision, 2026-11-16).
- Put record details (author session, requester, date, commits, source file) in the note's properties at the top (YAML front matter), and session ids, commands, file names, and check output in the body or appendix, not in the opening (user decision, 2026-11-16).

## Current state only

- Keep the document at its current state: when a review, a decision, or a follow-up request changes it, rewrite the opening and the affected sections, mark answered questions as decided with the date, and move superseded text into a dated history entry in the appendix, so the front never shows two summaries or a question already answered (user decision, 2026-11-16).
- Fold a follow-up request on the same topic into the existing sections, and give a separate topic its own report linked from this one rather than an appended chapter (user decision, 2026-11-16).

## Body and appendix

- Order sections by the reader's questions (what is wrong, why, which options, what is recommended, what changes), give each a heading that states its finding, and refer to another section by a heading link (`[[#heading]]`), not by its number (user decision, 2026-11-16).
- Keep tables to six columns and cells to a few words (about 40 characters in the opening, 100 in the body), and explain each option's reasons in a paragraph below the table: the note app's reading column is about 700 pixels wide, so a long cell turns one row into dozens of lines (user decision, 2026-11-16; measured 2026-11-13).
- Prefer plain words in the user's language to English terms and acronyms; write a needed term in the user's language with the original once in parentheses, and quote commands, file names, and rule text only where the reader uses them (user decision, 2026-11-16).
- Move what the reader only checks (logs, timelines, measurements, long source lists, request transcripts, history) into an appendix at the end, as folded callouts (`> [!note]- title`) (user decision, 2026-11-16).
- Head the appendix "부록" or "Appendix" and the summary section, if any, with "요약", "결론", or "Summary", since the check finds them by those words.

## Checks

- Before handing a report over, run `python3 ~/.claude/scripts/report_check.py <file>`, which measures the opening, table cells and columns, repeated summaries, section-number references, and bare paths, and fix what it reports or say in the report why a finding stays (user decision, 2026-11-16).
- In Claude Code, writing a report in a notes folder is refused until this skill is loaded, and a hook runs the same check after each write there.

## Notes folders

- Keep every note a group of sessions leaves for later (reports, plans other than the code plans `code-plan` keeps in `claude-plans/`, decision documents, handovers, review briefs and results, captures) in its project's notes folder, `~/Documents/session-notes/<project>/`, from the moment you write it, naming the folder after the project's repository in English lowercase with hyphens, or after the topic for work outside a repository (user decision, 2026-11-23). Do not keep them in the session folder the Claude desktop app names in a brief as the group's shared notes folder: the app deletes that folder, skipping the Trash, once the session that owns it and every session it started are closed (app code, read 2026-11-20), and 26 of the 54 such folders that transcripts name were gone by then.
- A hook refuses new files in the app's session folders and copies those folders, adding and updating files but never deleting any, into `~/Documents/session-notes-backup/app-notes/` at the end of every turn and before a session tool closes, deletes, or moves a session to the cloud, a safety net that a close from the sidebar after an interrupted turn can still miss; find in that copy what a closed group left in the app's folder.
- Keep an `INDEX.md` in each project's notes folder with one row per report or plan, in the user's language: date, file, topic, conclusion, user decisions with dates, and open items, marking what could not be verified (user decision, 2026-11-08).
- Keep each index cell to a sentence or two and the conclusion to one sentence, leaving details to the report (user decision, 2026-11-16).
- The session that writes a report adds its row in the same task, a session that later records a user decision on it updates that row, and the session that started it checks the row before closing it. Re-read the index right before editing and change only that row or append one; this exempts the index from the one-editor rule (user decision, 2026-11-08).
- Before researching or recommending in a project, read its notes folder's `INDEX.md` first, and do not re-recommend what it records as decided, rejected, or on hold unless there is new evidence.
- In a report or plan in a notes folder, which the user reads in Obsidian (user decision, 2026-11-13), write every file link, images included, as a `file:///` URL with spaces as `%20` and any line number as text after the link, such as `[hook](file:///path/to/hook.py) line 33`: a plain absolute path makes Obsidian create an empty note, `name.md:3` reads as a URL scheme, and `file:///` opens in Obsidian and the chat client alike (tested 2026-11-13).
- When you hand over such a report or plan in a reply, give its link and also `open "<absolute path>"` in a shell code block of its own, since the chat client opens every file link in its own pane, and that block's run button opens the file in Obsidian (tested 2026-11-13).
