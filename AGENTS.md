# AGENTS.md

The standard any code added to this project must meet — whether written by an AI
agent or a human. It is the contract code is **generated against** and **reviewed
against**.

One principle governs every rule below:

> **A rule must be enforceable.** If a violation can be rationalised as compliant,
> the rule is too soft and is rewritten as a hard line with its failing case shown.

These rules are the **bar**. The mechanical gates (`nox -s green`: ruff, ruff
format, pyright strict, coverage) are the **floor**. Where this document and the
floor overlap, this document is stricter and wins. Several rules here are things a
linter *cannot* see (whether a name is meaningful, whether logic sits in the right
layer); those are the ones that need a generating agent and a reviewer to hold.

The reasoning behind much of this is the body of work on software craftsmanship —
Robert C. Martin's *Clean Code*, McConnell's *Code Complete*, the hexagonal /
ports-and-adapters pattern. This file is not a summary of those; it is the subset
this project enforces, made concrete.

---

## The project in one paragraph (context for placement)

generic-ml-workflow is an engine that runs multi-step workflows where some steps
are plain programs and some are **shots** — interpretable ML calls routed out to
its sibling tool **generic-ml-cache** (`gmlcache`), which is the caller, the cache,
and the replayer. **Invariant 3: this engine executes no model call itself** — every
call goes through `gmlcache` as a subprocess. Everything the engine records is an
append-only event log carrying **pointers (path + sha), never content**; projections
are rebuildable views folded from that log, never a second source of truth.

Today the package is a single distribution (`src/generic_ml_workflow`) split into
two rings with a **hard wall** between them:

```
core/   the engine library: contract, loader, events, orchestrator, the gmlcache
        seam. No terminal code, no surface imports -- enforced by a gate test.
repl/   the terminal frontend: renders and routes. Holds no logic of its own.
```

The target shape (roadmap: the hexagonal restructure) is a **multi-domain hexagon**
— `core` as a pure library, `adapters` (SQLite store, filesystem, the gmlcache
execution adapter), `bootstrap` (the composition root), and driver packages (`repl`,
a future `daemon`) — with the gmlcache dependency behind an `ExecutionPort`. Until
that lands, place new code by the two rules that already hold: **which ring** it
belongs to (`core` vs `repl`), and **the dependency direction** (`repl` imports
`core`; `core` imports no surface). The conceptual model lives in `docs/DESIGN.md`.

---

# Family A — Naming

## 1. Variable names

A name states **what specifically** the value is, not **what kind** of thing it is.
The type is already in the type; the name must add the referent and the role.

- **No abbreviations. No single-letter names. In any binding.** Locals, parameters,
  loop variables, comprehension targets — all held to the same bar. There is no case
  where `p`, `s`, `idx`, `tmp`, `cfg`, `e`, `n` is acceptable.
- **The name answers "of what?"** A name that only restates the type (`path`,
  `data`, `result`, `text`, `value`, `item`, `obj`) is a non-name — a type wearing a
  longer coat, and as much a defect as a single letter.

```python
# WRONG -- type-names and abbreviations; the reader still has to ask "of what?"
s = doc.get(section)
for e in store.replay(execution_id): ...

# RIGHT -- the referent and role are in the name
section_table = doc.get(section)
for event in store.replay(execution_id): ...
```

Disinformation is also banned: do not name a value for something it is not (a
mapping named `…_list`, a count named `…_flag`). The name and the thing must agree.

## 2. Method names

A method name is the **clear intent of the action**: a verb and its object.

- Actions get verbs: `resolve_inputs`, `record_event`, `load_workflow`,
  `usage_from_envelope`. Never a noun for an action.
- A query that returns a value without side effects reads as the question it answers:
  `is_builtin`, `has_grant`, `requested`. Honour command/query separation — a method
  either does something or answers something, not both.
- Vague verbs (`process`, `handle`, `manage`, `do`) are not intents. State the action.

---

# Family B — Structure & placement

Sonar/ruff enforce none of this; a generating agent and a reviewer must.

## 3. Code separation — what lives in its own file

- **One class per file.** A module holds one class (plus the small free functions
  that serve only it). The filename is the snake_case of the class.
