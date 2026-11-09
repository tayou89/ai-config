---
name: domain-pass
description: Describe the product by what it does, then run one subagent per relevant field in sequence, each producing that field's real artifact and fixing what it finds, up to a stop point.
argument-hint: "[what the product does] [stop after N fields]"
disable-model-invocation: true
---

# Domain Pass

Improve the product by having the fields that do its job best each do their actual work on it, one after another. Invoking this skill approves the subagents it describes (`agent-usage` section 1); anything beyond them still needs approval.

## 1. Describe the Product as a Verb

Write one sentence on what the product does for whom, not what it is ("lets a small team see which customer requests block revenue", not "a ticket tracker"). Take it from `$ARGUMENTS` or the project instructions. If neither has it, draft one and get the user's confirmation before spawning anything, because every field is chosen from this sentence.

## 2. Choose Fields and the Stop Point

List as many fields as genuinely do this job well, ordered so that each one's results feed the next. For each, name the real artifact a practitioner in that field produces (e.g. a measurement table, a taxonomy, an accessibility audit, a test suite, a spec section), the files or screens it works on, and the checks that show its result is right. The stop point is the number of fields the user gave, otherwise three. Show the list with a rough cost note (each subagent re-reads the project), then start.

## 3. Run the Fields One at a Time

For each field, spawn one subagent whose brief contains the verb sentence, the field and its artifact, the files and screens, the artifacts earlier fields produced, and these rules:

- do the work a practitioner does; do not role-play or write in a persona's voice;
- write the artifact to a file, change only the listed files, and run the project's checks;
- return what it looked at, what it produced and changed (paths), and the check output verbatim.

Only one subagent edits at a time. Before the next field, verify the result yourself: read the diff, rerun the checks, open the artifact. Fix a failed result before moving on; if it cannot be fixed, stop before any field that depends on it and report completed, failed, and not-run fields separately.

## 4. Stop and Report

At the stop point, stop and give a table per completed field: looked at, artifact, changes, check results, open issues. List the remaining fields so the user can continue later. Commit only through `git-commit`, which needs no approval for routine work (user decision, 2026-11-09).
