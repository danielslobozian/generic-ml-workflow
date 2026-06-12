# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.x.y` the project is in **alpha** and anything may change
between releases; see [`docs/ROADMAP.md`](docs/ROADMAP.md) for the path to `1.0.0`.

## [Unreleased]

### Added
- The **orchestrator** (`core.orchestrator`): runs an executable workflow end to
  end (zero ML). Opens an execution (mints the historization id, reads the
  meta-code **stamp**, emits `workflow_execution.started`), seeds the **context**
  from the run interview's answers (`run_input.provided`), then walks the steps
  maintaining the **context-fold** — resolving each bound artifact port from the
  context, running executables in isolated per-step run folders, emitting
  `step.*` and `artifact.created` (pointers), and adding durable products to the
  context. Closes with `workflow_execution.completed`/`failed`. A token-free
  **warm-up** verifies run-input / config / credential readiness before step one;
  an invalid workflow never runs; a step failure stops the run; an interpretable
  (shot) step stops honestly (the gmlcache seam is 0.0.6). Demo phase 1 — two
  executable steps wired by a binding — runs end to end in tests.

## [0.0.4] - 2026-06-12

### Added
- The typed, self-describing event vocabulary (`core.eventtypes`, DESIGN.md SS11):
  a closed engine-owned `EventType` enum, a typed payload **bean** per type
  (the single declaration of each type's shape, used by writer and rebuilder so
  they cannot drift), a registry, and a self-description capability
  (`event_types()` / `describe()`) -- the Swagger-for-events backbone. Payloads
  carry scalars, references (meta-code by name + commit), and pointers (path +
  sha) only; parsing fails loudly on an unknown or missing field.
- The event store wired to the typed vocabulary (`core.events`): the append-only
  log (sole source of truth) plus project-on-append read-models (`jobs`,
  `workflow_executions` with the commit/branch/engine-version stamp), all in one
  transaction (immediate consistency, no refresh). The run **historization key**
  (`execution_id`, minted in code) groups a run and doubles as the execution PK;
  `replay(execution_id)` returns typed events in order; `rebuild_projections`
  reconstructs every read-model from the log alone. A discipline test enforces the
  pointer-only payload rule (invariant 11): no payload bean may inline content.
- `/replay` reads the real event store: bare `/replay` lists recorded executions
  (honest empty state until `/run` exists in 0.0.5), `/replay <execution>` (full
  id or a unique prefix) renders the stamp and the ordered event story. `/status`
  now also shows the event-log database path.

## [0.0.3] - 2026-06-12

### Added
- Mandatory dependency gate at launch: the app checks for **git** (the versioning
  spine) and **gmlcache** (the execution arm) before the workspace opens, and
  refuses to start with a clear remedy if either is missing. The check is an edge
  concern in the launch wrapper (`core.deps`), never the workspace, so the
  workspace stays testable without those binaries. `core.deps.check()` is reusable
  for a future `/doctor` status view.
- The first-run interview now initializes the flows folder as a git repo (the app
  drives git over your meta-code for versioning and time-travel) and seeds a
  `.gitignore`. An existing repo at that path is left untouched.
- Workflow definitions load and validate (DESIGN.md SS7). The YAML loader builds a
  typed `Workflow<InputType>` contract; steps declare **local ports** with a
  requirement kind (run-input / config / credential / artifact), and the
  workflow's **bindings** block is the only wiring. `Workflow.validate()` is a
  token-free deduced-correctness pass: errors for an unbound required port, a
  binding naming a product nothing contributes, a duplicate product name, or a
  binding to a later product; and the **dead-branch lint** (a durable output no
  later step consumes) as a warning. `/list` (definitions in your flows folder,
  with their input types; honest empty state) and `/validate <flow>` go live.

### Changed
- The "home opens even without gmlcache" behavior of 0.0.1 is superseded: gmlcache
  is now required to open (it is the only path to any model call). Client
  *detection* stays advisory; the gmlcache *install* is mandatory.
