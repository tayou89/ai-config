# Shared Configuration Project Rules

Rules for work on this repository itself. The root `CLAUDE.md` is the global Claude file and `codex/AGENTS.md` the global Codex file that `scripts/install.py` installs for every project, so rules that only this repository follows live here: Codex reads this file in this repository, Claude Code reads it through `.claude/CLAUDE.md`, and the session hooks read its role list through `scripts/project_rules.py`.

## Role sessions

- `설정 관리 마스터`: the project master. Decisions between the two fields with the user, assigning requests whose owner is unclear or that change both fields, and the table of open sessions and what waits on the user.
- `설정 관리 AI`: shared rules, skills, hooks, and Claude and Codex settings other than their Git behavior; the app mods such as the usage panel, the Codex budget tool, the install, the rule sync, and the rule checks.
- `설정 관리 깃`: how every project uses Git: workflow records and their modes (direct commits in the original checkout, or task worktrees and pull requests), commits, merges, worktrees, worktree owners and session write records, the Git parts of the shell and worktree guards, keeping secrets out of commits, GitHub settings and the pull-request check, and the `git-commit`, `pr-create`, and `resolve-conflict` skills.

Each role also owns the edits to a project's rules that its shared changes bring with them, and other fields get a role session when their work appears (user decision, 2026-11-19). The role sessions use this repository's original checkout as their folder, where they only coordinate and read, since the session hooks look for this list in the rules of the session's own repository; their task sessions make the changes, runs, analyses, and reviews, as `session-management` describes.

## Where the two fields meet

- Ownership follows the behavior a change makes, not the kind of file it is in: Git behavior belongs to the Git role even inside a shared rule, skill, hook, setting, or document, and other configuration behavior to the AI role. The owning role's task session changes the source together with its generated copies, tests, settings, and documentation.
- For Git text in a shared rule or skill, the Git role owns the meaning, and the AI role reviews how it fits the other rules, their loading and size budget, and the generated copies before the user sees it.
- Command parsing, reading the session tree and the role list, and the install stay with the AI role; a change to their Git behavior, or to an interface the Git tools use (such as the start-session hook's shared-worktree check or the install's commit-hook setup), gets the Git role's review before the user sees it, and a Git change that alters shared configuration behavior gets the AI role's review.
- In the commit check, the list of checks and the rule checks belong to the AI role, and copying out the content being committed and running it from Git hooks and the pull-request check belong to the Git role; each role owns the tests of its behavior.
- A request that changes both fields' behavior, or whose owner is unclear, goes to the master, which names the leading role and the review it needs; sharing a file alone does not make a request the master's. Who writes where, overlapping work, and handovers follow `session-management` and `git-commit`.

## Decisions

- 2026-11-19, in the main session then titled “Claude 설정 관리”: the user proposed “아니면 그러면 이세션을 마스터 세션으로 두고 하위에 ai 설정세션 그리고 깃 설정 세션이런식으로 두던가 나머지는 필요하면 그때 늘리고”, and after the main session recommended keeping one session for all of it, chose the split: “일단 내가 이야기한대로 나눠줘 그게 나한테는 편할것 같아”. The main session chose the three titles above in the project master format; the user may change them. The user approved this text, with the handover of the existing Git session, on 2026-11-20 (“네, 둘 다 이대로” in the main session, “네, 맞아요” here).
