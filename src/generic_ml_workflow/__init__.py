# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""generic-ml-workflow: an interactive terminal workspace built around ML coding clients.

You launch it and you are home: a banner, the clients present on your machine, the
workflows it knows, and a prompt. The app owns the session; the client (Claude Code,
Codex, Cursor, ...) is a function it calls -- a stateless, headless, detached shot,
executed and cached by its sibling tool, generic-ml-cache.

The package is structured as a library and a surface, with a hard wall between them:

    core/   the engine library: contract, events, loader, paths, gmlcache detection.
            No terminal code, no surface imports -- enforced by a gate test.
    repl/   the terminal frontend: renders and routes. Holds no logic of its own.

The conceptual model lives in docs/DESIGN.md; the build order in docs/ROADMAP.md.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("generic-ml-workflow")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0+unknown"

__all__ = ["__version__"]
