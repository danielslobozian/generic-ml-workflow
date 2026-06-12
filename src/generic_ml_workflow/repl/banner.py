# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""banner.py -- the startup banner, in configurable styles.

The "you've entered a place" first impression: Rich-rendered, colour on a real
terminal and plain text when captured or piped. Styles are a small registry, so
"configurable" is literal -- the REPL's ``/banner`` verb previews and switches
them live (persisting the choice in config arrives with the 0.0.2 slice). Adding
a style is one entry in STYLES.

Rendering never gets to break startup: any failure falls back to a plain
one-liner.
"""

from __future__ import annotations

from io import StringIO

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

NAME = "generic-ml-workflow"
TAGLINE = "the app owns the session; the client is a function it calls."

_ACCENT = "#3fb6a8"
_ACCENT2 = "#a98bf5"


def _heading(version: str) -> Text:
    h = Text()
    h.append(f"{NAME} ", style="bold")
    h.append(version, style="dim")
    return h


def _mark() -> Text:
    """A small two-tone step-chain mark: bounded steps, wired in order."""
    t = Text()
    for i, ch in enumerate("\u25a0\u2500\u25a0\u2500\u25a0"):  # ■─■─■
        t.append(ch, style=_ACCENT if i % 4 == 0 else _ACCENT2)
    return t


def _render_panel(version: str) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column()
    grid.add_row(_mark(), Group(_heading(version), Text(TAGLINE, style="dim")))
    return Panel(grid, box=box.ROUNDED, border_style=_ACCENT, expand=False, padding=(0, 1))


def _render_minimal(version: str) -> Group:
    return Group(_heading(version), Text(TAGLINE, style="dim"))


STYLES = {
    "panel": _render_panel,
    "minimal": _render_minimal,
}
DEFAULT = "panel"


def names() -> list[str]:
    return list(STYLES)


def render(style: str, version: str, *, color: bool = True) -> str:
    """Render a banner style to a string. Unknown style -> the default. On any
    failure, fall back to a plain one-liner so startup never dies on cosmetics."""
    renderer = STYLES.get(style, STYLES[DEFAULT])
    try:
        buf = StringIO()
        con = Console(
            file=buf,
            force_terminal=color,
            color_system="auto" if color else None,
            width=100,
            highlight=False,
            soft_wrap=False,
        )
        con.print(renderer(version))
        return buf.getvalue().rstrip("\n")
    except Exception:
        return f"{NAME} {version}\n{TAGLINE}"
