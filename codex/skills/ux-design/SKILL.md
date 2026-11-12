---
name: ux-design
description: Apply screen and UX design principles to screen design, print and PDF document design, design review, UI copy, and design-tool work.
---

<!-- Generated from shared-rules/design.md by scripts/sync-shared-rules.py. Edit the source, not this copy. -->

# Screen and UX Design Principles

Apply to screen design, print and PDF documents such as résumés and reports, design review, and UI copy in every project. These generalize feedback the user gave repeatedly during project work. Follow the project's own design rules first; use this document as the default where they are silent.

## Design skills

- For visual design (a new or reshaped screen, layout, typography, color, motion), read the `frontend-design` skill as well and follow its process: a short plan of palette, type, layout, and principles, a check of that plan against generic defaults, the build, then a critique of captures. Decisions the user or the project already made win over its list of generic defaults; where they overlap, point it out instead of changing them (user decision, 2026-11-09).
- Use the tool's dedicated skills where it has them: `design-critique` for design critique, `ux-copy` for UI copy, and `accessibility-review` for accessibility checks.
- Give each installed design skill one role so they do not compete (user decision, 2026-11-09): `frontend-design` sets the direction and plan; a typography skill, where the project has one installed, refines type within `frontend-design`'s type plan; `design-taste-frontend` (Taste) builds and browser-checks pages, and a site of a kind it does not define (for example a personal site) follows the nearest kind it does define, with the steps that break a higher rule left out; `emil-design-eng` and `apple-design` decide motion and interaction details, `web-motion-techniques` supplies tested recipes for the chosen motion, and `review-animations` reviews them; an anti-pattern detector or critique skill, where installed, reviews the result, and `web-interface-guidelines` checks the page code before it is shown or released.
- When skills conflict, follow this order: the user's decisions, then the project's rules, then these shared rules, then the skills. Skip a skill step that breaks a higher rule, and say in the report which step was skipped and why. A skill never adds stand-in or stock media, credits, page metadata, or dependencies the project has not approved, and never overrides a choice the user made because the skill lists it as a generic default.
- Give an independent design reviewer the same design skill files to read, so author and reviewer judge by one standard.
- Read only the skills of the stage at hand (user decision, 2026-11-09): direction `frontend-design`; building a web page `design-taste-frontend`; motion `emil-design-eng` (`apple-design` for gesture-driven UI), then the one recipe needed from `web-motion-techniques`; copy and accessibility `ux-copy` and `accessibility-review` after the project's own standard; before showing or releasing, scripts first, then `web-interface-guidelines` for code, `review-animations` for motion, and one critique skill. Code-only skills apply to code, not to design-tool frames.
- When two skills disagree, the skill whose role covers the point wins (motion: `emil-design-eng` and `review-animations`; direction: `frontend-design`); a skill's English-only copy or typography rules apply only where the page's language fits.
- Use one critique skill per review, and give a reviewer only `ux-design` and the skills of the stage it reviews.
- In every design round, when making and when reviewing, run the skills of the stage at hand from this section and report which ran, what each changed, and which were skipped and why (user decision, 2026-11-11).
- When choosing reference or role-model sites, judge each candidate by how well its owner's content and work match the product's stated goal, never by whether it matches the structure of an existing draft; decide structure after studying the references, and search beyond showcase galleries when the subject is not design work itself (user decision, 2026-11-12).
- Before building options, set a visual target from the references and the user's own past work, written as measurable traits (typefaces, size and weight contrast, number of colours, rule weight), and make the options differ against that target rather than only fixing defects (user decision, 2026-11-11).
- In every design or copy review, your own check and an independent reviewer's alike, look for anything that reads as AI-made or unnatural, in the design (layout, type, colour, motion) and in the copy of every language the page uses (user decision, 2026-11-11): judge the design against `frontend-design`'s list of traits generated design clusters around, English copy with Taste's `scripts/copy-lint.mjs` and `references/copy-tells.md`, Korean copy with `humanizer-ko`, and any other language by a native-language review as in "Copy". Report each spot with what it points at and the fix, or why it stays (for example, a trait the user chose), and name what was left unchecked.

## Hierarchy

- Use one primary button per screen. Do not stack buttons of the same shape and weight; demote other actions to secondary buttons, text buttons, or text links.
- Fix the primary button that completes a step to the bottom of the screen. Place actions tied to specific content (undo, change, view details) right next to or below that content.
- Run reversible actions immediately without a confirmation dialog and offer undo. Reserve confirmation dialogs for irreversible or weighty actions such as money or personal data.
- On screens where the selected value becomes a record, do not save on selection. Save with a confirm button after selecting, keep that button disabled until something is selected, and let users change a wrong choice immediately.
- When listing several choices, do not stack borderless text or identical buttons. Put them as rows (title + ›) inside one bordered group.
- Concentrate the accent color on the screen's main action. Demote secondary links near the primary button to body color, regular weight, and ›, without accent color or bold.
- Do not run supplementary info on rows and cards (recently used, duration, source) at the same size and weight as the title. Show it in small, muted text to the right of the title (before ›) or below it.
- Organize related content with clear hierarchy and spacing. Prefer a shared section with subheadings when separate top-level navigation adds no value for the reader.
- Give each screen one main message that a first-time viewer gets at a glance, and check it on a capture at real size before showing: what stands out first should be that message. Raise it by quieting what competes with it (smaller, lighter, muted, fewer) rather than enlarging it or adding elements, and keep required information, changing its weight instead of deleting it (user decision, 2026-11-11).

## Alignment and layout

