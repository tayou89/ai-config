---
name: outside-review
description: Have another model family review the current work read-only through the Codex CLI, then reproduce each finding before fixing it.
argument-hint: "[what to review]"
disable-model-invocation: true
---

# Outside Review

Get an independent review of the work named in `$ARGUMENTS` (default: the change or document this conversation produced) from another model family, following `decisions-and-collaboration` "Independent review". Its purpose is to find problems and improvements that Claude's own perspective tends to miss, not to repeat Claude's check from the same viewpoint. The reviewer runs once and read-only; there is no automatic fix-and-review loop.

## 1. Before Running

- Run the automatic checks the rules make checkable first (types, lint, tests, layout checks), so the reviewer spends nothing on what a script can check.
- Choose the reviewer's output folder: the session's shared notes folder if it has one, otherwise `claude-plans/reviews/` in the project root.
- List what the reviewer reads by itself: the artifacts, the applicable rules (`~/.claude/CLAUDE.md`, the relevant skills, project instructions; for visual design work, `~/.codex/skills/frontend-design/SKILL.md` too, the copy of the skill Claude designs with), the latest user comments, captures at every checked width, and this conversation's transcript, `~/.claude/projects/<working-directory-slug>/${CLAUDE_SESSION_ID}.jsonl`. Confirm that file exists; never substitute the newest file in that folder, which may belong to another session. Without it, say the review lacks the conversation record. Write it as text with `python3 ~/.claude/skills/outside-review/transcript-text.py "<transcript>" "<output folder>/transcript-<topic>.md"` and list the text first: it keeps every message and tool call and gives the original line for each tool result, image, and reminder, so the reviewer reads the whole conversation instead of 10,000-token slices of the raw file and opens the original only for those lines.
- Read the remaining Codex quota with `python3 ~/.claude/skills/outside-review/codex-limits.py`, which asks Codex's own server and spends nothing. If either window is 90% used or more, or the read fails, say so with the reset time and use the same-family fallback in section 2, since a review cut off by the limit wastes what it already spent. If its `running_reviews` is 2 or more, wait with a Monitor until-loop on that count until one finishes, then read the quota again: reviews that start together, as after a quota reset or a plan change, spend the week at once, and the last ones risk being cut off.
- Tell the user in one or two lines what goes to OpenAI. Parse the transcript as JSON lines and look at every image content block and every image file path in it, and check what the listed captures show. Leave personal photos out unless the user approves; if the transcript contains one, leave the transcript itself out of the list and tell the reviewer the decisions from it in the brief instead.

## 2. Run the Reviewer

Create the output folder if missing, write the brief to `<output folder>/review-brief-<topic>.md`, then run in the background, quoting every path (notes folders often contain spaces):

```bash
codex exec -m gpt-6-astra -c model_reasoning_effort="xhigh" -s read-only -C "<project root>" -o "<output folder>/review-<topic>.md" "$(cat "<output folder>/review-brief-<topic>.md")" </dev/null
```

`-m` and `-c model_reasoning_effort` follow the reviews row of the work-type table in `decisions-and-collaboration`: Codex's strongest model at xhigh, set here so the Codex app's own default does not decide it (user decision, 2026-11-08). Do not drop to a cheaper model or lower effort to save quota until a comparison on real reviews shows it finds as much; change the model name when a stronger one ships. State the model, effort, and quota used in one line when the run starts. `-s read-only` lets it read any file but write none, `-o` saves its final answer, and closing stdin keeps piped input out of the prompt. Leave out `--ephemeral` so Codex keeps the session log and the run shows in its usage history. Add `--skip-git-repo-check` outside a git repository. If `codex` is missing, fails, or the quota check stops it, say why and run one fresh same-family subagent with the same brief instead, at the strongest model and xhigh; this invocation approves that one agent.

The brief states:

- the goal and the outcome the work must achieve, and the paths from section 1 to read directly rather than copies;
- a reading map for each long file (over about 30 KB): the sections or line ranges the work touches, to read first, with the whole file still open to the reviewer when it doubts the map, since material read early is re-sent at every later step and a long file read in pieces fills the context until Codex compacts it;
- who wrote each text (the user, this session, another session), since only the user's own drafts are protected from rewrites;
- to report both what is wrong, with file-and-line or capture evidence, and how it would write or design the piece as the author, with no length limit;
- not to modify anything.

## 3. Check Every Finding

Reproduce each finding on the artifact before acting: open the cited line, run the failing case or test, or render the screen. Classify it as reproduced, not reproduced, or a judgment difference. Fix only reproduced findings within the approved scope and report the rest. Show a proposal that differs materially from yours next to yours, and let the user choose.

Report a table of finding, evidence checked, verdict, and action, then state what the automatic checks covered and what was not checked. Run the reviewer again only if the user asks.
