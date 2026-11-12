---
name: typescript
description: TypeScript and JavaScript rules applied when writing, converting, or modifying .ts, .tsx, .js, or .jsx files. Automatically referenced when creating or editing TypeScript or JavaScript code.
user-invocable: false
---

# TypeScript Rules

Apply these rules when writing or modifying TypeScript code. The "JavaScript and TypeScript" section also applies to `.js` and `.jsx` files; the "TypeScript" section applies to TypeScript only. Long examples and rarely needed procedures are in [references/examples-and-procedures.md](references/examples-and-procedures.md), which the rules below call the reference file.

## JavaScript and TypeScript

### Strict Equality Only

Always use `===`/`!==`. Never use `==`/`!=`.

### Nullish Checks

For null+undefined checks, prefer in this order:

1. **Truthy check** (default): `if (value)` / `if (!value)` — when `0`, `""`, `false` are NOT valid values
2. **Explicit comparison** (when `0`/`""`/`false` are valid): `value === undefined || value === null` (or `value !== undefined && value !== null`)

### No Unnecessary `return undefined`

When return type includes `undefined`, don't write `else { return undefined; }` or `else { return; }`. Just omit the else block.

### Prefer const

Use `const` unless the variable is actually reassigned. `let` only for reassigned variables.

### Variable Scope at try-catch Boundaries

`let`/`const` declared inside `try` are not visible in `catch`/`finally` (block-scoped) — declare them outside `try` when shared across blocks, and inside the block when used only there.

### await Over Promise Chains

Use `async/await` + `try/catch` instead of `.then().catch()`.

### throw Over Promise.reject

In async functions: use `throw` not `Promise.reject`. Use `await this.send()` not `return this.send()`. Never use `new Promise(async (resolve, reject) => ...)` anti-pattern.

### Floating Promises in Fan-out Callbacks

Inside fire-and-forget callbacks (forEach without await, event-emitter listeners, setInterval): never leave a Promise unhandled. Use `await Promise.allSettled([...].map(fn))` with per-result `if rejected` log for parallel fan-out, or `for-of + await + try/catch` for sequential. Per-promise `.catch()` chaining is forbidden — violates "Don't mix await with .catch()" and returns void instead of propagating reject to the caller's try-catch.

### Await Only Thenables

Never `await` a function with `void` or non-Promise return — silent no-op masking a sync/async mismatch. Verify callee's return type before adding `await`. If the callee body holds floating Promises (`.catch()` chains, fire-and-forget), the callee itself is mis-declared sync and must be converted to `async` with proper internal `await`; conversely, if a function is declared `async` but its body has no real await target, demote it to sync.

### Remove Unnecessary async

Don't add `async` to functions that don't use `await`.

### Sync Return When Signature Allows

When the signature accepts both sync and async (`() => Promise<T> | void`), write sync logic without `Promise.resolve()` wrap or `async` keyword.

### File Top Structure: import → type → interface → const

Order file top sections with **one blank line** between sections:
1. **import / require**
2. **type aliases** (`type Foo = ...`)
3. **interface definitions** (`interface Foo { ... }`)
4. **const/let declarations**

### Import Ordering

Order imports by group, no blank lines between groups:

1. **Node.js built-ins** (`events`, `fs`, `path`, `net`, etc.)
2. **External packages** (`axios`, `lodash`, `ws`, etc.)
3. **Internal modules** (relative `./` `../` imports)
4. **Internal constants** (large destructured imports from a shared constants module)
5. **`import type`** (always last)

Multiline destructured imports (4+ items) use one item per line with trailing comma.

### Imports at File Top Only

All `import`/`require` at file top. No inline `import()` or `require()` mid-code. No `import('path').Type` inline type references. If inline import is unavoidable (circular dependency), explain and get user approval.

## TypeScript

### Build Quality

