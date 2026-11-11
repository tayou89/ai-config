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
- List what the reviewer reads by itself: the artifacts, the applicable rules (`~/.claude/CLAUDE.md`, the relevant skills, project instructions; for design work, the design skills the work used), naming for each skill its Codex copy in `~/.codex/skills/` where one exists instead of the Claude copy, since the two copies hold the same rules and a reviewer given both reads and compares them, the latest user comments, captures at every checked width, and this conversation's transcript, `~/.claude/projects/<working-directory-slug>/${CLAUDE_SESSION_ID}.jsonl`. Confirm that file exists; never substitute the newest file in that folder, which may belong to another session. Without it, say the review lacks the conversation record. Write it as text with `python3 ~/.claude/skills/outside-review/transcript-text.py "<transcript>" "<output folder>/transcript-<topic>.md"` and list the text first: it keeps every message and tool call and gives the original line for each tool result, image, and reminder, so the reviewer reads the whole conversation instead of 10,000-token slices of the raw file and opens the original only for those lines.
- Read the remaining Codex quota with `python3 ~/.claude/skills/outside-review/codex-limits.py`, which asks Codex's own server and spends nothing. If either window is 90% used or more, or the read fails, say so with the reset time and use the same-family fallback in section 2, since a review cut off by the limit wastes what it already spent. If its `running_reviews` is 2 or more, wait with a Monitor until-loop on that count until one finishes, then read the quota again: reviews that start together, as after a quota reset or a plan change, spend the week at once, and the last ones risk being cut off.
- Tell the user in one or two lines what goes to OpenAI. Parse the transcript as JSON lines and look at every image content block and every image file path in it, and check what the listed captures show. Leave personal photos out unless the user approves; if the transcript contains one, leave the transcript itself out of the list and tell the reviewer the decisions from it in the brief instead.

## 2. Run the Reviewer

Create the output folder if missing, write the brief to `<output folder>/review-brief-<topic>.md`, then run in the background, quoting every path (notes folders often contain spaces):

```bash
codex exec -m gpt-6-astra -c model_reasoning_effort="xhigh" -s read-only -C "<project root>" -o "<output folder>/review-<topic>.md" "$(cat "<output folder>/review-brief-<topic>.md")" </dev/null
```

`-m` and `-c model_reasoning_effort` follow the reviews row of the work-type table in `decisions-and-collaboration`: Codex's strongest model at xhigh, set here so the Codex app's own default does not decide it (user decision, 2026-11-08). Do not drop to a cheaper model or lower effort to save quota until a comparison on real reviews shows it finds as much; change the model name when a stronger one ships. `-s read-only` lets it read any file but write none, `-o` saves its final answer, and closing stdin keeps piped input out of the prompt. Leave out `--ephemeral` so Codex keeps the session log and the run shows in its usage history. Add `--skip-git-repo-check` outside a git repository. If `codex` is missing, fails, or the quota check stops it, say why and run one fresh same-family subagent with the same brief instead, at the strongest model and xhigh; this invocation approves that one agent.

The brief states:

- the goal and the outcome the work must achieve, and the paths from section 1 to read directly rather than copies;
- a reading map for each long file (over about 30 KB), skill and reference files and the transcript text included: the sections or line ranges the work touches, and for the transcript the line where this work starts, to read first, with the whole file still open to the reviewer when it doubts the map, since material read early is re-sent at every later step and a long file read in pieces fills the context until Codex compacts it;
- when the reviewer will drive `ego-browser` and `~/.codex/rules/` allows it, to pass each script as `ego-browser nodejs -e '<script>'` (multi-line inside the quotes is fine): that form matches the allow rule and runs directly, while a heredoc or `< file` is sandboxed, fails, and starts an automatic approval review run each time it is escalated;
- who wrote each text (the user, this session, another session), since only the user's own drafts are protected from rewrites;
- to report both what is wrong, with file-and-line or capture evidence, including for design or copy every spot that reads as AI-made or unnatural (`ux-design` "Design skills"), and how it would write or design the piece as the author, with no length limit;
- not to modify anything.

Right after the run starts, before any other work, since reviews often take 20 minutes or more and the user can skim this meanwhile, show the user, in their language and without a question or buttons, the package the reviewer received: what changed in one or two lines; each screen and file it covers as a clickable link (a preview URL with the widths and states to check, a design-tool node, a file with its line); one or two of the captures it gets, sent with the client's file-sending feature; what the brief asks it to look at, in plain words; the brief's path; and the model, effort, quota used, expected finish, and what goes to OpenAI. Take all of it from the brief and the captures already made rather than writing anything new, so the review costs the reviewer nothing extra, and save the same text as `<output folder>/review-packet-<topic>.md`. Make any comparison image you planned to show before starting the run, or send it the moment it exists. Keep the package local, never on a hosted page, since captures may show personal photos (user decision, 2026-11-09).

When the user comments while the run is going, add the time and their words to the package file under a heading for comments during the review, written in their language, without stopping the run or sending them to the reviewer. Act on a clear correction or decision at once as usual, and then check each returning finding against the changed version.

## 3. Check Every Finding

Reproduce each finding on the artifact before acting: open the cited line, run the failing case or test, or render the screen. Classify it as reproduced, not reproduced, or a judgment difference. Fix only reproduced findings within the approved scope and report the rest. Show a proposal that differs materially from yours next to yours, and let the user choose.

Report each finding to the user as one entry in their language: the finding in plain words; what it points at, as a capture for a screen (the reviewer's own or the one you made reproducing it, sent with the file-sending feature) or a file-and-line link for code and text; whether it reproduced; and your decision, accepted and fixed where, or not accepted and why. Put the user's comments from the run first in the same list, merging one with a finding that points at the same thing, and mark each entry's source (user, reviewer, or both) and each comment as reflected, not reflected with the reason, or waiting on the user; where a finding contradicts the user, show both and keep the user's direction unless the finding shows a factual error. Then put findings that need the user's choice and group small ones that share a cause. Append the same entries to the package file with a link to the reviewer's raw output, then state what the automatic checks covered and what was not checked. Run the reviewer again only if the user asks.
