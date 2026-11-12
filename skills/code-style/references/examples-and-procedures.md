# Code Style Examples and Procedures

Long examples and rarely needed procedures for the `code-style` skill. The skill's rule of the same name applies; this file adds detail.

## Boilerplate Belongs on the Owner

- Anti-pattern: two modules define `_registerXxxHandler` private methods, each performing identical narrowing on `jobQueue.getCurrentJob()`. → Promote into `jobQueue.registerXxxHandler(callback)` and call sites become one line.
- Anti-pattern: multiple consumers each implement the same `find by id + null check + cast` lookup. → Expose `findX(id): X | undefined` on the owner.

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

## Pre-Commit Checks

A pre-commit check examines what is being committed — the index git uses, including a private `GIT_INDEX_FILE` — not the working tree; copy the index out with `git checkout-index` and check that copy.
