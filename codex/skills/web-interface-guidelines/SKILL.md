---
name: web-interface-guidelines
description: Pre-release review of web UI code against Vercel's Web Interface Guidelines (accessibility, focus states, forms, animation, typography, content handling, images, performance, navigation, touch, layout, theming, locale), reported as file:line findings. Use when reviewing a page or component before showing or releasing it, or when asked to audit UI code for accessibility and interface quality.
---

# Web Interface Guidelines

Review the given files against `guidelines.md` beside this file and report findings in the output format it defines. `guidelines.md` and `LICENSE` are unchanged MIT copies from `vercel-labs/web-interface-guidelines`; read the local copy instead of fetching the rules from the web.

- Treat it as the reviewer role in the skill roles of `ux-design`: it checks the result and does not set the design direction.
- Follow the conflict order in `ux-design`: the user's decisions and the project's rules come first. Its copy and typography rules are written for English interfaces (Title Case, curly quotes, second person, numerals); apply them only where they fit the page's language and the project's copy rules, and say which you skipped.
- Report a finding the project's automatic checks already cover only once, and say which check found it.
