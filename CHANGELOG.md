# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.x.y` the project is in **alpha** and anything may change
between releases; see [`docs/ROADMAP.md`](docs/ROADMAP.md) for the path to `1.0.0`.

## [Unreleased]

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

[Unreleased]: https://github.com/danielslobozian/generic-ml-workflow/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/danielslobozian/generic-ml-workflow/releases/tag/v0.0.1
