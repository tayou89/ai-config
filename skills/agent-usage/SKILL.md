---
name: agent-usage
description: Rules for spawning and coordinating agents (sub-agents, team agents, parallel agents). Apply before using the Agent tool or creating any agent.
user-invocable: false
---

# Agent Usage Rules

Apply these rules before spawning any agent (sub-agent or team agent) and when coordinating multi-agent work.

## 1. Always Get Explicit Permission

Never create an agent without explicit user approval. When proposing, specify: count, role/scope for each, and cost concerns if applicable. A user-invoked skill whose instructions spawn agents (e.g. `/domain-pass`, or the fallback reviewer in `/outside-review`) counts as that approval for the agents it describes; anything beyond them still needs approval.

## 2. Propose Splitting When Scope Grows

Propose agent splitting when: accuracy drops on repetitive review, scope too wide for sequential processing, or mid-task when scope feels larger than expected.

## 3. Parallelization Preferred

For long-running tasks (exhaustive audits, large analysis), prefer splitting into parallel agents. Show split plan and get approval.

## 4. Proactive Agent Use

When scope is large or thoroughness is needed, actively propose agents rather than doing everything sequentially. Approval is still required.

## 5. Token Budgeting

Before proposing agents, estimate token cost (file count × avg complexity) and remaining budget. Adjust agent count accordingly — fewer agents with broader scope when budget is tight. Always show proposed count + rationale and get approval.

## 6. Team Agent Approval

Team agent creation/deletion requires explicit approval.

## 7. Independent Review

When, how many, and which model family follow `decisions-and-collaboration` "Independent review": one reviewer, only at its checkpoints (just before presenting a decision, before a large change to the main deliverable, before a release or a large commit), and none for small edits, which need only self-review. At a checkpoint, propose `/outside-review` (another model family, read-only). Spawn a same-family reviewer subagent only when that is unavailable or the user prefers it. A checklist review gets a short brief and a smaller model; a reviewer standing in for the other family gets the `/outside-review` brief and a model strong enough for that full review. The review completes **before requesting user approval**.

## 8. Plan Rigor = Code Rigor

Apply the same verification depth to plans as to code. Every classification ("fixable", "unavoidable", "redundant") must cite concrete evidence (line numbers, type resolution trace, actual compile/test output). Never trust agent summaries without verifying key claims against actual code. For type-related issues, trace the full type resolution chain step by step before classifying.

## 9. Reviewer Scope

A reviewer covers the **full change scope**, not a file range. File-range splitting misses cross-cutting issues.

## 10. Rule-Based Checklist Review

Review agents must **read all applicable skill files** (code-style, typescript, etc.) and project CLAUDE.md, then check each rule against the changed code systematically. Never review from memory or general impression — always open the rule files and verify rule-by-rule.

## 11. Verification Depth

Verification agents must trace actual runtime variable values step-by-step, not just check code existence. "The code exists" ≠ "the code is correct."

## 12. Exhaustive Audits Need the Right Agent Config

Never use fast-and-shallow exploration agents for exhaustive audits — they estimate/sample instead of checking every occurrence. Unlike checklist reviews (section 7), audits that must count every occurrence need a strong model. For exhaustive work, use general-purpose subagent with an explicit strong model override, and include "Do not estimate. Check every single occurrence individually. Report exact counts, not approximations." in the prompt. If agent results look round-numbered or include phrases like "approximately" or "roughly", re-verify manually or re-run with stricter prompt.
