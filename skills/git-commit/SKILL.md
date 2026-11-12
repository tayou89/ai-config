---
name: git-commit
description: Review changes, write the commit message, and commit and push without asking through the claude-commit.py wrapper, stopping only when a check fails.
---

# Commit Workflow

All commits MUST go through `~/.claude/scripts/claude-commit.py`. Direct `git commit` is denied by a PreToolUse hook with no bypass. The wrapper enforces the full checklist (subject prefix, line-length, body-lines, bullet-count, internal-terms, amend check, staged-stat review, stage-matches-argv) automatically, so no step can be skipped.

Routine commits and pushes need no user approval (user decision, 2026-11-09, replacing the per-commit approval and the 2026-11-08 push prompt): this skill's self-check, the wrapper, the shell guard, and a repository's pre-commit check stand where that approval was. Changes to rules, skills, hooks, and shared settings still need the user's review before they are applied (section 5).

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

## 5. Self-Check Before Invoking Wrapper

Check on your own, in this order, and fix what fails before running the wrapper:

1. **Scope:** list each file you changed by name, from your own edits, never from `git status` output; leave out other sessions' changes and other topics. The shell guard refuses a file list known only at run time. Inside a file Git cannot tell whose lines are whose, so in a shared checkout commit with `--own-lines` (6a), which stages only the lines your session's write record shows as yours; `python3 ~/.claude/scripts/session_writes.py report <file>...` lists the lines it would leave out.
2. **Secrets:** no `.env`, keys, or tokens. The wrapper refuses credential files and live-looking tokens on added lines.
3. **Checks:** type, lint, and test runs pass when code changed. The shared configuration repository also runs its pre-commit check.
4. **Message:** the rules in section 4, including those the wrapper cannot check (why rather than what, no shorthand a reader cannot decode).
5. **Large change:** when the staged change touches 200 or more lines outside documents and lock files, run a checklist review of the staged diff against this skill and `code-style` in a subagent at the checklist-review row of the model table (smaller model, high effort), fix what it finds, and pass `--reviewed`. The wrapper refuses a large change without it (exit 7). Pass `--reviewed` only after that review has run.

Stop and ask the user only when a check fails and cannot be fixed within the task, when your change cannot be separated from another session's edits in the same file (the wrapper's exit 8 marks those lines with `!`), or when the shell guard asks. Rules, skills, hooks, and shared settings are the exception: show the change and get the user's review before applying it, then commit, push, and install it without asking again (user decision, 2026-11-09).

After pushing, report each commit in one line of your closing message: short hash, subject, file count, and the other sessions' lines `--own-lines` left out (file and line count), if any.

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

For a large change, after the checklist review, add `--reviewed` before the message:

```bash
~/.claude/scripts/claude-commit.py --reviewed "<message>" <file1> ...
```

The wrapper counts the staged change only: on `--amend` that is the new part, so review the whole amended commit when a series of amends grows past the limit, and on a `--no-stage` merge it is everything the merge brings in.

For amend:

```bash
~/.claude/scripts/claude-commit.py --amend "$(cat <<'EOF'
<updated message>
EOF
)" <file1> ...
```

In a shared checkout, add `--amend` to the 6a command instead. The wrapper rewrites only a commit this session made through it (section 7).

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
2. Refuses if HEAD is not the `--expect-head` commit, which a private index requires (FAIL exit 6; see 6a), and refuses `--amend` unless HEAD is a commit this session made through the wrapper and on no remote-tracking branch (FAIL exit 9)
3. Prints `git log --oneline -3` for amend awareness
4. Stages exact files passed as argv (skipped with `--no-stage`)
5. Prints `git diff --cached --stat`
6. Verifies no extra files staged beyond argv (with `--no-stage`, only when files are given; FAIL exit 3)
7. Refuses a staged `.env` or credential file, or an added line holding a live-looking token, judged by `scripts/secret_files.py` (FAIL exit 5)
8. In a Claude session that has a write record, refuses staged lines the record does not show as the session's own, and own lines that change or split another session's uncommitted lines (FAIL exit 8); `--own-lines` stages each listed file as the base plus only the session's lines, and `--include-unrecorded <file>` commits that file whole after the shell guard has asked the user
9. Refuses a change of 200 or more lines outside documents and lock files without `--reviewed` (FAIL exit 7)
10. Prints verification table
11. Invokes `git commit` (subprocess, hook does not re-fire) with `CLAUDE_SKILL_GIT_COMMIT=1` marker; the repository's own pre-commit hook still runs
12. Reads the commit `git commit` made from the HEAD reflog entry that carries the wrapper's `GIT_REFLOG_ACTION` mark and the commit's subject, not from a later HEAD read, since another session may commit the moment `git commit` returns (commands a hook runs inherit the mark, hence the subject); checks that it replaced the expected HEAD, and if HEAD moved while it started, undoes it with `git update-ref HEAD <moved> <made>`, which moves HEAD only while HEAD is still that commit (FAIL exit 6). With no reflog entry carrying the mark, it checks and records nothing (FAIL exit 1)
13. Adds that commit to this session's commit list, which `--amend` checks

