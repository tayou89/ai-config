---
name: code-plan
description: Prepare evidence-based plans for substantial features, structural changes, or complex fixes; skip trivial edits and already settled planning.
---

<!-- Adapted from skills/code-plan/SKILL.md at 39f8aff2f8d5c731fb2dfd0858ad9d361930b725 -->
<!-- Left out or changed on purpose (audited 2026-11-24):
- The opening authorization line, and sections 7 and 9 reuse existing approval instead of asking again: Codex permissions (codex/MIGRATION.md, "Repeated plan/build/commit approvals"). Section 10 also asks to confirm the commit is your own and unpushed before amending, which Claude's commit wrapper checks.
- Section 1 uses the project's plan folder or plans/ and leaves global Git ignore settings alone: codex/MIGRATION.md, "claude-plans/ plus global Git ignore changes".
- Section 3 names typescript for .ts only: the Codex code-style still holds the rules JavaScript and TypeScript share, which Claude moved into typescript on 2026-11-12.
- Section 3b adds how a review applies the check, since Codex reviews plans and installs nothing (user decision, 2026-11-24).
- Section 3c says "command guards and approval reviews": Codex has no shell guard hook (codex/MIGRATION.md, "Claude commit wrapper and PreToolUse hooks").
- Section 9 lets work that does not depend on the halted design go on, and section 12 names the session's status record instead of memory alone: Codex wording kept on restoring the Claude text (user decision, 2026-11-24).
- Section 4a item 5 points at the root-cause principle in the global instructions, where Codex keeps the work principles.
-->

# Plan Workflow

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

## 1. Choose the Plan Location

Use the project's existing plan/document location; otherwise use `plans/` at the project root, the original checkout's when the project uses worktrees, since a plan must outlive the task's worktree (user decision, 2026-11-13). Respect a user-designated output directory. Do not modify global Git ignore settings merely to store a plan. Follow the repository's tracking policy.

## 2. Determine Plan File

- Filename: `plan-{feature-name}.md` (English kebab-case)
- Existing file: add new version at top (keep previous versions)
- New file: create fresh
- **Parent-child structure**: Child plans live in a subfolder named after the parent's topic (parent filename without `plan-` prefix and `.md` suffix). When a plan first gains children, the parent itself moves INTO that folder (`plan-foo.md` → `foo/plan-foo.md`). Children sit alongside the parent (`foo/plan-bar.md`). Grandchildren recurse: `foo/bar/plan-baz.md`. Standalone plans (no children) stay flat at `plans/plan-xxx.md`.
- **Split rule**: If parent file exceeds ~30k tokens OR a task is conceptually separate, create a child plan following the structure above. Include `### Parent Plan` section with sibling path (`plan-foo.md`) or ancestor path (`../plan-foo.md`), version, task reference, prerequisites. Update parent's task entry with a cross-reference to the new file.
- **Completion marking**: When a plan's latest version's work is fully complete (all implementation steps committed, no follow-up scope remaining, no further versions expected), rename `plan-foo.md` to `plan-foo.done.md` and change the latest version's status line to `COMPLETED (<commit-hash>)`. Parent plan references to this file must update to the new `.done.md` suffix. Plans with any pending/active work stay as `plan-foo.md`. Filter active plans with `find plans -name 'plan-*.md' ! -name '*.done.md'`.

## 3. Reference Relevant Skills

Plans must apply the same skill rules as implementation code. Before drafting diff blocks or step descriptions, read every skill whose trigger condition matches the plan's content. Common cases:

- **code-style**: any code change
- **typescript**: `.ts` file changes — includes type-system reasoning needed at planning time (e.g. structural-compat check before declaring an index signature dead)
- **typescript-migration**: JS→TS conversion
- **python**: `.py` file changes — includes the formatter-owned layout and tool order the plan's sample code must pass
- **integration-testing**: live/simulator runtime test
- **agent-usage**: plans that spawn agents

Skipping this step produces avoidable plan revisions when implementation surfaces skill-documented constraints.

## 3a. Design Prerequisites Gate (mandatory)

