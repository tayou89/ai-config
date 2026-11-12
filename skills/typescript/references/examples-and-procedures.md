# TypeScript Examples and Procedures

Long examples and rarely needed procedures for the `typescript` skill. The skill's rule of the same name applies; this file adds detail.

## Discriminated Unions for Typed Dispatch

```ts
type CmdValue = (typeof CMD)[keyof typeof CMD];
type Event = { sid: string } & (
    | { command: typeof CMD.MOVE; parameter: number }
    | { command: typeof CMD.SET_ID; parameter: string }
    | { command: Exclude<CmdValue, typeof CMD.MOVE | typeof CMD.SET_ID>; parameter?: undefined }
);
```

## Generic Variance — Plan-Time Check

TypeScript generic types' variance is determined by how the parameter is used:

- **Return position** (`get(): T`, `metadata?: T`, `() => Promise<T>`): **covariant** — `Container<narrow>` → `Container<wide>` is assignable
- **Parameter position** (`set(x: T)`, `func(t: T)`): **contravariant** — `Container<wide>` → `Container<narrow>` is assignable
- **Both** (`(x: T) => T`, or any field where `T` is in both an argument position and a return position): **invariant** — only exact match assignable

**Plan-time rule**: When introducing a new generic or changing one in a widely-referenced interface, map every usage position of the type parameter explicitly:

```
Task<T>:
  metadata?: T               → covariant
  resolvedMetadata?: Resolved<T>  → covariant
  resolve?: () => Promise<T> → covariant
  Result: covariant, Task<narrow> assignable to Task<wide>
```

If any position is "both" (or any function has `T` in an argument position), expect invariance — pre-verify consumer compatibility on stubbed sample code before plan approval.

**Common pitfall** — function field with `T` in argument:

```ts
interface Step<T> {
    func: (x: Resolved<T>) => void;  // ← Resolved<T> in argument position
}
interface Container<T> {
    steps: Step<T>;  // ← propagates argument-position usage of T
}
// → Container<T> is invariant — Container<Narrow> NOT assignable to Container<Wide>
```

Sibling generics (collections containing `Container<NarrowT>` typed as `Container<WideT>[]`) won't compile. This is a frequent silent-failure pattern when refactoring lazy callbacks.

**Workarounds (order of preference)**:
1. **Restructure**: keep `T` in return positions only (callbacks don't accept `T` as argument)
2. **Method-level generic**: shift variance to call site with `method<U extends T>(x: U)`
3. **Boundary cast in one controlled location** (last resort, only when 1 and 2 are infeasible)

Generic variance is the most common cause of "plan compiles in isolation but fails when integrated" — always include variance mapping in plans that touch generic interfaces.

## Data vs Policy Separation

```ts
interface Task<TMetadata> {
    code: string;
    inputEnable?: InputEnable;     // policy: user-input behavior
    cancelOnTimeout?: boolean;     // policy: cancel behavior (correct: top-level, peer of inputEnable)
    metadata?: TMetadata;          // data: orderId, customerId — identifiers/payload
}
```

## Initial State / Default Value Pattern

```typescript
// ✓ Preferred
const INITIAL_STATUS: Status = { a: 0, b: 0, c: 0 };

class Foo {
    private _status: Status = { ...INITIAL_STATUS };
    reset = (): void => { this._status = { ...INITIAL_STATUS }; };
}

// ✗ Avoid (verbose, class-name repetition)
class Foo {
    private static _createInitial(): Status { return { a: 0, ... }; }
    private _status: Status = Foo._createInitial();
}
```