- Start result, completion, and error screens at the same position as other screens (top, left-aligned). Do not center content vertically.
- Do not mix centered text with left-aligned cards or lists on one screen.
- Build cards of the same kind with the same structure, with or without an image, so text starts on the same line.
- Review screens at a real phone height (e.g., 812px). An overly tall canvas exaggerates empty space and leads to wrong judgments.

## States and accessibility

- Never distinguish states by color alone. Selected chips and rows show a checkmark together with a border or background.
- On brand-color surfaces whose contrast falls below WCAG AA 4.5:1, place only large bold text (16px or larger, bold). Keep small text off those surfaces.
- Use checkmarks only for states already in effect (selected, saved), never on buttons before they are pressed.
- Make touch targets at least 44×44px, including text links and inline text buttons (44px height).
- Give chips and buttons horizontal padding (e.g., 16px) so text does not touch the border. If text and icons look cramped, check the padding first.
- Design empty, loading, error, and no-results states for every screen, and show one next action in every state.
- When there are no records or ratings at all, do not display a large “0”. Collapse the number area, show one short line inviting the first contribution, and list ranked items below without rank numbers. Once at least one record exists, show numbers as they are, including 0. With small samples, hide average scores; counts can still be shown.
- Check screens not only with ideal sample data but also with the most common real situation (e.g., almost no records, as in launch week).
- Design previews should include clearly labeled, representative sample content when real content is unavailable, so typography, spacing, hierarchy, and content density can be evaluated. Keep samples distinct from the user's actual history, achievements, and published work.
- Place gray placeholder text at the top left of empty input fields and remove it once the user types. Do not let blank lines push the placeholder to the middle of the field.
- Do not put instructions meant for mockup reviewers (e.g., “Tap to see an example”) in product copy. Keep them in notes outside the screen or in frame names.

## Choosing images

- Show real images of the actual subject (the person's own photos or screenshots of the real work) before concept graphics or stock photos. If the subject itself was never photographed, use a real photo of the same place or object, say in the caption what it shows, and credit its source and license; keep concept graphics for what cannot be photographed, such as a system's structure, and label them as such.

## Missing images

- Keep the image slot when there is no image; use a slot of the same size to preserve alignment.
- Do not leave an empty gray box; it looks like loading or a broken image. Place a clear icon in the center (e.g., cutlery for food).
- Do not substitute emoji; they render differently across devices and often lack a fitting image.
- Distinguish loading (slot without icon) from no image (slot with icon).

## Handing off to other services

- Hand off tasks that other apps do better or that have no public integration (directions, booking, ordering, payment) via links.
- Do not offer only one company's app; people use different apps. Open an app picker from a single link, with the most recently used app at the top. Use app names instead of logos.
- Do not list one equal-weight button per app.
- Record which app is chosen so rarely used options can be reduced later.

## Copy

- Write participation prompts as one short, warm sentence that leads with the benefit to the user or community. Do not stack conditions, caveats, or reassurances, and do not list analysis methods. Add only brief required/optional status and visibility.
- Write copy in the screen's own language from the start, beginning from the user's own words, never as a translation of sentences thought out in another language; when the available tools cannot judge that language's naturalness, say so and add a native-language review (user correction, 2026-11-09: Korean drafts read like translated English).
- Before proposing copy, check the project's existing copy rules and screens and start from them. If the user has written a draft, do not replace it; offer one small revision for the single issue that concerns you.
- Do not put multiple bold instruction sentences on one screen. Place one line of guidance directly above the input that needs it.
- Keep labels in lists and summaries short. Do not add parenthetical explanations such as “Get notifications (when there is news)”; explain once, on the input screen.
- Match button names to what actually happens after pressing. If the screen clears a value and returns to selection, the button is “Change”, not “Delete”.
- Do not show operating status such as “pilot” or “beta” on user screens; it only raises questions and suspicion. Build trust with transparent history and promises you can keep (e.g., “Points you earn never decrease”).
- Describe data use as it actually is. Do not call private records contributions that help others, and do not hide data that is actually aggregated; state it in one line.
- Decide sharing separately for selected values (chips) and free text. Selected values can be aggregated anonymously into statistics, but free text carries one person's words verbatim and may contain personal data, so by default share it only when the user turns sharing on.

## Rewards such as points and levels

- Do not attach points to input screens where users choose answers or reasons; points bias the answer. Show earned points after submission, on the result screen or in notifications.
- Award points for contributions actually used by other people or the service. Give the same points for positive and negative answers.
- Check fairness. Verified actions (records with proof) earn substantially more than unverified ones, and easily repeated actions have per-target and daily caps. Do not block the record itself; limit only the points.
- Make units large enough to feel rewarding (+50 rather than +5), but avoid sizes that could be mistaken for money (+1,000 or more). Use thousands separators from 1,000.
- Never reduce points already earned. When changing criteria, change only in the additive direction.
- Do not add pressure mechanics such as leaderboards, rewards, or streaks at first. Set the first level low so users level up once on day one.

## Design tool work

- Build repeated UI as components and edit the main component once. Do not detach instances to edit them.
- Do not rely on instance size overrides for sizes that differ per screen (e.g., progress bar length); use variants or properties.
- When adding a component property or changing layer bindings, immediately check that existing bindings (such as text properties) were not overwritten and that screens already using the component keep their values. Set a new property's default to the current appearance so existing screens do not change.
- Keep a copy of the original before large changes, and always inspect a capture yourself after editing. Check that duplication did not bring along wrapper frames and that text is not clipped or overlapping.
- Keep sample data (names, dates, numbers) consistent across screens that show one flow, and check that totals add up.
- At the start of every round, list the user's open requests and every unresolved design-tool comment on every page, each with its status, and keep each on the list until it is done or the user drops it (user decision, 2026-11-11).
