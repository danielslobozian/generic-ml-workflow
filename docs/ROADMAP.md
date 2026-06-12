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

**Pre-0.0.1.** Nothing published yet.

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

### 0.0.4 — the event spine
- Append-only SQLite event store + projections; events carry pointers
  (path + sha256), never content. Run/StepExecution records.
- `/replay <execution>` and `/status` read real (if sparse) data.
- **Tests:** append/replay round-trip; projection correctness; pointer-only
  payload rule enforced.

### 0.0.5 — executable steps run (first end-to-end run, zero ML)
- The executable nature, **user-supplied origin**: declared invocation, isolated
  per-step run folder, declared-output collection, declared-input materialization.
- **Execution stamping from day one:** every execution records the meta-code
  commit/branch (when the flows folder is a git repo; "unversioned" recorded
  honestly otherwise) and the engine version. The time-travel machinery comes much
  later; the history it needs starts here, because append-only history cannot be
  back-filled.
- The run interview: `/run` lists workflows, then asks for the **computed**
  interview — the union of the steps' unsatisfied run-inputs — with validation;
  the **warm-up** verifies configuration requirements before step one fires;
  progress per step; outputs land in the app workspace.
- Demo phase 1 runs: a supplied script fetches a public web page → a transform
  step extracts text. Two steps, wired by a file, no model involved.
- **Tests:** isolation (the invocation sees only its run folder); output
  collection; missing-declared-output is a step failure; scripted full run.

### 0.0.6 — interpretable steps run (the gmlcache seam)
- The shot path: engine builds `[context, prompt, files]`, resolves nothing fancy
  yet (explicit client/model in the demo config), invokes
  `gmlcache run --context-file … --prompt-file … --store …` in the step's run
  folder, collects stdout/stderr/exit + produced files into the spine.
- Engine-side purity enforcement v1: the request builder refuses run-specific
  material (timestamps, ids, absolute paths) in the context block.
- Demo phases 2–3: analyze the fetched page (shot) → generate a summary file
  (shot). **Cassettes recorded once and committed**; CI runs the whole demo
  offline. **⇗ gmlcache** ≥ 0.0.4 (input files) — already shipped.
- **Tests:** seam argv construction (never executed); purity violations rejected;
  full demo offline via committed cassettes; cache-miss-in-offline surfaces
  gmlcache's error verbatim.

### 0.0.7 — tiers and reconciliation
- `[tiers]` config: `tier → {client, model, effort?}` per provider, seeded with
  documented defaults at init or when a new client appears.
- Pure `resolve(tier, config) → (client, model, effort)`; per-step override at
  run time, recorded on the execution.
- Startup reconcile: seed / configured-but-absent warning / stale-model warning
  (free drift check), models relayed via `gmlcache models`.
- **Tests:** resolution table; reconcile table (seed/keep/warn×2); override
  recorded as event.

### 0.0.8 — gates and the unattended flag
- `questions` transport output: present → block, ask at the prompt, record answer
  event, lift + sweep the file; absent → proceed. Per-step `unattended: true`
  never blocks.
- **Tests:** block/answer/resume round-trip; sweep; unattended bypass; answers
  visible in `/replay`.

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
