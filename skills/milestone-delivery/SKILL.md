---
name: milestone-delivery
description: Milestone workflow for a project whose rules explicitly adopt milestone delivery: a planning report at each planning-scope approval, approved designs and milestone plan with a versioned feature list, automatic checks between demos, an always-ask list, and a milestone-end report. Do not apply it in other projects.
---

# Milestone Delivery

Apply only after a project's rules explicitly adopt milestone delivery (user decision, 2026-11-11); other projects keep the plain `code-plan` flow. The adoption record in the project rules holds where it applies and from when, a link to this skill, the project's document locations and decision record, its learning goal if it keeps one, and the deviations the user approved, without copying this skill or the vault templates: this skill owns the workflow and approvals, and the vault owns template formats and what its report collection requires (user decision, 2026-11-12). Templates come from the knowledge vault (`knowledge-vault`).

## Approvals

- A milestone is one flow the user can use from start to end.
- First get the designs the milestone depends on approved, as `code-plan` 3a requires. Then get the milestone plan approved: its scope, the feature list version, and each feature's acceptance check.
- An approved milestone plan authorizes the implementation inside that version. Record substantial implementation units as child plans under `code-plan`, and revise a child plan without a new approval while it stays inside the approved scope and the approved designs.
- All existing approval requirements remain in force. Milestone approval does not waive the prerequisite-design check, required reviews, or the `code-plan` stop after three unanticipated changes. Ask when work leaves the approved scope or reaches an action that needs approval; do not ask again for a concrete action already authorized.
- The other user confirmations are the planning scope, screen drafts on a comparison board, each production deploy, each change to a running service, and the demo at the milestone end.

## Always ask, even mid-milestone

- Stop and ask before anything that adds cost; a change to what personal data is collected, how long it is kept, or who receives it; a hard-to-undo production database or data change; a change to sign-in or permission rules; adding, removing, or redefining a feature or its acceptance check; a technology outside the vault's stack catalog; or a dependency whose license restricts commercial use or requires publishing code.
- Decide everything else yourself (permissively licensed libraries, code structure inside approved designs, test approach, performance methods, development setup, bug fixes within approved behavior), record each decision with its reason in the project's decision record at once, and report them together at the milestone end.

## Feature list, checks, and progress

- Keep the feature list as JSON from the vault template: the approved version with the user's approval words and date, then one entry per user-visible behavior with its parent item in the project's task list, steps, an executable acceptance check, and `passes`.
- During implementation change only `passes`. Any other change needs the user's approval and a new version that records it; the project's automatic checks reject a changed definition without a new version.
- `passes` tracks progress only. The milestone is complete when every acceptance check passes on the current commit, and the report cites that run. When a passed feature fails again, set it back to false and log the failing check.
- Verify each feature end to end in a browser as a user would, not only with unit tests.
- Work on one feature at a time, commit after each, and append to the progress log what was done, what is next, and which checks fail.
- Start each session by reading the progress log, the git log, and the feature list.

## Planning report

Each time the user approves the project's planning scope (the first scope or a changed one), write a planning report from the vault template in the project's report folder: the approved scope with a commit-pinned link, the user's approval words and date, the decisions made without asking, the open decisions, and lessons for the vault in the same form as the milestone report's.

## Milestone report

At the milestone end, write the report from the vault template: how to try the demo, the acceptance check run, the feature table, the decisions made without asking, the always-ask items and how they were settled, "what to learn this time" when the project keeps a learning goal, lessons for the vault (each one general sentence with its original scope, its limits, and a commit-pinned link to its evidence), and the next milestone. Then ask for the demo check.

## Quality bar

Apply the vault's quality criteria for the project's kind, including the reviews they require before the first public launch and before adding payments. Before a milestone's first production deploy, show on the test deploy that the operations plan works as written: logs and errors arrive where it shows them, a test alert reaches its receiver, and its rollback or restore steps have run once.
