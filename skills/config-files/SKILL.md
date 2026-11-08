---
name: config-files
description: Rules for designing config modules and .env files. Apply when creating or refactoring config code, env files, or env-driven setup.
user-invocable: false
---

# Config & Env File Rules

## File Structure by Size

- **1-2 files** → flat at root (`config.ts`, `util/env.ts`). Canonical for small projects — Vite, Next.js, Fastify, Drizzle, webpack all use this shape.
- **3+ files with distinct concerns** → group in `config/` folder. Common shape: `index.ts` (assembly) + `env.ts` (primitives) + `helpers.ts` (domain parsers).
- **5+ files split by concern** (devices / server / notification / external services) → folder with per-concern split. Medium project standard (NestJS-like).
- **Per-instance split** (1 file per device, per channel, per record) → forbidden. Over-fragmentation that buys nothing over inline arrays.

Don't introduce a folder solely to host 1-2 files — that's speculative future-proofing. Folder needs ≥3 meaningfully-divided files to justify the extra indirection.

## Match Config Shape to Consumer Shape

Singleton consumer → singleton config. Array consumer → array config. Don't force array on a singleton domain — asymmetric config matches asymmetric domain. Multi-instance support is an architecture change (consumer signatures, ownership, dispatcher logic), not a config refactor — defer to a separate plan.

## YAGNI for Config Abstractions

Don't introduce dynamic count-driven generation (factory with count-env input + per-id builder) when the deployment has a fixed number of instances. Inline array literals are clearer at small scale, and adding one unit later is a single copy-paste — not a recurring cost worth abstracting. Only introduce factories when the same shape genuinely recurs across variable deployments (multi-tenant, multi-site, variable instance counts).

## Helpers in Same File as Config

A 20-50 line helper preamble (consts, parsers, small builders) at the top of a config file is the industry-standard pattern — webpack.config.js, next.config.js, vite.config.ts, drizzle.config.ts all do this. Don't extract to a separate file under this threshold. Extract when:

- helpers exceed ~100 lines
- helpers contain logic unrelated to config (generic utilities → `util/`)
- the same helpers are needed by other modules

## Env File Hygiene (12-factor)

- `.env.example` git-tracked as the key catalog with placeholder values (empty `=` or `<replace>`).
- `.env`, `.env.<environment>` gitignored, distributed per-machine via secure channel (password manager / SharePoint / encrypted share).
- Use platform-native env loader (Node `--env-file` ≥20.6, framework equivalent). Don't roll a custom loader unless necessary.
- Add blank lines between multi-line unit blocks within a section (e.g. between `UNIT_1_*` block and `UNIT_2_*` block). Single-line entries within a section don't need separators.

## Required vs Optional Env

`requireEnv(name)` throws on missing/empty — fail-fast at module load surfaces deploy errors immediately. `optionalEnv(name)` returns `string | undefined` for genuinely optional fields. Treat empty string as missing.

## Type Narrowing for Env Values

Env values are always `string`. When the consumer expects a literal union or enum, introduce a parse-and-validate helper that returns the narrowed type — never `as` cast on env values. Parsing happens once at module load; downstream code gets the typed value automatically.

```ts
function parseTier(envName: string): 'dev' | 'staging' | 'prod' {
    const value = requireEnv(envName);

    if (value === 'dev' || value === 'staging' || value === 'prod') {
        return value;
    } else {
        throw new Error(`Invalid tier: ${value}`);
    }
}
```

## Framework Convention Citation

Folder vs flat at 2-3 files is genuinely framework-dependent. Flat is canonical for Vite / Next.js / Fastify / Drizzle / webpack; folder is canonical for NestJS / Strapi / AdonisJS. Don't push one as "more standard" without naming concrete framework precedents at the margin — at unclear boundaries, frame as preference, not standard.
