---
name: typescript-migration
description: Preserve runtime behavior while converting JavaScript to TypeScript; use only for JS-to-TS migration.
---

<!-- Adapted from skills/typescript-migration/SKILL.md at a364ddfbf2465906ff86a4fb1c16965847c0a4ce -->

# JS → TS Conversion Rules

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

## Core Principle: Add Types, Preserve Behavior

Goal: **100% preserve existing runtime behavior** while adding type information. Never change behavior to satisfy types.

## Preserve Instance Properties

Never convert `this.xxx = ...` instance properties to local variables (`const xxx`). This breaks external consumer code that accesses `obj.xxx` at runtime. TS `private` is compile-time only — JS consumers bypass it.

**Before conversion, always grep for external property access:**
```bash
rg "\.<member-name>" <source-and-consumer-directories>
```

## Value Preservation

Never change **values passed to functions, callback args, or return values** during type conversion. When types don't match, **widen the type** to accept the original value instead of changing the value.

Violation patterns:
- `callback(null, error)` → `callback(undefined, error.message)` — value changed
- `callback(err, msg)` → `callback(new Error(msg))` — param count/type changed
- Omitting `this._client = undefined` dispose logic — cleanup code lost

## Code Structure Preservation

Don't restructure code during type-only conversion. Keep original variable positions, destructuring patterns, control flow. Separate refactoring into different work items. Only change structure when type system constraints make it unavoidable (explain and get approval).

## Library Callback Undeclared Variables

When callback code references apparently undeclared variables, inspect the library source, callback signature, lexical scope, and any code-generation step. Libraries cannot normally inject lexical variables into an existing callback. Do not invent replacement values; establish the actual runtime source and preserve intended behavior.

## Post-Conversion Verification

Compare original JS (from git history) with compiled JS output:
```bash
git show <prev-commit>:path/to/file.js  # original
cat path/to/file.js                      # compiled
```

**Checklist:**
1. Callback/function values identical?
2. Instance properties still instance properties?
3. Dispose/cleanup logic preserved?
4. Consumer-accessed properties all maintained?

## export = Transition Rule

Modules consumed via `require()` by JS code maintain `export =` until all consumers are converted to TS. Convert to named export after all consumers are migrated.
