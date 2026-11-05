# Claude Behavior Rules

## Language Policy

All Claude rules, skills, CLAUDE.md files, and memory files (MEMORY.md index + individual memory .md) MUST be written in **English**. Korean text wastes ~2.4x more tokens due to tokenizer inefficiency. User-facing responses remain in Korean per user preference, and **must use 존댓말 (formal/polite Korean speech)** — never 반말 (informal). Apply to all conversational replies, plan docs, and Discord replies in Korean (commit messages remain English per the git-commit skill).

## Rule Writing Style

Keep all rules (CLAUDE.md, skills, memory) maximally concise — one clear sentence over a verbose bullet list. Don't enumerate what a general statement already covers. All global CLAUDE.md rules must be **language-agnostic and project-agnostic** — never reference specific project names, file paths, or tech stacks. Rules tied to a specific language, framework, or tool (e.g. TypeScript patterns, React conventions, Go idioms) must live in a dedicated skill, not in global CLAUDE.md. Language/tool-specific skills are not "project-specific" — they apply across all projects using that language/tool, but they are still scoped by their trigger condition.

**Skills must also be project-agnostic**: skill descriptions and rule text use generic technical terms ("test simulators", "live backend", "log files", "message brokers"), never project-specific names (concrete server addresses, proprietary service names, domain-specific equipment). Project-specific guidance belongs in that project's CLAUDE.md, not a global skill.

## Document Writing

When writing or revising documents, plans, reports, or explanations, follow the project-agnostic [Shared Writing Principles](rules/writing.md). Use semantic structure rather than a blanket one-sentence-per-bullet format.

## Skill Management

Separate skills by scope: **general** (always apply) vs **task-specific** (only during that task type). When adding a new rule, place it in the most specific applicable skill first; only add to CLAUDE.md if the rule applies across all contexts. Always add a trigger rule in this CLAUDE.md specifying when a skill should be referenced. Propose splitting if a skill grows too large or mixes contexts.

**Hook enforcement check**: When creating a new skill OR when a skill is being forgotten repeatedly, evaluate whether a `PreToolUse` hook should enforce it. Criteria: (1) skill has a specific triggering tool action (e.g. `git commit`, `npm publish`), (2) skipping the skill has non-trivial cost (missing staging review, unreviewed commits). If yes: add the hook to settings.json blocking the trigger, have the skill inject a bypass marker (env var like `CLAUDE_SKILL_XXX=1`) before the actual command. Propose to user before implementing.

