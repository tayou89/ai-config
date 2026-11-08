---
name: agent-usage
description: Coordinate subagents only when the user explicitly authorizes delegation; define review scope and verify their evidence.
---

# Agent Coordination

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

Do not spawn subagents merely because this skill is loaded. Obtain explicit user authorization for delegation; prior authorization for the same scope remains valid. Propose count, independent roles, expected benefit, and material cost when parallel work would help. Do not invent token budgets or model capabilities.

Use Codex's available collaboration tools for subtasks, not new user-owned chats. Create or message another chat only when the user explicitly requests it. Follow actual tool availability and permission limits.

For independent review, follow the decisions-and-collaboration skill: one reviewer, only at its checkpoints, preferring another model family, covering the full change rather than a file range. Ordinary small work needs self-review, not mandatory agents.

Give each agent bounded ownership and shared contracts. Avoid concurrent edits to the same files. Reviewers read the actual applicable AGENTS.md and skills; they trace runtime behavior, type resolution, and test evidence rather than checking only for code presence.

Verify important agent claims against source and outputs before reporting them. Exhaustive audits require checking every occurrence and reporting exact counts; estimates or round numbers are not proof. Choose each subagent's model and reasoning effort from the work-type table in the decisions-and-collaboration skill, set with Codex's own options, rather than hard-coding a Claude-specific agent type. For unfamiliar tool/model options, consult current supported capabilities.

Complete and integrate all authorized subtasks before declaring the parent work complete. Do not turn an agent suggestion into permission for unrelated changes or external messages.
