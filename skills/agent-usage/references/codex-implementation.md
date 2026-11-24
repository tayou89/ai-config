# Codex Work Runs

Steps for a Codex run the user asked for or approved for work other than a review (`agent-usage` section 14): implementation in a worktree, and research or record collection in an empty folder.

## 1. Prepare

- The user asked for or approved this Codex run in words, as `agent-usage` section 14 says. Reserve its share before starting: `python3 ~/.claude/skills/agent-usage/codex-budget.py reserve --kind implementation --percent <estimate> --topic <topic> --worktree <path> --session ${CLAUDE_SESSION_ID}`, or `--kind research --folder <empty folder>` for research and record collection. Estimate from earlier closed entries of the same kind in `~/.claude/codex-budget/ledger.jsonl` (their `actual_percent`); with none, use 1 for a small exact edit, 2 for a record count, 3 for web research, and 5 for a feature-sized bundle. Put the id it prints on the brief's first line as `Budget reservation: <id>`, so the budget tool counts the run's use to it and not as review use (user decision, 2026-11-19); a non-review reservation also names its folder, for runs whose brief lacks the line. Start only after the reservation succeeds: when it is refused, the run would take share the reviews need, and when the quota cannot be read, nothing shows it fits, so tell the user the refusal or the read error with Codex's reset time where known, and do the work yourself unless they choose to wait. If `python3 ~/.claude/skills/outside-review/codex-limits.py` shows a five-hour window 90% used or more, wait for its reset rather than start.
- A project's own Codex runs, such as a builder that drafts design options, are reserved the same way, with the folder they run in.
- For code with many hidden edge cases (parsers, concurrency, security, hooks and guards, code that can lose data or money), Claude first writes the cases it must handle and the tests that prove them, names those tests in the brief, and reviews Codex's result at the strongest level; the independent reviews the rules require still run (user decision, 2026-11-18).
- Close every entry you open: an entry left `running` expires after 12 hours and one never started after 24, but until then it holds its share.
- For implementation, create the worktree and branch the way the repository's recorded Git workflow names them; in a repository without that workflow, use `git worktree add -b codex/<topic> ../<repository>-codex-<topic>`. Record the base commit in the brief. For research, create a new empty folder for the run, such as `<project notes folder>/codex-<topic>/` (`report-writing` "Notes folders").
- Write the brief from a template below to the project's notes folder as `codex-brief-<topic>.md`.

## 2. Run

Implementation:

```bash
codex exec -m <model> -c model_reasoning_effort="<effort>" -s workspace-write -C "<worktree>" -o "<brief folder>/codex-result-<topic>.md" -- "$(cat "<brief folder>/codex-brief-<topic>.md")" </dev/null
```

Research or record collection, outside any repository:

```bash
codex --search exec -m <model> -c model_reasoning_effort="<effort>" --skip-git-repo-check -s workspace-write -C "<run folder>" -o "<brief folder>/codex-result-<topic>.md" -- "$(cat "<brief folder>/codex-brief-<topic>.md")" </dev/null
```

Only the run command writes the result file: `-o` saves Codex's final message there when the run ends and replaces whatever was in it, so a report Codex wrote to that path is lost (seen in two runs on 2026-11-18 and eight on 2026-11-23; reproduced with codex-cli 0.163.0, whose sandbox refused a write beside the brief). So the brief never names that file or asks Codex to save its report to a file, and its final message is the report; the result file sits beside the brief, outside the worktree or run folder Codex writes in, never under a temporary folder its sandbox can write. Codex may save scripts and data under its own names in that folder and list them in the report.

`--search` turns on web search and goes before `exec`, which rejects it; leave it out for record collection that needs no web. `--skip-git-repo-check` lets Codex run in a folder that is not a Git repository (a run without it fails there; seen 2026-11-18). `--` ends the options, so a brief is never read as one. Codex can still read files outside the run folder, such as transcripts and logs.

Take `<model>` and `<effort>` from `agent-usage` section 13; change the names when `outside-review/run-review.py` changes its own. Run it in the background with the shell tool's own background option, one run per call, rather than `&` or `nohup` inside a foreground call, so the session is told when the run exits (2026-11-23); set the entry to `running` with `codex-budget.py update <id> --state running`, and keep working on other things. `-s workspace-write` limits file writes mostly to the worktree or run folder, but extra writable paths, temporary folders, command rules, and connected tools can reach further, so the review below checks what changed. Leave out `--ephemeral` so the run shows in the Codex session log, which the budget check reads.

## 3. Review and Commit

- Wait until the process has exited. Exit is not completion: read the result file and check that its first word is `done`, `partial`, or `blocked` and that it holds the report the brief asked for, not a short note pointing to another file; then set the entry to `review-waiting`. If it does not, take the full text from the run's session log under `~/.codex/sessions/`, and fix the brief before any rerun.
- For implementation, run `git status`, `git diff <base>`, and `git log <base>..` in the worktree and check that only the brief's files changed and Codex made no commit; anything else is a finding. The brief's "do not commit" is an instruction, not a block. Read the diff against the brief and the skills it names, run the brief's checks yourself, and fix what fails: Codex's sandbox can block some checks, such as tests that list processes, so its failures are not the repository's until you rerun them. Do not trust its report without the diff. A Claude review of Codex's code is the other-family review; a later Codex review of the same code is not independent.
- For research, open each cited source for the findings you use and check that it says what the report claims; drop or mark what does not hold, and count those in the result.
- Commit implementation from the worktree through `git-commit`: the session that made the worktree owns it, so Codex's writes there are its own and the owner commits them without being asked about their lines (another session cannot commit there). Finish through the repository's workflow, then close the entry with `--state done`. Close a research entry once its findings are checked.
- When the result is wrong or unfinished and fixing it would cost more than doing it, close the entry as `failed`, keep the worktree or folder for one look by the user, and do the work yourself. Do not rerun Codex on the same brief without finding why it failed.
- If Codex output ended up in a shared checkout, pass the reviewed files to `--include-unrecorded` so the commit dialog shows them once; this is a recovery step, never the normal path.

## Implementation Brief Template

```markdown
Budget reservation: <id>
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
<for code with many hidden edge cases: the tests Claude already wrote, by path; they must pass unchanged>

## Rules
Read these before writing: <skills under ~/.codex/skills/ and project instructions>.

## Done when
<commands and tests that must pass, with their expected result>

## Report
Your final message is the report; do not save it to a file. Start it with one word: `done`, `partial`, or `blocked`. Then list the files changed, the commands run and their results, and anything you could not do or decided yourself.
```

## Research Brief Template

```markdown
Budget reservation: <id>
# Codex research brief — <topic> (<date>)

You collect facts that Claude will check and use. Write only in `<run folder>`. Do not change files anywhere else, and do not recommend or decide: report what the sources say.

## Questions
<numbered questions, each answerable from sources>

## Sources
<where to look: sites, documents, local folders or logs, and any that are off limits>

## Done when
<every question answered or marked not found, with the counts or quotes asked for>

## Report
Your final message is the report; do not save it to a file, and list any scripts or data files you saved in the run folder. Start it with one word: `done`, `partial`, or `blocked`. For each question, give the answer, each source as a link or path with the place in it (section, line, timestamp), and a quote of at most two sentences. List what you could not find and why.
```
