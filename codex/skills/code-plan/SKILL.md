---
name: code-plan
description: Prepare evidence-based plans for substantial features, structural changes, or complex fixes; skip trivial edits and already settled planning.
---

# Plan Workflow

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

## 1. Choose the Plan Location

Use the project's existing plan/document location; otherwise use `plans/` at the project root. Respect a user-designated output directory. Do not modify global Git ignore settings merely to store a plan. Follow the repository's tracking policy.

## 2. Determine Plan File

- Filename: `plan-{feature-name}.md` (English kebab-case)
- Existing file: add new version at top (keep previous versions)
- New file: create fresh
- **Parent-child structure**: Child plans live in a subfolder named after the parent's topic (parent filename without `plan-` prefix and `.md` suffix). When a plan first gains children, the parent itself moves INTO that folder (`plan-foo.md` → `foo/plan-foo.md`). Children sit alongside the parent (`foo/plan-bar.md`). Grandchildren recurse: `foo/bar/plan-baz.md`. Standalone plans (no children) stay flat at `plans/plan-xxx.md`.
- **Split rule**: If parent file exceeds ~30k tokens OR a task is conceptually separate, create a child plan following the structure above. Include `### Parent Plan` section with sibling path (`plan-foo.md`) or ancestor path (`../plan-foo.md`), version, task reference, prerequisites. Update parent's task entry with a cross-reference to the new file.
- **Completion marking**: When a plan's latest version's work is fully complete (all implementation steps committed, no follow-up scope remaining, no further versions expected), rename `plan-foo.md` to `plan-foo.done.md` and change the latest version's status line to `COMPLETED`. Parent plan references to this file must update to the new `.done.md` suffix. Plans with any pending/active work stay as `plan-foo.md`. Filter active plans with `find plans -name 'plan-*.md' ! -name '*.done.md'`.

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

Before planning an implementation step, list the designs it depends on (module structure, data model, request/response and error contracts, authentication flow, non-functional targets such as response time), each with its status (approved, draft, missing) and the steps it gates, and show the list to the user whenever it changes. Plan the step only after those designs are approved, and propose a missing design as its own task first. Design the data model for the whole release scope before writing the first table, then build it incrementally: tables reference each other, and schema changes become the costliest to undo once real data exists. Work that depends on no pending design (tooling, scaffolding) may proceed in parallel when stated as such.

## 3b. Visible Live Switch

When a plan gives an application a production, staging, or live mode, select that mode only on the command line that starts it: a mode or environment option (`--mode live`, `--env production`) or an environment variable set on the command itself (`APP_MODE=live <command>`), with the safe mode as the default. Never let a config-file or `.env` value alone turn it on, and do not use a separate confirmation flag as the switch. Command guards and approval reviews see only the command line, so the mode must be readable there before the run; tested 2026-11-09, a command guard that recognizes mode words such as `production`, `staging`, and `live` asked before `--mode live` and `<NAME>_MODE=live` but let `--confirm-live` and a config-file `mode = "live"` run without asking.

## 4. Pattern Alignment Check

For structural decisions (new file layout, where a new type lives, module split, naming), grep 2-3 analogous cases in the same codebase. Match the dominant local pattern, or justify the deviation in the plan. Skill rules don't override local conventions. If no precedent exists, flag it.

## 4a. Pre-Plan Exhaustive Analysis (mandatory)

Before drafting any plan, perform all of the following. Skipping any is the root cause of avoidable plan revisions.

1. **Duplication scan**: Search the codebase for identical guard/query/narrow patterns the plan would introduce. If the same multi-line pattern would appear in 2+ call sites, the owner is missing an API — the plan must add the API on the owner, not duplicate the pattern on clients.
2. **Owner identification**: For every piece of state, config, or flag the plan touches, name the owner class/module. If the plan has client code accessing it via gates (NO_OPERATION guards, type narrows, optional chains), prefer exposing a typed `getX()` / `registerXHandler(callback)` API on the owner. Client boilerplate against owner internals always signals a missing owner API.
3. **3+ alternatives required**: Never propose the first working design. Evaluate at least three: (a) the dominant local pattern, (b) an industry-standard pattern for this concern (Observer, Strategy, etc.), (c) any cross-cutting-concern separation form. Compare them on the shared decision axes, naming the lifetime assumptions, failure conditions, and evidence that tell them apart. Present the best, not the first found.
4. **Boilerplate detection**: Where `code-style` "Boilerplate Belongs on the Owner" calls for it, move the guard/query/narrow pattern into the owner.
5. **Cross-cutting policy fields vs metadata**: Behavior-controlling flags (cancel policy, retry policy, timeout policy) belong as top-level fields on the entity, at the same level as other policy fields, not crammed into `metadata`/generic data containers. `metadata` is for identifiers and payload, not for policy.

