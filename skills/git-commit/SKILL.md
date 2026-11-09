---
name: git-commit
description: Review changes, write commit message, and commit with user approval via the claude-commit.py wrapper.
---

# Commit Workflow

All commits MUST go through `~/.claude/scripts/claude-commit.py`. Direct `git commit` is denied by a PreToolUse hook with no bypass. The wrapper enforces the full checklist (subject prefix, line-length, body-lines, bullet-count, internal-terms, amend check, staged-stat review, stage-matches-argv) automatically, so no step can be skipped.

## 1. Branch Check (First Commit of Session Only)

Check current branch with `git branch --show-current`. If on `main`, `dev`, or other base branch, ask user if they want a new working branch (e.g. `fix/xxx`, `feat/xxx`). Skip if already on a working branch (`fix/`, `feat/`, `refactor/`). Skip for subsequent commits.

## 2. Review Changes (informational, before drafting message)

```bash
git status
git diff --stat
```

## 3. Code Review (before drafting message)

- **Logic errors/bugs**: race conditions, uninitialized vars, missing error handling
- **Commit scope**: no unrelated changes (test config, debug code, other features) mixed in
- **Security**: no hardcoded keys, tokens, passwords
- **Build artifact consistency**: if `.ts` changed with `.js`/`.js.map`, verify build output matches source
- **Build side effects**: check for unrelated file changes after `tsc` (LF/CRLF diffs etc). Restore unrelated changes with `git restore`. Exception: `tsbuildinfo` is always meaningful during TS migration.

Report: "Code review done — no issues" if clean. If issues found, report specifically and confirm with user. Flag files that should be excluded.

## 3a. Release Commit Bundling

Release-prep changes for a single version (changelog entry, doc sync, version manifest bump, dependency lock snapshot, release-only metadata) go in **one** commit, not split per file. Per-topic splitting is the default for regular work; **a release is one topic** — atomic revert restores prior version state, and the release boundary stays visible in `git log --oneline`.

- Subject pattern: `Chore: release vX.Y.Z`
- Bundle everything tied to *this* version: changelog, readme/docs sync, version manifest, lock/snapshot file, release-only metadata
- Pre-release work unrelated to *this* version (retroactive changelog backfill, large doc restructure) commits separately **before** the release commit

This applies regardless of language or build system (any version manifest, any lockfile format, any release tool).

## 4. Commit Message Format

```
<Type>: <subject>

- <detail 1>
- <detail 2>
```

**Type** (capitalized): `Feat`, `Fix`, `Refactor`, `Chore`, `Merge`

**Subject**: English, start with verb (add, fix, update, remove), no period, specific, **≤72 chars** (50 recommended).

**Body**: English `- ` bullets after blank line. First bullet carries purpose (*why* + *what*). **3-5 bullets, ≤8 non-blank body lines, ≤72 chars/line**. Focus on context not visible from diff — delete bullets that restate the diff. **No internal terms**: write subject and body for a reader who has not seen the project's own documents, saying why the change was made rather than summarizing the changed document, and leave out internal shorthand (Step N, Phase N, Pattern N, document section numbers such as `0-16`, option or draft letters, session or tool nicknames) and the work's date or author, which git already records; the wrapper catches only the Step/Phase/Pattern forms and an upper-case document name followed by a section number (`HANDOFF 0-16`).

The wrapper rejects messages violating any of: type prefix, subject ≤72, line length ≤72, body lines 1-8, bullets 3-5, internal-term ban.

## 5. Show User Before Invoking Wrapper

Every commit gets its own approval (user decision, 2026-11-09: kept per commit rather than approved with the plan). Ask in the same layout every time, in the user's language and plain words, so the scope reads at a glance:

