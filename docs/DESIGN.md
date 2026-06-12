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
caps; an executable step wears none — and that absence is meaningful.

**Rules** are compressed, interpretation-only guidance blocks — the system's
memory of "we fixed this once." Rules bind to **caps, not steps**; a step inherits
rules transitively through what it wears. Applicability is computed at compile
time, cached by content hash, overridable per cell. (The applicability matrix and
automatic rule proposal are roadmap items; the binding model is design now.)

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

## 11. The event log

An **append-only event log** plus thin projections, in one local SQLite database.
Files are the substance; events are the spine: an event holds metadata and
**pointers** (path + content hash), never file contents. Consequential decisions
(clients detected, config seeded, drift warnings, probe verdicts, every shot's
resolution, gate answers) are **events**; noisy diagnostics are logs. `replay`
reconstructs a run's story by reading forward; the same keys give cost attribution
for free.

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

**Open question (deliberately undecided): the placement taxonomy.** When a user
wants to correct how a judgment step behaves, the fix has three legitimate
homes — the **cap** (every step wearing it benefits), a **user rule** (it is
about this user and follows them everywhere), or the **step's own context**
(local to one judgment). The choice is the user's; the app's job is to make the
question askable. The taxonomy and its ergonomics are not yet designed.

## 16. Design invariants — do not re-litigate

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
10. The question file is the gate; unattended steps never block.
11. Events are decisions; logs are diagnostics; event payloads hold pointers,
    never blobs.
12. Tokens and usage are the currency; no pricing scraper.
13. Meta-code is versioned by git, not by a hand-built mechanism; every execution
    stamps the meta-code commit it ran against, from the first run slice onward.
14. The verb set is closed; any natural-language surface (workspace prompt or
    companion) is a router onto it, never an actor with free-form execution.
15. Zero tolerance for cwd pollution, hidden state, or auto-written files beyond
    the answered-for config.