- **Data is separated from behaviour at the file level.** A value object / DTO and
  the service that acts on it do not share a file.
- A cohesive family may stay together where splitting would scatter meaning (e.g. a
  single exception hierarchy, or the closed event vocabulary in `eventtypes.py`).
  This is the only exception, and it is about cohesion, not convenience.

## 4. Code positioning — which ring holds what (and the direction)

Place new code by ring, and let the dependency direction decide.

- **`core/` is the engine library.** Domain, the event vocabulary, the loader/
  contract, the orchestrator, the gmlcache seam. It contains **no terminal code and
  imports nothing from `repl/`** — enforced by the wall test (`test_wall`). A `core`
  module that imports `repl`, prompt_toolkit, or anything surface-shaped fails CI.
- **`repl/` is the surface.** It renders and routes; it holds no engine logic. It
  depends on `core`; `core` never depends on it.
- **The engine executes no model call itself (Invariant 3).** Every shot goes through
  `gmlcache` as a subprocess (`core.shotrunner`); the engine builds *what* to call and
  collects the result. It parses no client output — usage is read from gmlcache's
  normalized `run --json` envelope.

*Target state (the restructure): the ring wall becomes a package boundary, `core`
sheds its I/O into `adapters` behind ports, and the gmlcache dependency sits behind
an `ExecutionPort`. New code should already respect "domain logic inward, I/O at the
edge" so the split is a move, not a rewrite.*

## 5. Layer & dependency discipline

- **The log is the single source of truth; projections are derived.** Anything
  queryable is folded from the append-only event log and is rebuildable
  (`rebuild_projections`). A projection that becomes a second authority is a defect.
- **Events carry pointers, never content.** A produced file enters the log as
  `path + sha256`, never its bytes.
- **No run-specific material in a shot's context block.** Timestamps, ids, absolute
  paths must not enter the context an ML step is given (the request builder refuses
  them) — purity is what makes a shot cacheable and replayable.
- **Fail loud in the core.** The engine verifies (a declared output exists, a key
  matches) rather than trusting; a broken assumption raises, it does not pass
  silently.

## 6. Logic placement (domain-driven)

Behaviour lives **on the object whose data it concerns**, not leaked into a service
or an adapter. A use case orchestrates (decides *what* happens in *what order*) and
delegates the *rules* to the domain and the *I/O* to the seam. A method that computes
a value purely from an object's own fields belongs on that object.

---

# Family C — Code-quality floor

Generated code clears these on the first write, so the gate never sends it back.

## 7. Method size & complexity

A function does **one thing**. Mechanical ceilings (treated as hard limits):

- **Cognitive complexity ≤ 15.** Deep nesting is penalised hardest — prefer
  extraction and early return.
- **Nesting depth ≤ 4; return statements ≤ 3; parameters ≤ 7** (more than a handful
  means a parameter object / command is missing); **function length** kept short.

When a function approaches a ceiling the fix is **extract a well-named method**, not
a comment that announces sections.

## 8. Reusability / no duplication (and YAGNI)

- A value or expression built in more than one place becomes **one named method**.
- No duplicated string literals (hoist to a named constant), no copy-pasted blocks,
  no dead or commented-out code, no unused imports / variables / parameters.
- **No code for unbuilt futures.** A symbol with zero callers and no committed plan
  is deleted, not kept "just in case". The line is *callers + a plan*: a stubbed-but-
  wired-and-tested seam on the roadmap is a walking skeleton (keep); a symbol with no
  caller and no plan is a relic (delete).
- **A removed concept leaves no trace.** When a feature is removed, its vocabulary
  goes with it — a grep for the retired name returns nothing in live code, comments,
  docstrings, help text, or docs. (The one exception is a released `CHANGELOG` entry,
  which is a factual record of what shipped.)

## 9. Control flow

- **Guard clauses over buried conditionals.** Test the exceptional case first and
  return early; keep the main path unindented.
- **Compose, don't branch.** Replace `if/elif` ladders that select behaviour with
  injected strategy objects where the ladder will grow.

## 10. Error handling

- **A real, cause-named exception hierarchy** (`WorkflowError`, `ConfigError`,
  `OrchestratorError`, `ShotError`, …). Never raise or catch bare
  `Exception`/`BaseException` except at a deliberate, commented best-effort boundary.
