# Codex Implementation Runs

Steps for a bundle that `agent-usage` section 14 hands to Codex.

## 1. Prepare

- Get the run approved as section 1 says, then reserve its share: `python3 ~/.claude/skills/agent-usage/codex-budget.py reserve --kind implementation --percent <estimate> --topic <topic> --worktree <path> --session ${CLAUDE_SESSION_ID}`. Estimate from earlier closed entries of the same kind in `~/.claude/codex-budget/ledger.jsonl` (their `actual_percent`); with none, use 1 for a small exact edit and 5 for a feature-sized bundle. An implementation reservation needs `--worktree`, so its use is not counted as review use. If `python3 ~/.claude/skills/outside-review/codex-limits.py` shows a five-hour window 90% used or more, wait for its reset rather than start.
- Close every entry you open: an entry left `running` expires after 12 hours and one never started after 24, but until then it holds its share.
- Create the bundle's worktree and branch the way the repository's recorded Git workflow names them; in a repository without that workflow, use `git worktree add -b codex/<topic> ../<repository>-codex-<topic>`. Record the base commit in the brief.
- Write the brief from the template below to the session's shared notes folder, or to `claude-plans/codex/` in the project root, as `codex-brief-<topic>.md`.

## 2. Run

```bash
codex exec -m <model> -c model_reasoning_effort="<effort>" -s workspace-write -C "<worktree>" -o "<brief folder>/codex-result-<topic>.md" "$(cat "<brief folder>/codex-brief-<topic>.md")" </dev/null
```

Take `<model>` and `<effort>` from `agent-usage` section 13: `gpt-6.1-sol` at `medium` for an exact edit already decided, `gpt-6-astra` at `high` for other code changes; change the names when `outside-review` changes its own. Run it in the background, set the entry to `running` with `codex-budget.py update <id> --state running`, and keep working on other things. `-s workspace-write` limits file writes mostly to the worktree, but extra writable paths, temporary folders, command rules, and connected tools can reach further, so the review below checks what changed. Leave out `--ephemeral` so the run shows in the Codex session log, which the budget check reads.

## 3. Review and Commit

- Wait until the process has exited. Exit is not completion: read the result file, then set the entry to `review-waiting`.
- Run `git status`, `git diff <base>`, and `git log <base>..` in the worktree and check that only the brief's files changed and Codex made no commit; anything else is a finding. The brief's "do not commit" is an instruction, not a block. Read the diff against the brief and the skills it names, run the brief's checks yourself, and fix what fails. Do not trust its report without the diff. A Claude review of Codex's code is the other-family review; a later Codex review of the same code is not independent.
- Commit from the worktree through `git-commit`, which commits a worktree no other session wrote in without asking about its lines, finish through the repository's workflow, then close the entry with `--state done`.
- When the result is wrong or unfinished and fixing it would cost more than doing it, close the entry as `failed`, keep the worktree for one look by the user, and do the work yourself. Do not rerun Codex on the same brief without finding why it failed.
- If Codex output ended up in a shared checkout, pass the reviewed files to `--include-unrecorded` so the commit dialog shows them once; this is a recovery step, never the normal path.

## Brief Template

```markdown
# Codex implementation brief — <topic> (<date>)

You implement one bundle that Claude planned and will review. Work only in `<worktree>` (base commit `<sha>`). Do not commit, push, or create branches, even where AGENTS.md says to finish through Git: Claude reviews and commits this work.

## Approved plan
<path to the plan and the step this bundle covers; the user approved it on <date>, so do not plan again or ask for approval>

## Goal
<what the bundle does and why, one paragraph>

## Decided
<decisions already made that you must not reopen: design, wording, interfaces>

## Files
<files to change or create. If the work needs any other file, stop before writing it and say why in your report.>

## Rules
Read these before writing: <skills under ~/.codex/skills/ and project instructions>.

## Done when
<commands and tests that must pass, with their expected result>

## Report
Start with one word: `done`, `partial`, or `blocked`. Then list the files changed, the commands run and their results, and anything you could not do or decided yourself.
```
