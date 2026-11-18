# Codex Work Runs

Steps for work that `agent-usage` section 14 hands to Codex: implementation in a worktree, and research or record collection in an empty folder.

## 1. Prepare

- The run is approved as section 1 says. Reserve its share before starting: `python3 ~/.claude/skills/agent-usage/codex-budget.py reserve --kind implementation --percent <estimate> --topic <topic> --worktree <path> --session ${CLAUDE_SESSION_ID}`, or `--kind research --folder <empty folder>` for research and record collection. Estimate from earlier closed entries of the same kind in `~/.claude/codex-budget/ledger.jsonl` (their `actual_percent`); with none, use 1 for a small exact edit, 2 for a record count, 3 for web research, and 5 for a feature-sized bundle. A non-review reservation needs its folder, so its use is not counted as review use. If `python3 ~/.claude/skills/outside-review/codex-limits.py` shows a five-hour window 90% used or more, wait for its reset rather than start.
- A project's own Codex runs, such as a builder that drafts design options, are reserved the same way, with the folder they run in.
- Close every entry you open: an entry left `running` expires after 12 hours and one never started after 24, but until then it holds its share.
- For implementation, create the worktree and branch the way the repository's recorded Git workflow names them; in a repository without that workflow, use `git worktree add -b codex/<topic> ../<repository>-codex-<topic>`. Record the base commit in the brief. For research, create a new empty folder for the run, such as `<notes folder>/codex-<topic>/`.
- Write the brief from a template below to the session's shared notes folder, or to `claude-plans/codex/` in the project root, as `codex-brief-<topic>.md`.

## 2. Run

Implementation:

```bash
codex exec -m <model> -c model_reasoning_effort="<effort>" -s workspace-write -C "<worktree>" -o "<brief folder>/codex-result-<topic>.md" "$(cat "<brief folder>/codex-brief-<topic>.md")" </dev/null
```

Research or record collection, outside any repository:

```bash
codex --search exec -m <model> -c model_reasoning_effort="<effort>" --skip-git-repo-check -s workspace-write -C "<run folder>" -o "<run folder>/codex-result-<topic>.md" "$(cat "<brief folder>/codex-brief-<topic>.md")" </dev/null
```

`--search` turns on web search and goes before `exec`, which rejects it; leave it out for record collection that needs no web. `--skip-git-repo-check` lets Codex run in a folder that is not a Git repository (a run without it fails there; seen 2026-11-18). Do not use `-s read-only` for research: `codex-limits.py` counts read-only runs as running reviews, so independent reviews would wait behind it. Codex can still read files outside the run folder, such as transcripts and logs.

Take `<model>` and `<effort>` from `agent-usage` section 13; change the names when `outside-review` changes its own. Run it in the background, set the entry to `running` with `codex-budget.py update <id> --state running`, and keep working on other things. `-s workspace-write` limits file writes mostly to the worktree or run folder, but extra writable paths, temporary folders, command rules, and connected tools can reach further, so the review below checks what changed. Leave out `--ephemeral` so the run shows in the Codex session log, which the budget check reads.

## 3. Review and Commit

- Wait until the process has exited. Exit is not completion: read the result file, then set the entry to `review-waiting`.
- For implementation, run `git status`, `git diff <base>`, and `git log <base>..` in the worktree and check that only the brief's files changed and Codex made no commit; anything else is a finding. The brief's "do not commit" is an instruction, not a block. Read the diff against the brief and the skills it names, run the brief's checks yourself, and fix what fails: Codex's sandbox can block some checks, such as tests that list processes, so its failures are not the repository's until you rerun them. Do not trust its report without the diff. A Claude review of Codex's code is the other-family review; a later Codex review of the same code is not independent.
- For research, open each cited source for the findings you use and check that it says what the report claims; drop or mark what does not hold, and count those in the result.
- Commit implementation from the worktree through `git-commit`, which commits a worktree no other session wrote in without asking about its lines, finish through the repository's workflow, then close the entry with `--state done`. Close a research entry once its findings are checked.
- When the result is wrong or unfinished and fixing it would cost more than doing it, close the entry as `failed`, keep the worktree or folder for one look by the user, and do the work yourself. Do not rerun Codex on the same brief without finding why it failed.
- If Codex output ended up in a shared checkout, pass the reviewed files to `--include-unrecorded` so the commit dialog shows them once; this is a recovery step, never the normal path.

## Implementation Brief Template

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

## Tests to add
<for code with many hidden edge cases: the cases Claude wrote, one test each>

## Rules
Read these before writing: <skills under ~/.codex/skills/ and project instructions>.

## Done when
<commands and tests that must pass, with their expected result>

## Report
Start with one word: `done`, `partial`, or `blocked`. Then list the files changed, the commands run and their results, and anything you could not do or decided yourself.
```

## Research Brief Template

```markdown
# Codex research brief — <topic> (<date>)

You collect facts that Claude will check and use. Write only in `<run folder>`. Do not change files anywhere else, and do not recommend or decide: report what the sources say.

## Questions
<numbered questions, each answerable from sources>

## Sources
<where to look: sites, documents, local folders or logs, and any that are off limits>

## Done when
<every question answered or marked not found, with the counts or quotes asked for>

## Report
Start with one word: `done`, `partial`, or `blocked`. For each question, give the answer, each source as a link or path with the place in it (section, line, timestamp), and a quote of at most two sentences. List what you could not find and why.
```