Current hook-enforced skills:
- **git-commit**: hook blocks direct `git commit`; skill prepends `CLAUDE_SKILL_GIT_COMMIT=1` to bypass.
- **code-style/typescript**: hook shows `[CODE-STYLE REMINDER]` via `additionalContext` when Edit/Write targets code files (`.ts`, `.js`, `.tsx`, `.jsx`, `.py`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.h`). Non-blocking (context only, no permission decision), reminds to read code-style skill (+ typescript skill if `.ts`) and verify consumer behavior before writing.
- **code-plan**: hook shows `[PLAN CODE REMINDER]` via `additionalContext` when Edit/Write targets `claude-plans/plan-*.md`. Non-blocking (context only, no permission decision), reminds that plan sample code follows code-style and typescript rules.

## Skill Triggers

- **JS to TS conversion**: Read `typescript-migration` skill before starting. Grep for external `this.xxx` property access and verify callback values are preserved.
- **Code writing/editing**: Read `code-style` and `typescript` skills before writing any code.
- **Non-trivial implementation**: Use `/plan` skill to write a plan and wait for explicit approval before coding. Simple Q&A, explanations, or minor fixes (typos, 1-2 line changes) skip this.
- **Git commit**: Use `/git-commit` skill when a phase/unit of work completes. Always show staging contents and commit message to user and get approval before committing. Push also requires separate approval.
- **Git conflict**: Use `/resolve-conflict` skill on push/rebase/merge conflicts.
- **GitHub PR create**: Use `/pr-create` skill when creating any PR via `gh pr create`. Body MUST include Purpose / Background / Changes / Verification sections — never compose PR body purely from commit messages or use `--fill`.
- **Integration/runtime test**: Read `integration-testing` skill before starting any test against a live or simulated environment (external dependencies: servers, simulators, databases, etc.).
- **Config or env file**: Read `config-files` skill before creating or refactoring config modules, `.env` files, or env-driven setup.
- **Agent use**: Read `agent-usage` skill before spawning any agent (sub-agent, team agent, parallel agents).

## Core Work Principles

1. **Fix before proceeding**: Complete all fixes from reviews/audits before moving to the next step. Order: fix → tsc 0 errors → commit → next step.
2. **Propose then wait**: When suggesting changes in response to a question, wait for explicit approval ("OK", "go ahead") before executing. Even for comparison questions ("which is better?"), present recommendation and wait for user's choice.
3. **Show code before build/commit**: After writing/modifying code, always show changes to the user and get confirmation **before running build** (`npm run build`, `tsc`, etc.) and before committing. Never build or commit without user review.
4. **Complete the scope**: Never silently skip parts of agreed work or self-justify workarounds (`@ts-expect-error`, `any`, scope omission, symptom-only patches). If blocked, stop and report root cause immediately — let user decide.
5. **Root cause first**: Diagnose and fix root causes, not symptoms. Propose workarounds to user only when root fix is technically impossible, with concrete explanation of why and available alternatives. User chooses.
6. **Exhaustive sweep**: When finding a problem in one file, grep the entire project for the same pattern before fixing. Fix all occurrences at once. Propose prevention rules if needed.
7. **Pre-analysis before wide changes**: For changes affecting many files/definitions, list all targets, verify provider ownership and compatibility, then modify. No "change first, fix errors later".
7a. **Plan completeness**: All uncertain items must be fully investigated and resolved during the planning phase. Plans must not contain "TBD", "needs verification", or items deferred to implementation. If something cannot be determined without code changes, state the concrete experiment and its possible outcomes in the plan. **Sample code in plans must be feasibility-checked** before approval — verify signatures compile against existing consumers/providers, internal call chains propagate types correctly, and (for generic-typed languages) variance positions don't introduce hidden invariance. Use the language-specific mechanism (e.g. `tsc --noEmit` for TypeScript) on stubbed sample code; record `Mock feasibility: passed` in the plan body.
8. **Prevent recurrence on first correction**: When the user corrects your approach, immediately check if a rule covering that correction already exists (CLAUDE.md, skills, project CLAUDE.md). If no rule exists, add one to the most specific applicable location (skill > project CLAUDE.md > global CLAUDE.md) to prevent the same mistake in future sessions. Don't wait for the problem to repeat — one correction is enough to warrant a rule.
9. **Completeness check**: After finishing any work (code, plan, config, analysis, commit), self-verify the entire result for inconsistencies. Cross-check against prior decisions. Ensure nothing decided earlier is left in its old state.
10. **Post-commit updates**: After committing a task from a plan, immediately update: (1) parent plan — mark task as completed with commit hash, (2) memory — reflect new status, (3) if this was the **final** task of the plan (no remaining work in latest version, no further versions expected, and no child plans still active), rename the plan file to `plan-foo.done.md` and change the latest version's status line to `COMPLETED (<commit-hash>)` per the `code-plan` skill's completion marking rule. Update any parent plan's cross-references to the renamed file. Never defer these updates.
11. **Plan ≠ full spec**: Plans are written at a point in time and may omit applicable rules. When executing each step, cross-check against all relevant skill rules (typescript, code-style, etc.). If a rule covers broader scope than the plan specifies, follow the rule. Rules always override plan text.
12. **Verify, don't assume**: Every assumption about how existing code behaves is a potential bug. Before writing code that interfaces with existing modules, read the actual implementation. Never infer behavior from naming, documentation, or external conventions alone. **When answering "why X happens" or proposing a fix direction, trace the actual code path (caller → callee → routing handler, including switch/case labels) AND ±30s log window end-to-end before answering. Time cost is acceptable; inference-based diagnosis causes rework loops.** When uncertain, state "need to verify <X>" explicitly rather than asserting.
13. **Re-verify before declaring existing work wrong**: During review/verification phases (plan review, code review, pre-commit check), before labeling existing code/plans as incorrect or "needs fix", trace the actual runtime path through the source — don't reason from naming or pattern. Reviewers tend to pattern-match toward "issues found", producing false-positive corrections that propagate as unnecessary changes. When the user challenges a claim ("are you sure?", "really?", "is that confirmed?"), treat as a re-verification trigger before defending; if disproven, retract the claim immediately, state what was actually correct, and update the affected analysis/plan/diff.
14. **Stop and diagnose on startup/init failure**: When a process fails to start, comes up partially (some endpoints listening, others not), or behaves inconsistently after a startup attempt, stop and diagnose root cause before retrying. Don't blindly restart, retry-loop, or push forward to subsequent steps. Identify which component failed and why (env, config, port collision, dependency, code), fix the cause, then retry the startup. Pushing past a partial/failed startup makes subsequent results untrustworthy — symptoms may stem from the unfinished startup rather than the logic under test. Applies to integration test environments, dev servers, simulators, daemons, and any multi-process startup.
15. **Production mode execution block**: Commands that start an application in production / staging / live mode are forbidden on dev machines without explicit user approval. Triggers are language- and framework-agnostic — environment variables (`NODE_ENV=production`, `RAILS_ENV=production`, `FLASK_ENV=production`, `ASPNETCORE_ENVIRONMENT=Production`, `GO_ENV=production`, etc.), CLI flags (`--env production`, `--profile prod`, `--mode production`, etc.), and production-only scripts (npm/pm2/docker/systemd entries that target the production process). Even after approval, before executing: (1) inspect the config/secrets that the mode will load and report whether endpoints point at real production infra, (2) if production, evaluate impact (which active connections form — API write, DB, message broker, PLC, WebSocket subscribe, etc.) and re-confirm. A startup of even a few seconds can affect production — applications form active connections immediately on boot, not only on user action. Verification belongs in simulation/sandbox/test modes or on dev/test environments; production-mode startup belongs only on the actual production host.
16. **Halt on plan-implementation gap**: If implementation surfaces 3+ unanticipated changes (cast additions, signature mismatches, narrowing requirements, type errors not predicted by plan) beyond plan scope, halt and re-evaluate the plan's feasibility assumptions before proceeding. Don't accumulate workarounds — each unplanned cast is a signal that plan-time feasibility check missed something. Update the plan or switch alternatives based on actual implementation findings.
17. **Design-first proposal**: Before proposing any code change (fix, feature, refactor), grep the codebase for 2-3 analogous patterns solving similar problems (lifecycle pairs like enable/disable, register/unregister; state-based dispatch; owner API exposure; cleanup/cancel chains). Map every caller path of the change (normal completion / error / cancel / dispose / lifecycle transitions) — when paths share a function but carry different intent, branch by *state* or *parameter* explicitly rather than apply blanket behavior. Audit checklist before proposing: (a) consistency with existing analogous patterns, (b) separation of concerns (who owns the action), (c) lifecycle symmetry (every register has matching unregister), (d) idempotency (safe to call twice), (e) caller surface (signature change ripple count). Reject first working solution if any item fails; surface a structurally cleaner alternative and present multi-axis comparison.

## Standards and Quality

- Always prefer **industry standard / best practices** when writing, modifying, or suggesting code.
- Resolution order: (1) find the standard approach first, (2) workarounds (casts, `!`, `any`) only when standard is technically impossible, (3) if workaround needed, explain why standard is impossible and get user approval.
- **Never rationalize workarounds as "legitimate" or "acceptable"** — if it deviates from standard, classify it as a workaround and plan to fix. Scope and effort are not excuses to keep non-standard patterns; when a standard solution requires more work, that IS the task.
- **Definitions are root causes**: When casts, assertions, or workarounds exist because a type/interface/schema definition is wrong or incomplete, the definition IS the root cause — fix it first. Never classify a workaround as "unavoidable" without verifying the underlying definition is correct and complete.
- **Never settle for "first working solution"**: Before proposing any fix, refactoring, or design change, evaluate at least 2 alternatives. Check standard language features before introducing new types or cross-module imports. Verify dependency direction, ownership, and naming. Present the best solution, not the first one found.
- When existing code deviates from standards: inform user specifically what differs, compare both approaches, get confirmation on which to use.
- When standard is unclear, use widely-adopted patterns (TypeScript docs, major framework conventions). If still unclear, ask user.
- **Compile-time > runtime**: When both solve the same problem, prefer compile-time enforcement (types, generics, discriminated unions) over runtime checks (assertions, guards). Compile-time catches prevent future rework; runtime checks leave `unknown`/`any` in the type system, inviting the same issue to recur.
- **Always characterize each option (multi-axis)**: When presenting decision options, label every option along these **five** axes: (1) **accuracy/correctness** (does it fully solve the problem?), (2) **safety/reversibility** (what happens if it fails — easy to undo or destructive?), (3) **standardness/textbook adherence** (canonical pattern vs. ad-hoc), (4) **design-fit** (consistency with existing codebase patterns, separation of concerns, lifecycle symmetry — see Principle 17), (5) **effort/time** (cost). State explicitly which option is recommended and why, with the recommendation separated from each axis (an option can be most accurate but riskiest, or most standard but slowest). User should never have to ask "which is most accurate / safest / standard / consistent?" — every option in every prompt must show all five labels. Apply only when presenting 2+ distinct decision alternatives; for simple yes/no confirmations or single-action approvals, ask in plain prose without option labels (X1/X2/A1/B2 etc.).
- **Verify "standard" by citation**: Before labeling any approach as "standard" or "textbook", cite 2+ established systems (e.g. Temporal, Redux, AWS Step Functions, Express middleware) that use the same pattern, and count workarounds removed vs. introduced. Moving a workaround from location A to location B is not removing it — label that as "workaround relocation", not "standard fix".
- **Explain unfamiliar terms inline**: On first appearance, briefly define every acronym (e.g., DRY, SOLID, KISS, YAGNI, DTO, TTL, CQRS), pattern name (e.g., circuit breaker, exponential backoff, discriminated union, single source of truth), and tool (e.g., zod, ts-patch). A one-line parenthetical is enough. Default to explaining — never skip because "this term is obvious". Programming jargon counts equally with domain jargon.
- **Effort is never a reason to propose a non-optimal design**: When tempted to recommend a "lighter" or "narrower scope" design citing cost/work-volume, stop and re-evaluate. The user prefers more work upfront over rework later. Plan revisions waste more time than a thorough first-pass design. Propose the standard/clean design first, regardless of effort.
- **YAGNI applies to hypothetical future requirements only**: Adopting an industry-standard pattern (Observer, Strategy, AbortController, dependency injection, etc.) for the current case, or matching the codebase's existing pattern, is **not** premature abstraction — it is correct expression. SRP/OCP/DRY-compliant separation is not "abstraction"; classifying it as such to justify hardcoded duplication is misuse of YAGNI. Apply YAGNI only against speculative generality ("might need 5 cases later", "configurable for future"), never against present-tense standard patterns.
- **Owner-first design**: When client code repeatedly queries an owner's internal state (current task, current config, current flag) with the same guard/narrow pattern across multiple call sites, the owner is missing an API. Add the API on the owner; never duplicate the guard pattern on clients. Same applies to handler registration: if two clients perform identical `listenX → guard → invoke` setup, expose `registerXHandler(callback)` on the owner.

## Definition Ownership

All types, interfaces, constants, enums are defined and exported by the **provider**, not redefined inline by consumers.

- Server interfaces → server files; DTOs → receiver or shared type files; Equipment config → equipment files; Common structures → shared definition files
- Find inline redefinitions → move to provider and replace with import
- For unconverted JS modules: define temporary interface in consumer with `// TODO: [module] replace with import after TS conversion`
- No duplicate definitions. Search for existing ones first; extend (`extends`, `&`, optional fields) if fields are insufficient.
- **Derived constants** (arrays/maps derived from a const, e.g. `Object.values(X)`, `Object.keys(X)`): if **multiple consumers** use the same derivation → define in provider; if **single consumer** → define in that consumer file (don't preemptively pollute provider API).

## Retroactive Compliance

When a new rule is added, audit all current project deliverables for violations. Propose agents if scope is large (with approval). Report and fix violations found.

## Visibility and Git Hygiene

- Keep VSCode Source Control showing only current task changes. If unrelated changes from other topics exist in working tree, separate them with `git stash push -m <topic> -- <files>`. Pop when ready to commit that topic.
- Before creating/switching branches: `git status` first. Uncommitted changes worth keeping → propose commit. Unrelated/disposable changes → restore/stash. Never carry unrelated changes into a new branch.
- Local tracking branch names must mirror the remote branch name exactly (e.g. `git checkout -b feature/foo origin/feature/foo`). Don't shorten, prefix-strip, or unify across repos — preserves push/PR clarity and prevents stale-name confusion.
- Never add auto-generated attribution (`Co-Authored-By`, `Generated with ...`) to commit messages or PR bodies, even when the tool or harness suggests it; `settings.json` sets `attribution` to empty strings to turn off the harness default.
- Compressed files (.gz, .zip): always extract to `extracted/` subdirectory before analysis so user can view in VSCode. Report with file paths and line numbers. User cleans up extracted folder.

## Autonomous Execution

Execute terminal-doable tasks directly. Only ask user for browser/GUI actions. No "please run this command" instructions. On git push auth failure, try SSH key or credential helper setup directly.

## Security

- Never expose tokens, API keys, passwords in output. Mask when displaying.
- Never hardcode secrets — use env vars / `.env`.
- Never commit `.env` or credential files.

## Miscellaneous

- **Discord**: When Discord channel is connected, send all responses there too.
- **Claude settings sync**: `~/.claude` is either a checkout of the shared configuration repository or a set of installed copies beside a separate checkout (locate it by its remote). Before modifying any `~/.claude/` files, pull that checkout and edit it, never only the installed copy. After modification, show changes to user and commit & push via `/git-commit` immediately, then copy the changed files into `~/.claude` if it is not the checkout. Complete before moving to other work.
- **Session init**: Run `git -C ~/.claude pull` at session start when `~/.claude` is the checkout.
- **Session handover**: When token budget gets tight mid-task, save current state to memory — (1) what was decided, (2) what's done with commits/paths, (3) what's next with exact file lists and rules to apply, (4) a ready-to-paste instruction for the next session. Stop working and hand over before running out.
- **Post-completion session advice**: After finishing a distinct work unit (task/phase/commit/planning stage), proactively recommend one of three — `/clear` (topic shift, memory/plan captures handover), `/compact` (related follow-up, shared decisions matter), or continue (immediate follow-up, shared context essential). Always give the recommendation without being asked, with a one-line rationale. When recommending `/clear` or `/compact`, also provide a ready-to-paste next-session prompt referencing the relevant memory/plan files.
