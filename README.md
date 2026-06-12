# generic-ml-workflow

An **interactive terminal workspace built around ML coding clients** (Claude Code,
Codex, Cursor, …). You launch it and you are home: a banner, the clients present
on your machine, the workflows it knows, and a prompt. The app **owns the
session**; the client is a function it calls — a stateless, headless, detached
shot, executed and cached by its sibling tool,
[`generic-ml-cache`](https://github.com/danielslobozian/generic-ml-cache).

It is **not** a CLI you invoke with arguments: there is no usage model in which a
human types a workflow name and flags on a shell command line. Launching it *is*
the interface.

> **Status: alpha, `0.0.4`.** The app checks its mandatory dependencies
> (git + gmlcache), interviews you on first run and git-inits your flows
> folder, loads and validates workflow definitions, and has an event-sourced
> store (`/replay`, `/status`) plus the executable-step building blocks. It is
> honest about what it can't do yet.

## The family

- **[`generic-ml-cache`](https://github.com/danielslobozian/generic-ml-cache)** —
  the execution arm. This engine executes **no model call itself** and carries
  zero client knowledge; all client execution, caching, recording, and discovery
  is delegated to `gmlcache` as a subprocess. One consequence worth advertising:
  the bundled demo workflow will ship with its cassettes committed, so CI
  exercises the entire engine loop offline.
- **`generic-ml-workflow`** (this project) — the workflow engine and its REPL
  workspace.
- A third project (planned, far future): a chat-first application for
  non-engineers, built on this engine's stable Python API.

## Install

Requires Python 3.11+.

```bash
pip install generic-ml-workflow   # once published to PyPI
# or, from source:
pip install .
```

Then:

```bash
gmlworkflow
```

You'll get the banner, a detection pass (`gmlcache doctor`, relayed — advisory,
token-free, never gating), and the prompt. `/help` lists the closed verb set; in
this slice most verbs are honest stubs that tell you which roadmap slice brings
them to life. `/quit` leaves.

Without `gmlcache` installed the workspace still opens and tells you what's
missing — this app does nothing model-shaped on its own, by design.

## Shape

One repo, two packages, a wall between them:

```
src/generic_ml_workflow/
  core/   the engine library: contract, events, loader, paths, gmlcache detection.
          Surface-agnostic — no terminal code, enforced by a gate test.
  repl/   the terminal frontend: renders and routes. Holds no logic of its own.
```

The dependency arrow points one way — surfaces import the core; the core imports
no surface. An import-rule test in CI fails on any violation. The repo-split
decision is deliberately scheduled at the stable-API slice (roadmap 0.1.4).

## Development

```bash
git clone https://github.com/danielslobozian/generic-ml-workflow.git
cd generic-ml-workflow
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

The suite is offline and token-free: no real client, no `gmlcache`, and no model
call is needed — the detection relay is tested against frozen fixtures. See
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
