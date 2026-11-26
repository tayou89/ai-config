---
name: pr-create
description: Create or update GitHub PRs whose body opens with what changed and whether the user must act, in plain words, keeps checks, risks, and undo, and ends with the developer record; honor authorization and repository conventions.
---

<!-- Adapted from skills/pr-create/SKILL.md at 9f0260071e16e04fa2e73d35be03268d15525935 -->
<!-- Left out or changed on purpose (audited 2026-11-24):
- The opening authorization line, and section 4 reuses an existing authorization outside guarded repositories instead of always showing the body first: Codex permissions (codex/MIGRATION.md, "Repeated plan/build/commit approvals").
- The opening and section 5 work through the GitHub connector, CLI, or browser and pass the body as a file, not a heredoc: Codex reaches GitHub through whichever of these is connected, and a file keeps the body's newlines out of shell quoting.
- Section 6 attaches the pull request to the Codex task where that tool exists, and applies the reviewers, labels, or milestone the user asked for instead of asking about them: a Codex adaptation from 2026-10-28 with no recorded reason beyond reusing authorization (codex/MIGRATION.md, "Repeated plan/build/commit approvals").
-->

# PR Creation Workflow

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

Use this skill when creating or updating GitHub pull requests through the available GitHub connector, CLI, or browser. The user reads every pull request without the code at hand, so the body states what changed for them and whether they must act before any developer detail, and still keeps every fact a reviewer needs.

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
- English subject only, though the notification email shows it as its subject: a squash merge of more than one commit saves the title as the history's subject (GitHub's default), and commit messages are English.

## 3. PR Body

In a repository with its own pull request template or team rules, follow those. Elsewhere, write the body for the user: they read each pull request without the code at hand, in a notification email or on the pull request page, and do not know developer terms (user decision, 2026-11-18); reviewers and later sessions read the same page for the record. Put what changes and whether the user must act first and background toward the end, keep every fact a reviewer needs in a developer record at the end, and write in the user's language and everyday words, as `collaboration-core` "Plain replies and labels" describes.

Use these parts in this order, with headings in the user's language (Korean shown):

```
<One or two sentences before any heading: what is now different for the
user or for sessions.>

**할 일:** <없습니다, with who merges it and when it takes effect; or the
action and its deadline>

### 달라지는 점
<Two to four points: what the user or a session sees before and after.>

### 바꾼 까닭
<The problem, its cause, and the user decision behind it.>

### 확인한 것
<What was checked and what it showed, and what was not checked.>

### 남은 위험
<What can still go wrong and what happens then, and work left for later.>

### 되돌리기
<How to undo it, and what undoing cannot reverse: data written, messages
sent, orders placed, a deploy already live.>

<details>
<summary>개발 기록 (원인, 바뀐 코드, 시험)</summary>

<The plan and the decision quote, the technical cause, each commit's hash and
subject, changed files and functions, exact commands and results, reviews and
models, cases not handled.>

</details>
```

- Before creating, read the opening and the 할 일 line alone: they must say what changed, what it means for the user, and whether to act.
- Before the developer record, use no paths, identifiers, commit hashes, or command options, except one the user will type; plain counts are fine. In the record, backtick every identifier, path, and command.
- Never leave out 확인한 것, 남은 위험, or 되돌리기, and write 없습니다 there only after checking.
- Keep the parts before the developer record within about 1,200 characters, moving detail into the record rather than dropping a check, risk, or undo fact.
- Write the body complete when creating it, and tell the user something learned later in a comment rather than only by editing the body.
- Gmail shows `<details>` unfolded (caniemail, tested 2019-07) while GitHub folds it, so the order, not the fold, puts what matters first.
- Each part adds what the earlier ones do not; use paragraphs for connected explanations and bullets for independent points, as `report-writing` "Prose" describes.

## 4. Show Body Before Creating

Prepare the complete title, body, base/head branches, and verification result before publication. In a repository whose recorded workflow is the guarded pull request, create and update the task's pull request without a separate body approval and merge it as the git-commit skill's Repository Workflow describes (user decision, 2026-11-13). Elsewhere, reuse explicit authorization to create or update this PR, ask only when the intended publication or a material decision is not authorized, and do not merge.

## 5. Run gh pr create

Prefer a structured connector argument. With GitHub CLI, save the exact Markdown to a temporary UTF-8 file and use `gh pr create --title <title> --body-file <file>`. Preserve actual newlines and avoid shell-interpolated multiline bodies.

Flags:
- `--draft` — when work is review-ready but not merge-ready
- `--base <branch>` — when target is not the default branch
- `--reviewer <user>` — only if user explicitly named reviewers
- Never use `--fill` — it auto-fills from commits and skips the required sections.

## 6. After Creation

- Return the PR URL, and attach the created or worked-on PR to the current Codex task where the environment offers an artifact attachment tool.
- Apply requested reviewers, labels, or milestones; do not contact reviewers without authorization.
- Never enable GitHub auto-merge, and merge only as section 4 allows

## Rules

- **Never create a PR without the opening, 할 일, 확인한 것, 남은 위험, and 되돌리기** — the user must see what changed, whether to act, and how safe it is without reading the record.
- **Never compose body purely from commit messages** — commits describe WHAT not WHY.
- **Do not include `Generated with Claude Code` or similar trailers** per global policy.
- **Do not reference bare issue numbers** — always include what the issue is about.
- **Do not push `--force` without explicit user approval**, especially after PR creation.
