---
name: typescript-migration
description: Rules for JS to TS conversion. Referenced only during JS→TS migration work, not for general TypeScript writing (use typescript skill for that).
user-invocable: false
---

# JS → TS Conversion Rules

## Core Principle: Add Types, Preserve Behavior

Goal: **100% preserve existing runtime behavior** while adding type information. Never change behavior to satisfy types.

## Preserve Instance Properties

Never convert `this.xxx = ...` instance properties to local variables (`const xxx`). This breaks external consumer code that accesses `obj.xxx` at runtime. TS `private` is compile-time only — JS consumers bypass it.

**Before conversion, always grep for external property access:**
```bash
grep -rn "\.<property>" <consumer directories>
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

When JS code references undeclared variables inside library callbacks, check the **library source** first — the library may dynamically inject variables into callback scope. Don't blindly replace with `Buffer.alloc(0)` etc. Understand the library's intent before deciding alternatives.

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
