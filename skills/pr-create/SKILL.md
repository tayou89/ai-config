---
name: pr-create
description: Create or update a GitHub pull request with gh pr create. Apply whenever a PR is opened or its body rewritten; the body needs Purpose, Background, Changes, and Verification sections.
---

# PR Creation Workflow

Use this skill when creating GitHub pull requests via `gh pr create`. PRs MUST include **Purpose** and **Background** sections so reviewers can grasp *why* before looking at the diff. A diff without context forces reviewers to reverse-engineer intent — state the problem and reasoning first, then the solution.

## 1. Pre-PR Review

Before drafting the PR, run in parallel:

```bash
git status                              # ensure no uncommitted changes
git log <base>..HEAD --oneline          # verify all intended commits are present
git diff <base>...HEAD --stat           # see overall scope
gh pr list --head <branch>              # confirm no existing PR for this branch
```

Where `<base>` is the target branch (usually `main`, `dev`). If a PR already exists for this branch, propose `gh pr edit` instead of creating a duplicate.

## 2. PR Title

- Format: `<Type>: <short subject>` matching commit style (`Feat:`, `Fix:`, `Refactor:`, `Chore:`, `Merge:`)
- ≤72 chars (50-60 recommended)
- English subject only.

## 3. PR Body — Required Sections

The body MUST include all four sections, in this order. Section headers can be either English (`## Purpose`) or the project's local language equivalent — choose what matches the repository convention. The semantic requirement is fixed; the header label is local.

```
## Purpose

<2-5 sentences explaining WHY this update is needed. State the user/system impact,
the safety/correctness gap being closed, or the business reason. Do NOT describe
the diff here.>

## Background

<Context that led to the work: sweep/audit findings, upstream spec or contract,
prior incident, alternatives considered. Reference relevant files, line numbers,
or external systems. Reviewers should understand the problem space without
opening the diff.>

## Changes

<Commit-by-commit breakdown. Each commit hash + subject line, then bullets listing
the substantive change. Group by module/component if applicable.>

## Verification

<How the change was tested: type-check, lint, unit/integration tests, runtime
smoke test logs, screenshots. Be specific — "tested locally" alone is insufficient.
State what passed and what was skipped or deferred.>
```

Optional final section (when applicable):

```
## Follow-ups

<Anything intentionally deferred to follow-up PRs: scope splits, post-merge
testing, future refactors. Helps reviewers see what's NOT in this PR.>
```

### Section content guidance

**Purpose — focus on WHY**
- Good: "The service's retry logic and its test double diverged, making integration tests unreliable."
- Bad: "Add retry handling to the upload, email, and payment clients." (that's WHAT, belongs in Changes)

**Background — focus on what reviewers need to know upstream**
- Sweep / audit findings that motivated the work (tables of findings welcome)
- Upstream spec or contract being implemented (cite files + line numbers)
- Prior incident or pain point being addressed
- Decision history — alternatives considered, why this approach

**Changes — group by atomic unit**
- One subsection per commit OR per logical module
- Each subsection: commit hash + subject + bullet list of substantive changes
- Skip changes already obvious from the commit subject

**Verification — be precise**
- Type-check / lint results with the command and outcome (e.g. `tsc --noEmit → 0 errors`)
- Test results (which tests, pass/fail count)
- Runtime/integration logs (which processes, duration, error grep result)
- Skipped or deferred verification — explicitly state and explain

### Readability

Reviewers skim. Optimize every section for scannability.

- Structure over prose: bullets, sub-headings, or tables for any section longer than 3 lines.
- Inline-code mandatory: backtick every identifier, path, command, env var. References use `path:Lnn`.
- No repetition across sections — each must add what previous sections cannot.
- Active voice, short verbs. Wrap ~100 chars.

### Language policy for body content

PR title and the skill rule text are English. Body **content** language follows user preference and project convention — the PR may be written in the team's working language. Code references, commit hashes, command output, and external system names stay in their original form (usually English).

## 4. Show Body Before Creating

Show the drafted title + body to the user before invoking `gh pr create`. Get explicit approval. PRs are externally visible and editing after creation is awkward — confirm content upfront.

## 5. Run gh pr create

Use HEREDOC to preserve markdown formatting:

```bash
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Purpose

...

## Background

...

## Changes

...

## Verification

...
EOF
)"
```

Flags:
- `--draft` — when work is review-ready but not merge-ready
- `--base <branch>` — when target is not the default branch
- `--reviewer <user>` — only if user explicitly named reviewers
- Never use `--fill` — it auto-fills from commits and skips the required sections.

## 6. After Creation

- Return the PR URL to the user
- Ask if reviewers / labels / milestone should be added
- Do NOT auto-merge or auto-enable auto-merge — user decision

## Rules

- **Never create PR without Purpose and Background sections** — content must explain WHY, not just WHAT.
- **Never compose body purely from commit messages** — commits describe WHAT not WHY.
- **Never use `--fill`** — bypasses the required sections.
- **Do not include `Generated with Claude Code` or similar trailers** per global policy.
- **Do not reference bare issue numbers** — always include what the issue is about.
- **Do not push `--force` without explicit user approval**, especially after PR creation.
- **Do not auto-merge** even if checks pass.