- **Translate foreign errors at the boundary** into the project's own vocabulary; the
  core never leaks a library's exception type.

## 11. Typing & contracts (pyright strict — zero errors)

- **Parse at the edge.** Untyped external input (YAML/TOML/JSON, client stdout, a
  subprocess envelope) is converted into typed objects **once, at the boundary** — an
  `object` parameter narrowed by `isinstance` then `cast` to the concrete shape; the
  core then trusts the types. Dicts of loose strings do not travel into the domain.
- **Frozen objects are deeply immutable.** `@dataclass(frozen=True)` freezes only the
  bindings; a `list`/`dict`/`set` field is still mutable in place. A frozen object's
  collection fields are `tuple[...]` / `frozenset[...]` — anything keyed on or cached
  must be truly immutable.
- **Ship `py.typed`.** The package publishes its types.
- **`# type: ignore` / `# pyright: ignore` is a last resort** — only for a provably
  safe case that cannot be expressed in the type system, with a comment saying why.
  The zero-error pyright pass added none; keep it that way. Prefer a structural fix
  (e.g. hand a collaborator the bound callables it needs) over a suppression.

---

## Using this document

- **Read this file before writing any code.** An agent that skips this step will
  violate rules that are clearly stated here.
- New code is generated to clear every rule above on the first write — not corrected
  toward it afterward.
- **Show, don't assert.** A claim that something is *removed, clean, done, or passing*
  is demonstrated, never asserted — a grep that returns nothing, a green test run, a
  tool report. "I removed all of X" without the search that proves it is how a stray
  name survives.
- **Green means the whole gate.** A change is not green until `nox -s green` passes:
  1. `ruff check .`
  2. `ruff format --check .`
  3. `pyright` — strict mode, **zero errors** (`pyrightconfig.json`).
  4. the test suite across the supported interpreters (py3.11–3.13), and
  5. `nox -s coverage` — coverage floor (≥ 80%).

  Gates 1–3 also run as **pre-commit hooks** (`.pre-commit-config.yaml`). After
  cloning: `nox -s dev`, then `.venv/bin/pre-commit install` and
  `.venv/bin/pre-commit install --hook-type commit-msg`. CI is a thin caller of the
  same nox sessions, so local-green == CI-green byte-for-byte.

- **No AI attribution in commits or pull requests.** Commit messages and PR
  titles/bodies must never contain any reference to the AI tool that produced them —
  no `Co-Authored-By: <assistant>` trailer, no `Generated with …` / `🤖` line, no
  mention of the assistant's name. The history must read as written by the human
  author. Enforced by the `commit-msg` hook.

- **Never work directly on `main`.** Every change — no matter how small — is on a
  dedicated branch. Branch naming:
  - `feature/<scope>` — user-facing capability
  - `tech/<scope>` — internal refactor, tooling, or build change
  - `fix/<scope>` — bug fix
  - `release/<version>` — version bump + changelog only (no code)
  - `docs/<scope>` — documentation only
  - `chore/<scope>` — housekeeping
  Create the branch before touching any file. `main` is only ever updated via a
  merged PR. Enforced by the `guard-branch` hook.

- **Version and release documentation are release-branch-only.** The version (in
  `pyproject.toml` — the single source; this project single-sources from package
  metadata, no separate `VERSION` file), `CHANGELOG.md`, and `docs/ROADMAP.md` are
  modified only on a `release/<version>` branch, and only after the feature work for
  that version has merged into `main`. A release PR touches exactly those files:
  1. `pyproject.toml` — `version` bumped to the new string.
  2. `CHANGELOG.md` — the `[Unreleased]` section replaced with `[X.Y.Z] - YYYY-MM-DD`
     and the notes written under it.
  3. `docs/ROADMAP.md` — the released milestone gains `*(released YYYY-MM-DD)*`.

- This file evolves with the project. When a new structural decision is made, it is
  recorded here as an enforceable line with its failing case, so the standard and the
  code never drift. The hexagonal-placement rules (§4–6) tighten into full ports/
  adapters/import-linter contracts when the restructure lands.
