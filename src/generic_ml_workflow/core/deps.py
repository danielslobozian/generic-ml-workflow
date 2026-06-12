# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""deps.py -- the mandatory dependency check.

The app has hard external dependencies it cannot function without, and it refuses
to open without them (design invariant: these are checked at launch). They are
the two arms the whole architecture rests on:

  * git      -- the app's time machine. Versioning of meta-code and the
                back-in-time/resume machinery (roadmap 0.1.5) are not optional
                features layered on top; they are how the engine works. No git,
                no engine.
  * gmlcache -- the execution arm. This engine executes NO model call itself; every
                call (CLI today, API/HTTP tomorrow) goes through gmlcache. An engine
                that cannot execute anything is a dead shell, so gmlcache is required
                to open, not merely to run.

Design note (why this lives here and not in the workspace): checking whether an
external tool is installed is an *edge* concern -- the equivalent of REST-input
validation in a hexagonal architecture. It belongs at the boundary (the launch
wrapper, ``repl.app.main``), which calls :func:`require` before the workspace is
ever built. The workspace (the REPL) trusts that the check already ran and never
performs it itself -- so the workspace stays unit-testable without these binaries
present (CI runners have git but not gmlcache). This module is the small,
isolated "checker" both use: pure :func:`check` for tests and a ``/doctor``-style
status read, and :func:`require` for the hard gate.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


class DependencyError(Exception):
    """A mandatory dependency is missing. Raised by :func:`require`."""


@dataclass(frozen=True)
class DependencyStatus:
    """One mandatory dependency, as found (or not) on this machine."""

    name: str
    present: bool
    version: str | None = None  # first line of its --version output, when present
    remedy: str | None = None  # how to install it, when absent


@dataclass(frozen=True)
class DependencyReport:
    """The outcome of one dependency check."""

    statuses: tuple[DependencyStatus, ...]

    @property
    def satisfied(self) -> bool:
        return all(s.present for s in self.statuses)

    @property
    def missing(self) -> tuple[DependencyStatus, ...]:
        return tuple(s for s in self.statuses if not s.present)


def _probe(executable: str, args: tuple[str, ...] = ("--version",), timeout: float = 15.0):
    """Return (present, version_or_None). Never raises -- any failure is 'absent'."""
    path = shutil.which(executable)
    if path is None:
        return False, None
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001 -- any launch failure means "unusable" == absent
        return False, None
    if proc.returncode != 0:
        return False, None
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    return True, (first[0] if first else None)


# Each mandatory dependency: (name, probe-executable, remedy-if-missing).
_MANDATORY = (
    (
        "git",
        "git",
        "install git from https://git-scm.com/downloads (or your OS package manager), "
        "then relaunch.",
    ),
    (
        "gmlcache",
        "gmlcache",
        "install generic-ml-cache (the execution arm) -- e.g. 'pip install generic-ml-cache' "
        "into the same environment -- then relaunch.",
    ),
)


def check() -> DependencyReport:
    """Probe every mandatory dependency. Pure of side effects; never raises."""
    statuses = []
    for name, exe, remedy in _MANDATORY:
        present, version = _probe(exe)
        statuses.append(
            DependencyStatus(
                name=name,
                present=present,
                version=version,
                remedy=None if present else remedy,
            )
        )
    return DependencyReport(statuses=tuple(statuses))


def format_missing(report: DependencyReport) -> str:
    """A user-facing message listing what's missing and how to fix it."""
    lines = [
        "generic-ml-workflow cannot start: a required dependency is missing.",
        "",
    ]
    for s in report.missing:
        lines.append(f"  \u2717 {s.name} -- not found")
        lines.append(f"      {s.remedy}")
    lines.append("")
    lines.append("Both git and gmlcache are required: git is the versioning spine, and")
    lines.append("gmlcache is the execution arm through which every model call is made.")
    return "\n".join(lines)


def require(report: DependencyReport | None = None) -> DependencyReport:
    """The hard gate. Returns the report if all dependencies are present; otherwise
    raises :class:`DependencyError` carrying the user-facing message. The launch
    wrapper calls this before building the workspace."""
    report = report if report is not None else check()
    if not report.satisfied:
        raise DependencyError(format_missing(report))
    return report
