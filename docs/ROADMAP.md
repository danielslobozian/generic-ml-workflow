# generic-ml-workflow — roadmap

The path from 0.0.1 to 1.0.0, in deliberately small slices. One slice = one
release = one independently testable increment. The conceptual model lives in
[`DESIGN.md`](DESIGN.md); this file only sequences it.

**Standing gate for every slice:** `ruff check` + `ruff format --check` + `pytest`
(offline, token-free) + secret-audit. CI green before tagging.

**Cross-project dependencies** on [`generic-ml-cache`](https://github.com/danielslobozian/generic-ml-cache)
are marked **⇗ gmlcache**. When a slice needs a gmlcache feature that doesn't exist
yet, the gmlcache slice ships first.

---

## Where we are

**0.0.8 — running for real** is the latest release: a run advances on a background
worker while the prompt stays live, picks a mode at launch (full-auto / full-manual
checkpointing / questions-only), can be **stopped** cleanly (the teardown cascades
into gmlcache) and **resumed** from its own event log, and the **questions gate**
lets a step ask, block, take the user's answers, and feed them into later steps.
0.0.1–0.0.8 are published; next up: **0.0.9 — credentials and roles** (a credential
role declared by a step, satisfied at launch, with the token never transiting a
model call — the handling sinks toward gmlcache).

---

## Design notes — decided 2026-06-13

Settled directions captured so they aren't lost; the slice that builds each is
noted inline.

- **Layered step config.** A step's effective config resolves through layers,
  most-specific wins: step default → per-workflow-use override → run-time override.
  The run-time override shipped in 0.0.7 is the bottom rung. A step *declares* which
  knobs are overridable — behavioural (tier / effort / force-pause), never structural
  (id / ports / outputs; changing those is a different step). An override is a thin
  delta on a step's *use*, never a mutation of the shared definition. (Needs reusable
  steps + a per-workflow override block — a 0.1.x slice.)
- **Standalone step authoring** is the target shape: steps (and caps) are first-class
  files referenced by workflows, not embedded inline. Inline definitions in the
  workflow YAML are early-slice scaffolding; the end state is authored `steps/` /
  `caps/` / `workflows/` (foreshadowed by reusable-step ports and 0.2.2 generated
  bodies). Lands across 0.1.x.
- **Inspect-pause knob (0.0.8).** Beyond a `questions` gate, a step may be marked to
  always pause so the user can open and inspect its outputs before the run continues
  — a distinct reason to pause from "the step asked a question". Folds into the 0.0.8
  gates / unattended design.
- **Human-handoff is just an executable step.** A step that prepares something, hands
  off to the user, then resumes needs no new *kind* of step — an executable step
  already runs any program and blocks until it exits. The only open question is making
  the attended case ergonomic (0.0.8), not a new primitive.
- **Pre-1.0 readability pass.** Before 1.0, a dedicated pass renames for intent
  (descriptive, format-revealing identifiers; no single-letter or type-named variables)
  and restructures toward one-thing-per-unit. Code from 0.0.7 on is already written that
  way; the back-catalogue gets the pass. Open choice for that pass: adopt a lean
  permissively-licensed typed-decode helper (e.g. msgspec, BSD) for JSON→typed payloads
  vs. keep stdlib helpers — decided by how many complex nested payloads exist by then.
  gmlcache stays zero-dependency regardless.

---

## Design notes — decided 2026-06-15

The user / projection model, captured in DESIGN.md §16; the slice that builds
each is noted inline.

- **Rules are the user's projection onto a cap; caps are generic.** A cap ships as
  a generic role carrying no user; a run becomes *yours* by rules accrued onto it,
  never by mutating it — authored ships, accrued stays. "Digital me" is a bound
  result, never authored or exported. (Foundation: caps v1, **0.1.0**; full model
  in DESIGN §16.)
- **Placement taxonomy resolved** (supersedes the old §15 open question): a
  correction is a rule on a cap; a "user rule" is a rule on the user-cap; a
  non-generalizing fix is a per-binding tweak (layered-config override), not a
  rule. (DESIGN §15, §16.)
- **Personal context is a snapshot, projected per cap.** Seed rules are extracted
  from the snapshot at warm-up (one cached interpretive shot); the raw snapshot
  never transits a model call. The snapshot is user-owned and app-immutable.
  (Builds on context buckets, **0.1.1**; warm-up extension, **0.1.0–0.1.1**.)
- **Warm-up cap personalization.** Per cap, warm-up offers generic / project-my-
  context / play-it-myself; "play it myself" is the per-role face of the 0.0.8 run
  modes. (**0.1.0–0.1.1**, after the 0.0.8 modes.)
