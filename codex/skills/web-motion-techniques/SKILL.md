---
name: web-motion-techniques
description: Reference catalog of 58 tested web motion, type, scroll, surface, and layout techniques with parameter values, vanilla implementations, and reduced-motion fallbacks, plus craft rules, a build-spec template, and a look-and-fix review. Use when choosing or implementing a page's signature motion or visual technique, or when reviewing motion and craft on a web page.
---

# Web Motion Techniques

A reference library, not a workflow. The files under `references/` are unchanged copies of the MIT-licensed instructions from the MotionSites Claude skill (`Motionsites-AI/claude-skill`); its example prompts and media library are not included and are not to be fetched.

## How to use

- Start from `references/techniques/INDEX.md`: pick one signature technique for the page and two or three supporting ones that serve the subject, then open only the file that holds them.
- Use `references/craft.md` for composition, type, colour, and motion rules, `references/spec-template.md` to write a build spec before coding, and `references/review.md` for the look-and-fix pass on captures.
- Follow the skill roles and conflict order in `ux-design`: user decisions and project rules come first. Do not add stock or stand-in footage, images, or remote libraries the project has not approved; a technique built on video or 3D needs the project's own media or explicit approval.
- Some recipes match patterns `frontend-design` lists as generic defaults (for example a single italic accent word in a headline); prefer the one that fits the subject and say why when using one.
