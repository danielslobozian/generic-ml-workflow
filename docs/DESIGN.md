# generic-ml-workflow — design

This document is the project's constitution: the conceptual model and the invariants.
The build order lives in [`ROADMAP.md`](ROADMAP.md). Where the two conflict, this
document wins for *what the thing is* and the roadmap wins for *when it arrives*.

---

## 1. What it is

**generic-ml-workflow is an interactive terminal workspace built around ML coding
clients.** You launch it and you are home: a banner, the clients present on your
machine, the workflows it knows, and a prompt. Running a workflow is one of the
things you do there — alongside replaying history, watching cost, and (later)
authoring workflows. The client (Claude Code, Codex, Cursor, …) is never something
you steer by hand inside this app: the app **owns the session** and drives every
client as a **stateless, headless, detached shot** — one bounded job, write the
result, exit, forget. The app is the session; the client is a function the app calls.

It is **not** a CLI you invoke with arguments. There is no usage model in which a
human types a workflow name and flags on a shell command line. An argument surface
may exist as hidden plumbing for automation, never as the product.

## 2. The family

This project executes **no model call itself**. All client execution, caching,
recording, and client discovery is delegated to its sibling tool,
[`generic-ml-cache`](https://github.com/danielslobozian/generic-ml-cache)
(`gmlcache`), invoked as a subprocess. The engine builds *what* to call (context,
prompt, input files, concrete client/model/effort); `gmlcache` is *the caller* —
and the cache, the recorder, and the replayer.

**Capability-sinking principle.** When this engine needs something that is really
about *calling models* (usage extraction, API/HTTP calls, new client quirks), it
becomes a `gmlcache` feature and this engine merely consumes it. The two roadmaps
cross-reference each other where that happens. The engine carries **zero client
knowledge**: if a client quirk surfaces here, it is a `gmlcache` issue by definition.

Consequence worth advertising: the bundled demo workflow ships with its **cassettes
committed**, so CI exercises the entire engine loop — fetch, ML steps, produced
files — offline, deterministically, and for free, via `gmlcache` offline mode.

**Mandatory dependencies, checked at launch.** Two external tools are not
optional and the app refuses to open without them: **git** (the versioning spine
— meta-code history and the time-travel/resume machinery are how the engine
works, not a feature on top) and **gmlcache** (the execution arm — the engine
makes no model call any other way, so an engine without it is a dead shell). The
launch wrapper checks both before the workspace is built and, on a miss, prints
the remedy and exits. This is an *edge* check (like input validation at a service
boundary): it lives at the entry point, never inside the workspace, so the
workspace stays testable without those binaries present. Detecting which
*clients* gmlcache sees remains separate and advisory — zero installed clients
is fine (you may be validating, or running cached work); a missing gmlcache is
not.

**The family's third layer (planned, separate project).** A complete,
user-friendly, chat-first application built *on* this engine, for people who are
not engineers: create a workflow conversationally ("I want to download a ticket
into a file" → the app interviews, asks for credentials, tests the step live),
then run and manage workflows from a clean interface. This engine is its backend.
The architectural obligation that puts on *this* project: **the core is
surface-agnostic.** Every capability is reachable through the event log, the
closed verb set, and the stable Python API; the REPL is this project's product
and its first surface, but it holds no logic of its own — a GUI is just another
reader of the log and another router onto the verbs, exactly like the companion.

**Core and REPL: one repo, a wall inside.** The engine is structured as a library
(`…core`: contract, events, orchestrator, loader, the gmlcache seam, projections,
the verb implementations) and a thin terminal frontend (`…repl`) that only renders
and routes. The dependency arrow points one way — surfaces import the core; the
core imports no surface and nothing terminal-shaped — and the boundary is
**enforced by the standing gate** (an import-rule test fails CI on any violation),
not by convention. They ship as one repo and one release train deliberately: a
repo split today would impose two CIs and a version-compatibility contract before
any second consumer exists. The split decision is scheduled, not forgotten — it is
taken at the stable-API slice (roadmap 0.1.4), when the REPL is rebased onto the
public API and a real second consumer is in sight.

## 3. The home

- **The app is location-blind.** Launching from any folder is identical. It never
  reads the current directory, never creates anything in it, never resolves a
  relative path against it. Run outputs land in the app's own workspace area;
  getting them out is an app verb (export), never a file appearing where you stood.
- **Only the config location is fixed.** The config lives at the OS-standard path
  (`~/.config/gmlworkflow/config.toml` on Linux; platform equivalents elsewhere),
  overridable by exactly one env var (`GMLWORKFLOW_CONFIG`). Every *other* path —
  state, flows, workspace — is a setting inside that config.
- **First run is an interview, not an error.** No config found → the app proposes:
  (1) standard OS folders, (2) one single folder for everything, (3) custom paths.
  It writes the config only after you answer — the one file it ever creates
  unasked-for-content, and it asks first. The config it writes is documented inline:
  seeded defaults plus allowed values live in comments.

## 4. Entities

```text
Job  ──< WorkflowExecution >──  Workflow⟨InputType⟩ (definition @version)
                  │
                  └──< StepExecution >── Step (definition; declares a tier)
```

- **Job** — the organizing unit: an identifier and a label for *the thing you are
  working on*. Everything the app keeps (history, cost, documents) regroups by job.
  The job is deliberately generic: what it denotes is the user's business, not the
  engine's.
- **Workflow⟨InputType⟩** — a definition: ordered, named steps, typed by the kind
  of input it works on (`file` / `folder` / `url` / `freestyle`; the set is
  extensible). **The input belongs to the workflow's declaration**: you design a
  workflow and declare what it takes — you never pick a workflow *for* an input —
  and what it asks for at launch is computed from its steps' requirements (§7).
  A new workflow is **data** — the workspace vocabulary never grows when you add
  one.
- **WorkflowExecution** — one run: binds a job to a workflow definition at a
  version, and carries the per-step concrete `(client, model, effort)` resolution
  plus any user overrides — the choice belongs to the run, not the definition.
- **Step** — the atom. State, resume, cost, and documents all attach here.
- **StepExecution** — one shot or one body invocation, with its timing split
  (client-execution time vs. user-reaction time).

Definitions live in source (the user's flows folder, versionable); runtime — events
and projections — lives in the app's database.

## 5. The step model

**The spec is the only thing a human authors** (when authoring at all): prose that
says what the step does, what it needs, what it produces. Nothing in a spec names a
technology.

**At runtime a step has exactly one of two natures:**

- **Interpretable** — a *shot*: the engine compiles the request (caps, context,
  instruction), resolves the tier, and fires once through `gmlcache`.
- **Executable** — a *local invocation*: deterministic code run by the engine,
  declared inputs in, declared outputs out.

**The executable nature has three origins, and the runtime cannot tell them apart:**

1. **User-supplied executable.** A bash script, a binary, anything launchable. The
   step config declares the invocation, the inputs, the outputs, optionally a
   credential role. No spec, no interpretation, no generation — if you already have
   the thing, the engine does not force you to reinvent it through a spec.
2. **Generated body.** Creation-mode (an ML-assisted compiler, later on the
   roadmap) analyzes a spec and, where the work is deterministic, generates a
   Python body. The spec is the source; the body is a regenerable view and is never
   hand-edited — wanting to edit a body is the signal the spec was incomplete.
3. **Pure-ML fallback.** Any spec can always run as a shot instead. **Generation is
   an opt-in optimization, never an obligation**: a user who reads Python reviews
   and adopts a generated body; a user who doesn't simply declines and pays tokens
   per run. The choice is per step and eyes-open about the cost trade.

**Granularity invariant — always separate.** One concept per step. A spec that does
several things is split into several steps wired by files; there is no hybrid
runtime atom. If a step conceptualizes a whole workflow, what was the point of
separating workflows into steps? Small steps mean bounded context (cheaper, more
cacheable shots), smaller blast radius on change, and reuse across workflows.

## 6. Caps and rules (the interpretable side)

A **cap** carries both halves of what a judgment step needs: *who the model is*
(persona + methodology) and *which slice of context the step reads* (binary bucket
flags). A cap lives once and is referenced by name; a step wears zero, one, or two
caps; an executable step wears none — and that absence is meaningful. A cap is
**generic** — a role, possibly inspired by real people but scrubbed to the role,
carrying no user — and it ships and is shared unchanged. A cap either **plays** the
user (stands in for them when absent) or **accompanies** them (the companion, §14);
the full model is §16.

**Rules** are compressed, interpretation-only guidance blocks — the system's
memory of "we fixed this once." Rules bind to **caps, not steps**; a step inherits
rules transitively through what it wears. Applicability is computed at compile
time, cached by content hash, overridable per cell. (The applicability matrix and
automatic rule proposal are roadmap items; the binding model is design now.)

Rules are also the unit of the user's **projection** onto a cap (§16): the user
reacts at a step, and the app — reading the cap's description — diagnoses and
phrases the correction. Rules **never rewrite** the cap; they accrue as a separable
layer (**seed rules** projected from the user's context snapshot, **app rules**
accrued from live reactions), so the authored cap stays pristine and shareable.
The separation plane is one line: **authored ships, accrued stays.**

## 7. The workflow context, ports, and bindings

A workflow execution owns a **workflow context**: one execution-scoped pool of
named values. At launch the engine **warms it up**: the run interview's answers
and everything configuration satisfies are loaded in; from then on, every
materialized input and every durable output a step produces lands in the context
under a unique name as the run advances. A step never consumes "another step's
output" — it asks the context, and resolution is uniform whether the value came
from the launch, from configuration, or from a step that ran earlier. (The
context holds a credential role's *presence*, never the token — secrets keep
their separate path, §10.)

**Steps declare requirements, in kinds.** A requirement is one of: a
**run-input** (asked at launch — the run interview is *computed*, the union of
the steps' unsatisfied run-inputs; adding a step extends the interview
automatically), a **configuration requirement** (satisfied from the user's
config — set once, shared by every workflow that reuses the step), a
**credential role** (§10), or an **artifact** (a named product some earlier step
must contribute).

**Ports and bindings.** A step's declared inputs and outputs are **local names —
ports** — which is what makes a step reusable across workflows untouched. The
*workflow's* composition data holds the **bindings**: each consuming port mapped
to a context name (a launch input or another step's product). Steps never name
other steps; the binding block is the only wiring, and adding a step to a
workflow *is* creating its bindings — the authoring flow asks "this step needs
`source_text`; which existing product is that?". The engine derives the
dependency graph from the bindings at compile time.

**Deduced correctness, before any token is spent.** Load/validation **errors**:
an unbound required port; a binding naming a product nothing contributes ("your
third step requires a file nobody adds to the context — did you forget a
step?"); two products under one name — product names are unique per workflow,
and a step that updates an artifact contributes a **new** name that says why
(`x` → `x_enriched`), never a second `x`. Validation **warning**: a durable
output no later step consumes — the **dead-branch lint**. It exists for workflow
*evolution*: insert an enriching step between steps two and three and forget to
rebind step three, and the old product is silently consumed while the new one
hangs unconsumed — which is exactly what the lint surfaces. It stays a warning
because terminal deliverables are legitimately unconsumed. The same warm-up
verifies configuration and credential requirements at `/validate` and at run
start, token-free: "you created this workflow but never configured it" arrives
before step one fires, not at step four.

**The question file is the gate.** A step may declare a `questions` transport
output. Present → the run blocks and the app asks you at the prompt, recording
the answer as an event; absent → proceed. There is no separate gate machinery. A
step marked **unattended** never blocks: it proceeds (or fails) without asking,
which is what makes fully auto-advancing workflows possible.

**Run modes — the run-wide posture.** How a run advances from one step to the
next is *one run-level selector with three positions*, chosen at launch — not
three independent switches. **Full-auto**: the run never stops; it takes its best
default on anything unclear and does not halt to wait even on a step's failure —
for when you are away, or trust the run and want it uninterrupted. **Full-manual**:
the run stops at a **checkpoint** after *every* step, whether or not the step asked
anything, so you can read each result and may re-run a step against a different
tier to compare — for first contact with a workflow, especially one you did not
write. **Questions-only**: the run flows freely and stops *only* where a step is
genuinely unsure and emits a `questions` file — the gear you graduate into as
trust builds. The three are not new machinery: questions-only *is* the question
gate above; full-auto *is* `unattended` lifted from one step to the whole run;
full-manual *is* a checkpoint generalized to every step. A **checkpoint** is any
point where the run pauses and can later resume (§11). Per-step or per-cap
refinement — one unattended step inside an otherwise-manual run — is layered-config
work (roadmap 0.1.x); the run-wide selector comes first. "Play the role yourself"
at warm-up is the per-role face of full-manual on that step.

**Personalizing a cap at warm-up.** The same warm-up offers, per cap a workflow
uses, a three-way choice: run it **generic** (testing the workflow, not yourself),
**project your context** onto it (the app extracts the role-relevant slice of your
snapshot into seed rules — one interpretive shot through gmlcache, cached
thereafter), or **play the role yourself** (you are present, so the step pauses for
you — the per-role face of the run modes). The user's personal context is itself a
**configuration requirement** satisfied per cap; the raw snapshot never transits a
model call, only the derived rules do. The full model — projection, snapshot,
"digital me" — is §16.

## 8. The request envelope and purity

A shot's request is one ordered structure:

```text
[ context , prompt , files ]
```

- **context** first: the stable, compressed, run-agnostic prefix — no dates, no
  job identifiers, no session ids, no paths. The high-cache-hit part.
- **prompt**: the instruction for this step.
- **files**: the work-input content — the subject of the work, uncompressed.

**Purity is load-bearing twice**: it maximizes the client's own prefix cache, and
it is what makes `gmlcache` cassette keys stable — a request that is a pure
function of its declared inputs hits the cassette forever. Purity is enforced in
the request builder, not hoped for.

External reference material ("read the official docs at these links") enters either
as compile-time context (fetched, compressed, cached) or as declared input files /
read paths — mechanisms `gmlcache` provides natively (`--input-file`,
`--allow-path`).

## 9. Tiers, clients, and validation

- **A workflow never names a vendor.** Each step declares an abstract tier —
  `high` / `medium` / `low`. Config maps `tier → {client, model, effort?}`;
  execution resolves each step to a concrete triple, user-overridable per step,
  recorded on the execution. This is what makes workflows **portable** across
  machines that have different clients installed.
- **Effort** is a uniform second axis in config; how it reaches each client is
  `gmlcache`'s problem, not this engine's.
- **Launch is detection, not selection.** Startup asks `gmlcache doctor` /
  `gmlcache models` what exists, reconciles against config (seed defaults for a new
  client, warn on configured-but-absent, warn on stale model), and spends no tokens.
- **The list is advisory; the run is the truth.** Validation ladder: free drift
  check at startup → deliberate recorded one-shot probe per unique triple (a spend,
  so probed once and remembered as an event) → the real run, which must surface the
  client's own error verbatim.
- **The currency is tokens and usage, not dollars.** Subscription cost is flat;
  dollars exist only on api-key paths. No pricing scraper, ever.

## 10. Credentials

- Named **roles** in config (`base_url` etc.); tokens live in a separate
  `credentials.toml` (chmod 600, never in config, never versioned, never leaves
  the machine). Env-var override per role for single runs.
- **Secrets never transit a model call.** A role token can reach only the
  executable side: a built-in or generated body uses `ctx.fetch(role, path)` — the
  token never enters step code at all, and the host is pinned by the role's
  `base_url`; a user-supplied executable that declares `needs: [role]` receives the
  token as an env var in its process — a wider grant, but it is the user's own
  script, explicitly declared. In all cases the token never appears in events,
  logs, prompts, context, or cassettes. The interpretable side cannot receive a
  token, by construction. Deterministic local steps exist partly *for* this:
  fetch-the-data locally, send only the data onward.

## 11. The event log — the persistence architecture

An **append-only event log** plus thin projections, in one local SQLite database.
Files are the substance; events are the spine: an event holds metadata and
**pointers** (path + content hash), never file contents. Consequential decisions
(clients detected, config seeded, drift warnings, probe verdicts, every shot's
resolution, gate answers) are **events**; noisy diagnostics are logs. `replay`
reconstructs a run's story by reading forward; the same keys give cost attribution
for free.

This section fixes the *principles*; the concrete tables grow slice by slice as
real events justify them (we do not pin a whole schema up front — event sourcing's
gift is that projections are rebuildable, so the schema may evolve freely). The
sketch below is **illustrative direction, not a committed schema.**

**The model is event sourcing — and explicitly not event streaming.** This is a
single-user, single-writer, local application. The event log is the **single
source of truth**; everything else is derived. We are *not* Kafka and *not* an
event-streaming system: there is no message bus, no subscribers, no inter-service
pipe, no eventual consistency. Those tools solve decoupling many services at
volume; we have the opposite need — strong consistency and constantly fetching
*one* run's full history to rebuild it, which a streaming log is poor at and an
indexed local store is good at. The distinction matters: problems attributed to
"event sourcing" are usually problems of *streaming* (staleness, no atomicity);
they do not apply here.

**Events are the only truth; projections are derived, disposable, rebuildable.**
The `events` table is authoritative and never updated or deleted. Every other
table (`jobs`, `workflow_executions`, `step_executions`, `artifacts`, gate
questions, the context lookup) is a **read-model**: drop them all and replay the
log (plus git, below) to reconstruct them identically. They are **not refreshed on
a schedule** — each event's projection is applied *in the same transaction* as the
append (project-on-append, immediate consistency, no staleness window). A full
rebuild happens only on a deliberate projection schema change or disaster
recovery. Projection updates must be a **pure, deterministic function of the
event** (no clocks, randomness, or external calls), or replay would not reproduce
the same tables.

**The historization key groups a run.** Launching a workflow mints an in-memory
**execution identifier** (a uuid, born in application code *before* the first
event). It is stamped on every event of that run; loading a run is one indexed
query, `WHERE execution_id = ? ORDER BY seq`. The same value doubles as the
`workflow_executions` projection primary key — one value, three roles (minted in
code, first persisted by the `…started` event, scope key on every later event),
with no chicken-and-egg because it precedes the first event.

**The execution context is a read-model; the engine holds no run state.** At any
moment a run's live state — *where it is* (which steps completed, which failed,
the current step and its status), its *accumulated values* (the context-fold of
run-inputs and product pointers), and *what, if anything, it waits on* (nothing, a
user's answer to a `questions` gate, or a manual checkpoint) — is the **execution
context**: one execution's slice of the projections (`workflow_executions`,
`step_executions`, `artifacts`, gate questions), read directly. It is never a
separately persisted blob and never held in the engine between actions. The engine
is **stateless**: each action reads the execution context, advances one step,
appends its events (the projections update in the same transaction, above), and
forgets. **Starting a run is resuming from an empty execution context** — there is
no privileged "fresh start" path, only "advance from where this context says we
are", with step zero the empty case. **Resume reads the projections, not a
re-execution**: event sourcing makes the log the *authority* and the *rebuild*
path, but normal operation reads the project-on-append read-models — it does not
replay the log each time a run is reopened. The **step is the unit of resume**: a
run interrupted mid-step re-runs that step on resume — cheap when the cache already
holds the completed call (invariant 3); finer partial-step bookkeeping is
deliberately deferred to real use. Because the engine is stateless and the state
*is* the projections, advancing on a background worker, pausing, quitting the app,
and resuming later are one operation seen at different moments. The engine takes a
caller-supplied **progress reporter** (where it announces advancement) and a **stop
check** (whether to halt at the next boundary) and knows nothing of threads,
screens, or the keyboard — those belong to the surface (invariant 1).

**The event envelope is uniform columns + a heterogeneous payload.** Event bodies
differ by type, so we do not force a pure-SQL structure onto them. Each event has
a uniform **envelope** (queryable columns: `seq` total order, `event_id`,
`event_type`, `occurred_at` — UTC, **mandatory on every event** — `execution_id`,
`actor`, and optional finer scope keys) plus one opaque **`payload`** (JSON) for
the type-specific body. You query and order on columns; you read the payload only
after selecting. Scope keys **nest** (execution is mandatory; step and step-attempt
narrow within it, null when N/A) — never a web; an event has one required parent
(execution) and optional nested ones, and is never the child of an artifact (the
`artifact.created` event *creates* the artifact row).

**Events + git are self-sufficient to rebuild everything; events reference
meta-code by name + commit, never by database id.** Because projections must be
rebuildable, every value an event carries to enable that rebuild must survive the
rebuild. Database-internal ids of *definitions* would not — so events reference
meta-code by its **authored name** (the user's step code, workflow name, cap name)
**resolved against the run's stamped commit**, not by any stored id. Git is a
co-equal source of truth for definitions: the `…started` event stamps
`{workflow_name, commit, branch, engine_version, input_type}` (scalars and
references — never the workflow *object* itself; the definition lives in git and
is read with `git show <commit>:<path>`). Runtime *occurrences* (execution,
step-attempt) are log-born: an execution gets a minted uuid (fine — its birth is
an event, so replay reproduces it); a step attempt is identified by the natural
key `(execution_id, step_name, attempt)`, needing no stored id at all.

**The event vocabulary is engine-owned, typed, and self-describing.** Event types
are *engine* concepts (the engine records how it ran things) — a **closed enum in
the core**, owned by the engine version that reads the log, never authored as
meta-code. Each type has a **typed payload bean** (a dataclass with to/from-JSON):
the bean *is* the schema, the single place a type's shape is declared, used by both
the writer and the projection-rebuilder so they cannot drift, and failing loudly on
an event that does not fit (exactly where a schema-evolution problem should
surface). A small **registry** maps type → bean, from which the engine offers a
**self-description capability** (enumerate the event types; describe one type's
structure) — the Swagger-for-events backbone that `/replay`, the companion, and the
future versioned schema doc (roadmap 0.1.0) consume to narrate any event without
hard-coding each type. The discovery capability starts as a thin core function and
grows a surface when a consumer needs one.

**What actually goes in SQL, in one line:** the **event log** (stored truth,
never derived), **projections** (stored but rebuildable read-models, written
transactionally with each event), and **documents/artifacts** referenced by
**pointer (path + sha)** — the file content itself lives on disk (the
content-addressed store, §12), never inside the log.

*Illustrative entity sketch (direction, will evolve):*
`Job ──< WorkflowExecution(stamp: commit/branch/engine_version) ──< StepExecution
(natural key: execution + step_name + attempt) >── Step(referenced by name@commit)`.
The **workflow context** (§7) is not a stored truth but the **event-fold**: replay
a run's events and collect the named values they introduced (run-inputs inline,
products as pointers) — a derived projection, optionally materialized for fast
lookup. **Companion** conversations (§14, far future) are future event *types* on
the same spine, which the uniform envelope already accommodates without a schema
change.

## 12. Documents (direction)

A document is a row in a **catalog** pointing into a content-addressed **store**;
the human-readable foldered form is an **export projection** rendered on demand.
Step scratch space is ephemeral and wiped on retry; durable outputs migrate to the
store on completion. (Settled as design; implementation is on the roadmap.)

## 13. Versioned meta-code: git is the time machine

Everything the engine *interprets* — workflow definitions, step specs and bodies,
caps, methodologies, rules and their cap-mapping, config keys — is **meta-code**,
and the user's flows folder is expected to be a **git repo**. The repo *is* the
versioning; no hand-built version store exists or ever will:

- a commit is a re-runnable system version; checkout is "go back"; diff is "what
  changed"; branches are deliberate flow-system versions, tagged at trusted points;
- **every execution stamps the meta-code commit, branch, and engine version it ran
  against** — stamped from the very first run slice, because an append-only history
  cannot be back-filled;
- resuming old work reads the stamp, offers to check that commit out, renders the
  difference since ("these rules were added — resume on the old image, or restart
  fresh?"), and resumes against the system as it was;
- the working tree is **one state**, which is why one workflow runs at a time;
  uncommitted work is captured per session via stash, so resume = checkout the
  stamped commit + restore the session's stash + replay the execution position;
- **safe mode** falls out: run against committed state only, learning loop off,
  for a run you trust not to reshape the system underneath it;
- anything whose rollback would change how a future run is interpreted belongs in
  the repo (including the rule↔cap applicability mapping, as a source file —
  a database row would desync from source the instant you time-travel); records of
  what happened, build outputs, and secrets belong outside it. Config *keys* are a
  stability contract; config *values* are not versioned.

The boundary with the public/private split: the engine ships the *git-driving
behavior*; the user's meta-code repo is theirs and never seen by this project.

## 14. The companion

The companion exists because of a **context economics** problem. The work session
carries a heavy compiled context — caps, rules, the job's material. Asking a
general question inside that session ("what design pattern fits here?") re-sends
the whole payload for a conversation that needed none of it: tokens burned for
nothing. Today people solve this with two screens — a chat in the browser for
thinking, the coding client in a terminal for working. **The companion merges the
two screens into the workspace**, while keeping their contexts strictly separate.

In §16's terms the companion is itself a cap — of the **accompanying** nature:
where a work cap *plays* the user, the companion talks *with* them and adapts,
never replaces. It is the home of the personal-**personal** context (how you
talk, the human level), while the personal-**professional** you lives on the
playing caps; provenance routes each correction to the right home.

- **An in-app surface, not a second process.** `/companion` shows the chat;
  hide it and you are back at the work prompt. (An earlier sketch ran it as a
  second terminal on the same on-disk session; superseded — the REPL makes that
  ceremony unnecessary. What survives is the medium: every surface talks to the
  event log, never to another surface.)
- **Its own light context.** The companion's context is personal and small: a
  profile (how you like to talk, your style of work — the "personality and
  preferences" a web chat carries) plus a **narrated digest of the work**, drawn
  from the event log — what steps ran, what they produced, what questions they
  asked. Never the work session's compiled context. Related-or-unrelated
  questions are equally welcome and equally cheap.
- **Multi-actor by nature.** The conversation is not just user ↔ companion: the
  workflow's own actors are visible in it, because the companion reads the log —
  a step's output or a gate's question appears in the story as it happens, and
  you can discuss it the moment it does.
- **It can act, within the fence.** "Relaunch that step", "do this" routes onto
  the closed allow-listed verb set — never free-form execution — and the
  companion is **state-aware**: it can tell you what is possible from here
  ("you can rerun, answer the gate, or export; nothing else") because the
  projections already know.
- **Any client, any model.** The companion resolves through the same tier
  config (its own entry), so it can run on a cheap model while the work runs on
  an expensive one — or the reverse, your choice.
- Companion conversations are records, stored as documents, included in a job's
  export alongside the artifacts.

The same router discipline applies to the **main workspace prompt**: the closed
verb set is the target; natural-language routing onto it is planned, so "type
what you want" is literal — and the vocabulary still never grows when a workflow
is added.

## 15. The tuning loop: optimization as a first-class user journey

The AI landscape this app sits on is an unknown that keeps moving: providers
release new models, new effort notions, new price points. Some steps benefit
immediately, some need adapted context, some don't care. The only honest way to
know is **controlled repetition — the same input with exactly one axis changed,
outputs compared**. Without a cache that loop is the definition of insanity;
with `gmlcache` it is nearly free: an identical configuration replays at zero
cost, and each variant (a different model, a different effort, a compression
toggle) costs exactly one fresh call. The cache is not just cost protection —
it is what makes experimentation affordable, and tuning is how a user goes from
"I preset a workflow" to "I dialed in every step."

**The pre-launch manifest.** Before a step fires, the app can show the bill of
materials of its envelope: every context source (the cap, the user context, the
organization context, the rules it inherits, the step's own inputs) with its
token weight, the projected total — and the **toggleable transforms**, each
showing its delta: compile-time compression, additional input-side styles,
an output-side brevity instruction (a different lever entirely — it spends
out-tokens, not in-tokens), the tier/model/effort override. Toggle, compare,
launch, read, iterate. The cost view closes the loop afterwards: per-job totals,
hit ratios, and the signal that **price zero = cache hit** — including for the
compression shots themselves, since compilation runs through `gmlcache` too.

**One mechanism, two scales.** Preparing a step's context is itself a pipeline
of data transformations — group, merge, compress, assemble — where every
stage's output is a pure function of its inputs; and a workflow is a chain of
steps with the same property. Both are content-addressed chains, so both are
**probeable**: the system can walk a chain in cache-only mode — recomputing the
deterministic stages live, asking the cache (never the client) about the ML
stages — until the first miss, and show the **cache frontier**: cached, cached,
cached, *fresh from here*. The first miss marks where spending starts; anything
past a stage that cannot be answered from cache is honestly *unknown*. Because
cassettes are immutable and append-only, flipping a toggle back snaps the chain
back to fully cached — every configuration ever paid for stays one toggle away,
free. That reversibility is what makes the loop psychologically safe, not just
cheap.

Two honest notes. First, this is purity made visible: the frontier display is
only truthful because the request builder enforces run-agnostic context — one
leaked timestamp would shatter every key downstream and the display would lie.
Second, a transform never yet recorded can show *that* it will cost a call, but
not its output size until that one run happens; afterwards the delta is exact
and free forever.

**The placement taxonomy (resolved 2026-06-15).** A correction to a judgment step
has one home: a **rule on a cap** (§6, §16). The earlier "user rule" is not a
separate home — it is a rule on the *user-cap*, a "digital me" being a cap like
any other (§16). A correction that must *not* generalize to every step wearing the
cap is not a rule at all but a **per-binding tweak** on that step's use (the
layered-config override; see ROADMAP design-notes), kept distinct from the cap's
accrued rules. The app routes a reaction to the right cap by reading the cap's
description; the user is not asked to sort it.

**Stale rules on a changed snapshot.** Seed rules derive from the user's context
snapshot (§16); editing the snapshot is a cache-invalidation event. The app
detects the rules a change touches and **surfaces** them with before/after for the
user to keep, revise, or drop — never a silent recompute, the same eyeball-first
posture the exact-policy buckets keep.

## 16. The user, the context snapshot, and projection

A cap is generic by construction (§6): a role — persona + methodology — perhaps
inspired by real people but **scrubbed to the role**, carrying no user. What
makes a run *yours* is not a different cap but a **projection** cast onto it.
This section states the model the rest of the document points at; §6 (caps and
rules), §7 (context), and §14 (the companion) each see one face of it.

**Two natures of cap.** A cap either **plays** the user or **accompanies** them.
A *playing* cap stands in for the user where the user is absent — it produces
work *in their place* (the engineer cap writes code as you, judged by "would I
have written this?"). The *companion* (§14) is an *accompanying* cap: it talks
*with* the present user and adapts to them, and never replaces them. Same
primitive, opposite relation to the user. A third posture lives at the edges —
**elicitation**, where the user is the live source and the cap only draws them
out (a CV-builder interviewing you); whether that is a sub-kind of accompaniment
or its own nature is left open.

**"Digital me" is a bound result, never a shipped thing.** It is what a generic
*playing* cap becomes once the user's projection is layered onto it, locally —
never authored, never exported. When the user is *present* in the loop —
answering, deciding, pouring experience in — there is nothing to stand in for,
and the cap reverts to generic or to elicitation. A digital-me is a stand-in for
the user's **absence**.

**A rule is a projection onto a cap.** Rules (§6) are the projection's unit: a
compressed, interpretation-only correction the user casts onto a cap — "have
this role behave thus." Rules accrue on the cap and **never rewrite** its base
definition; the generic cap underneath stays pristine, which is exactly what
keeps it shareable. Two origins feed the one accrued layer: **seed rules**,
projected from the user's context snapshot at warm-up, and **app rules**, accrued
from the user's live reactions during runs. Both sit atop the authored cap. This
is the single separation plane — **authored ships, accrued stays**.

**The cap description is a dual-purpose contract.** Because both projection and
rule-identification read it, a cap's description must be written specifically
enough to serve both: the app reads it to *diagnose and phrase a rule* when the
user reacts at a step wearing the cap, and the user's context is *projected onto
it*. The user never has to know which cap, or how to phrase the rule — the app
does that work from the description.

**Personal context is a snapshot, not a living self.** The CV, journal pages, an
AI-written self-description — each is a fixed-in-time image of how the user saw
themselves, or was seen by another actor (human or AI). The application **never
edits** it; only the user adds, updates, or removes. It is the *seed*, not the
growth: the living representation of the user is the accruing rules, not the
static file.

**Raw context never reaches the model.** The snapshot is the *source* from which
projectable rules are derived; the **rules**, compressed, are what a cap carries
into a call — never the raw CV. This keeps personal data out of model calls (save
the one deliberate, consented extraction shot) and keeps the companion's many
turns cheap, since a heavy profile is never re-sent.

**Provenance classifies the personal.** What a *playing* cap's observer catches is
personal-**professional** (how you work) and lands on that cap; what the
**companion** catches is personal-**personal** (how you talk, the human level) and
lands on the companion. Who captured a correction decides where it belongs; the
user is not asked to sort it.

**An artifact's role is workflow-relative.** The same CV is *context* in a
workflow that plays you and *raw input* in the workflow whose job is to rebuild
it. Ingest only *structures* an upload; **binding** (§7) assigns its role, per
workflow — nothing is classified once and globally.

**Adaptation is the default; opting out is a setting.** By default the system
learns the user — seeds and accrues. A profile switch ("stay generic, never
adapt") mutes both; a user who sets it will rarely supply context anyway, so the
tension is self-resolving.

**New cap, existing user — an offer, not an assumption.** When a workflow
introduces an unmet cap, warm-up *offers* to project the user's existing
rules/context onto it, matched by description, exposing overlaps and conflicts to
curate — never a silent bind.

**Editing the snapshot is a cache-invalidation event.** Rules seeded from a
snapshot become possibly-stale when the snapshot changes; the app **detects and
surfaces** them with before/after, and leaves the decision to the user. It never
silently recomputes.

## 17. Design invariants — do not re-litigate

1. The REPL is this project's product; there is no argument-driven human usage
   model. The core is **surface-agnostic**: no capability exists only as REPL
   code — every surface (REPL, companion, a future GUI) is a reader of the log
   and a router onto the closed verbs.
2. The app is location-blind; only the config path is fixed.
3. The engine executes no model call and carries no client knowledge — `gmlcache`
   does; capabilities sink to the lowest generic layer of the family.
4. One concept per step; no hybrid runtime atom; always split.
5. Generation is an opt-in optimization; pure-ML fallback always exists.
6. The runtime cannot distinguish executable origins (supplied / generated / —);
   declared inputs/outputs are the whole contract.
7. Secrets never transit a model call; tokens never appear in events, logs,
   prompts, context, or cassettes.
8. A workflow declares tiers, never vendors.
9. Purity of the request envelope is enforced, not hoped for.
10. How a run advances is one run-level selector with three positions —
    full-auto (never blocks), full-manual (checkpoints after every step),
    questions-only (blocks only on a `questions` file). The question file is the
    gate; unattended/full-auto never blocks. Per-step refinement is later
    (layered config).
11. Events are decisions; logs are diagnostics; event payloads hold pointers,
    never blobs.
12. Tokens and usage are the currency; no pricing scraper.
13. Meta-code is versioned by git, not by a hand-built mechanism; every execution
    stamps the meta-code commit it ran against, from the first run slice onward.
14. The verb set is closed; any natural-language surface (workspace prompt or
    companion) is a router onto it, never an actor with free-form execution.
15. Zero tolerance for cwd pollution, hidden state, or auto-written files beyond
    the answered-for config.
16. git and gmlcache are mandatory runtime dependencies, checked at launch; the
    app refuses to open without them. The check is an edge concern (the launch
    wrapper), never the workspace's.
17. The wheel ships engine code only -- never workflow definitions, configuration,
    or other data. Meta-code is the user's; bundling any would violate the
    engine/meta-code boundary. Examples, if shared, live in a separate repo.
18. Persistence is event sourcing, not event streaming: the event log is the sole
    source of truth; all other tables are projections, written transactionally
    with each event and rebuildable from the log plus git. Events reference
    meta-code by name + commit, never by database id. Event types are an
    engine-owned closed enum with typed, self-describing payloads.
19. Caps are generic and carry no user; a "digital me" is the generic cap with the
    user's projection bound at runtime — never authored, never exported.
20. A cap's base is never mutated by use; the sole accrual is rules — seed (from the
    snapshot) and app (from reactions) — one separable layer where authored ships and
    accrued stays.
21. The user's raw context snapshot never transits a model call; only the compressed
    rules derived from it do, and only the user ever edits the snapshot.
22. Adaptation — seeding and rule accrual — is the default; a single profile switch
    ("stay generic") mutes it.
23. The execution context — a run's live state (position, accumulated values, what
    it waits on) — is a read-model of the projections, never a separate blob and
    never held in the engine. The engine is stateless: every action reads the
    context, advances, appends events, forgets. Starting is resuming from an empty
    context; resume reads the projections, not a log replay. The step is the unit
    of resume.
24. The engine is synchronous and surface-unaware: it takes a progress reporter and
    a stop check, and knows nothing of threads, screens, or input. Concurrency,
    live rendering, and interruption are the surface's. A clean stop cascades — the
    engine signals the cache subprocess it spawned and the cache tears down the
    client (invariant 3); the engine never reaches past the cache.
