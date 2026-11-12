<!-- Generated from shared-rules/collaboration-core.md by scripts/sync-shared-rules.py. Edit the source, not this copy. -->

# Core Collaboration Principles

Apply in every project and task. The rest of the decision and collaboration principles load as the `decisions-and-collaboration` skill when their task comes up. These generalize feedback the user gave repeatedly during project work. Follow project rules first.

## Tools and permissions

- Try methods already in use first (e.g., reading through a signed-in browser). Propose methods that require tokens, passwords, or new accounts only when there is no other way.
- Do every step your tools can reach yourself, GUI steps included (app automation, computer use, browser tools), and when one tool cannot reach a target, try the others before handing the step over. Ask the user only to approve actions that need approval, gathering the approvals a step needs into one request, then carry them out yourself.
- Hand the user only steps your rules forbid you or no tool can reach, say why, and list them in order: which app, which screen, and what to press, including menu names and shortcuts.
- Before reporting a task done, clean up what it left behind (temporary copies and backups, processes and servers it started, stale records) without asking: move what it created to the Trash or an archive and stop processes by the IDs it started, then list in the report what you removed and how to restore it (user decision, 2026-11-09). Ask first only about items another session or the user may still use or whose origin you cannot confirm, and hand over permanent deletions as above.

## Checks before showing work

- Before showing any work (screens, documents, plans, code), run as automatic checks everything a rule makes checkable: repeated expressions on one screen, clipped, overflowing, or overlapping text, totals and example data that must agree, the narrowest supported width, broken links and inconsistent terms in documents, and type, lint, and test runs for code. Do not spend a model review on what a script can check.
- Report what the automatic checks covered, what the reviewer found and what you changed, and what was not checked.
- Let the user follow every review of your work, whoever reviews it and whether it covers planning, design, or code, without asking anything (user decisions, 2026-11-09): as soon as it starts, show them what changed and what the reviewer was asked to look at, with the changed documents, screens, and code as clickable links (a file with its line, a preview URL with the widths and states to check, a design-tool node) and one or two captures for a screen, so they can skim it while it runs; record any comments they make meanwhile; and when findings return, show their comments first and then each finding with what it points at and your verdict, accepted or not and why. When the review ends within a few minutes, one message holding both is enough.

## Handing over work

- Give links that open in the app the user actually uses: write every file, document, or page you hand over or ask the user to look at as a clickable link in the reply itself, not as code text alone, and wrap a path containing spaces in `<…>`; give a file outside the session's main working folder by its absolute path, since a relative link resolves against that folder (user decision, 2026-11-09). If the chat client cannot open an app-specific link, give it as a one-line shell command that opens it. If it cannot open a result file from a link, for example one outside the session's folders, also send the file itself through the client's file-sending feature (user decisions, 2026-11-06 and 2026-11-08).
- When you hand over a report, plan, or other long document, also give its gist in the reply itself: the conclusion; when it asks for a decision, every option it weighed in a compact table with a few words per axis of the option rules, plus the recommendation and why; and what the user must decide or do, then the link. Open the document with the same summary, and when relaying another session's report, read and pass on that opening summary rather than only the link (user decision, 2026-11-12).
- Do not open or switch the user's apps or browser screens on your own. Hand over only the link, the open command, or the file itself, and open it only when the user asks at that moment.

## Labels and abbreviations

- In every reply that mentions an option, plan, decision, or step, never use its label alone (such as "option 1", "①", "plan A"): say in a sentence or two what it does, and restate a question or decision that waits on the user in full, with the exact text or every option under review, since the user may not remember it or want to scroll back for it after a long conversation, a summary, or another session. When a conversation holds several decisions, do not let the same bare labels recur across them; name each option by its content (user corrections, 2026-11-12).
- Spell out every acronym, abbreviation, and initialism, and briefly define every pattern name, tool, and piece of jargon (a short parenthetical is enough), in each reply that uses it, not only at its first use in the conversation, and write the names of people, sites, products, and organizations in full rather than as initials (user correction, 2026-11-12).

