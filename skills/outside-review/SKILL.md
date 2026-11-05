---
name: outside-review
description: Have another model family review the current work read-only through the Codex CLI, then reproduce each finding before fixing it.
argument-hint: "[what to review]"
disable-model-invocation: true
---

# Outside Review

Get an independent review of the work named in `$ARGUMENTS` (default: the change or document this conversation produced) from another model family, following `decisions-and-collaboration` "Checks and independent review". The reviewer runs once and read-only; there is no automatic fix-and-review loop.

## 1. Before Running

- Run the automatic checks the rules make checkable first (types, lint, tests, layout checks), so the reviewer spends nothing on what a script can check.
- Choose the reviewer's output folder: the session's shared notes folder if it has one, otherwise `claude-plans/reviews/` in the project root.
- List what the reviewer reads by itself: the artifacts, the applicable rules (`~/.claude/CLAUDE.md`, the relevant skills, project instructions), the latest user comments, captures at every checked width, and this conversation's transcript, `~/.claude/projects/<working-directory-slug>/${CLAUDE_SESSION_ID}.jsonl`. Confirm that file exists; never substitute the newest file in that folder, which may belong to another session. Without it, say the review lacks the conversation record.
- Tell the user in one or two lines what goes to OpenAI. Parse the transcript as JSON lines and look at every image content block and every image file path in it, and check what the listed captures show. Leave personal photos out unless the user approves; if the transcript contains one, leave the transcript itself out of the list and tell the reviewer the decisions from it in the brief instead.

## 2. Run the Reviewer

Create the output folder if missing, write the brief to `<output folder>/review-brief-<topic>.md`, then run in the background, quoting every path (notes folders often contain spaces):

```bash
codex exec -s read-only --ephemeral -C "<project root>" -o "<output folder>/review-<topic>.md" "$(cat "<output folder>/review-brief-<topic>.md")" </dev/null
```

`-s read-only` lets it read any file but write none, `-o` saves its final answer, and closing stdin keeps piped input out of the prompt. Add `--skip-git-repo-check` outside a git repository. If `codex` is missing or fails, say why and run one fresh same-family subagent with the same brief instead; this invocation approves that one agent.

The brief states:

- the goal and the outcome the work must achieve, and the paths from section 1 to read directly rather than copies;
- who wrote each text (the user, this session, another session), since only the user's own drafts are protected from rewrites;
- to report both what is wrong, with file-and-line or capture evidence, and how it would write or design the piece as the author, with no length limit;
- not to modify anything.

## 3. Check Every Finding

Reproduce each finding on the artifact before acting: open the cited line, run the failing case or test, or render the screen. Classify it as reproduced, not reproduced, or a judgment difference. Fix only reproduced findings within the approved scope and report the rest. Show a proposal that differs materially from yours next to yours, and let the user choose.

Report a table of finding, evidence checked, verdict, and action, then state what the automatic checks covered and what was not checked. Run the reviewer again only if the user asks.
