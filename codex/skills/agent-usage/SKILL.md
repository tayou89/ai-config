---
name: agent-usage
description: Coordinate subagents only when the user explicitly authorizes delegation; define review scope and verify their evidence.
---

<!-- Adapted from skills/agent-usage/SKILL.md at 8ad68834bb18245a443e37a55b31cd50acc6fd5b, skills/agent-usage/references/codex-implementation.md at c71e065c4860d8527659b1bebf1134cdedd226f3 -->
<!-- Left out or changed on purpose (audited 2026-11-24):
- The copy is a short rewrite, not a section-by-section port: Claude's Agent and team-agent tools, slash commands, and model names do not exist in Codex (codex/MIGRATION.md, "Claude Skill/Agent tools and slash commands").
- Sections 1 to 6 are joined into the opening paragraph; section 6's team agents are left out, since Codex has no team-agent tool.
- Section 7 keeps one reviewer at the checkpoints and points to decisions-and-collaboration; running outside-review is Claude's side, since Codex is that reviewer.
- Section 12's model override and Section 13's Claude model names, session tools, and Agent options become "choose from the work-type table with Codex's own options".
- Section 14 and the codex-implementation reference are left out: they are Claude's procedure for handing work to Codex, which Codex does not do (the 2026-11-17 plan that added them left the copy's content as it was for this reason; Codex has run only reviews since 2026-11-23).
- Codex adds that it uses its own collaboration tools for subtasks and opens or messages another chat only when the user asks.
- Sections 2 to 4 narrow optional delegation proposals in Claude because Anthropic's migration guidance says Claude Opus 5 and later spawn subagents readily (prompt audit of 2026-11-24, item M1); the copy keeps its splitting and parallel-audit proposal, since that guidance is about Claude models and the copy already starts no subagent without the user's explicit authorization.
-->

# Agent Coordination

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

Do not spawn subagents merely because this skill is loaded. Obtain explicit user authorization for delegation; prior authorization for the same scope remains valid. Propose splitting when accuracy drops on repetitive review or the scope grows past what one pass checks well, and parallel agents for long audits and analyses, with count, independent roles, expected benefit, and material cost. Estimate the token cost from the files and their complexity and give the rationale for the count; do not invent token budgets or model capabilities. A skill the user invokes whose instructions start agents counts as approval for the agents it describes; anything beyond them still needs approval. An agent that only stands in for a tool the session lacks, such as a web-fetch agent reading a page, is a tool call and needs no approval. Ask before running any agent at max effort, which the work-type table in the decisions-and-collaboration skill keeps for the user's approval. A reviewer a rule requires, such as the checklist reviewer of a large commit or a checkpoint reviewer, needs no separate authorization; say in one line that it ran (user decision, 2026-11-09).

Use Codex's available collaboration tools for subtasks, not new user-owned chats. Create or message another chat only when the user explicitly requests it. Follow actual tool availability and permission limits.

For independent review, follow the decisions-and-collaboration skill: one reviewer for each piece of work, at the checkpoint before anything relies on it and reused at later checkpoints for the same piece, preferring another model family, covering the full change rather than a file range. Ordinary small work needs self-review, not mandatory agents.

Give each agent bounded ownership and shared contracts. Avoid concurrent edits to the same files. Reviewers check the changed code, document, or screen against each applicable rule one by one, reading its actual text in AGENTS.md and the skills, never from memory or general impression; an independent reviewer reads in the order decisions-and-collaboration "Independent review" sets; they trace runtime behavior, type resolution, and test evidence rather than checking only for code presence.

Apply the same verification depth to plans as to code. Every classification ("fixable", "unavoidable", "redundant") must cite concrete evidence (line numbers, type resolution trace, actual compile/test output). Never trust agent summaries without verifying key claims against the actual code, document, or source they summarize. For type-related issues, trace the full type resolution chain step by step before classifying. Exhaustive audits require checking every occurrence and reporting exact counts; estimates or round numbers are not proof. Choose each subagent's model and reasoning effort from the work-type table in the decisions-and-collaboration skill, set with Codex's own options, rather than hard-coding a Claude-specific agent type. For unfamiliar tool/model options, consult current supported capabilities.

Complete and integrate all authorized subtasks before declaring the parent work complete. Do not turn an agent suggestion into permission for unrelated changes or external messages.