## 4b. Plan Revision Minimization

v2+ plan revisions are failure signals — the design thinking that led to v(N+1) should have happened at v1. Acceptable revisions: user requirement change, externally-discovered constraint. Unacceptable: design omissions that section 4a should have caught (missed duplication, missed owner, missed alternative).

For a revision, state what the prior version missed and why. Check existing rules before adding a narrowly scoped correction; keep project-specific facts in the project and reusable guidance in the relevant skill. Do not turn a one-off example into an unrelated universal restriction.

## Language

Plan content (headings, descriptions, steps) must be written in **Korean**.
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
2. **Stub consumers and providers** in a minimal scratch environment (or TypeScript playground for `.ts`).
3. **Run the language's type checker** — `tsc --noEmit` for TypeScript, `mypy` for Python with type hints, `cargo check` for Rust, etc. Verify it passes against the stubbed environment.
4. **Map variance positions** of every generic parameter in the plan (covariant / contravariant / invariant). Confirm the variance is what the plan assumes — sibling-generic assignability often fails silently when a callback puts `T` in argument position. See `typescript` skill "Generic Variance — Plan-Time Check" for details.
5. **Trace internal call chains**. If method A calls method B, and B's signature changes, A must still compile. Map A→B→C... chains explicitly.
6. **Record result in the plan body** — `Mock feasibility: passed` (with brief notes on what was stubbed) or `Mock feasibility: failed (reason)`.

Failure → revise the plan before requesting approval. Don't defer this to implementation — implementation surprises ("the plan compiled in isolation but the whole project doesn't") almost always trace back to skipping this step.

**Skip allowed only when**: the plan adds optional fields without changing existing signatures, generics, or call chains. Even then, briefly note `Mock feasibility: skipped (additive-only change)` in the plan body for traceability.

## 6a. Style Compliance Check (mandatory before approval)

Before requesting plan approval, audit every diff block line-by-line against `code-style` and language-specific skill rules. Verify common violations: early void return / guard throw, blank-line placement after const block, truthy vs nullish check default, consecutive try-catch in same scope, useless `await` on non-thenable, `.catch()` chaining, type assertions, named-export style, type/interface section blanks. Record result in plan body — `Style compliance: passed (rules audited: ...)` or `Style compliance: failed (reason → fixed)`. Failure → revise diff before approval; don't carry violations into implementation.

**Formatter check**: When the project uses or the plan adopts an auto-formatter or a linter with automatic fixes, run them in the project's order (fixes, then formatting) over sample code that exercises every wrapping and layout rule the plan keeps: a wrapped condition, a long signature and call, multi-item collections and imports, and blank lines between definitions. Sample code without those cases passes trivially and hides conflicts. Formatter output overrides hand style; where they differ, follow the formatter and record which one wins in the language skill, not only in the plan. Record `Formatter check: passed (<tool> <version>, cases exercised)` or the differences found and how they were resolved.

## 7. Request Approval

Show a concrete plan and obtain explicit approval when scope or design still requires a user decision. Approval already given for the same plan or concrete implementation remains valid; record it and continue. Do not turn routine implementation choices or fixes within an approved task into repeated approval gates.

## 8. After Approval

Mark version as `APPROVED` and begin implementation.

## 9. Mid-Implementation Changes

**Material design changes** outside the approved behavior or scope: write a new version, mark the old one SUPERSEDED, and obtain the missing decision. Implementation refinements within approved scope do not require renewed approval.
**Minor fixes** (params, typos, user-directed): inline edit in current version, no separate approval needed.

## 10. Commit Granularity

Plan steps ≠ commit count. Group steps so each commit is **independently buildable, self-sufficient (a reviewer understands why without reading the next commit), and revertable as one topic**. Write proposed commit groups in the plan alongside the step list — don't default to one-commit-per-step.

Belong-together signals: a definition with its first consumer ("introduce X" with X actually used); a producer change with the contract tightening it enables; small follow-up cleanup that depends on a prior structural change. If splitting them leaves one commit reading as "why is Y still stale?", fold into one. Decide by topic, not by step count or LOC.

If an unpushed partial commit and a follow-up are one topic, consider amending after confirming ownership and authorization. Never rewrite someone else's work or published history without explicit approval.

## 11. Verification Batching

Verification with high setup cost (live runtime, simulator startup, external service stubs, manual operator steps) should be **batched across plans that touch the same system or pattern**. Don't run the same expensive verification twice when one round can cover both.

Batch-together signals: shared external system, shared simulator/harness setup, shared operator-driven flow, same pattern being introduced across multiple plans (validation, retry, caching, etc.). Don't batch unrelated topics — a problem in one section blocks the whole batch.

When deferring verification, record in the plan **what other plans the verification will batch with** so the deferral isn't forgotten and individual plans don't get pushed without coverage.