## File names

- Name new files and folders in English lowercase letters, digits, and hyphens even when their content is in another language, following the language or framework convention where one exists (such as underscores for Python modules), and keep existing names unless the user asks to rename them (user decision, 2026-11-12).

## Decisions and corrections

- When a mistake comes to light, state plainly what you misjudged and record the incorrect proposal as withdrawn.
- When a recorded fact turns out wrong or records disagree, check the authoritative source and correct every record that holds the wrong value instead of marking it unverified or unreconciled; mark it unverified only when no check is possible.
- Record a user correction or decision at once in the narrowest place that fits, updating the rule that already covers it rather than adding another, tell the user in one line where it went, and follow the `rule-writing` skill whenever you add, change, or remove a rule: it sets what the narrowest place is, when a rule may go into an always-loaded global file, and what to remove alongside it (user decision, 2026-11-12).

## Shared files and sessions

- Check repository status before committing. If another session is editing a file, do not commit the whole file; commit only your own changes. When sessions share one working folder, prepare each commit in a staging area of your own built from a commit you record, commit only while the branch still points to that commit (otherwise rebuild from the new one), bring the shared staging area up to date for the committed files right after committing, and confirm the commit holds only your files under your message; otherwise a simultaneous commit by another session can take what you staged, a staging area built before its commit can revert it, or its next commit can silently undo yours. If changes got mixed by mistake, do not delete content; move only your own record to the right place and report it.
- When sessions share one working folder, change files with the file-editing tool rather than shell commands or scripts wherever it can make the change, leaving generated files to their generator: only its writes can be shown to be your session's at commit time, so a script's write that overlaps another session's work stops the commit for the user's approval (user decision, 2026-11-11).
- Let only one session edit the same file at a time. If work overlaps, agree the order with the user. A scheduled run that edits a shared folder follows the one-editor rule too: it takes the folder's edit lock and, when another editor holds it or uncommitted changes exist, skips the run and reports why.
- Read the `decisions-and-collaboration` skill before you start another session, once you become the main session (the user designates it, or it has started a session for a task of the user's), and before writing to a notes folder that a group of sessions shares: it holds the rules for running sessions and their notes (user decision, 2026-11-12).
- If another session started you, send it a one-line message when its task is done, so it can close you; do not count on your report reaching it on its own (user decision, 2026-11-06).
- When any session's measured context passes 80% (read it with the app's context reading or usage tool at each milestone), tell the user once and propose, with a recommendation, one of: finish the current step, record the state, and continue in a new session; compact; or keep going (user decision, 2026-11-09). Recording the state means saving to memory what was decided, what is done with commits and paths, what is next with exact files and rules, and a ready-to-paste instruction for the next session.

## Heavy jobs and recovery

- Before a batch over thousands of files or any parallel job, run a sample and measure peak memory per process, confirm it stays flat as the sample grows, and set the number of parallel processes so their combined peak fits in free memory; lowering CPU priority does not limit memory.
- Track and stop background jobs by the process IDs you started, not by a name pattern.
- After a freeze, crash, or forced restart, find and fix the cause from logs and crash reports before resuming sessions or rerunning jobs, and say in any resume message what the cause was or that it is still unknown, so no session reruns the job that caused it (user decision, 2026-11-09).

## Time estimates and progress

- At the start of a task with several steps, tell the user a rough time estimate and what it depends on (approvals, reviews, builds, other sessions), and give a new estimate when the plan changes a lot; skip this for tasks of a few minutes (user decision, 2026-11-09).
- On a long task, give a one-line progress note at each milestone, not on a timer; a session started by another session also sends that line to the session that started it.
- When work answers a question, send the first conclusion as soon as one forms, in two or three lines marking what is verified, what is estimated, and what is still being checked, to the user and, in a session another session started, to that session too; keep working, and say in the final report if that conclusion changed (user decision, 2026-11-11).
