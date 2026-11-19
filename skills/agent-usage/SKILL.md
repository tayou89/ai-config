---
name: agent-usage
description: Rules for spawning and coordinating agents (sub-agents, team agents, parallel agents). Apply before using the Agent tool or creating any agent.
user-invocable: false
---

# Agent Usage Rules

Apply these rules before spawning any agent (sub-agent or team agent) and when coordinating multi-agent work.

## 1. Always Get Explicit Permission

Never create an agent without explicit user approval. When proposing, specify: count, role/scope for each, and cost concerns if applicable. A user-invoked skill whose instructions spawn agents (e.g. `/domain-pass`) counts as that approval for the agents it describes; anything beyond them still needs approval. A reviewer a rule requires needs no separate approval: the checklist reviewer of a large commit (`git-commit`) and a checkpoint reviewer (section 7), on Codex or on a Claude model in its place; say in one line that it ran (user decisions, 2026-11-09 and 2026-11-18). Ask before any other agent, any other Codex run outside section 14, and max effort. An agent that only stands in for a tool the session lacks, such as the app's web-fetch agent reading a page, is a tool call: it needs no approval, and its prompt needs no skills (user decision, 2026-11-11). Starting another session follows the session rules in `collaboration-core` and `session-management` "Working alongside other sessions", not this section. Section 14 is the user's standing approval for each Codex run it assigns once the budget tool grants its reservation (user decision, 2026-11-18).

## 2. Propose Splitting When Scope Grows

Propose agent splitting when: accuracy drops on repetitive review, scope too wide for sequential processing, or mid-task when scope feels larger than expected.

## 3. Parallelization Preferred

For long-running tasks (exhaustive audits, large analysis), prefer splitting into parallel agents and show the split plan.

## 4. Proactive Agent Use

When scope is large or thoroughness is needed, actively propose agents rather than doing everything sequentially.

## 5. Token Budgeting

Before proposing agents, estimate token cost (file count × avg complexity) and remaining budget. Adjust agent count accordingly — fewer agents with broader scope when budget is tight, with the rationale for the count in the proposal.

## 6. Team Agent Approval

Team agent creation/deletion requires explicit approval.

## 7. Independent Review

When, how many, and which model family follow `decisions-and-collaboration` "Independent review": one reviewer, only at its checkpoints (just before presenting a decision, before a large change to the main deliverable, before a release or a large commit), and none for small edits, which need only self-review. At a checkpoint, run the `outside-review` skill (another model family, read-only) without asking (user decision, 2026-11-18). Spawn a same-family reviewer subagent in its place only when the budget tool refuses its reservation or Codex cannot run, saying why with the reset time, or when the user asks for one. A checklist review gets a short brief and a smaller model; a reviewer standing in for the other family gets the `outside-review` brief and a model strong enough for that full review. The review completes **before requesting user approval**.

## 8. Plan Rigor = Code Rigor

Apply the same verification depth to plans as to code. Every classification ("fixable", "unavoidable", "redundant") must cite concrete evidence (line numbers, type resolution trace, actual compile/test output). Never trust agent summaries without verifying key claims against the actual code, document, or source they summarize. For type-related issues, trace the full type resolution chain step by step before classifying.

## 9. Reviewer Scope

A reviewer covers the **full change scope**, not a file range. File-range splitting misses cross-cutting issues.

## 10. Rule-Based Checklist Review

Review agents must **load all applicable skills** (code-style, typescript, etc.) with the Skill tool and read the project CLAUDE.md, then check each rule against the changed code, document, or screen systematically. Never review from memory or general impression — always verify rule-by-rule against the loaded text.

## 11. Verification Depth

Verification agents must trace actual runtime variable values step-by-step, not just check code existence. "The code exists" ≠ "the code is correct."

## 12. Exhaustive Audits Need the Right Agent Config

Never use fast-and-shallow exploration agents for exhaustive audits — they estimate/sample instead of checking every occurrence. Unlike checklist reviews (section 7), audits that must count every occurrence need a strong model. For exhaustive work, use general-purpose subagent with an explicit strong model override, and include "Do not estimate. Check every single occurrence individually. Report exact counts, not approximations." in the prompt. If agent results look round-numbered or include phrases like "approximately" or "roughly", re-verify manually or re-run with stricter prompt.

## 13. Model and Effort for Subagents and Sessions

