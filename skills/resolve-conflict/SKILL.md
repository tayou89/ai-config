---
name: resolve-conflict
description: Resolve git merge, rebase, cherry-pick, or push conflicts within the authorized task, keeping both sides' intent, and ask only when the resolution needs a new decision.
---

# Resolve Conflict Workflow

Conflicts are routine when tasks work in their own worktrees and merge the base into their branches, so resolve them within the authorized task (user decision, 2026-11-13).

1. Inspect the current operation, the actual target branch, the common base, both sides' changes, and what each meant to do (`git status`, `git log --merge`, the conflicted files from `git diff --name-only --diff-filter=U`).
2. Resolve mechanical conflicts within the authorized task without asking, keeping both sides' intent. Ask only when the resolution needs a new product decision, discards work, or widens the approved scope; who wrote a line does not decide that.
3. Resolve source files first, then regenerate derived files (generated rule copies, lock files) with their generator instead of editing them by hand. Inspect the combined diff and rerun the checks the resolution affects.
4. Continue the merge, rebase, or cherry-pick. Do not abort, reset, take a whole side, or reapply a whole branch by default. On a task branch already pushed, merge the base in rather than rebasing.
5. When the resolution changes a planned design, record it as a new version of the plan.

## Rules

- Never force-push without the user's confirmation.
- Verify both sides' changes are preserved after resolution.
