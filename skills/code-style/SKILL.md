---
name: code-style
description: Coding style rules applied when writing or modifying code. Automatically referenced when generating or editing code.
user-invocable: false
---

# Coding Style Rules

Apply these rules when writing or modifying code. **Applies everywhere including plans, explanations, and example code.** No exceptions. For `.py` files the `python` skill overrides these rules where they conflict.

## Design-First Proposal

Before proposing any code change (fix, feature, refactor), grep the codebase for 2-3 analogous patterns solving similar problems (lifecycle pairs like enable/disable, register/unregister; state-based dispatch; owner API exposure; cleanup/cancel chains). Map every caller path of the change (normal completion / error / cancel / dispose / lifecycle transitions) — when paths share a function but carry different intent, branch by *state* or *parameter* explicitly rather than apply blanket behavior. Audit checklist before proposing: (a) consistency with existing analogous patterns, (b) separation of concerns (who owns the action), (c) lifecycle symmetry (every register has matching unregister), (d) idempotency (safe to call twice), (e) caller surface (signature change ripple count). Reject first working solution if any item fails; surface a structurally cleaner alternative and present multi-axis comparison.

## Standards Over Workarounds

- Always prefer **industry standard / best practices** when writing, modifying, or suggesting code.
- Resolution order: (1) find the standard approach first, (2) workarounds (casts, non-null assertions, loose types) only when standard is technically impossible, (3) if workaround needed, explain why standard is impossible and get user approval.
- **Never rationalize workarounds as "legitimate" or "acceptable"** — if it deviates from standard, classify it as a workaround and plan to fix. Effort never justifies keeping it (CLAUDE.md "Proposals and Options").
- **Definitions are root causes**: When casts, assertions, or workarounds exist because a type/interface/schema definition is wrong or incomplete, the definition IS the root cause — fix it first. Never classify a workaround as "unavoidable" without verifying the underlying definition is correct and complete.
- **Never settle for "first working solution"**: Before proposing any fix, refactoring, or design change, evaluate at least 2 alternatives (plans: at least 3, see `code-plan` 4a). Check standard language features before introducing new types or cross-module imports. Verify dependency direction, ownership, and naming. Present the best solution, not the first one found.
- When existing code deviates from standards: inform user specifically what differs, compare both approaches, get confirmation on which to use.
- When standard is unclear, use widely-adopted patterns (official language documentation, major framework conventions). If still unclear, ask user.
- **Compile-time > runtime**: When both solve the same problem, prefer compile-time enforcement (types, generics, discriminated unions) over runtime checks (assertions, guards). Compile-time catches prevent future rework; runtime checks leave loose types in the type system, inviting the same issue to recur.
- **YAGNI applies to hypothetical future requirements only**: Adopting an industry-standard pattern (Observer, Strategy, AbortController, dependency injection, etc.) for the current case, or matching the codebase's existing pattern, is **not** premature abstraction — it is correct expression. SRP/OCP/DRY-compliant separation is not "abstraction"; classifying it as such to justify hardcoded duplication is misuse of YAGNI. Apply YAGNI only against speculative generality ("might need 5 cases later", "configurable for future"), never against present-tense standard patterns.

## Definition Ownership

All types, interfaces, constants, enums are defined and exported by the **provider**, not redefined inline by consumers.

- Server interfaces → server files; DTOs → receiver or shared type files; component or device config → that component's files; common structures → shared definition files
- Find inline redefinitions → move to provider and replace with import
- No duplicate definitions. Search for existing ones first; extend the existing definition if fields are insufficient (TypeScript specifics in `typescript` "Definition Ownership in TypeScript").
- **Derived constants** (arrays/maps derived from a const, e.g. the list of a const object's keys or values): if **multiple consumers** use the same derivation → define in provider; if **single consumer** → define in that consumer file (don't preemptively pollute provider API).

## Control Flow Braces

