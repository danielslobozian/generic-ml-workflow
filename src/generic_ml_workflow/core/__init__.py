# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The engine core: a library, surface-agnostic by construction.

Nothing in this package may import the repl package, prompt_toolkit, rich, or
anything else terminal-shaped. The dependency arrow points one way -- surfaces
import the core; the core imports no surface. The rule is enforced by a test in
the standing gate (tests/test_wall.py), not by convention.
"""
