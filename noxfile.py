# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Task automation for generic-ml-workflow.

``noxfile.py`` is the single source of truth for the project's gates -- lint,
format, type-check, tests, coverage. CI (``.github/workflows/``) is a *thin caller*
of these sessions, so there is exactly one definition of "what green means": the
gate that runs locally is byte-for-byte the gate that runs in CI. No local/CI drift.

Gate sessions build their own hermetic environments via the ``uv`` backend, synced
from the committed ``uv.lock`` (``--frozen``: the locked versions are installed as-is,
never re-resolved), so every gate runs the exact pinned resolution locally and in CI.
This uses ``--frozen`` rather than ``--locked`` on purpose: the ``--locked`` up-to-date
re-check re-resolves against the live index and is environment-sensitive (a locally
valid lock can read as "needs update" on a fresh CI runner); ``--frozen`` sidesteps
that by trusting the committed lock as the source of truth. The persistent root
``.venv`` is built only by
``nox -s dev`` and is the IDE's interpreter; the gate sessions never touch it.

This project is single-package today (``src/generic_ml_workflow``). Two gates from the
sibling ``generic-ml-cache`` setup are intentionally absent until the hexagonal
restructure cuts the rings and package split: the import-linter contracts (an
``imports`` session over ``.importlinter``) and the built-wheel content check
(``wheels``). They are authored with the rings they police, not before.

Usage::

    nox                       # the default gates: lint, typecheck, tests
    nox -s tests              # the suite across every supported interpreter
    nox -s tests -- -k name   # args after -- pass through to pytest
    nox -s coverage           # enforce the coverage floor + write coverage.xml (Sonar's input)
    nox -s green              # the whole local gate in one environment
    nox -s dev                # (re)build the IDE .venv at ./.venv
"""

from __future__ import annotations

import os
import sys

import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["lint", "typecheck", "tests"]

# The import package under test, used for ``--cov=``.
PACKAGE = "generic_ml_workflow"

# The interpreters the suite is gated on. Mirrors the CI matrix in
# ``.github/workflows/ci.yml`` so ``nox --python <ver> -s tests`` selects a matching
# session -- each matrix job runs the suite under exactly one of these.
PYTHON_VERSIONS: tuple[str, ...] = ("3.11", "3.12", "3.13")

# The package-wide coverage floor (matches the cache's baseline).
COVERAGE_FLOOR = 80


def _session_python(session: nox.Session) -> str:
    """Path to this session's interpreter (to override the IDE-only ``.venv`` pin).

    The executable is ``python.exe`` on Windows and ``python`` elsewhere -- uv's
    ``--python <path>`` needs the exact file, not the extensionless stem.
    """
    exe = "python.exe" if sys.platform == "win32" else "python"
    return os.path.join(session.virtualenv.bin, exe)


def _install(session: nox.Session) -> None:
    """Sync the session env from the committed ``uv.lock``.

    ``--frozen`` installs the committed lock's exact versions without re-resolving,
    so the gate is deterministic across environments. Installs the package editable
    with its dev toolchain (the ``dev`` extra: ruff, pyright, pytest, coverage).
    """
    session.run_install(
        "uv",
        "sync",
        "--frozen",
        "--extra",
        "dev",
        "--python",
        _session_python(session),
        external=True,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


@nox.session
def lint(session: nox.Session) -> None:
    """Ruff lint + format check (ruff at the locked version)."""
    _install(session)
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")


@nox.session
def typecheck(session: nox.Session) -> None:
    """Pyright static type checking (strict; see ``pyrightconfig.json``).

    Pointed at this session's interpreter via ``--pythonpath`` so it resolves imports
    from the hermetic env rather than the IDE-only root ``.venv``.
    """
    _install(session)
    session.run("pyright", "--pythonpath", _session_python(session))


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """The test suite under one interpreter (the matrix selects which)."""
    _install(session)
    session.run("python", "-m", "pytest", *session.posargs)


@nox.session
def coverage(session: nox.Session) -> None:
    """Enforce the coverage floor and write ``coverage.xml`` -- the exact file Sonar
    ingests. The scanner itself stays in CI (it needs the token + scan action); this
    session reproduces the number Sonar will report before any push.
    """
    _install(session)
    session.run(
        "python",
        "-m",
        "pytest",
        f"--cov={PACKAGE}",
        "--cov-report=xml:coverage.xml",
        f"--cov-fail-under={COVERAGE_FLOOR}",
    )


@nox.session
def green(session: nox.Session) -> None:
    """The whole local gate in one environment: lint, format, typecheck, tests+coverage."""
    _install(session)
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")
    session.run("pyright", "--pythonpath", _session_python(session))
    session.run(
        "python",
        "-m",
        "pytest",
        f"--cov={PACKAGE}",
        "--cov-report=xml:coverage.xml",
        f"--cov-fail-under={COVERAGE_FLOOR}",
    )


@nox.session(venv_backend="none")
def dev(session: nox.Session) -> None:
    """(Re)build the persistent root ``.venv`` -- the IDE interpreter.

    This is the one env the gate sessions never use. It holds the package editable
    plus the dev toolchain and pre-commit, so opening the project in an editor and
    running ``git commit`` both work with no further setup.
    """
    session.run(
        "uv",
        "sync",
        "--frozen",
        "--extra",
        "dev",
        external=True,
        env={"UV_PROJECT_ENVIRONMENT": ".venv"},
    )