Exit codes: `0` success, `1` git error, `2` message validation, `3` staging mismatch, `4` usage error, `5` credential file or token staged, `6` HEAD moved since the index was built (nothing committed), `7` large change without `--reviewed` (nothing committed), `8` staged lines outside the session's write record, or own lines tangled with another session's uncommitted lines (nothing committed), `9` `--amend` of a commit this session did not make or one already pushed (nothing committed).

Right after a successful commit that completes a task from a plan, apply `code-plan` "After Each Commit" before anything else.

## 6a. Shared Checkout (several sessions in one folder)

Git's default index is shared by every session in the checkout: another session's commit can take what you staged, and if the index is left stale after your commit, its next commit silently reverts yours. So stage in a private index. It records file contents, not the commit it was read from, so one built before another session's commit holds that commit's files in their old state, and committing it reverts them. Record the commit you build from and pass it to the wrapper, which refuses when HEAD has moved since, like `git update-ref <ref> <new> <old>` and `git push --force-with-lease=<ref>:<expect>`:

```bash
git rev-parse HEAD
```

```bash
idx="$(mktemp)" && GIT_INDEX_FILE="$idx" git read-tree <base> &&
  GIT_INDEX_FILE="$idx" ~/.claude/scripts/claude-commit.py --expect-head <base> --own-lines "<message>" <file1> <file2>
rm -f "$idx"; git reset -q -- <file1> <file2>
```

`<base>` is the full sha the first command printed; with `GIT_INDEX_FILE` set, the wrapper requires `--expect-head` and the file list. Git cannot tell which session wrote a line, so a hook records every file this session writes (`scripts/session-writes-hook.py`), and `--own-lines` stages each file as `<base>` plus only the lines that record shows as yours. Without it, the wrapper refuses (exit 8) a staged file that holds lines the record does not show as yours, and lists them. Command writes that overlap another session's call count as possibly theirs, also when that call's snapshot failed (it moved a repository away, say); writes after the call ended do not. The wrapper also refuses your lines that change or split another session's uncommitted lines; ask the user then, since they cannot be committed apart. Add `--include-unrecorded <file>` only for lines the user typed or a command wrote outside the record; the shell guard asks the user before such a call. Keep `--no-stage` for an index that a merge or cherry-pick prepared.

On exit 6 nothing was committed: another commit landed after `<base>`, and the wrapper names it and the files it shares with yours. Take the new HEAD it names as `<base>`, rebuild the private index and any partial-file content from it, show the user the staged diff again if a shared file changed, and run the wrapper again. When the wrapper instead says your commit was not undone because another commit already sits on it, do not push; tell the user.

Then, as its own command before `git push` and never chained with it, run `git show --stat <sha>` for the commit the wrapper printed under `=== Done ===` (HEAD may already be another session's later commit) and confirm it has your subject and only your files; if your changes landed in someone else's commit, report it instead of rewriting pushed history.

## 7. Amend Check

Before drafting a new commit, check `git log --oneline -3`. When the current changes continue your own previous commit (same file and purpose, filling its gaps, same plan or feature) and that commit is not pushed yet, amend it with `--amend` instead of adding a commit. Never amend a pushed commit or someone else's: rewriting pushed history needs a force push, which the shell guard asks the user about.

The wrapper enforces this, since sessions in one checkout commit as the same person and another session's commit can land seconds before yours: it lists each commit it makes for the session and refuses `--amend` (exit 9) unless HEAD is on that list and on no remote-tracking branch. `--expect-head` alone does not catch it, because a base read inside the amend command is that other commit. On exit 9 nothing was committed; commit the change as a new commit on top instead. A commit made outside a Claude session, or before the wrapper listed commits, cannot be amended through it either.

## 8. Push

Push once per task, after its last commit rather than after each commit, and run `git push` as a command of its own, not chained with the commit or other steps (user decision, 2026-11-09). A routine push, the current branch to its own remote, runs without asking (user decision, 2026-11-09, replacing the 2026-11-08 push prompt). The shell guard still asks before force pushes, deletions, mirror and tag pushes, pushes to another remote or ref, and every push in a repository where `git config shellGuard.askBeforePush true` is set; if the user rejects it, stop and report. Where the hook is not installed, push routine pushes the same way and ask before the others yourself.

## 9. Pull Requests and Deploying Pushes

Commit straight to the branch a repository works on, its default branch for a solo repository, without a pull request unless the project's rules say otherwise: with no reviewer and no deploy on push, a pull request adds a step nobody reads (user decision, 2026-11-09). When a push to that branch starts deploying, set `git config shellGuard.askBeforePush true` in the checkout and say so in the project's rules, or move that repository to pull requests with required checks.

## Rules

- Never commit `.env`, auth keys, passwords
- Never stage/commit unrelated changes
- Use `git add -A` / `git add .` with caution (the wrapper stages exactly what argv specifies)
- **No `Co-Authored-By`** or auto-generated trailers
- Commit only portable entries to the shared `settings.json`: no machine-specific paths, one-off commands, or catch-all allows such as `Bash(*)`. A machine that needs its own entries installs a copy instead of using a checkout.