- **Export with a personal-flagging pass.** Exporting a locally-built workflow
  ships the authored caps and their seed rules clean, strips bound personal
  context, and marks accrued rules as probable-personal by default — the user's
  call to keep or drop. (**0.1.2**, with `/export`.)
- **Automatic rule proposal, by a per-step observer.** A short-lived observer
  judges how a step went and proposes the rule; which observer fires (a playing
  cap's vs the companion's) routes the result to professional vs personal. Enriches
  the existing automatic-rule-proposal item. (Tuning loop **0.1.6+**; companion
  side **0.3.x**.)
- **Stale-rule detection on snapshot change.** Editing the context snapshot
  surfaces the seed rules it touched, before/after, for the user to keep / revise /
  drop — never silent. (Tuning loop, **0.1.6+**.)

Open, not yet decided: a shared "professional-you" base beneath role-caps; the
bucket boundaries inside personal context; whether elicitation is a cap nature or
a companion sub-kind.

---

## 0.0.x — the runtime, dumb and solid

### 0.0.1 — a home that opens
The app exists, installs, launches, and is honest about what it can't do yet.
- Package skeleton (`src/` layout), version single-sourced from package metadata.
- **The wall, from the first commit:** two packages — core (library, no terminal
  code) and repl (renders + routes, no logic) — with an import-rule test in the
  standing gate: core importing repl or anything terminal-shaped fails CI.
- REPL entry point: banner (name, version), `gmlcache` detection
  (`doctor --json` relay; graceful, advisory message when absent), `/help`,
  `/quit`; every other verb answers "not yet — see the roadmap."
- Full public scaffold: Apache-2.0 + NOTICE, SPDX headers, CoC, SECURITY,
  GOVERNANCE, CONTRIBUTING, CHANGELOG, CI matrix, issue/PR templates, dependabot;
  local-only secret-audit / identity scripts (gitignored).
- **Tests:** banner snapshot via scripted-input harness; version single-source;
  doctor-relay parsing against frozen fixtures; the gmlcache-absent path.
- **Decide here:** final command name (`gmlworkflow` vs shorter).

### 0.0.2 — config + first-run interview
- OS-standard config path + `GMLWORKFLOW_CONFIG` as the only override; precedence
  flag-equivalents > env > file > default, mirroring gmlcache conventions.
- First-run interview: standard folders / single folder / custom; writes the
  documented, comment-seeded config and creates the folders; subsequent launches
  go straight to the banner. `/status`: each effective setting with its source.
- **Tests:** pure path-resolution table; scripted interview (all three choices);
  precedence table; never-write-unasked.

### 0.0.3 — workflow definitions load and validate
- YAML loader → validated `Workflow⟨InputType⟩` contract: typed inputs, ordered
  steps, per-step declared requirements (run-input / configuration / credential
  role / artifact), outputs, tier, nature.
- **Ports and bindings** (DESIGN.md §7): step inputs/outputs are local port
  names; the workflow's binding block is the only wiring. Load errors with
  precise messages: unbound required port; a binding naming a product nothing
  contributes; duplicate product names. The **dead-branch lint** (a durable
  output no later step consumes) as a validation warning. `/list` (definitions
  found, with their input types), `/validate <flow>` (graph + requirements
  readiness, token-free).
- The bundled demo workflow definition ships (not yet runnable).
- **Tests:** loader happy path; every distinct contract violation; binding/graph
  wiring errors; the dead-branch warning; bundled demo validates.

### 0.0.4 — the event spine + the executable building blocks
- Append-only SQLite event store + projections; events carry pointers
  (path + sha256), never content. The typed, self-describing event vocabulary
  (closed enum, payload beans, registry, `event_types`/`describe`); the
  `execution_id` historization key; `workflow_executions`/`jobs` projections with
  the commit/branch/engine-version stamp; project-on-append; `rebuild_projections`.
- `/replay <execution>` and `/status` read real (if sparse) data.
- **Execution stamping primitive:** `core.stamp` reads the flows-repo commit/branch
  (read-only; "unversioned" when not a repo), plus the engine version — the
  DESIGN §13 foundation, because append-only history cannot be back-filled.
- **The executable-step runner** (`core.runner`, user-supplied origin): an isolated
  per-step run folder, declared-input materialization, declared-output collection
  + fingerprinting, honest failure on a missing output.