All control statements (`if`, `else`, `for`, `while`, `do`) use braces `{}` with the body on a new line. Never omit braces or write single-line bodies.

## No Early Void Return / Guard Throw — Use if-else or Check Functions

Value-returning early returns (`return false`, `return 0`) are allowed. **Void returns and guard throws to exit a function are not.** Single condition: use if-else. Multiple conditions: extract to a check function (see check function pattern below).

## Positive Conditions First

Prefer positive conditions over negated (`!`) conditions. Exception: simple conditions without else where negation is natural.

## Always Write Explicit else

When if branches return values or diverge processing, **always wrap the last branch in else**. Don't fall through past if to return.

## No Unnecessary `return undefined`

When return type includes `undefined`, don't write `else { return undefined; }` or `else { return; }`. Just omit the else block.

## Strict Equality Only

Always use `===`/`!==`. Never use `==`/`!=`.

## Nullish Checks

For null+undefined checks, prefer in this order:

1. **Truthy check** (default): `if (value)` / `if (!value)` — when `0`, `""`, `false` are NOT valid values
2. **Explicit comparison** (when `0`/`""`/`false` are valid): `value === undefined || value === null` (or `value !== undefined && value !== null`)

## File Top Structure: import → type → interface → const

Order file top sections with **one blank line** between sections:
1. **import / require**
2. **type aliases** (`type Foo = ...`)
3. **interface definitions** (`interface Foo { ... }`)
4. **const/let declarations**

## Import Ordering

Order imports by group, no blank lines between groups:

1. **Node.js built-ins** (`events`, `fs`, `path`, `net`, etc.)
2. **External packages** (`axios`, `lodash`, `ws`, etc.)
3. **Internal modules** (relative `./` `../` imports)
4. **Internal constants** (large destructured imports from a shared constants module)
5. **`import type`** (always last)

Multiline destructured imports (4+ items) use one item per line with trailing comma.

## Imports at File Top Only

All `import`/`require` at file top. No inline `import()` or `require()` mid-code. No `import('path').Type` inline type references. If inline import is unavoidable (circular dependency), explain and get user approval.

## Line Wrapping Style

When wrapping a line over 120 chars: **one item per line** (destructuring, literals, params, args, imports) and **trailing operator** (`&&`, `||` at line end, not line start). Exceptions: type union `|` stays **leading**, ternary `?`/`:` stays **leading**, intersection `&` stays trailing. For long `if`/`while` conditions, break right after `(` and put `)` on its own line. Do not split string or template literals with `+`; wrap the call structure and keep the literal on one line, even past 120 characters.

## Type/Interface Section — No Blank Lines Between

Type and interface declarations form one section. No blank lines between them. One blank line only after the entire section ends (before const).

## Blank Line After Variable Declarations

Consecutive `const`/`let`/`var` declarations form **one block**. No blank lines within the block. **One blank line after the block ends** — even before `return`. Applies to all blocks (function body, if, else, try, catch). Applies regardless of initializer form (simple value, object literal, multi-line callback).

## Blank Line Between Method/Function Definitions

Always one blank line between class members, methods, and function definitions.

## Class Member Ordering

Order class members:

1. **Static properties** (public → protected → private)
2. **Static methods** (public → protected → private) — type order below
3. **Instance properties** (public → protected → private)
4. **Constructor**
5. **Public instance methods** — type order below
6. **Protected instance methods** — type order below
7. **Private instance methods** — type order below

Static members come first because they're class-level utilities available without instantiation; instance code (including field initializers) may reference them.

Within each access level, order method groups as (Angular/React/C++ convention — lifecycle first, predicates last):

1. **Lifecycle** (in order): `dispose*` → `create*` → `init*` → `start*` → `stop*` → `reset*` → `remove*` → `clear*`
   - `dispose` is the destructor counterpart to constructor — placed at the very top of each access section.
