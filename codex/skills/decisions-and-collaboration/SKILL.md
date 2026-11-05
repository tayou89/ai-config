---
name: decisions-and-collaboration
description: Present options for user decisions, record decision status, handle design-tool comments, coordinate with concurrent sessions, run automatic checks and checkpoint reviews before presenting work, or update the global rules.
---

<!-- Generated from rules/decisions-and-collaboration.md by scripts/sync-codex-rules.py. Edit the source, not this copy. -->

# Decision Requests and Collaboration Principles

Apply in every project when asking the user for decisions, recording decision status, and working alongside other sessions. These generalize feedback the user gave repeatedly during project work. Follow project rules first.

## Presenting decisions

- Do not ask the user to choose visual decisions (screens, copy, color, layout) from text or images in chat. Build a comparison board in real screen context in the design tool and give a link where the user can review and comment on the spot.
- Give links that open in the app the user actually uses. If the chat client cannot open an app-specific link, give it as a one-line shell command that opens it.
- Do not open or switch the user's apps or browser screens on your own. Give only the link and the open command; open it only when the user asks at that moment.
- Head each option on a comparison board with a large letter badge (A, B, C), a bold option name, and a marker (recommended, current), followed by one line of pros and cons at body size. Group each option's screens on one background and mark the recommended option with a border color. Do not rely on small column titles alone.
- Each time you ask for a decision, re-explain the options from scratch in plain language. Compare cost, effort, ease of changing later, and risk in a table, and state the recommendation with its reason, what the decision is blocking, and when it is needed.
- If the user's question suggests a direction not among the existing options, add it as a new option and compare it on the same criteria. If it holds up, change the recommendation and say why.
- When recommending, show how other services usually handle it, and distinguish what you verified with sources from what you only know.
- Show comparison boards not only with one ideal example but also with the most common real situation (e.g., the first week with almost no records). Count how often the same expression repeats on one screen.
- Distinguish whether a reason for a recommendation is essential or merely convenient, and do not overstate it. Correct any overstatement as soon as you notice it.
- Show only the decisions the user must make now on a comparison board. Collapse settled decisions into a one-line summary or remove them, and do not keep piling decisions onto one board. Build mockups at the actual width and situation of use (e.g., the width of a capture the user sent).

## Handling decision status

- Do not treat mockup defaults, placeholder values, or agent recommendations as settled principles. Before proposing to remove or change something, check the records for whether its basis is a user decision or a default, and what intent the user stated before.
- In documents, distinguish user-confirmed decisions, agent recommendations, agent defaults (not confirmed by the user), and pending decisions, with dates.
- When the user decides, record it in the rule document on the spot and tell the user in one line where it was recorded.
- Before proposing, check the proposal against existing rules. If it conflicts, say so or revise it to fit the rules before proposing.
- Before filing an item as a pending decision, check what it actually blocks (a screen, development, a launch step). If nothing is blocked now, file it as a later task with its deadline instead of asking now, and do not invent a quantity or choice the plan does not need.
- Before proposing a new feature or idea, search the planning documents for it. If it already exists, mention that record first and start from it; do not present an existing idea as new.
- When a mistake comes to light, state plainly what you misjudged and record the incorrect proposal as withdrawn.

## Tools and permissions

- Try methods already in use first (e.g., reading through a signed-in browser). Propose methods that require tokens, passwords, or new accounts only when there is no other way.
- For actions the user must perform, list in order which app, which screen, and what to press, including menu names and shortcuts. If the agent can do it instead, say how.

## Checks and independent review

