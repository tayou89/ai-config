---
name: git-commit
description: Review and create Git commits under existing user authorization, preserving exact change scope and personal message conventions.
---

# Commit Workflow

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

Use normal Git commands or the connected GitHub interface. Claude's commit wrapper and PreToolUse hooks are not installed or required by this skill. The checks below are instructions, not an automatic command interceptor.

## Review the Change

1. Inspect the current branch, `git status`, recent commits, and the full intended diff. For a base branch, follow the user's requested branch policy or use an appropriate working branch for implementation work. Do not require an extra branch for an explicitly approved configuration update.
2. Check logic, error handling, concurrent state, secrets, test-only changes, and source/generated-output consistency. Run relevant checks with the installed toolchain. Resolve findings within scope before proceeding.
3. Stage only exact intended paths or hunks. Preserve unrelated staged and unstaged work. Inspect `git diff --cached` and its file list; do not unstage, stash, restore, or overwrite user changes merely to make the tree look clean.
4. Group commits into independently coherent, buildable, revertible topics. Source definitions and their first consumers generally belong together. A version's release manifest, lockfile, changelog, and release documentation form one release commit; unrelated cleanup remains separate.

## Commit Message

Use English with a capitalized type: `Feat`, `Fix`, `Refactor`, `Chore`, or `Merge`. Subject starts with a concrete verb, has no trailing period, and is at most 72 characters (about 50 preferred).

Separate the body by one blank line. Use 3–5 `- ` bullets, at most 8 nonblank body lines, and at most 72 characters per line. Explain purpose and context, especially in the first bullet; avoid internal plan labels such as Step N, Phase N, or Pattern N. Do not add generated attribution or Co-Authored-By trailers unless the user explicitly requests them or the repository requires them.

For release preparation, use `Chore: release vX.Y.Z` and bundle the files for that version.

## Authorization and Execution

Show the file scope, proposed message, and review result before an unapproved commit. Existing explicit instructions to commit/publish the specific change authorize that action; do not ask again. Authorization to commit alone does not authorize pushing.

Write the exact message to a temporary file and run `git commit --file <message-file>` after verifying the staged scope.

When other sessions work in the same checkout, the default index is shared: their commit can take what you staged, and a stale index after your commit lets their next commit silently revert yours. Stage in a private index, which records file contents but not the commit it was read from: one built before another session's commit would revert that commit. So build it from a recorded base and commit only while HEAD is still that base, comparing again right after the commit: `base="$(git rev-parse HEAD)" && idx="$(mktemp)" && GIT_INDEX_FILE="$idx" git read-tree "$base" && GIT_INDEX_FILE="$idx" git add <paths> && test "$(git rev-parse HEAD)" = "$base" && GIT_INDEX_FILE="$idx" git commit --file <message-file> && new="$(git rev-parse HEAD)" && { test "$(git rev-parse "$new^")" = "$base" || { git update-ref -m "undo a commit built on a moved HEAD" HEAD "$(git rev-parse "$new^")" "$new"; false; }; }; rm -f "$idx"; git reset -q -- <paths>`. If a `test` fails, another commit landed after the base (the last group also undoes a commit that `git commit` built on it): rebuild from the new HEAD and commit again. For a file that also holds another session's uncommitted edits, build its content from `git show "$base":<path>` plus only your change and put it into the private index with `git hash-object -w` and `git update-index --cacheinfo`. Then, as its own command before any push, confirm with `git show --stat HEAD` that the commit has your subject and only your files; if your changes landed in someone else's commit, report it instead of rewriting pushed history. For an already prepared merge/cherry-pick index, inspect that index rather than re-staging indiscriminately. Follow repository hooks; do not bypass them.

Before amending, inspect recent history and determine whether the previous commit is owned, unpushed, and part of the same topic. Obtain authorization for history rewriting when not already given. Do not amend others' commits or force-push shared history on your own. If a force push is explicitly authorized, inspect the current remote head and use a lease.

Push only when explicitly authorized, which may already be part of the user's task. Verify the remote result. Update the associated plan and status records with the actual commit hash and completed scope; do not mark unverified work complete.

Never commit credentials, real `.env` files, unrelated configuration, or temporary test patches. Do not automatically include Claude settings or permission changes.