2. **Event/communication**: `on*`, `handle*`, `*EventHandler`, `send*`, `notify*`
3. **Accessors**: `get*`, `set*`
4. **Data operations**: `add*`, `update*`, `find*`, `parse*`, `make*`, `record*`, `alloc*`/`unalloc*`
5. **Misc** — methods not matching any listed prefix, grouped by their own prefix
6. **Predicates** (near bottom): `has*`, `is*`, `check*` (individual → composite)

Same-prefix methods must be adjacent. The numbered order above is the default; adjust if a file's domain makes a different grouping more natural, but never scatter same-prefix methods.

## Method Naming Convention

Method and function names use camelCase. PascalCase is reserved for classes, interfaces, types, and enums only.

## Avoid Filler Prepositions in Identifier Names

Avoid filler prepositions (`of`, `in`, `at`, `on`) in identifier names when they don't disambiguate. Use compound-noun form.

- `getNameOfPerson` → `getPersonName`
- `enableRetryOfUpload` → `enableUploadRetry`
- `listOfItems` → `items` or `itemList`

Keep meaningful prepositions (`to`, `from`, `by`, `with`) that express direction or relationship:

- `convertToString` (direction) ✓
- `parseFromJson` (origin) ✓
- `getOrderByCustomerId` (relation) ✓
- `mergeWithDefault` (combine) ✓

## Minimum Access Exposure

Prefer the most restrictive access modifier: private > protected > public. Use public only for methods **actually called from outside the class hierarchy**. Use protected for methods used by subclasses. Default to private for everything else — including `get*`/`set*`/lifecycle methods with no external callers. Promote to public when needed; demotion is harder than promotion.

**No speculative exceptions**: interface implementations must match the interface. Everything else follows the "no external caller → private" rule strictly.

**Underscore prefix**: All non-public members (both `private` and `protected` — methods *and* fields) use the `_` prefix. Public members never use `_`. Rationale: the prefix gives a visible cue at call sites (`this._foo`) so consumers can spot accidental access to non-public API without jumping to the definition. Renaming a member's visibility therefore also renames it (and updates all call sites in the class hierarchy).

## No Unnecessary Blank Lines Inside Methods

Blank lines inside method bodies are allowed **only after variable declaration blocks**. No blank lines between execution statements, before/after `}` blocks, or before/after comments. Applies to nested blocks too.

## Remove Unnecessary async

Don't add `async` to functions that don't use `await`.

## Sync Return When Signature Allows

When the signature accepts both sync and async (`() => Promise<T> | void`), write sync logic without `Promise.resolve()` wrap or `async` keyword.

## Avoid Redundant State Checks

Don't evaluate the same state predicate twice in one code path; restructure to mutually exclusive `if-else-if` with early returns.

## State Owner Lives with Usage

Keep shared runtime state (registries, counters, flags) inside the producing closure; expose a single callable handle to outside callers instead of threading adapter closures through parameters.

## Concrete Verbs for Identifiers

Use concrete verbs that describe the specific action (add/remove, mark/unmark, register/unregister, enable/disable), not abstract verbs (watch, track, handle, manage) that delegate meaning to context.

## Extract Multi-Line Inline Arguments

Function arguments spanning 2+ lines (object literals, multi-property configs, multi-line arrow callbacks) should be extracted to a named local variable so the call site stays single-line and the name documents intent.

## Prefer const

Use `const` unless the variable is actually reassigned. `let` only for reassigned variables.

## Repeated Literals → Constants

String/number literals with structural meaning that appear **2+ times** should be defined as constants. Match the existing constant definition pattern (e.g. object form).

## Constant Location by Scope

- Referenced from multiple files → shared constant file (e.g. `constants.js`)
- Used in one file/class only → file top or class internal

## Pure Helper Placement

Place pure helpers (no `this`) in the narrowest consumer scope: single-class → class member; multi-function in module → module-level const; cross-module → shared util. Within a class follow the Class Member Ordering rule.

## await Over Promise Chains

Use `async/await` + `try/catch` instead of `.then().catch()`.