- Before showing any work (screens, documents, plans, code), run as automatic checks everything a rule makes checkable: repeated expressions on one screen, clipped, overflowing, or overlapping text, totals and example data that must agree, the narrowest supported width, broken links and inconsistent terms in documents, and type, lint, and test runs for code. Do not spend a model review on what a script can check.
- Use an independent reviewer agent only at checkpoints: just before presenting a decision to the user, before applying a large change to the main deliverable, and before a release or a large commit. Skip it for small edits. A reviewer starts without the conversation and must re-read rules and artifacts, so each review costs tens of thousands of tokens.
- Give the reviewer a short brief instead of the conversation: the goal, the applicable rules as a checklist, and the artifact or one or two captures. Prefer a smaller, cheaper model for checklist reviews, and run it in parallel when the tool allows.
- Prefer a reviewer from a different model family than the author when one is available, because a model tends to miss its own habits. Run it read-only and non-interactively with the same brief, through a route that can pass what the review needs (captures for visual work, not only code diffs), and without an automatic fix-and-review loop when the user makes the decisions. Say that the brief and captures go to that vendor, and leave personal photos out unless the user approves. Check each finding on the artifact before acting on it. If no other family is available, say why and use a fresh reviewer of the same family.
- Report what the automatic checks covered, what the reviewer found and what you changed, and what was not checked.

## Working alongside other sessions

- Check repository status before committing. If another session is editing a file, do not commit the whole file; commit only your own changes. If changes got mixed by mistake, do not delete content; move only your own record to the right place and report it.
- Do not touch comparison boards, decisions, or screens made by other sessions. If two sessions must edit the same screen, ask the user which one takes it.
- Propose a new session for work that finishes on its own: it leaves its own result (e.g., implementing another page or researching another topic), and the decisions it needs are already recorded, so a short brief is enough to start it. Keep in the current session work that carries out what was just decided or that needs context found only in the conversation.
- Prefer a new session for such work even more when the current conversation is already long or has been summarized: carrying the whole history costs more every turn and summaries lose detail, while a new session re-reads only the recorded decisions it needs.
- Open a new session only after the user agrees to the proposal. State in the proposal what it takes on, which files it changes, and what it leaves when done.
- When a session you started has finished its task, check its result (work committed and recorded, nothing waiting on the user), then offer to close it, noting any proposals it left, and close it only after the user agrees. Leave open a session that is still working or waiting for the user.
- Give a new session a short title of a few words naming its topic, in the user's language. Leave scope or mode notes such as “(read-only)” out of the title and put them in the brief.
- Let only one session edit the same file at a time. If work overlaps, agree the order with the user.
- Determine what a design-tool comment refers to by matching its timestamp with the change history at that time, not only its position. If the comment was already handled, say when and by what.
- Resolve comments only after confirming the fix is reflected on the actual screen. Leave comments on pending decisions open. Replies cannot be resolved separately, so resolve the first comment of the thread.
- Check the shared to-do list for tasks other sessions handed to this session, finish those tasks, and then update the list.

## Growing the rules

- When a user correction, decision, or question is likely to recur in other projects or tasks, compare it on the spot with previous conversation records and existing rules (project rules and these global rules).
- If the global rules do not cover it, generalize it and add it to the right file immediately: screens, copy, and design tools in `design.md`; decisions, collaboration, and tool use in this file; planning, research, and vendor selection in `planning.md`; writing in `writing.md`. If no file fits, create a new file by topic.
- Keep project-specific colors, names, numbers, and decisions in project rules only. Record the generalized principle alongside the project rule, and tell the user in one line where it was recorded.
- Treat planning, research, development, tool use, and writing the same way, not only design.
- Keep only principles that fit any project in the global rules. Do not use project names, screen names, or project copy and numbers as examples; when an example is needed, use a generic one.
- Write global rules in English and keep them tool-neutral so every AI tool can use them. User-facing replies and project documents follow the user's language.
- The source of the global rules is `rules/` in the public shared configuration repository `tayou89/ai-config`. Installed local copies are not linked to it, so edit a checkout of the repository, regenerate the tool-specific copies with its sync script, re-check for project-specific terms, commit and push, and then refresh the installed copies. When adding a file, also add it to the README install table and the sync script.
