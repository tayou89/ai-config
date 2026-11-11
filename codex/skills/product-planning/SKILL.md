---
name: product-planning
description: Apply planning and research principles to product planning, external research and source selection, vendor or tool selection, pricing and quota research, and early-launch criteria.
---

<!-- Generated from shared-rules/planning.md by scripts/sync-shared-rules.py. Edit the source, not this copy. -->

# Planning and Research Principles

Apply to product planning, vendor and tool selection, and evidence research in every project. These generalize questions and feedback that recurred during project work. Follow project rules first.

## Planning skills

- Use the `grilling` skill to turn an idea or plan whose key decisions are not yet recorded into settled decisions: run it when the user asks for it, or propose it once and run it only if the user agrees (user decision, 2026-11-09). Read the project's decision records first and do not ask again what they settle, keep a session to one feature or decision area, and ask about five questions per round.
- When the skill conflicts with a higher rule, follow this order: the user's decisions, then the project's rules, then these shared rules, then the skill, and say in the report which step was skipped and why. Find facts yourself rather than starting helper agents the user has not approved; ask a question with two or more real options in the decision-request format rather than as a line that "yes" accepts; send visual decisions (screens, copy, color, layout) to a comparison board instead of settling them by question; and record what was decided in the project's decision documents before the session ends.

## Research and evidence

- Verify facts that change over time (pricing, free tiers, quotas, terms) in official documentation and note the date checked. Label blog and community answers as such, and state when a number could not be verified.
- When the user asks how things are usually done, answer with real service examples and cite sources. Mark practices you could not verify directly as what you know.
- Pick sources by what the question needs, not by what a search returns first. Take changing facts (pricing, limits, terms, version behavior) from official documentation, changelogs, and maintainers' issue trackers; when the question is how something is actually used or received, add at least one source of a different kind, such as a recorded talk or tutorial, app store reviews, community discussion, research papers, or public statistics (user decision, 2026-11-06).
- For a product aimed at one country or language, include that market's own platforms in its language (its leading portal's blogs and forums, app reviews, public data), and say which of them could not be reached.
- Search wide and read narrow, as a person would: skim each result list for relevance, source credibility, popularity (views, votes, review counts), and date, pick two or three items of each kind, and read only those in the cheapest form that answers the question (abstract, transcript, release notes); read a full text only when a conclusion rests on it (user decision, 2026-11-06). Label each source's kind and date, say when a video's visuals were not checked, summarize rather than reproduce, and leave out reviewers' and commenters' names.
- Before reporting a value in your own measurements (logs, tests, data) as a threshold or limit, say whether it is a bin edge you chose, recount in finer steps on both sides of it, and check whether another condition present at the same time explains it; report it as a limit only if it still holds (user correction, 2026-11-11).
- Save each plan and research report that later work builds on as a project file when you write it, never only in chat; add its row to the notes folder's `INDEX.md`, and make the chat reply a summary that links to the file (user decision, 2026-11-11).

## Choosing vendors and tools

- Do not compare existing services only; compare building it yourself, free public data, and starting without the feature on the same criteria.
- Compare cost at the actual scale, free-tier conditions (per-account or per-app limits, etc.), storage and display restrictions in the terms, development effort, user familiarity, and ease of switching later.
- When comparing tools or ways of working the user will use themselves, judge them by everyday use once set up and learned; list one-time costs (installation, initial setup, rewriting rules, learning) separately and keep them out of the recommendation unless the user asks to weigh them (user decision, 2026-11-08).
- Keep your own data (identifiers, records) separate from external vendors so records survive a vendor switch.
- Explain separately what is convenient to bundle (one vendor's map and search) and what is technically unrelated (login and maps).

## Decision criteria for early products

- Do not set ratio targets in advance while users are few. Record behavior that cannot be reconstructed later from the start, and just before launch set one sentence and a date stating when and on what basis to continue, revise, or reconsider.
- Choose names, metaphors, and copy that fit the product's intended scope, not only the first category it launches with. When the scope is unclear, confirm it before naming.
- Before proposing names, metaphors, or copy, check every candidate, including each step of a series, for strong associations in the users' culture: political movements and protests, memorials, religion, public figures, or a specific brand or well-known work. A word that evokes one feels borrowed or seems to take a side, so drop it, and say which candidates were checked and what was found.
- When a request starts from a reference service ("something like X's feature"), compare the finished concept with that reference as a whole, not word by word: the metaphor, how the value changes, and how it is displayed. Also avoid the words the main service in the same domain already uses for its features. A concept built from a reference drifts toward copying it.
- Address the empty-data problem on launch day (cold start) together with the feature list. Narrow the first area and first user group for collecting records, and design empty-state screens and first-participation prompts together.