## Floating Promises in Fan-out Callbacks

Inside fire-and-forget callbacks (forEach without await, event-emitter listeners, setInterval): never leave a Promise unhandled. Use `await Promise.allSettled([...].map(fn))` with per-result `if rejected` log for parallel fan-out, or `for-of + await + try/catch` for sequential. Per-promise `.catch()` chaining is forbidden — violates "Don't mix await with .catch()" and returns void instead of propagating reject to the caller's try-catch.

## Await Only Thenables

Never `await` a function with `void` or non-Promise return — silent no-op masking a sync/async mismatch. Verify callee's return type before adding `await`. If the callee body holds floating Promises (`.catch()` chains, fire-and-forget), the callee itself is mis-declared sync and must be converted to `async` with proper internal `await`; conversely, if a function is declared `async` but its body has no real await target, demote it to sync.

## Avoid Redundant Wrappers

Don't define a wrapper function (helper, adapter, `safely*`, `try*`) that duplicates a guarantee the wrapped function already provides — call it directly. Before introducing a wrapper, verify the wrapped function's try-catch scope, return guarantees, idempotency. If the wrapper adds nothing the original doesn't already offer, it's noise; fix the wrapped function instead (e.g. extend its try-catch scope to cover the missing area).

## Extract Long Inline Callbacks

Callbacks passed as parameters that exceed ~5 lines should be extracted to separate methods/functions.

## Direct Method Reference for Callbacks