Before planning an implementation step, list the designs it depends on (module structure, data model, request/response and error contracts, authentication flow, non-functional targets such as response time, and, for a step that ships to users, the operations plan's logging, alerts, and rollback from product-planning "Operations readiness"), each with its status (approved, draft, missing) and the steps it gates, and show the list to the user whenever it changes. Plan the step only after those designs are approved, and propose a missing design as its own task first. Design the data model for the whole release scope before writing the first table, then build it incrementally: tables reference each other, and schema changes become the costliest to undo once real data exists. Work that depends on no pending design (tooling, scaffolding) may proceed in parallel when stated as such.

## 3b. Agent Tooling Check

When a plan adopts a new language, framework, or external service, or enters a stage that handles sign-in, payments, personal data, real money, or a first deploy, check that tool's official agent tooling (plugin, MCP server, skill, language server) and the official plugin directory, and propose project-scope installs with their cost and risk (user decision, 2026-11-09). When reviewing such a plan, check that this research was done and that its proposals are justified; a review installs nothing.

## 3c. Visible Live Switch

When a plan gives an application a production, staging, or live mode, select that mode only on the command line that starts it: a mode or environment option (`--mode live`, `--env production`) or an environment variable set on the command itself (`APP_MODE=live <command>`), with the safe mode as the default. Never let a config-file or `.env` value alone turn it on, and do not use a separate confirmation flag as the switch. Command guards and approval reviews see only the command line, so the mode must be readable there before the run; tested 2026-11-09, a command guard that recognizes mode words such as `production`, `staging`, and `live` asked before `--mode live` and `<NAME>_MODE=live` but let `--confirm-live` and a config-file `mode = "live"` run without asking.

## 4. Pattern Alignment Check

For structural decisions (new file layout, where a new type lives, module split, naming), grep 2-3 analogous cases in the same codebase. Match the dominant local pattern, or justify the deviation in the plan. Skill rules don't override local conventions. If no precedent exists, flag it.

## 4a. Pre-Plan Exhaustive Analysis (mandatory)

Before drafting any plan, perform all of the following. Skipping any is the root cause of avoidable plan revisions.

1. **Owner and duplication scan**: For every piece of state, config, or flag the plan touches, name the owner class/module, and search the codebase for the guard/query/narrow patterns (NO_OPERATION guards, type narrows, optional chains) the plan would introduce. Where `code-style` "Boilerplate Belongs on the Owner" calls for it, the plan adds a typed API on the owner (e.g. `getX()` / `registerXHandler(callback)`) instead of the client code.
2. **3+ alternatives required**: Never propose the first working design. Evaluate at least three: (a) the dominant local pattern, (b) an industry-standard pattern for this concern (Observer, Strategy, etc.), (c) any cross-cutting-concern separation form. Compare them on the shared decision axes, naming the lifetime assumptions, failure conditions, and evidence that tell them apart. Present the best, not the first found.
3. **Cross-cutting policy fields vs metadata**: Behavior-controlling flags (cancel policy, retry policy, timeout policy) belong as top-level fields on the entity, at the same level as other policy fields, not crammed into `metadata`/generic data containers. `metadata` is for identifiers and payload, not for policy.
4. **Shared resources**: For a quota, port, lock, or other resource that several sessions or processes use at once, name its single owner and how concurrent users reserve and release it, and check the rules that already gate that resource for conflicts, since a read-only check cannot stop two users from taking the same share (user decision, 2026-11-17).
5. **Earlier repairs**: For a plan that fixes a failure, state the failed expectation and record `Earlier repairs:` with each earlier repair of it found in the commits, plans, and notes, searched by behavior and mechanism rather than only by file name or commit type, with what each was meant to prevent and whether it was in place when the failure recurred, or `none found` with where you searched. When there are any, say what they left in place and why this plan reaches it, as the root-cause principle in the global instructions describes (user decisions, 2026-11-18 and 2026-11-20).

## 4b. Plan Revision Minimization

v2+ plan revisions are failure signals — the design thinking that led to v(N+1) should have happened at v1. Acceptable revisions: user requirement change, externally-discovered constraint. Unacceptable: design omissions that section 4a should have caught (missed duplication, missed owner, missed alternative). When evidence shows the design is wrong, revise at once, however large the change, rather than finishing on it: the omission is the failure, not the revision (user decision, 2026-11-18).

When a revision happens, in the new version's "Changes from v(N-1)" section, briefly state what v(N-1) missed and why. Then check whether the miss should become a permanent rule and, if so, record it at once as `collaboration-core` "Decisions and corrections" describes; one correction is enough.

## 4c. Plan Completeness

All uncertain items must be fully investigated and resolved during the planning phase. Plans must not contain "TBD", "needs verification", or items deferred to implementation. If something cannot be determined without code changes, state the concrete experiment and its possible outcomes in the plan. Sample code in plans is feasibility-checked before approval: section 6 decides when the full check runs and when an additive-only plan records `Mock feasibility: skipped (additive-only change)` instead.

When a plan serves a goal made of listed parts (stages, areas, audiences), map each part to the steps and outputs that cover it before presenting the plan; an empty row is a gap to fill or a scope question to ask.

## Language

Plan content (headings, descriptions, steps) is written in the language `work-principles` "Language" sets for what the user reads.
Filenames stay English kebab-case (filesystem convention).
Commit messages follow git-commit skill rules separately.

## 5. Plan Format

New plan = v1, revision = previous version + 1.

```markdown
## v{N} — {YYYY-MM-DD} | PENDING APPROVAL
{revision only: ### Changes from v{N-1}\n- change list}

### Goal
What and why, concisely.

### Implementation Steps
1. Step-by-step plan with before/after diff blocks:

\`\`\`diff
# path/to/file.ts (L42-45)
- old code
+ new code
\`\`\`

### Architecture (if structural changes)
Module relationships, data flow, inheritance in ASCII diagrams. Skip for simple file edits.

### Files to Modify / Create
- `path/to/file.ts` — reason

### Test Approach
- How to verify
```

**Revision rules**: Mark previous version as `SUPERSEDED`. New version must be **independent and complete** — don't reference previous versions ("same as v2"). Keep previous versions as history.

## 6. Mock Feasibility Check (mandatory for type/signature-changing plans)

When the plan changes generic signatures, interface contracts, callback signatures, or types used across multiple consumers/providers, perform a mock feasibility check **before requesting approval**:

1. **Extract sample code's key signatures** — interface definitions, function signatures, generic declarations, call-site examples.
2. **Prepare a scratch copy of the project** (a temporary worktree or copy of the current commit, with its own configuration and dependencies), stubbing only consumers and providers that do not exist yet (or a TypeScript playground for `.ts` when the project has no code yet).
3. **Run the language's type checker and the project's full test suite** — `tsc --noEmit` for TypeScript, `mypy` for Python with type hints, `cargo check` for Rust, etc. — on the unchanged copy for a baseline, then apply the change and run the same commands again. Existing tests exercise signatures and behavior, also through subprocesses and fixtures that never name the changed code, so a search cannot pick the tests that matter: running only the new tests once missed three broken existing tests (2026-11-16). Focused runs may come first for quick feedback but never replace the full run; follow a narrower set only where the project's rules name one, and when the full run cannot finish, record the check as incomplete, not passed.
4. **Map variance positions** of every generic parameter in the plan (covariant / contravariant / invariant). Confirm the variance is what the plan assumes — sibling-generic assignability often fails silently when a callback puts `T` in argument position. See `typescript` skill "Generic Variance — Plan-Time Check" for details.
5. **Trace internal call chains**. If method A calls method B, and B's signature changes, A must still compile. Map A→B→C... chains explicitly.
6. **Record result in the plan body** — `Mock feasibility: passed` only when every required check finished with no new failures (with what was stubbed and the baseline and trial results: counts and the names of failures and skips), `Mock feasibility: failed (reason)`, or `Mock feasibility: incomplete (what did not run and why)`.

Failure → revise the plan before requesting approval. Don't defer this to implementation — implementation surprises ("the plan compiled in isolation but the whole project doesn't") almost always trace back to skipping this step.

**Skip allowed only when**: the plan adds optional fields without changing existing signatures, generics, or call chains. Even then, briefly note `Mock feasibility: skipped (additive-only change)` in the plan body for traceability.

Run the same baseline and full-suite steps whenever a plan's code is tried in the project as evidence for approval, whether or not it changes signatures; an isolated experiment shows only the property it tests.

## 6a. Style Compliance Check (mandatory before approval)

Before requesting plan approval, audit every diff block line-by-line against every rule of `code-style` and the language skill, not a subset. Record result in plan body — `Style compliance: passed (rules audited: ...)` or `Style compliance: failed (reason → fixed)`. Failure → revise diff before approval; don't carry violations into implementation.

**Formatter check**: When the project uses or the plan adopts an auto-formatter or a linter with automatic fixes, run them in the project's order (fixes, then formatting) over sample code that exercises every wrapping and layout rule the plan keeps: a wrapped condition, a long signature and call, multi-item collections and imports, and blank lines between definitions. Sample code without those cases passes trivially and hides conflicts. Formatter output overrides hand style; where they differ, follow the formatter and record which one wins in the language skill, not only in the plan. Record `Formatter check: passed (<tool> <version>, cases exercised)` or the differences found and how they were resolved.

## 7. Request Approval

Show a concrete plan and obtain explicit approval when scope or design still requires a user decision. Approval already given for the same plan or concrete implementation remains valid, whether given in this session or relayed as `session-management` "Working alongside other sessions" describes; record it and continue. Do not turn routine implementation choices or fixes within an approved task into repeated approval gates. In a project whose rules adopt `milestone-delivery`, an approved milestone plan is the approval for the implementation steps inside it, under that skill's conditions.

## 8. After Approval

Mark version as `APPROVED` and begin implementation.

Plans are written at a point in time and may omit applicable rules. When executing each step, cross-check against all relevant skill rules (typescript, code-style, etc.). If a rule covers broader scope than the plan specifies, follow the rule. Rules always override plan text.

## 9. Mid-Implementation Changes

**Material design changes** outside the approved behavior or scope: write a new version, mark the old one SUPERSEDED, and obtain the missing decision. Implementation refinements within approved scope do not require renewed approval. In a `milestone-delivery` project, a child-plan revision that stays inside the approved milestone scope and approved designs is recorded as a new version without a new approval; changing an approved design still needs approval.
**Minor fixes** (params, typos, user-directed): inline edit in current version, no separate approval needed.
**Plan-implementation gap**: If implementation surfaces 3+ unanticipated changes (cast additions, signature mismatches, narrowing requirements, type errors not predicted by plan) beyond plan scope, halt and re-evaluate the plan's feasibility assumptions before proceeding. Don't accumulate workarounds — each unplanned cast is a signal that the plan-time feasibility check missed something. Update the plan or switch alternatives based on actual implementation findings. Halt at once when one finding already shows the design is wrong; the count is for changes whose cause is not yet clear. Work that does not depend on the halted design may continue within its authorization.

## 10. Commit Granularity

Plan steps ≠ commit count. Group steps so each commit is **independently buildable, self-sufficient (a reviewer understands why without reading the next commit), and revertable as one topic**. Write proposed commit groups in the plan alongside the step list — don't default to one-commit-per-step.

Belong-together signals: a definition with its first consumer ("introduce X" with X actually used); a producer change with the contract tightening it enables; small follow-up cleanup that depends on a prior structural change. If splitting them leaves one commit reading as "why is Y still stale?", fold into one. Decide by topic, not by step count or LOC.

If a partial commit was already made and the follow-up completes the picture, amend it before pushing rather than landing the orphan commit, once you have confirmed it is your own and unpushed. Never rewrite someone else's work or published history without explicit approval.

## 11. Verification Batching

Verification with high setup cost (live runtime, simulator startup, external service stubs, manual operator steps) should be **batched across plans that touch the same system or pattern**. Don't run the same expensive verification twice when one round can cover both.

Batch-together signals: shared external system, shared simulator/harness setup, shared operator-driven flow, same pattern being introduced across multiple plans (validation, retry, caching, etc.). Don't batch unrelated topics — a problem in one section blocks the whole batch.

When deferring verification, record in the plan **what other plans the verification will batch with** so the deferral isn't forgotten and individual plans don't get pushed without coverage.

## 12. After Each Commit

After committing a task from a plan, immediately update: (1) parent plan — mark the task completed with the commit hash, (2) the status record the session keeps for handover (memory or the project's notes) — reflect the new status, (3) if this was the **final** task of the plan (no remaining work in the latest version, no further versions expected, and no child plans still active), apply the section 2 completion marking and update every parent plan's cross-reference to the renamed file. Never defer these updates.
