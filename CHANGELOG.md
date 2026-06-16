# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.x.y` the project is in **alpha** and anything may change
between releases; see [`docs/ROADMAP.md`](docs/ROADMAP.md) for the path to `1.0.0`.

## [0.0.9] - 2026-06-16

### Added
- **Providers — declare and validate (foundation).** A step can declare it needs a
  **provider** — a named external dependency like an issue tracker (a new `provider`
  input requirement). `Workflow.provider_requirements()` gathers the kinds a workflow
  needs, and the token-free warm-up check now refuses to open a run when a required
  provider has no configured instance, failing loud and specific alongside the
  existing run-input / config / credential checks. Reading instances from config +
  credentials and feeding them into a step are the next slices.
- **Providers — configure and consume.** Provider instances are now read from the
  config's `[providers.<kind>.<alias>]` tables (config plane) merged with a separate
  `credentials.toml` (credential plane) via `config.load_providers`. When a step
  declares a provider, the resolved instance's values are env-injected into the
  executable's process as `<KIND>_<KEY>` (e.g. `ISSUE_TRACKER_BASE_URL`,
  `ISSUE_TRACKER_TOKEN`) — the token reaches only the child process, never the
  context, events, logs, prompts, or cassettes (covered by a never-leaks test).
  Per-workflow alias binding, `ctx.fetch` host-pinning, and chmod enforcement remain
  for later slices.
- **Providers — self-describing schema.** A provider can now carry a description
  (meta-code, one YAML per provider in `flows/providers/`): its kind plus a list of
  properties, each tagged `config` or `credential`, with a human description.
  Validation became property-specific — instead of "issue_tracker isn't configured"
  it now says "your instance is missing token (credential)", naming each absent
  required property on the right plane. (`discover_providers`, `load_provider`,
  `ProviderSpec`/`ProviderProperty`; warm-up consults the schema when one exists.)
- **Providers — per-workflow instance binding.** A workflow can now choose *which*
  configured instance a provider kind resolves to, via a `provider_bindings` block
  (`{kind, alias}`). A step still declares only the kind, so the same step can hit
  one instance in one workflow and a different one in another; an unbound kind falls
  back to the `default` (or single) instance, and binding a kind to an unconfigured
  alias fails loud. (`ProviderBinding`, `Workflow.provider_aliases`, `load_providers`
  gained a `bindings` argument.)
- **Providers — the engine's built-in fetch.** A step with entrypoint `builtin:fetch`
  pulls from a provider with no user script: the engine reads the bound instance's
  `base_url` and token, fetches the step's `path` input, and writes the response to
  the step's output. The token is held only in-process — it never enters step code
  and never reaches the event log. The request is **host-pinned**: a path may name a
  resource under the configured base, never another host and never a climb above the
  base path (an escaping path fails the step). (`builtin_bodies` with `pin_url`;
  `run_executable` dispatches built-in bodies; the orchestrator hands it the instance.)
- **Providers — credentials-file privacy enforced.** On POSIX, `load_providers`
  refuses to read a `credentials.toml` that is group- or world-readable (any bit
  outside owner), telling the user to `chmod 600` it, so a token can't sit in a file
  others can see (no-op on Windows). The previously-mentioned per-instance env-var
  override was dropped from scope.
- **Providers — the guarantee test suite.** Beyond the per-feature checks (host-pinning
  on the built-in fetch, fail-clean naming each missing property, no token in event
  payloads), a comprehensive test asserts a provider token appears nowhere in a full
  text image of the entire persisted store — events, executions, gate, and jobs
  tables — which also covers the prompt side, since a prompt can only draw from
  recorded context products.

## [0.0.8] - 2026-06-16

### Added
- **Background execution + live progress.** A `/run` on the interactive terminal now
  advances on a background worker thread and returns the prompt immediately;
  progress (`→ step n/total`, `✓`/`✗` per step, the final outcome) appears above the
  live prompt as the run advances, rendered through prompt_toolkit's patched output.
  The engine gained a side-channel **progress reporter** (`Orchestrator.run(...,
  progress=...)`) — a no-op by default, so it stays synchronous and thread-unaware;
  the surface owns the thread and the rendering. A single run is active at a time (a
  second `/run` is refused until it finishes). Scripted / piped / non-interactive
  runs advance synchronously (no live prompt to render onto), preserving
  deterministic order. Stopping a run is the next slice; this one only adds
  background advancement and notifications.
- **Clean stop (across the engine and the cache).** A run in progress can be stopped
  with `/stop` or by pressing **Escape**. Between steps the engine halts cleanly;
  mid-step it tears down the child the step is running — which, for a shot, is the
  gmlcache subprocess, and gmlcache in turn tears down the client (capability sinks
  to the cache). A stop is recorded as a new `workflow_execution.stopped` event with
  its own `stopped` status — distinct from a failure — and the execution stays
  resumable. The engine gained a `stop=` argument (a `StopControl` the surface
  triggers from the prompt thread); it stays synchronous and thread-unaware. Child
  processes now run in their own process group so one signal reaches the whole tree.
