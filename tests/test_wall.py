# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The wall, enforced: core is a surface-agnostic library.

Core importing the repl package -- or anything terminal-shaped -- fails CI. This
is an AST scan of every core module's import statements, not a convention. The
forbidden list names the repl package itself plus the terminal-rendering and
terminal-input ecosystems; extend it before reaching for one of them in core.
"""

import ast
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent / "src" / "generic_ml_workflow" / "core"

FORBIDDEN_PREFIXES = (
    "generic_ml_workflow.repl",
    "prompt_toolkit",
    "rich",
    "readline",
    "curses",
    "termios",
    "tty",
)


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
            elif node.level:  # a bare relative import: resolve against the core package
                found.append("generic_ml_workflow.core")
    return found


def test_core_imports_no_surface():
    violations = []
    for py in sorted(CORE_DIR.rglob("*.py")):
        for name in _imports_of(py):
            if any(name == p or name.startswith(p + ".") for p in FORBIDDEN_PREFIXES):
                violations.append(f"{py.name} imports {name}")
    assert not violations, "the core/repl wall is breached:\n  " + "\n  ".join(violations)


def test_wall_scans_real_modules():
    """Guard the guard: if the core dir moves, this test must fail loudly rather
    than silently scanning nothing."""
    scanned = list(CORE_DIR.rglob("*.py"))
    assert len(scanned) >= 5, f"expected core modules at {CORE_DIR}, found {len(scanned)}"