- Maintain `npx tsc --noEmit` with **0 errors**
- Run `eslint` on changed `.ts` files before commit — **0 errors** required. Warnings from original JS code patterns are acceptable.
- Target **0 IDE warnings** (ESLint, TS hints). Fix via code changes or ESLint rule adjustments.
- Disable JS-native ESLint rules (`no-unused-vars`, `no-use-before-define`, `no-shadow`) that false-positive in TS. Use `@typescript-eslint/` equivalents.

### Build Procedure

Before running `tsc` build (output generation):
1. **Style check**: verify code-style compliance (all rules, not a subset)
2. **`npx tsc --noEmit`**: confirm 0 type errors
3. **`eslint`**: confirm 0 errors on changed `.ts` files
4. **User approval**: show code, get confirmation
5. **`npx tsc` build**: generate .js/.js.map only after approval

**Never run `npx tsc` (output build) without user approval.**

### Source and Build Artifacts

When `.ts` and corresponding `.js` coexist, `.js` is a build artifact. **Always edit `.ts` and build with `tsc`** — never edit `.js` directly. Check for corresponding `.ts` before modifying any `.js`.

- After `.ts` changes, build and commit `.js`, `.js.map`, `tsconfig.tsbuildinfo` together.
- `.ts` source and its `.js`/`.js.map` output MUST be in the **same commit**.

### ESModule Syntax

Use `import`/`export` instead of `require()`/`module.exports`. tsc compiles to CommonJS so runtime compatibility is preserved.

**require format in TS files**: When `require()` is needed (module with JS consumers), use `import X = require(...)` form only. Never mix `const X = require(...)` with import statements.

**Module with no JS consumers** → `import X from '...'` or `import { X } from '...'`
**Module with JS consumers** → `import X = require('...')`
**Type only** → `import type { X } from '...'`

**Named import for partial values**: With `esModuleInterop: true`, destructure at import time — don't create intermediate variables. Exceptions: `export = X` modules (tsc 2497), full module reference needed (`typeof module.FIELD`), single-value exports.

### Named Export (No Default Export)

Always use named exports. Never `export default`. Enforces import name consistency, IDE refactoring accuracy, and multi-export consistency.

### No import * (CJS Namespace Exception)

Don't use `import * as X`. Use named imports. **Exception**: CJS modules where `module.exports = { a: {...}, b: {...} }` makes the module itself a namespace (e.g. `jsmodbus`).

### Type Safety

- **`as const`**: Use for immutable constant data (lookup tables, configs). If `readonly` compatibility issues arise, adjust the **receiving interface** to accept `readonly`, don't remove `as const`.
- **`readonly` propagation**: Declare non-mutated array/object params as `readonly`.
- **Type narrowing first**: Prefer type guards, generics, and interface design over `as` casts.
- **Specific types**: Define concrete interfaces instead of `Record<string, unknown>`, `object`, `any`. Use base interface + extends for shared/individual fields. Get approval with justification if loose types are unavoidable.

### Access Modifiers and Naming

- **`public`**: Omit keyword (TS default)
- **`protected`**: Explicit. For subclass-accessed members.
- **`private`**: Explicit. For class-internal members.

Internal-only members are `private` or `protected`.

**`_` prefix**: Applied to `private` and `protected` members (properties and methods). Not applied to `public` members.

**Class member order**: follow `code-style` "Class Member Ordering".

### Uniform Category Conventions

Same-category modules (devices, controllers, drivers) must share the **same level of type definition, naming patterns, and class structure**. Don't make one module specific while others are loose. When upgrading type precision, upgrade all in the category together.

### Typed Event/Callback Systems

Design event bus/emitter/callback systems with **typed EventMap** so callback params are never `unknown`/`any`. Get approval if unavoidable.

### Discriminated Unions for Typed Dispatch

When a value's type depends on another field (command→parameter, action→payload, type→data), use a **discriminated union (DU)** to eliminate `unknown` at compile-time. Assertion helpers only validate at runtime and leave `unknown` in the type system — future code will add `as` casts again.

