---
name: python
description: Python rules applied when writing or modifying .py files. Automatically referenced when creating or editing Python code.
user-invocable: false
---

# Python Rules

Apply these rules together with `code-style` when writing or modifying Python code, including plans and example code. For `.py` files this skill overrides `code-style` where they conflict: naming, operator placement in wrapped lines, braces, and the other layout rules in "Overrides of code-style and the JavaScript rules".

## Tooling

In a uv project (a `pyproject.toml` with a `uv.lock`), run every command through `uv run` so the versions pinned in the lockfile run, such as `uv run pytest` or `uv run python -m <package>`. Add dependencies with `uv add` (`uv add --dev` for development tools). Never use `pip install`, and do not create or activate virtual environments by hand. Standalone scripts outside a uv project keep the invocation their own instructions give. Run a tool that is not a project dependency with `uvx <tool>` only when it is a well-known tool, because `uvx` installs whatever package name it is given.

Check changed files in this order; lint fixes such as import sorting change structure that formatting then settles:

1. `uv run ruff check --fix <files>`
2. `uv run ruff format <files>`
3. `uv run mypy --strict` (or the project's mypy configuration with `strict = true`): the final gate, with 0 errors
4. `uv run pytest`

Preview `--unsafe-fixes` with `--diff` and review them before applying. In a project without ruff configuration, where `ruff format --diff` would rewrite whole files, do not reformat; keep fixes to the code you change. Fix type and lint errors at their source instead of adding `# type: ignore` or `# noqa`. This is the Python form of `code-style` "Post-Change Verification".

## Formatter-Owned Layout

`ruff format` decides line wrapping and the blank lines around definitions, and its output wins over hand style and over `code-style` "Line Wrapping Style". Use the project's `line-length` (120 to match `code-style`). Verified with ruff 0.17.4 on 2026-11-09:

- A wrapped boolean condition goes inside parentheses with `)` on its own line, and `and`/`or` start each continuation line. The formatter has no option to change this, and PEP 8 (the official Python style guide) recommends breaking before binary operators in new code.
- The formatter joins items that fit on one line inside the brackets. To keep one item per line, put a trailing comma after the last item (the "magic trailing comma"); this works for parameters, arguments, collections, and parenthesized imports.
- Two blank lines surround top-level functions and classes, one blank line separates methods, and blank lines at the start of a block are removed.
- The formatter never splits a string literal, even past the line length. Keep it on one line instead of splitting it with `+` or implicit concatenation. A project that keeps this rule leaves ruff's line-length rule (`E501`) unselected, since the formatter already owns line length and cannot shorten such a line.

## Hand Rules the Formatter Keeps

The formatter keeps the blank lines written inside function bodies (collapsing runs to one), so these `code-style` rules stay manual:

- Consecutive assignments of local variables form one block, followed by one blank line, even before `return` ("Blank Line After Variable Declarations").
- No other blank lines inside a function body ("No Unnecessary Blank Lines Inside Methods").

## Naming

Follow PEP 8 instead of `code-style` "Method Naming Convention":

- Functions, methods, variables, modules, and packages: `snake_case`.
- Classes (including enums, exceptions, and protocols) and type aliases: `PascalCase`.
- Module constants and enum members: `UPPER_SNAKE_CASE`.

Python has no access modifiers, so `code-style` "Minimum Access Exposure" is expressed through the name: every non-public module member, method, and attribute (private and protected alike) starts with a single `_`, and public names never do. Use a double leading underscore only to avoid attribute clashes with subclasses, as PEP 8 describes.

`code-style` "check vs is Naming", "Avoid Abbreviations", "Avoid Filler Prepositions in Identifier Names", and "Concrete Verbs for Identifiers" apply unchanged in `snake_case`: `check_connection()` raises on failure and returns `None`, and `is_connected()` returns a `bool`.

## Control Flow and Comparisons

- **Explicit else** ("Always Write Explicit else") and **no void return or guard raise** ("No Early Void Return / Guard Throw") apply unchanged. A project that follows this skill turns off ruff's RET505–RET508 (superfluous `else` after `return`, `raise`, `continue`, or `break`), because they require the opposite.
- **Returning `None`:** a function annotated `-> T | None` ends every path with a return, so write `else: return None` where the JavaScript rule `typescript` "No Unnecessary `return undefined`" would omit the branch. Otherwise mypy reports a missing return statement and ruff RET503 flags it.
- **Comparisons:** compare values with `==` and `!=`, and `None` with `is None` and `is not None` (PEP 8), in place of `typescript` "Strict Equality Only".
- **Truthiness** (`typescript` "Nullish Checks"): use `if value:` when `0`, `""`, and empty collections are not valid values, and `is None` when they are.
- **Braces** ("Control Flow Braces") do not apply. Indentation delimits blocks, and each body stays on its own line; the formatter splits `if x: y` anyway.

## Imports

Put every import at the top of the file (PEP 8). Order them as standard library, third-party packages, then first-party modules, with one blank line between groups, as ruff's isort rules (`I`) enforce. This replaces `typescript` "Import Ordering", which puts no blank lines between groups.

## Constants and Enums

Express a fixed set of string values as a `StrEnum` and a single value as a module constant. `code-style` "Repeated Literals → Constants", "String Literal Constants", and "Constant Location by Scope" otherwise apply unchanged.

## Comments

`code-style` "Minimize Comments + Explicit Naming" applies unchanged: no comments that restate what the code does, and no section dividers (`# ---`, `# region`). Docstrings pass the same test: write one only when it states why, or a contract that names and type hints cannot show.

## asyncio

These are the Python forms of the JavaScript async rules in `typescript` ("Remove Unnecessary async", "Sync Return When Signature Allows", "await Over Promise Chains", "Floating Promises in Fan-out Callbacks", "Await Only Thenables", and "throw Over Promise.reject"):

- **No `async` without `await`:** declare `async def` only when the body uses `await`, `async for`, or `async with`; otherwise write `def`. When a callback type accepts both a plain and an awaitable return, write the plain function.
- **No floating coroutines or tasks:** await every coroutine, since calling a coroutine function without `await` never runs it (mypy reports `unused-coroutine`). Keep every `asyncio.create_task()` result in its owner and await or cancel it before the owner stops: the event loop holds tasks only weakly, so an unreferenced task can be garbage-collected mid-run and its exception is never seen (ruff RUF006 flags a discarded task).
- **Mis-declared sync functions:** a `def` that starts a task and neither returns it nor hands it to an owner should be an `async def` that awaits the work.
- **Structured fan-out with per-result handling:**
  - Independent items, where one failure is logged and the rest go on (the `Promise.allSettled` case): `await asyncio.gather(..., return_exceptions=True)`, then check each result with `isinstance(result, BaseException)`, which also narrows its type for mypy.
  - All-or-nothing, where one failure cancels the rest: `async with asyncio.TaskGroup() as group:`, handling the resulting `ExceptionGroup` with `except*`.
  - Sequential: a `for` loop with `await` and `try`/`except` per item.
- **No callback chains:** use `await` with `try`/`except` instead of `add_done_callback()` to read results or handle errors (the counterpart of `.then()`/`.catch()`); an error handled in a callback never reaches the caller's `try`/`except`.
- **Await inside the function:** write `return await self._send()`, so this function's `try`/`except` covers the call, and raise errors with `raise`. Create a `Future` by hand (`loop.create_future()` with `set_result()`) only to bridge a callback-based API, never for work an `async def` can express.
- **Cancellation:** catch `Exception`, not `BaseException` or a bare `except:`, so `asyncio.CancelledError` propagates; when cleanup must catch it, re-raise it.

```python
async def load_pages(urls: list[str]) -> list[str]:
    results = await asyncio.gather(*(load_page(url) for url in urls), return_exceptions=True)
    pages = []

    for url, result in zip(urls, results, strict=True):
        if isinstance(result, BaseException):
            LOGGER.warning("Could not load %s", url, exc_info=result)
        else:
            pages.append(result)
    return pages
```

## Overrides of code-style and the JavaScript rules

| `code-style` or `typescript` rule | Python rule |
| --- | --- |
| `code-style` "Method Naming Convention" (camelCase) | `snake_case`, with `PascalCase` classes and `UPPER_SNAKE_CASE` constants |
| `code-style` "Line Wrapping Style" (trailing `&&`/`\|\|`) | Formatter output: leading `and`/`or`, and a magic trailing comma for one item per line |
| `code-style` "Control Flow Braces" | Not applicable |
| `typescript` "Strict Equality Only" | `==` for values, `is None` for `None` |
| `typescript` "Import Ordering" (no blank lines between groups) | ruff `I` groups with one blank line between them |
| `code-style` "Blank Line Between Method/Function Definitions" and `typescript` "Type/Interface Section — No Blank Lines Between" | Two blank lines around top-level functions and classes, one between methods |
| `typescript` "No Unnecessary `return undefined`" | `return None` on every path of a `-> T \| None` function |
| `code-style` "Minimum Access Exposure" (access modifiers) | A single `_` prefix for every non-public name |

## Sources

- PEP 8 application decided by the user (2026-11-09). Formatter, linter, and type-checker behavior verified with ruff 0.17.4 and mypy 2.4.1 on 2026-11-09.
- The uv and ruff tool rules adapt lines from Astral's `uv` and `ruff` skills (`astral-sh/claude-code-plugins`, commit `f3ce88a`, MIT License, Copyright (c) 2025 Astral Software Inc.). Astral's `ty` skill is not used: it replaces mypy, which remains the type checker.
