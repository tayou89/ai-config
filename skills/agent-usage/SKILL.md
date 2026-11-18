---
name: agent-usage
description: Rules for spawning and coordinating agents (sub-agents, team agents, parallel agents). Apply before using the Agent tool or creating any agent.
user-invocable: false
---

# Agent Usage Rules

Apply these rules before spawning any agent (sub-agent or team agent) and when coordinating multi-agent work.

## 1. Always Get Explicit Permission

Never create an agent without explicit user approval. When proposing, specify: count, role/scope for each, and cost concerns if applicable. A user-invoked skill whose instructions spawn agents (e.g. `/domain-pass`, or the fallback reviewer in `/outside-review`) counts as that approval for the agents it describes; anything beyond them still needs approval. A reviewer a rule requires needs no separate approval: the checklist reviewer of a large commit (`git-commit`) and a checkpoint reviewer on a Claude model (section 7); say in one line that it ran (user decision, 2026-11-09). Ask before any other agent, any Codex run, and max effort. An agent that only stands in for a tool the session lacks, such as the app's web-fetch agent reading a page, is a tool call: it needs no approval, and its prompt needs no skills (user decision, 2026-11-11). Starting another session follows the session rules in `collaboration-core` and `session-management` "Working alongside other sessions", not this section. A Codex implementation run under section 14 is approved when the user approves the plan step that assigns it to Codex, or answers yes to a one-line request that names the bundle and its reserved share; a later budget check before the run does not need a new approval (user decision, 2026-11-17).

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

When, how many, and which model family follow `decisions-and-collaboration` "Independent review": one reviewer, only at its checkpoints (just before presenting a decision, before a large change to the main deliverable, before a release or a large commit), and none for small edits, which need only self-review. At a checkpoint, propose `/outside-review` (another model family, read-only). Spawn a same-family reviewer subagent only when that is unavailable or the user prefers it. A checklist review gets a short brief and a smaller model; a reviewer standing in for the other family gets the `/outside-review` brief and a model strong enough for that full review. The review completes **before requesting user approval**.

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
- **Codex runs**: `/outside-review` sets review runs; implementation runs under section 14 take the work-type row of their bundle (Codex's smaller model `gpt-6.1-sol` at medium for an exact edit already decided, its strongest model `gpt-6-astra` at high for other code changes; checked 2026-11-17). A project's own review command comes first.
- **Not reachable by these options**: your own session (propose instead), cloud sessions (the values do not apply there), and sessions the user opens, whose values come from the app's model and effort picker or a saved environment; a main session runs at xhigh.

## 14. Codex Implementation From Spare Quota

Spend Codex's spare weekly share on approved work so both tools end their windows close to fully used, after the reviews they need (user decision, 2026-11-17). Never create work to fill the share; when no suitable work exists, leave it and say so.

- Check the budget with `python3 ~/.claude/skills/agent-usage/codex-budget.py status`. It keeps a review reserve from the recent review use on each kind of day, both reset times, and the day-to-day swing. Its `priority` says whether to hand over work that would otherwise use Claude (`offload-claude`) or approved extra work (`extra-work`); `unknown` means Claude's usage could not be read, so pass it with `--claude-percent` and `--claude-resets`.
- Hand a bundle to Codex when the bundle is likely to save more Claude effort than its brief, review, and fixes cost. That means its decisions are made, its files are listed, and tests or commands can show it is done. Keep with Claude, unless the user asks otherwise, the bundles with many hidden edge cases (parsers, concurrency, security, code that can lose data or money) and changes to the shared configuration repository.
- Reserve the bundle's estimated share with `codex-budget.py reserve` before starting, and stop if the reservation is refused. Update its state as it runs, and close it as `done`, `failed`, or `abandoned` so its unused share returns.
- Run Codex in its own worktree, following [references/codex-implementation.md](references/codex-implementation.md). Codex does not commit. After Codex exits, the result waits as `review-waiting` until the session that started the run reviews it against the rules, fixes what needs fixing, and commits from that worktree through `git-commit`; nothing is merged before that, even while that session is blocked.
- Independent reviews reserve through the same ledger and may use the review reserve, so they come before implementation.