Apply the work-type table in `decisions-and-collaboration` "Model and effort for runs you start" (user decision, 2026-11-08). In Claude, "strongest" is Opus 5.5 (alias `opus`, id `claude-opus-5-5`) and "smaller" is Sonnet 5.5 (alias `sonnet`, id `claude-sonnet-5-5`).

- **Subagents**: pass `model` (alias) and `effort` on every Agent call; this rule is the explicit instruction the tool needs before it passes `effort`. Without `effort`, a subagent on a model other than the session's runs at that model's default, not the table's. Built-in search agents keep their own model and count as mechanical work. To raise one, run or resume it again with the higher value.
- **Sessions you start**: pass `model` (full id) and `effort` on the session-start tool with fresh context. A fork inherits the starting session's values, so when its row is lower, lower it right after the start with the session-effort tool. Raise a running session with the session-effort and session-model tools, which need no prompt up to the starting session's own level.
- **Codex runs**: `/outside-review` sets review runs; runs under section 14 take the work-type row of their work (Codex's smaller model `gpt-6.1-sol` at medium for an exact edit already decided and for collecting or counting records, its strongest model `gpt-6-astra` at high for other code changes and research, and at xhigh for code with many hidden edge cases; checked 2026-11-17). A project's own review command comes first.
- **Not reachable by these options**: your own session (propose instead), cloud sessions (the values do not apply there), and sessions the user opens, whose values come from the app's model and effort picker or a saved environment; a main session runs at xhigh.

## 14. Codex Does the Work From Spare Quota

Spend Codex's spare weekly share on approved work so both tools end their windows close to fully used, after the reviews they need (user decision, 2026-11-17). Never create work to fill the share; when no suitable work exists, leave it and say so.

- Check the budget with `python3 ~/.claude/skills/agent-usage/codex-budget.py status`. It keeps a review reserve from the recent review use on each kind of day, both reset times, and the day-to-day swing. Its `priority` says whether to hand over work that would otherwise use Claude (`offload-claude`) or approved extra work (`extra-work`); `unknown` means Claude's usage could not be read, so pass it with `--claude-percent` and `--claude-resets`.
- While its verdict is `delegate`, Codex does the actual work of every task (implementation, edits, record collection and counting, measurement, research) in any repository, the shared configuration repository included, and Claude writes the brief, decides, reviews, and fixes (user decision, 2026-11-18). Claude keeps only:
  - judgment work: decisions, plans, recommendations, rule wording, design choices;
  - work smaller than its brief and review, about a handful of tool calls;
  - work only Claude's tools reach: running sessions, the app's browser, design-tool connections, sites that need the user's sign-in;
  - work whose reservation the budget tool refuses.
- For code with many hidden edge cases (parsers, concurrency, security, hooks and guards, code that can lose data or money), Claude first writes the cases it must handle and the tests that prove them, as judgment work, then reviews Codex's result at the strongest level; the independent reviews the rules already require still run (user decision, 2026-11-18).
- Every plan step and session brief carries a line starting with `Codex:` that names the part Codex does, the exception that keeps the work with Claude, or that the verdict is not `delegate`; the session-start hook returns a brief without it. A brief never tells the session it starts to ask the user before a run this section approves (user decision, 2026-11-18).
- Reserve every Codex run that is not an independent review with `codex-budget.py reserve` before starting, builder runs of a project's own workflow included, and put the id it prints on the brief's first line as `Budget reservation: <id>`: the budget tool counts each run's use to the reservation that line names, and a run without the line counts as review use unless it runs inside a reserved folder (user decision, 2026-11-19). When the reservation is refused or the tool cannot read the quota, do the work yourself without asking and say so in one line with Codex's reset time or the read error; ask whether to wait only when Claude is also projected at 100% or more, or the user gave that work to Codex, showing both tools' usage and reset times. Update its state as it runs, and close it as `done`, `failed`, or `abandoned` so its unused share returns.
- Run implementation in its own worktree, and research or record collection in its own empty folder with web search on when its questions need it, following [references/codex-implementation.md](references/codex-implementation.md); check each research finding against its cited source before using it. Codex does not commit. After Codex exits, the result waits as `review-waiting` until the session that started the run reviews it against the rules, fixes what needs fixing, and commits from that worktree through `git-commit`; nothing is merged before that, even while that session is blocked.
- Independent reviews reserve through the same ledger, carry the same first line, and may use the review reserve, so they come before implementation.
