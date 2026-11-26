# Shared Configuration Project Rules

Rules for work on this repository itself. The root `CLAUDE.md` is the global Claude file and `codex/AGENTS.md` the global Codex file that `scripts/install.py` installs for every project, so rules that only this repository follows live here: Codex reads this file in this repository, Claude Code reads it through `.claude/CLAUDE.md`, and the session hooks read its role list through `scripts/project_rules.py`.

## Role sessions

- `설정 관리 마스터`: the project master. Decisions between the two fields with the user, assigning requests whose owner is unclear or that change both fields, and the table of open sessions and what waits on the user.
- `설정 관리 AI` (not main): shared rules, skills, hooks, and Claude and Codex settings other than their Git behavior; the app mods such as the usage panel, the Codex budget tool, the install, the rule sync, and the rule checks. It takes no new work during the structure trial below.
- `설정 관리 깃` (not main): how every project uses Git: workflow records and their modes (direct commits in the original checkout, or task worktrees and pull requests), commits, merges, worktrees, worktree owners and session write records, the Git parts of the shell and worktree guards, keeping secrets out of commits, GitHub settings and the pull-request check, and the `git-commit`, `pr-create`, and `resolve-conflict` skills. It takes no new work during the structure trial below.

Both role sessions run at the top level and work with the master by messages, since the app cannot move a session under another (2026-11-26); a move under the master, by the role-session replacement steps in `session-management`, is on hold during the structure trial, and a role's `(not main)` mark comes off once it has moved.

Each role also owns the edits to a project's rules that its shared changes bring with them, and other fields get a role session when their work appears (user decision, 2026-11-19). The role sessions use this repository's original checkout as their folder, where they only coordinate and read, since the session hooks look for this list in the rules of the session's own repository; their task sessions make the changes, runs, analyses, and reviews, as `session-management` describes.

## Where the two fields meet

- Ownership follows the behavior a change makes, not the kind of file it is in: Git behavior belongs to the Git role even inside a shared rule, skill, hook, setting, or document, and other configuration behavior to the AI role. The owning role's task session changes the source together with its generated copies, tests, settings, and documentation.
- For Git text in a shared rule or skill, the Git role owns the meaning, and the AI role reviews how it fits the other rules, their loading and size budget, and the generated copies before the user sees it.
- Command parsing, reading the session tree and the role list, and the install stay with the AI role; a change to their Git behavior, or to an interface the Git tools use (such as the start-session hook's shared-worktree check or the install's commit-hook setup), gets the Git role's review before the user sees it, and a Git change that alters shared configuration behavior gets the AI role's review.
- In the commit check, the list of checks and the rule checks belong to the AI role, and copying out the content being committed and running it from Git hooks and the pull-request check belong to the Git role; each role owns the tests of its behavior.
- A request that changes both fields' behavior, or whose owner is unclear, goes to the master, which names the leading role and the review it needs; sharing a file alone does not make a request the master's. Who writes where, overlapping work, and handovers follow `session-management` and `git-commit`.

## Structure trial

This trial applies only to settings management (decision 2026-11-25: “네, 둘 다” in `설정 관리 마스터` for the direction; the plan below confirmed on 2026-11-26 in the task session for the usage table and structure trial under `설정 관리 AI`: “네, 셋 다 맞아요”). It starts after the approved rules are installed, the master and both role sessions have read them, and the owners of open work and waiting decisions are recorded. The target start is 2026-11-26; record the actual start and an end exactly seven days later, and revise both before starting if it slips.

- The master assigns new work in both fields directly, using only the session starts the current tool permits. Each brief names the leading field and the reviews "Where the two fields meet" requires; the task's independent reviewer also checks the other field's rules and open items. Those responsibilities stay; the trial changes who coordinates them.
- Both role sessions take no new work and stay open. Their existing tasks keep their owners and parents until finished. The master holds their waiting decisions and open items for the trial, and pending start cards are moved or withdrawn so the same work cannot start twice.
- The master may run one helper at a time without separate approval for reading, collecting existing records, comparing files, or checks within work already approved: inputs and outside state stay unchanged, and the only write is a new report at an unused path it assigns in the project notes folder. The helper returns any needed user decision, tool permission, other write, or further delegation to the master. This exception holds only for this master during the active trial, lifting there the coordinating boundary in `session-management` and the approval in `agent-usage` section 1; required independent reviews and Codex's review-only use stay.
- The master keeps the trial log, its notes index rows, and its memory pointer itself. The log links each piece of work to its sessions, helpers, reviews, result, and start and end, and keeps transition work, unfinished work, rework, late questions, and shared coordinating cost visible; no saving per finished piece is claimed unless the baseline can be rebuilt on the same basis.
- The master reads this section when the trial starts and after each compaction or replacement, from the repository path recorded in its memory, and a replacement master inherits the trial's status, dates, open work, and permissions.
- At the recorded end, without the user's word to continue, the helper exception stops and the master asks; routing returns to the role sessions only by the plan's rollback steps, after each has accepted its handover. The usage table and the corrected cost wording stay after a rollback.

The plan, measurements, stop signals, and rollback steps are in `~/Documents/session-notes/ai-config/structure-trial-2026-11-25.md`.

## Decisions

- 2026-11-19, in the main session then titled “Claude 설정 관리”: the user proposed “아니면 그러면 이세션을 마스터 세션으로 두고 하위에 ai 설정세션 그리고 깃 설정 세션이런식으로 두던가 나머지는 필요하면 그때 늘리고”, and after the main session recommended keeping one session for all of it, chose the split: “일단 내가 이야기한대로 나눠줘 그게 나한테는 편할것 같아”. The main session chose the three titles above in the project master format; the user may change them. The user approved this text, with the handover of the existing Git session, on 2026-11-20 (“네, 둘 다 이대로” in the main session, “네, 맞아요” here).
