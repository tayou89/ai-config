---
name: resolve-conflict
description: Resolve Git merge, rebase, cherry-pick, or push-divergence conflicts while preserving both sides and the current operation.
---

<!-- Adapted from skills/resolve-conflict/SKILL.md at bb490d7be25407e8a7f99a4355465939e93c718a -->
<!-- Left out or changed on purpose (audited 2026-11-24):
- The opening authorization line: Codex permissions (codex/MIGRATION.md, "Repeated plan/build/commit approvals").
- The steps are written as paragraphs. Recorded: inspecting and preserving concurrent work and continuing the Git operation instead of resetting it (codex/MIGRATION.md, "Automatic stashing, rebase aborts, and cleanup"). Added in the 2026-10-28 copy from that principle, with no record of their own: naming the actual upstream branch, staging only resolved paths, and checking the remote head before a push.
- Force pushes and history rewrites wait for explicit authorization and go through git-commit, since Codex has no shell guard to ask for it.
-->

# Resolve Conflict Workflow

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

Inspect `git status`, the active operation, current/upstream branches, conflicted paths, both sides' commits, and the relevant base. Use the actual upstream, not an assumed main branch.

Explain the cause and intended resolution. When the resolution changes a planned design, record it as a new version of the plan. Resolve routine conflicts within the already authorized work; ask when conflicting product intent, destructive loss, or a new scope requires a user decision. Who wrote a line does not decide that.

Resolve source files first, then regenerate derived files (generated rule copies, lock files) with their generator instead of editing them by hand. On a task branch already pushed, merge the base in rather than rebasing (user decision, 2026-11-13).

Preserve the intended behavior from both sides. Do not accept all of one side, abort a rebase, reset, or reapply a whole branch by default. Resolve the current operation's files, check the combined diff and markers, run relevant verification, stage only resolved paths, and continue with the corresponding merge/rebase/cherry-pick command.

Use the git-commit skill for separately created commits and existing commit/push authorization. Inspect the remote head before pushing; never force-push or rewrite published history without explicit authorization. Record any remaining conflict or failed check accurately.
