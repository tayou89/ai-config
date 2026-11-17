# Release commit bundling

Release-prep changes for a single version (changelog entry, doc sync, version manifest bump, dependency lock snapshot, release-only metadata) go in **one** commit, not split per file. Per-topic splitting is the default for regular work; **a release is one topic** — atomic revert restores prior version state, and the release boundary stays visible in `git log --oneline`.

- Subject pattern: `Chore: release vX.Y.Z`
- Bundle everything tied to *this* version: changelog, readme/docs sync, version manifest, lock/snapshot file, release-only metadata
- Pre-release work unrelated to *this* version (retroactive changelog backfill, large doc restructure) commits separately **before** the release commit

This applies regardless of language or build system (any version manifest, any lockfile format, any release tool).