Group commands by parameter shape; use `Exclude` for the no-parameter remainder. Access `event.command` and `event.parameter` on the object (not destructured) so TypeScript narrowing works. An example is in the reference file "Discriminated Unions for Typed Dispatch".

### Minimize Type Casting

**`as unknown as` (double cast) forbidden.** Fix type design with interfaces/generics.

**`as Type` (single cast)** — depends on data source:

- **Allowed** for internal structured data: hardware comm data parsed by same-codebase drivers, fixed-structure event payloads (open-protocol, ROS), narrowing already-typed function returns. Define interface and cast with `as InterfaceName`.
- **Require type guard or Zod** for untrusted external data: REST API responses, user input, external WebSocket messages, parsed JSON files. **Get user approval before writing new type guards** — don't over-engineer for internal hardware data.

**`undefined!` exception**: Allowed only in `dispose()` for forcing GC reference release.

**Local variable to eliminate `as`**: After type guard, if `as` is still needed (e.g. array index access), assign to local variable first — TS narrows it automatically.

### Honest Nullability + Guard Narrowing

Declare values that can be undefined or null at runtime as optional or nullable, and narrow them with a compound guard (`if (a && b)`). Do not declare them required and force them into place with `as`.

### Code Line Length

Max **120 characters** per line. When breaking multi-item imports/exports across lines, put **one item per line**.

### Generics

When a base class/common module handles **different data structures per subclass/consumer**, use generics. Don't use `Record<string, unknown>`, `any`, or casts. Review existing `any`/`Record<string, unknown>`/casts during conversion for generic replacement.

**Generic classes**: Design common base with generics so subclasses specify concrete types. Callers shouldn't need to specify types manually each time.

### Generic Variance — Plan-Time Check

When introducing a new generic or changing one in a widely-referenced interface, map every usage position of the type parameter as covariant (return position), contravariant (parameter position), or invariant (both), and pre-verify consumer compatibility on stubbed sample code before plan approval. The procedure, the common pitfall, and the preferred workarounds are in the reference file "Generic Variance — Plan-Time Check".

### Common Interfaces

When multiple classes share the same methods, define a common interface with `implements`. Eliminates casts in factories/containers.

### any / unknown Rules

**`any` — Forbidden.** No exceptions.

**`unknown` — External boundaries only, requires user approval.** Before using `unknown`:
1. Trace actual call/data flow to find concrete type
2. Only if truly impossible, explain with justification and get approval
3. Never write `unknown` without approval

External boundaries: hardware raw data, WebSocket messages, JSON.parse results. Internal code (function params, return values, callbacks) must use concrete types.

### `Record<string, unknown>` Minimization

It's a variant of `unknown`. Before using, check: (1) trace actual fields, (2) define interface if known, (3) use recursive types for nested structures, (4) for library options, define known fields + `[key: string]: unknown` for extensibility.

**Allowed**: external API metadata, undocumented library options, generic constraints (`TTags extends Record<string, unknown>`).

### Data vs Policy Separation

Distinguish **data fields** (identifiers, payload — what the entity carries) from **policy fields** (cross-cutting behavior flags — how the entity should be handled). Place policy as dedicated top-level fields at the same level as other policy fields, not inside `metadata` or generic data containers. An example is in the reference file "Data vs Policy Separation".

Anti-pattern: stuffing policy flags into `metadata` ("metadata bloat"). Policy and data have different responsibilities; mixing them violates SRP and obscures intent. When a new cross-cutting policy emerges, add a peer field next to existing policy fields, never widen `metadata`.

### Index Signature Removal

Before removing `[key: string]: T` from an interface, verify **both**:

1. **No dynamic key access** in consumers (`obj[anyKey]`, `Object.keys(obj).forEach(...)`)
2. **No structural-compat consumer** — no caller accepts the type as `Record<string, T>` / `{ [k: string]: T }`, and no field passes it to a parameter of such a type