- **Resume a stopped run.** `/resume [<execution>]` continues a stopped or
  interrupted execution — bare, it picks the most recent resumable one. The engine
  rebuilds the run's context-fold (interview answers + artifact pointers) and the
  set of completed steps from the run's own events — a read-model, not a
  re-execution — marks it running again with a `workflow_execution.resumed` event,
  and walks only the unfinished steps on the same execution id. A step that was cut
  off mid-flight simply runs again: the step is the unit of resume, and a fresh run
  folder plus a shot's cache hit make that cheap. `run` and `resume` now share one
  step-walk (`run` is "resume from an empty context"). Resume uses the
  currently-loaded workflow and config, not the originally-stamped commit
  (same-commit time travel is a later slice).
- **Run modes — full-auto and full-manual.** A run's mode is chosen at launch and
  recorded in its start event (so a `/resume` continues in the same mode and it
  survives a restart). `full-auto` (the default) walks straight through;
  `full-manual` (`/run <wf> --manual`) checkpoints after every step — the run pauses
  (stop-and-resume), the prompt comes back, and `/resume` advances one step before
  pausing again. A checkpoint is a resumable pause (rendered as "paused after
  '<step>'", distinct from a stop or a failure); after the last step the run simply
  completes. (`questions-only`, the third mode, is the next slice.)
- **Questions gate — it fires and blocks (`questions-only` mode).** A step asks by
  producing its declared `questions` output (a transport courier, kind `questions`);
  when it does, the engine reads the structured set (`{id, text, blocking}`), records
  a `questions.asked` event, and **blocks** the run awaiting answers — a resumable
  pause, with the questions shown at the prompt. `full-auto` (and a per-step
  `unattended`) sails past untouched (invariant 10); `questions-only` (`/run <wf>
  --questions`) honors it. The gate's own read-model arrives as planned: a new
  `gate_questions` projection table, one row per question per run (pending →
  answered/skipped), projected from the gate events like the other read-models.
  Answering the questions and feeding them back into the run is the next slice.
- **Questions gate — answering and consumption (the loop closes).** `/answer
  [<execution>]` walks a blocked run's pending questions one at a time at the prompt
  (like the launch interview), recording each as `answer.submitted` (answered, or
  skipped for an optional one) and updating the gate table; the questions courier
  file is then swept. Each answer re-enters the run's context under its question id,
  and a new **`answer` input kind** lets a later step declare it consumes a specific
  answer by that id — so a step can ask "tone?", you answer "formal", and a
  downstream step reads `tone` and uses it. `/resume` refuses while a blocking
  question is still unanswered, then continues. This completes the questions gate and
  the 0.0.8 run-modes work; a shot (rather than an executable) consuming an answer is
  a later refinement.

### Changed
- **Documentation — run modes, the execution context, and resume.** DESIGN §7
  defines **run modes** (one run-level selector: full-auto / full-manual /
  questions-only, generalizing the gate and `unattended`); §11 defines the
  **execution context** (a run's live state as a read-model of the projections —
  the engine is stateless, starting is resuming from empty, the step is the unit of
  resume) and the engine's surface-unaware progress-reporter / stop-check shape;
  invariant 10 is generalized to run modes and invariants 23–24 are added. ROADMAP
  reshapes 0.0.8 into *running for real — run modes, background execution, live
  progress, clean stop* (the clean stop spans gmlcache), and records the
  resume-or-new multi-execution launch choice under 0.0.12. Documentation only; no
  runtime change.
- **Documentation — the user / projection model.** DESIGN.md gains §16 (*the user,
  the context snapshot, and projection*) and four matching invariants; §§6, 7, 14,
  15 updated to reference it — caps' playing/accompanying natures, rule-as-projection,
  seed vs app rules, the raw snapshot never reaching the model, the placement
  taxonomy resolved, and stale-rule detection on snapshot change. ROADMAP records
  the 2026-06-15 design notes. Documentation only; no runtime change.

## [0.0.7] - 2026-06-13

### Added
- **Tab-completion for `/run` and `/validate` workflow names.** Pressing Tab after
  `/run` (or `/validate`) now offers the workflow names found in the flows folder,
  matching as you type. The names are cached at startup and refreshed on `/list`
  and `/run`; the completer itself stays pure (it reads the in-memory list).
- **Advisory gmlcache-version check at startup.** The engine drives gmlcache the
  way its 0.0.7 release established (the cache owns its store; the engine passes no
  `--store` / `--output-dir`). At launch the engine now relays `gmlcache --version`
  and, if it reads older than 0.0.7, prints a one-line advisory — never blocking,
  and silent whenever the version cannot be read ("cannot verify, don't guess",
  the same rule as detection). Pure version parse/compare; the probe is injectable.
- **Per-step tier override at run time** (DESIGN.md SS9). `/run <flow> <step>=<tier>`
  runs a chosen step at a different tier than its spec declares, for that run only.
  It's a user decision that changes the run, so it's recorded as a new
  `tier.overridden` event (actor `user`, scoped to the step, carrying `from_tier`
  / `to_tier`) — and **only** when the chosen tier actually differs from the
  declared one, since a no-op override changes nothing. Overrides are validated
  before the run (unknown step, non-shot step, or unknown tier each stop the run
  with a clear message rather than being silently dropped). The override decides
  which tier the shot resolves against; the concrete client/model it lands on stays
  gmlcache's, captured in the shot, not duplicated into the event.
- **Tier reconciliation, detection-driven** (`core.reconcile`, DESIGN.md SS9):
  the engine now anticipates a workflow's failure *before* it runs by comparing
  the user's configured `[tiers]` against what gmlcache reports is actually
  installed. Two advisory checks, each only as far as the cache can see:
  **missing client** (a configured tier names a client `gmlcache doctor` does not
  report present) and **stale model** (a configured tier names a model the client
  no longer lists, via `gmlcache models`). When gmlcache cannot enumerate a
  client's models, the model check is skipped -- "cannot verify" is never reported
  as "model is gone." Nothing is gated; the list is advisory, the run is the
  truth.
- The relay grew a **models probe** (`core.detect.discover_models` +
  `parse_models_output`), the `gmlcache models --json` counterpart to the existing
  `doctor` relay -- pure parse, graceful on every failure path (returns `None`,
  i.e. "cannot verify", never a crash or a false warning).
- The free check (client presence, from the `doctor` data already in hand) runs at
  **startup** -- silent unless a configured tier is unreachable. The new **`/tiers`**
  command runs the full reconciliation on demand, fetching the model listings to
  add the drift check, and prints the whole tier→client/model/effort mapping.


## [0.0.6] - 2026-06-13

### Added
- The shot **request envelope** (`core.envelope`, DESIGN.md SS8):
  `[context, prompt, files]`, with **purity enforced in the builder** -- the
  run-agnostic context prefix is refused if it carries run-specific material
  (timestamps, absolute paths, execution/session ids), because an impure prefix
  would shatter every downstream cassette key and the client's prefix cache. The
  prompt and files may legitimately carry run-specific material; the context may
  not.
- The **gmlcache seam** (`core.shotrunner`): runs an interpretable step (a shot)
  by building the `gmlcache run` argv (client/model/effort, the context/prompt
  files, the envelope's input-files, the mode) and invoking it in the step's
  isolated run folder, then collecting the declared outputs. The engine passes no
  `--store` and no `--output-dir`: as of **gmlcache ≥ 0.0.7** the cassette store is
  the cache's own (config-owned) concern, and gmlcache writes produced files into
  its working directory (the run folder), exactly as the client would -- so the
  engine dictates neither location. argv construction is pure and unit-tested; the
  subprocess is injectable. gmlcache's passthrough is preserved -- stdout/stderr/
  exit captured, an offline cache miss surfaces gmlcache's error verbatim. The
  engine still executes no model call itself (invariant 3).
- The orchestrator runs **interpretable (shot) steps** through the seam: when a
  step is a shot, it builds the `[context, prompt, files]` envelope (cap/
  methodology as the run-agnostic context, bound artifact products as input
  files), resolves the tier to a concrete client/model via a supplied
  `ShotConfig` (full tier reconciliation is 0.0.7), runs the shot, and feeds the
  product into the context-fold — so a shot can consume an earlier step's output.
  Without a `ShotConfig`, a shot step still stops honestly. `run_shot` is
  injectable, so the path is tested without a real client.
- **Tier resolution from config** (`config.load_tiers`): the optional
  `[tiers]` section maps each abstract tier (high/medium/low) to a concrete
  `{client, model, effort?}`. `/run` reads it, builds a real `ShotConfig`
  (cassette store under `state_dir/cassettes`), and passes it to the
  orchestrator -- so a REPL user can now run a shot workflow end to end. The
  mapping is the user's: the clients share no tier nomenclature, so nothing is
  seeded, and an unconfigured tier stops the shot with a clear message.
  Pulled forward from 0.0.7; detection-assisted seeding/reconciliation against
  installed clients remains a later slice.

## [0.0.5] - 2026-06-12

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
- `/run` goes live: lists runnable workflows, selects one (named or the sole one),
  validates it, drives the **computed interview** at the prompt (the union of
  unsatisfied run-inputs), reads the flows-repo stamp, and runs the orchestrator
  against the real event store in the workspace — showing per-step progress and
  the completed/failed/stopped outcome. `/replay` then shows the real story.

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

[Unreleased]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.7...HEAD
[0.0.7]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.6...v0.0.7
[0.0.6]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.1...v0.0.2
[0.0.1]: https://github.com/danielslobozian/generic-ml-workflow/releases/tag/v0.0.1
