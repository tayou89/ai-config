<!-- Generated from shared-rules/collaboration-core.md by scripts/sync-shared-rules.py. Edit the source, not this copy. -->

# Core Collaboration Principles

Apply in every project and task. The rest of the decision and collaboration principles load as the `decisions-and-collaboration` skill when their task comes up. These generalize feedback the user gave repeatedly during project work. Follow project rules first.

## Tools and permissions

- Try methods already in use first (e.g., reading through a signed-in browser). Propose methods that require tokens, passwords, or new accounts only when there is no other way.
- Do every step your tools can reach yourself, GUI steps included (app automation, computer use, browser tools), and when one tool cannot reach a target, try the others before handing the step over. Ask the user only to approve actions that need approval, then carry them out yourself.
- Hand the user only steps your rules forbid you or no tool can reach, say why, and list them in order: which app, which screen, and what to press, including menu names and shortcuts.

## Checks before showing work

- Before showing any work (screens, documents, plans, code), run as automatic checks everything a rule makes checkable: repeated expressions on one screen, clipped, overflowing, or overlapping text, totals and example data that must agree, the narrowest supported width, broken links and inconsistent terms in documents, and type, lint, and test runs for code. Do not spend a model review on what a script can check.
- Report what the automatic checks covered, what the reviewer found and what you changed, and what was not checked.

## Decisions and corrections

- When the user decides, record it in the rule document on the spot and tell the user in one line where it was recorded.
- When a mistake comes to light, state plainly what you misjudged and record the incorrect proposal as withdrawn.
- When a user correction, decision, or question is likely to recur in other projects or tasks, update the global rules as "Growing the rules" in the `decisions-and-collaboration` skill describes.

## Shared files and sessions

- Check repository status before committing. If another session is editing a file, do not commit the whole file; commit only your own changes. When sessions share one working folder, prepare each commit in a staging area of your own, bring the shared staging area up to date for the committed files right after committing, and confirm the commit holds only your files under your message; otherwise a simultaneous commit by another session can take what you staged, or its next commit can silently undo yours. If changes got mixed by mistake, do not delete content; move only your own record to the right place and report it.
- Let only one session edit the same file at a time. If work overlaps, agree the order with the user.
- Each time a session you started reports a finished turn, check right then whether its task is done: work committed and recorded, nothing waiting on the user, and no session it started still working. If so, offer in that same reply to close it, without waiting to be asked, noting any proposals it left; close it only after the user agrees. Leave open a session that is still working or waiting for the user.
- When the user designates a main session, keep it free for conversation: answer quick questions there, start each task the user asks for that needs edits or several steps in its own session right away without a separate proposal, send that task's follow-ups to its session, and report each result in the main session in one or two lines. Work you propose yourself still waits for the user's agreement. In a main session this replaces keeping just-decided work in the current session. If no session can be started from there, say so and do the task in the main session.