- The flows-dir config comment now states that the app creates and drives the git
  repo, rather than asking the user to.

## [0.0.2] - 2026-06-12

**Config + the first-run interview.** The app now has its one fixed location and
asks before it ever writes.

### Added

- Config resolution from the OS-standard path (`~/.config/gmlworkflow/config.toml`
  on Linux; platform equivalents elsewhere), overridable by exactly one
  environment variable, `GMLWORKFLOW_CONFIG`. Per-setting precedence, mirroring
  gmlcache conventions: session override > environment (`GMLWORKFLOW_FLOWS` /
  `GMLWORKFLOW_STATE` / `GMLWORKFLOW_WORKSPACE` / `GMLWORKFLOW_BANNER`) > config
  file > built-in default. Unknown sections/keys are kept, not rejected; an
  unparseable file or wrong-shaped value is reported loudly at launch and the
  workspace survives on defaults without writing anything.
- The first-run interview: no config found → the app proposes (1) standard OS
  folders, (2) one single folder for everything, (3) custom paths — and writes
  the config only after you answer, then creates the answered-for folders. The
  written config is documented inline (seeded values, allowed values, precedence,
  env names). Skipping (EOF or an invalid choice) writes nothing; defaults carry
  the session and the interview returns next launch. Custom paths must be
  absolute (`~` allowed): the app is location-blind and resolves nothing against
  the cwd.
- `/status` goes live: the config file in use (or its absence, or its brokenness)
  and every effective setting with its value and source
  (session / env / config / default).
- `/banner <style>` now persists: an explicit choice updates the `[ui] banner`
  line in place, preserving the rest of the file byte-for-byte (comments
  included); without a config file the switch is session-only and says so.

### Changed

- `core.paths.resolve()` removed; effective locations now come from
  `core.config.load(...).as_paths()` with full source tracking.

## [0.0.1] - 2026-06-12

The first slice: **a home that opens**. The app exists, installs, launches, and is
honest about what it can't do yet.

### Added

- The REPL workspace as the product: launching `gmlworkflow` (no arguments — there
  is no argument-driven usage model) lands at a banner, a detection pass, and a
  prompt with a closed verb set. `/help`, `/quit`, `/clients`, and `/banner` are
  live; every other verb (`/run`, `/list`, `/validate`, `/replay`, `/status`,
  `/cost`, `/export`, `/companion`) is an honest stub that names the roadmap slice
  that brings it to life.
- gmlcache detection by relay: startup asks `gmlcache doctor --json` which clients
  are installed and presents the answer. Advisory and graceful on every path —
  gmlcache absent, erroring, or unparseable all produce a friendly message and the
  workspace still opens. This engine carries zero client knowledge, by design.
- The core/repl wall, from the first commit: the package is a surface-agnostic
  library (`core`) plus a thin terminal frontend (`repl`), and an import-rule test
  in the standing gate fails CI if the core imports the repl or anything
  terminal-shaped.
- Version single-sourced from package metadata; `pyproject.toml` holds the only
  hardcoded number.
- Engine groundwork (library-only in this slice, not yet reachable from the REPL):
  the append-only SQLite event store with projections (its slice is 0.0.4), the
  step contract with the two natures — interpretable / executable — and tiers (its
  slice is 0.0.3), the YAML workflow loader (0.0.3), and the location resolver
  with the one fixed config path, `GMLWORKFLOW_CONFIG`-overridable (its
  interview slice is 0.0.2).
- Full public scaffold: Apache-2.0 + NOTICE, SPDX headers, Code of Conduct,
  SECURITY, GOVERNANCE, CONTRIBUTING, CI across ubuntu/macos/windows × Python
  3.11–3.13 (tests + ruff check + format check), issue/PR templates, dependabot.

[Unreleased]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.4...HEAD
[0.0.4]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/danielslobozian/generic-ml-workflow/releases/tag/v0.0.1
