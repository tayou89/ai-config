# Claude-to-Codex Rule Migration

Updated: 2026-10-28. Source: the repository's CLAUDE.md and ten skills at the time of migration.

## Applied

`AGENTS.md` contains reusable engineering, ownership, planning, verification, Git, communication, and security preferences, plus the complete shared writing principles. Ten skills retain task-specific rules: code-style, typescript, typescript-migration, code-plan, config-files, integration-testing, git-commit, resolve-conflict, pr-create, and agent-usage. The python skill was added to both tools on 2026-11-09; its Codex copy adds the authorization preamble and, like the Codex code-style, keeps intentional background-task contracts.

The detailed code style remains a personal preference: braces, positive conditions, explicit branching, private/protected naming, import/member order, async/error handling, provider ownership, and TypeScript type safety are retained where the language supports them. These rules are not represented as universal standards.

## Codex Adaptations

| Source behavior | Codex behavior and reason |
| --- | --- |
| Claude Skill/Agent tools and slash commands | Discoverable SKILL.md files and available Codex tools; no assumed Claude commands or model names. |
| Repeated plan/build/commit approvals | Preserve actual review checkpoints and explicit authorization; do not re-ask for an action already approved in the same scope. Read-only verification proceeds normally. |
| Claude commit wrapper and PreToolUse hooks | Preserve scoped staging, review, message checks, and push authorization as instructions. No automatic command interception is installed. |
| `claude-plans/` plus global Git ignore changes | Use the project's established plan directory, otherwise `plans/`; no automatic global Git configuration changes. |
| Automatic stashing, rebase aborts, and cleanup | Inspect and preserve user/concurrent work; continue the appropriate Git operation instead of blindly resetting it. |
| Runtime test probes and remote fixes | Probe required authorized test endpoints; a blocked ping is not proof of failure. Other repositories and infrastructure need their own authorized scope. |
| Mandatory soak tests and automatic test-stash pop | Use bounded soak tests when in scope; inspect and apply only the specific authorized test patch. |
| PR prose split by line count | Keep required Purpose/Background/Changes/Verification, but choose paragraphs/lists by meaning under the shared writing principles. |
| Automatic Claude settings sync, Discord forwarding, session commands | Not migrated: tool-specific behavior and unrelated external actions. No credentials are copied. Codex command rules in `codex/rules/` (one allow rule for the ego browser command, added 2026-11-11) are merged into `~/.codex/rules/` three ways. |
| User-invoked `deep-prompt`, `outside-review`, and `domain-pass` skills (added 2026-11-05) | Not migrated yet: they are written for Claude slash commands and subagents, and `outside-review` calls Codex itself as the other model family. |

## Clarified Technical Assumptions

- Syntax-specific style does not impose braces, JavaScript equality, or TypeScript layout on unrelated languages.
- Type/interface declarations are one contiguous section; TypeScript follows the complete static/instance ordering from code-style.
- Direct callback references must preserve receiver binding. Intentional background work must not be converted to awaited work without checking its contract.
- Use the installed repository toolchain. Do not fetch ESLint implicitly or weaken lint rules to conceal real findings.
- A sibling `.js` file is treated as generated only after checking the build. Commit generated artifacts only when the repository tracks them. Module output follows actual compiler/package settings, not an assumed CommonJS target.
- Generic variance depends on all input/output uses, syntax, and compiler flags. The former blanket claim that a nested input callback implies invariance was corrected; verify actual assignments with the compiler.
- A library does not normally inject lexical variables into an existing callback. Migration checks the real callback signature, source, and scope before choosing values.

## Shared Rules

Updated: 2026-11-06. The design, decisions-and-collaboration, and planning rules were written as Claude user rules and are now shared through `shared-rules/`. Codex receives them as the generated `ux-design`, `decisions-and-collaboration`, and `product-planning` skills instead of embedded text, because embedding them would add about 24 KB to a global AGENTS.md whose combined size with project instructions is capped by `project_doc_max_bytes`. Only the short `collaboration-core.md` (principles every task needs, about 5.6 KB) is embedded with the writing principles. Since 2026-11-06 Claude loads the same three skills instead of three always-loaded rule files. Tool-specific wording in the source was made neutral, such as a one-line open command instead of a client's Run button.

## Verification and Limits

Validate skill frontmatter and local references, compare installed files with these sources, and verify discovery using the local Codex skill registry. This confirms installation and discovery, not guaranteed compliance in every future output. Hooks are not enforcement mechanisms in this package. Restart Codex if changed skills do not appear; a new conversation provides a clean instruction-loading check.

GitHub and local copies do not synchronize automatically. Update both deliberately; `scripts/install.py --check` lists what differs in either direction. Preserve existing unrelated global instructions and skills when reinstalling.