1. Title line: what the commit does, in one phrase.
2. What and why, in one or two sentences.
3. Files table: each file as a link, lines added and removed, and what changed.
4. Left out: changed files not included and why (another session's work, another topic), or none.
5. The exact message in a code block, with the line-length self-check noted.
6. Checks: code-review result, secrets, other sessions' changes kept out, and type, lint, and test results when code changed.
7. After the commit: the push prompt and any notification or record update that follows.

Get explicit approval ("응 진행해줘" or equivalent). The wrapper then runs deterministically and commits.

## 6. Run the Wrapper

```bash
~/.claude/scripts/claude-commit.py "$(cat <<'EOF'
<Type>: <subject>

- <bullet 1>
- <bullet 2>
- <bullet 3>
EOF
)" <file1> <file2> ...
```

For amend:

```bash
~/.claude/scripts/claude-commit.py --amend "$(cat <<'EOF'
<updated message>
EOF
)" <file1> ...
```

For merge / cherry-pick / other commits where the index is already prepared (re-staging would fail on deleted/renamed paths), use `--no-stage`:

```bash
~/.claude/scripts/claude-commit.py --no-stage "$(cat <<'EOF'
Merge: <subject>

- <bullet 1>
- <bullet 2>
- <bullet 3>
EOF
)"
```

`--no-stage` skips `git add` and commits the existing index as-is. File args are optional with `--no-stage`; when given, the wrapper still checks that only those files are staged.

The wrapper:
1. Validates subject prefix + length, body line length, body line count, bullet count, internal-term ban
2. Refuses if HEAD is not the `--expect-head` commit, which a private index requires (FAIL exit 6; see 6a)
3. Prints `git log --oneline -3` for amend awareness
4. Stages exact files passed as argv (skipped with `--no-stage`)
5. Prints `git diff --cached --stat`
6. Verifies no extra files staged beyond argv (with `--no-stage`, only when files are given; FAIL exit 3)
7. Refuses a staged `.env` or credential file, judged by `scripts/secret_files.py` (FAIL exit 5)
8. Prints verification table
9. Invokes `git commit` (subprocess, hook does not re-fire) with `CLAUDE_SKILL_GIT_COMMIT=1` marker
10. Checks that `git commit` replaced the expected HEAD; if HEAD moved while it started, undoes the commit with `git update-ref HEAD <moved> <new>` (FAIL exit 6)

Exit codes: `0` success, `1` git error, `2` message validation, `3` staging mismatch, `4` usage error, `5` credential file staged, `6` HEAD moved since the index was built (nothing committed).

Right after a successful commit that completes a task from a plan, apply `code-plan` "After Each Commit" before anything else.

## 6a. Shared Checkout (several sessions in one folder)

Git's default index is shared by every session in the checkout: another session's commit can take what you staged, and if the index is left stale after your commit, its next commit silently reverts yours. So stage in a private index. It records file contents, not the commit it was read from, so one built before another session's commit holds that commit's files in their old state, and committing it reverts them. Record the commit you build from and pass it to the wrapper, which refuses when HEAD has moved since, like `git update-ref <ref> <new> <old>` and `git push --force-with-lease=<ref>:<expect>`:

```bash
git rev-parse HEAD
```

```bash
idx="$(mktemp)" && GIT_INDEX_FILE="$idx" git read-tree <base> &&
  GIT_INDEX_FILE="$idx" ~/.claude/scripts/claude-commit.py --expect-head <base> "<message>" <file1> <file2>
rm -f "$idx"; git reset -q -- <file1> <file2>
```

`<base>` is the full sha the first command printed; with `GIT_INDEX_FILE` set, the wrapper requires `--expect-head` and the file list. For a file that also holds another session's uncommitted edits, build its content from `git show <base>:<path>` plus only your change, put it into the private index with `git hash-object -w` and `git update-index --cacheinfo` (same `GIT_INDEX_FILE`), then run the wrapper with `--expect-head <base> --no-stage`, still listing your files.

On exit 6 nothing was committed: another commit landed after `<base>`, and the wrapper names it and the files it shares with yours. Take the new HEAD it names as `<base>`, rebuild the private index and any partial-file content from it, show the user the staged diff again if a shared file changed, and run the wrapper again.

Then, as its own command before `git push` and never chained with it, run `git show --stat HEAD` and confirm the commit has your subject and only your files; if your changes landed in someone else's commit, report it instead of rewriting pushed history.

## 7. Amend Check

Before drafting a new commit, check `git log --oneline -3`. If current changes are a continuation/supplement of the previous commit:
1. Propose `--amend` to user
2. On approval: invoke wrapper with `--amend`
3. If already pushed: warn about `--force-with-lease` requirement

Amend when: same file/same purpose, filling gaps from previous commit, same plan/feature.
Don't amend: different purpose, already pulled by others.

## 8. Push

Push once per task, after its last commit rather than after each commit, and run `git push` as a command of its own, not chained with the commit or other steps, so the permission prompt shows only the push (user decision, 2026-11-09). Where the shell guard hook is installed, start that push without asking in chat: the hook's permission prompt is the user's separate approval (user decision, 2026-11-08). If the user rejects it, stop and report. Without the hook, ask the user about push once the task's commits are done and push only on approval.

## Rules

- Never commit `.env`, auth keys, passwords
- Never stage/commit unrelated changes
- Use `git add -A` / `git add .` with caution (the wrapper stages exactly what argv specifies)
- **No `Co-Authored-By`** or auto-generated trailers
- Commit only portable entries to the shared `settings.json`: no machine-specific paths, one-off commands, or catch-all allows such as `Bash(*)`. A machine that needs its own entries installs a copy instead of using a checkout.