An interface without an explicit index signature is NOT assignable to `Record<string, T>` even when all field types match — this is a TS structural-typing limitation. Skipping #2 produces TS2345 "Index signature for type X is missing" at consumer call sites after removal. The index sig may *look* dead by access pattern but be load-bearing for structural compat.

### External Library `.d.ts` Declarations

For libraries without `@types/`: methods accepting/returning "anything" should use `unknown`, not `Record<string, unknown>`. The latter forces downstream code to add `[key: string]: unknown` index signatures, causing type pollution.

### Root Cause for Type Errors

Never add `[key: string]: unknown` or `as` casts to **dodge** type errors. When errors cascade (A fix → B error → B fix → C error), find the **cascade start point** and fix that declaration. Multiple places needing index signatures/casts = signal that root cause is elsewhere.

### No Type Suppression Comments

Never use `@ts-expect-error`, `@ts-ignore`, `eslint-disable`. Fix the actual type error.

### No `Function` Type

Define concrete function signatures instead.

### interface vs type

**Object shapes → `interface`**. Unions/intersections/utility types → **`type`**.

### Type/Interface Section — No Blank Lines Between

Type and interface declarations form one section. No blank lines between them. One blank line only after the entire section ends (before const).

### Extract Long Inline Types

When inline types (function types, object types, unions) in parameters/returns/properties get long, extract to `type` alias or `interface`.

### No Repeated Inline Types

When the same type expression (object shape, union, intersection) appears in 2+ places — parameters, return types, `as` casts, local variables — extract to a named type.

- **Object shapes** → `interface` in the provider module
- **Unions/intersections** → `type` alias in the file where constituent types converge; export only if multiple files need it
- Owner: the class/module that produces or stores the data (provider principle)
- Violation signal: identical union repeated in multiple signatures, or inline `as { field: Type }` cast

### No enum — Use const Object

Use `as const` objects with derived types instead of TypeScript `enum`.

```ts
const STATUS = { ACTIVE: 'active', PAUSED: 'paused' } as const;
type Status = (typeof STATUS)[keyof typeof STATUS];
```

### Function Style

- **Class instance methods**: arrow functions (`=`) for safe `this` binding
- **Class static methods**: regular method syntax (`static foo() {}`) — `this` binding is irrelevant for static, arrow form adds no value
- **Module-level utilities**: function declarations

### Initial State / Default Value Pattern

For class instance fields initialized to a fixed default value (e.g. status objects, config defaults), prefer **module-level `const` + spread** over static factory method. An example is in the reference file "Initial State / Default Value Pattern".

**Why:** Module-level const exposes the default shape at the top of the file, eliminates `ClassName.method()` repetition, and matches standard patterns (React initial state, Redux initial state). Use a function instead when the default depends on runtime values or generics.

### null vs undefined

Default to **`undefined`** (compatible with optional params `param?: Type`). Use `null` only for explicit "intentionally empty" (e.g. API response with no data).

### Type File Location

Co-locate types near their module: `types/` folder with `{module}.types.ts`.
- Module-shared types → module's `types/` folder
- Cross-module shared types → a shared types file (e.g. `types/shared.ts`)
- File-internal types → inline at file top

### Definition Ownership in TypeScript

Follow `code-style` "Definition Ownership". When the provider's definition lacks fields, extend it (`extends`, `&`, optional fields) instead of redefining it. For an unconverted JS module, define a temporary interface in the consumer with `// TODO: [module] replace with import after TS conversion`.

### Named Alias for Complex Type Constraints

When `extends` condition in conditional types exceeds one line or same structure repeats 2+ times, extract to a named type alias.

### Conditional Types for Input Shape Detection

Type utilities handling multiple input shapes should use conditional types for automatic detection with a single entry point (like Zod's `z.infer<>`, `Awaited<T>`, `ReturnType<T>`).

### Explicit Return Types

Always specify function/method return types. Don't rely on inference.