Pass method references directly to callback APIs instead of wrapping in a lambda that just invokes them. Wrap only when args/return need adapting or extra logic is required. JS/TS: `setInterval(this._refresh, ms)` not `setInterval(() => { this._refresh(); }, ms)`. Same principle for other languages with first-class functions (Python `Timer(5, self.refresh)`, Java `this::refresh`, C# `this.Refresh`).

## try-catch Scope

Default: wrap **entire function body**. Partial try-catch only when error handling differs by section. Don't nest try-catch with identical error handling. Don't mix `await` with `.catch()`. Don't nest try-catch — extract inner logic to a separate method. Don't write consecutive try-catch in the same scope — merge into one try-catch or extract to separate methods.

**Handler/callback functions** (event handlers, setInterval/setTimeout, Express middleware): wrap **everything including variable declarations** in try-catch since errors can't propagate upward.

**Auxiliary functions** (logging, monitoring, metrics, diagnostics): when called inside another function's logic, must contain their own try-catch internally so they never propagate errors to the caller. Auxiliary work must not break primary logic.

## Variable Scope at try-catch Boundaries

`let`/`const` declared inside `try` are not visible in `catch`/`finally` (block-scoped) — declare them outside `try` when shared across blocks, and inside the block when used only there.

## User-Friendly Error Messages

Write error messages that non-technical people can understand. Avoid internal terms (tags, registers, instances).

## Avoid Abbreviations

Use full words in variable names, function names, and **type/interface names** unless the name becomes unreasonably long.

## Value Names Follow Source of Truth

When a data source (backend API, protocol, external system) already defines value names (enum values, status strings, constants), consumers use the same names directly. Never create consumer-specific aliases that require a mapping layer (e.g. backend `'FULL'` → frontend `'fullCharge'`). Display labels that differ from data values belong in the rendering layer (e.g. a label map for UI text), not as renamed data values.

## check vs is Naming

| Prefix | Role | Returns | Example |
|--------|------|---------|---------|
| `check` | Precondition validation, throws on failure | `void` | `checkConnection()` |
| `is` | State query | `boolean` | `isConnected()` |

check functions validate only — they don't return values. If you need the value, call check then get separately.

## Check Function Pattern

**Individual check**: validates one condition, throws on failure, returns void.
**Composite check**: combines individual checks. Don't inline conditions in composite — call individual checks. Place at class bottom: individual → composite order.

Use in methods: common preconditions via composite, method-specific conditions by combining composite + individual. Even single-use preconditions should be extracted to check functions. Don't branch check calls with if-else in method body — use composite with parameters.

## throw Over Promise.reject

In async functions: use `throw` not `Promise.reject`. Use `await this.send()` not `return this.send()`. Never use `new Promise(async (resolve, reject) => ...)` anti-pattern.

## Minimize Comments + Explicit Naming

No comments explaining "what" code does — use clear names instead. Only allow comments explaining "why". **Section divider comments are forbidden** in all forms (text, lines, emoji, `// ===`, `// ---`, `// MARK:`, `#region`, etc.).

## String Literal Constants

Repeated or meaningful string literals should be defined as `const` constants. Don't use string literals directly in function args, event types, identifiers.

## Minimize Duplicate Code

Extract identical code into shared functions. In switch-case: if all cases produce same result, put it outside switch. If results differ, set in each case.

## Name by Purpose, Not Input Shape

Name functions/utilities by **what they produce**, not what input shape they take. Same role + different input shapes → one name, handle shapes internally (overloads, conditional types, type guards).

## Function Name over Literal Argument

If an argument is always a literal at every call site (never dynamic), encode it in the function name instead of passing it (e.g. `openReadStream()` / `openWriteStream()` rather than `openStream('read' | 'write')`).

## No Duplicate Logic

Logic repeated in 2+ places → extract to a method. Check existing codebase for same patterns before writing new code.

## Boilerplate Belongs on the Owner

When two or more clients perform the same multi-line guard/narrow/query pattern against the same owner's internal state (current task, config, flag; e.g. `owner.getCurrent()` → `if (!== undefined && !== SENTINEL && .flag)` → invoke), the boilerplate belongs on the owner as a typed API, not as a shared helper outside the owner.

- Anti-pattern: two modules define `_registerXxxHandler` private methods, each performing identical narrowing on `jobQueue.getCurrentJob()`. → Promote into `jobQueue.registerXxxHandler(callback)` and call sites become one line.
- Anti-pattern: multiple consumers each implement the same `find by id + null check + cast` lookup. → Expose `findX(id): X | undefined` on the owner.

Rule of thumb: client code accessing owner internals with guards is a signal the owner is missing an API. Add the API on the owner.

**General API + specialized helper coexist on the owner**: When the owner already exposes a general API (`listenX`, `on`, `emit`) and clients wrap it with the same guard/check boilerplate, add a specialized helper on the owner that takes only the action-specific callback. The general API stays for arbitrary handlers; the specialized helper takes one line at the call site. Threshold: ~5+ lines of repeating boilerplate across 2+ clients warrants the specialized helper; less than that, direct use of the general API is fine.

## Named Alias for Complex Type Constraints

When `extends` condition in conditional types exceeds one line or same structure repeats 2+ times, extract to a named type alias.

## Resource Cleanup in Owner, Not Caller

Callback release, listener removal, schedule cancellation — place cleanup logic inside the method that owns the resource, not in callers. Otherwise other call paths may miss cleanup.

## Logging Quality

- Log constant/config values once at initialization, not on every call.
- Log state after the action (or in `finally`), not before — pre-action logs capture stale state.
- Skip logging when nothing meaningful changed (idle/zero state). Only log when there's actual activity or warning conditions.

## Post-Change Verification

After completing code changes, run the project's linter on changed files (TypeScript: `typescript` "Build Quality"; Python: `python` "Tooling"). Report new errors and warnings to user. Changes must not increase the lint error/warning count compared to before the change.

Then show the changes to the user and get confirmation **before running an output-generating build** (e.g. `npm run build`, `tsc`). Never run such a build without user review. Commits follow `git-commit`, which needs no approval for routine work (user decision, 2026-11-09).

## Pre-Commit Checks

A pre-commit check examines what is being committed — the index git uses, including a private `GIT_INDEX_FILE` — not the working tree; copy the index out with `git checkout-index` and check that copy.