- **Tests:** append/replay round-trip; projection correctness; pointer-only
  payload rule enforced; vocabulary round-trip + self-description; stamp reading;
  runner isolation/collection/failure.

### 0.0.5 — executable steps run (first end-to-end run, zero ML)
- The **orchestrator**: opens an execution (mints the id, reads the stamp, emits
  `workflow_execution.started`), runs the **computed** run interview (the union of
  the steps' unsatisfied run-inputs, each emitted as `run_input.provided`), the
  **warm-up** (verifies configuration/credential requirements before step one,
  token-free), then walks the steps maintaining the **context-fold** — resolving
  each bound artifact port from the context, running executables via `core.runner`,
  emitting `step.*` and `artifact.created`, durable outputs landing in the workspace.
  Stamps every execution from day one.
- `/run`: lists workflows, drives the interview at the prompt, shows per-step
  progress; `/replay` now lights up with real stories.
- Demo phase 1 runs: a supplied script fetches a public web page → a transform
  step extracts text. Two steps, wired by a binding, no model involved.
- **Tests:** the computed interview; warm-up readiness; context-fold resolution;
  a scripted full demo run end-to-end; `/replay` of a real execution.

### 0.0.6 — interpretable steps run (the gmlcache seam)
- The shot path: engine builds `[context, prompt, files]`, resolves the step's
  tier to a concrete client/model via the `[tiers]` config (below), invokes
  `gmlcache run --context-file … --prompt-file … --store …` in the step's run
  folder, collects stdout/stderr/exit + produced files into the spine.
- Engine-side purity enforcement v1: the request builder refuses run-specific
  material (timestamps, ids, absolute paths) in the context block.
- Demo phases 2–3: analyze the fetched page (shot) → generate a summary file
  (shot). **Cassettes recorded once and committed**; CI runs the whole demo
  offline. **⇗ gmlcache** ≥ 0.0.7 — the cache owns its store; the engine passes no
  `--store`/`--output-dir`, so an older gmlcache (whose default store is the cwd)
  would lose replay.
- **Tier resolution from config (pulled forward from 0.0.7):** a `[tiers]`
  section maps `tier → {client, model, effort?}`; `/run` reads it into a real
  `ShotConfig` so shots run from the REPL with **no stub**. Unseeded -- the
  clients share no tier nomenclature -- so an unconfigured tier stops the shot
  honestly. Detection-assisted seeding/reconciliation stays 0.0.7.
- **Tests:** seam argv construction (never executed); purity violations rejected;
  full demo offline via committed cassettes; cache-miss-in-offline surfaces
  gmlcache's error verbatim.

### 0.0.7 — tier reconciliation (detection-driven)
- The `[tiers]` config + pure `resolve` landed in 0.0.6; this adds the
  **detection** layer on top.
- Startup reconcile against installed clients: seed a default for a detected
  client, warn on configured-but-absent, warn on stale model (free drift
  check); clients/models relayed via `gmlcache doctor` / `gmlcache models`.
- Per-step tier override at run time, recorded on the execution.
- Advisory **gmlcache version check**: warn — never block, the engine does not
  refuse to launch — when the detected gmlcache is below the floor the engine
  needs (currently `≥ 0.0.7`, since an older one's cwd-relative store silently
  loses replay). Reuses the `gmlcache --version` probe the launch check already
  runs; purely informational.
- **Tests:** reconcile table (seed/keep/warn×2); per-step override recorded as
  an event.

### 0.0.8 — running for real: run modes, background execution, clean stop
- **Run modes** — one run-level selector, three positions: full-auto / full-manual
  / questions-only (DESIGN §7). Questions-only is the `questions` gate (present →
  block, ask, record the answer as an event, sweep; absent → proceed); full-auto
  generalizes per-step `unattended` to the whole run; full-manual checkpoints after
  every step.
- **Background execution + live progress** — a run advances on a background worker
  while the prompt stays live and typable; the engine announces advancement through
  a caller-supplied **progress reporter**; the surface renders it. The engine stays
  synchronous and thread-unaware (DESIGN §11; invariant 24).
- **Clean stop (spans gmlcache)** — the user can stop a run; at a boundary the
  engine simply halts, and mid-step it signals the cache subprocess, which tears
  down its client. The step is recorded interrupted and the run stays resumable.
  **⇗ gmlcache: graceful stop on signal must land** (see the cache roadmap) — the
  engine signals, the cache owns the teardown.
- **Execution context + resume** — a run can be resumed; its live state is read from
  the projections (DESIGN §11); starting is resume-from-empty; the step is the unit
  of resume.
- Deferred: per-step / per-cap mode refinement (layered config, 0.1.x); the launch
  chooser's multi-execution presentation (relates to jobs, 0.0.12).
- **Tests:** questions-only round-trip (block / answer / resume; sweep; answers in
  `/replay`); full-auto bypass; a background run emits progress events and leaves
  the prompt responsive; stop at a boundary; stop mid-step cascades and records the
  step interrupted; resume rebuilds the execution context from the projections.

### 0.0.9 — credentials and roles
- Role config + separate `credentials.toml` (chmod 600, enforced), env-var
  override per role; `needs: [role]` → env injection for user-supplied
  executables; the never-in-events/logs/prompts/cassettes guarantee, tested.
- A built-in `fetch` body using `ctx.fetch(role, path)` (host pinned by the
  role's `base_url`; token never enters step code).
- **Tests:** chmod enforcement; fail-clean per missing role; redaction
  everywhere; host-pinning (a path cannot escape the base_url).

### 0.0.10 — cost
- `/cost`: per step / per execution / per job, in tokens + usage units.
- Usage comes from gmlcache's normalized result envelope so the engine parses no
  client output. **⇗ gmlcache: "normalized usage in run --json + cassette"
  must ship first** — the engine slice blocks on it.
- **Tests:** aggregation from fixture envelopes; cache-hit reports recorded
  usage; unknown-usage degrades gracefully.

### 0.0.11 — the validation ladder, rungs 2–3
- The recorded probe: one tiny shot per unique `(client, model, effort)`,
  verdict stored as an event, re-probe on demand or when stale; real-run errors
  surfaced verbatim.
- **Tests:** probe verdict events via injected runner; staleness; verbatim
  surfacing.

### 0.0.12 — jobs become persistent
- Create/select a job at `/run`; executions regroup by job; `/cost` and
  `/replay` take a job as subject; resume an interrupted execution.
- **The launch chooser**: launching on an input offers — resume an existing
  execution or start a new one, with several executions coexisting on the same
  workflow + input (each keeping its own documents) and the user free to switch
  back to an earlier one and resume it. The choosing/showing is the surface's; the
  engine only exposes list / start-new / resume (invariant 1).
- **Tests:** regrouping; resume from event stream; cross-launch persistence.

---

## 0.1.x — the workspace grows up

- **0.1.0 — versioned schema + caps v1.** The event/config/workflow schemas get a
  versioned document; caps (persona + methodology + context flags) compiled into
  the envelope; rules bound to caps (static binding; the applicability matrix
  stays deferred).
- **0.1.1 — context compilation + compression.** Context buckets, compile-time
  compression through cacheable shots (**⇗ gmlcache** as the cache for
  compilation calls), the full purity discipline. The per-bucket compress-prompts
  (user / organization / rules, plus the workflow-mechanics prompt repurposed for
  methodology compression) are **meta-code** in the user's flows repo — versioned,
  tunable; a prompt edit re-keys the compression cassette and naturally triggers
  recompression, while unchanged content + unchanged prompt = cache hit = free.
  Each bucket declares its compression policy (`light` / `exact` / `aggressive`);
  validation = deterministic anchor/ref/structure checks after compression, plus
  an optional recorded original-vs-compressed probe — with the standing caveat
  that no validator can prove an exact constant survived character-for-character
  (exact-policy buckets keep the eyeball-first ritual).
- **0.1.2 — documents.** Catalog over a content-addressed store; scratch-space
  migration on step completion; `/export` renders the foldered bundle (the only
  way files leave the app).
- **0.1.3 — REPL ergonomics.** Completion everywhere, history, per-step live
  progress, `/doctor` deep view.
- **0.1.4 — stable public Python API.** Load-bearing, not a nice-to-have: this is
  the seam the family's third project (the user-friendly chat application built on
  this engine) will stand on. Every verb and projection reachable without the
  REPL; the REPL itself is rebased onto this API to prove it. **Decision point:**
  whether core and repl split into separate repos/distributions — deferred until
  here on purpose, taken here with a real second consumer in sight.
- **0.1.5 — time travel.** The git-driving layer over the user's meta-code repo:
  stamped-commit resume (offer checkout, render the diff since, resume the event
  stream against the system as it was), per-session stash capture/restore, and
  **safe mode** (committed state only, learning loop off). Builds entirely on the
  stamps recorded since 0.0.5.
- **0.1.6 — the pre-launch manifest + cache frontier.** The tuning loop's surface
  (DESIGN.md §15): before a step fires, show the envelope's bill of materials —
  every context source with its token weight — and the toggleable transforms
  (compression stages, input-side styles, the output-side brevity instruction,
  tier/model/effort override), each with its live delta. The engine walks the
  transformation chain and the step chain in **cache-only mode**: deterministic
  stages recomputed live, ML stages asked of the cache (never the client), the
  **frontier** rendered — cached / fresh-from-here / unknown-past-here — with a
  pre-launch cost forecast as `/cost`'s twin ("this run ≈ N fresh calls +
  M replays"). Probing is progressive: downstream steps resolve as upstream
  outputs materialize; a re-run of a recorded job can probe the whole chain.
  **⇗ gmlcache: a cache-probe ("check"/cache-only resolve: compute the key,
  answer hit / miss / non-cacheable + cassette metadata, launch nothing) must
  ship first** — the slice blocks on it.

## 0.2.x — creation mode (the compiler)

- **0.2.0 — specs as data.** Authored step specs (portable text) load, attach to
  steps, round-trip.
- **0.2.1 — spec analysis.** ML-assisted analysis of a spec: deterministic vs
  judgment parts; proposes the split (always-separate enforced); proposes caps.
- **0.2.2 — body generation, opt-in.** Generate a Python body from a deterministic
  spec into the user's flows folder; regeneration; never auto-adopted — the user
  adopts (and may review) or declines and stays pure-ML.
- **0.2.3 — creation REPL flow.** The authoring interview: new workflow, new step,
  edit spec, regenerate.

## 0.3.x — the companion (inside the 1.0 bar)

- **0.3.0 — natural-language routing for the workspace prompt.** Intent → the
  closed verb set, via a cheap shot; unroutable input degrades to `/help`, never
  to execution. The verb set stays closed; routing adds no vocabulary.
- **0.3.1 — the companion surface.** `/companion` shows/hides the chat inside
  the workspace. Its own light context: a personal profile (tone, style of
  work) + a narrated digest from the event log — never the work session's
  compiled context. Resolves through its own tier-config entry (any client, any
  model). Conversations stored as documents, included in export.
  **⇗ gmlcache: a conversational passthrough path** (chat turns are unique;
  caching them is pointless) — to be specified on the gmlcache roadmap before
  this slice.
- **0.3.2 — the companion acts.** Routed actions onto the allow-listed verbs,
  with **state-awareness**: the companion answers "what can I do from here?"
  from the projections, and step outputs / gate questions surface in the chat
  as they happen (the multi-actor view).

## 1.0.0

Ships when: the demo workflow (and a second, executable-heavy example) run
end-to-end attended and unattended; offline CI covers the full loop; schemas are
versioned and documented; the Python API is stable; creation-mode authors a
working step from a spec; **and the companion converses, narrates the work, and
acts within the fence**. Alpha label drops.

## Post-1.0 (explicitly not the mission yet)

- **The third project**: a clean, chat-first, user-friendly application for
  non-engineers, built on this engine's stable API — conversational workflow
  creation (interview, credential setup, live step testing), running and managing
  workflows from a graphical surface. A separate repo; this engine is its backend.
- API-call steps: the engine sets up the call, **⇗ gmlcache v2 HTTP/API proxy**
  is the caller and the cache. Includes the tuning-loop payoff: side-by-side
  CLI-subscription vs API comparison of the same step (speed / quality / price)
  for steps that need no filesystem scanning.
- The rules applicability matrix + automatic rule proposal from the event log.
- An `antigravity` adapter — in gmlcache, where adapters live.

## Graveyard — do not re-propose

- Lossy local compression (LLMLingua-2): measured ~30–40% behavioral loss on the
  rules context, non-instructable (cannot be told to preserve exact constants),
  and its one argument — free tokens versus a paid native call — dissolves once
  compression shots are cached: native costs one call per content version, then
  replays free forever. Native-only; no compression-backend seam.
- Runtime pricing scrapers; pricing as the route to model lists.
- Single-provider-per-execution (contradicts stateless shots).
- A hybrid runtime step (executable + interpretable in one atom).
- Client adapters, client-output parsing, or a memo cache inside this engine.
- A human usage model based on shell arguments ("launch the binary with the
  workflow name and flags") — humans enter the REPL, always; anything
  cwd-relative.
- An open verb set or free-form command execution from any natural-language
  surface.
- A second-terminal companion process — superseded by the in-app `/companion`
  surface; the log remains the only medium between surfaces.
- Sending the work session's compiled context to the companion — the separate
  light context is the companion's reason to exist.
- Model self-report as a dependency-tracking mechanism.
