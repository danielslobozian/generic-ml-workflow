# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The terminal frontend: renders and routes, holds no logic of its own.

The REPL is this project's product and its first surface -- but only a surface.
Every capability lives in the core; this package imports the core, never the
other way around (the wall, enforced by tests/test_wall.py).
"""
