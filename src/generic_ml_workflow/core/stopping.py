# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""stopping.py -- clean stop: halt a run between steps, and tear down the child a
step is running mid-step (DESIGN.md invariant 24).

The engine is synchronous and thread-unaware; a stop is a flag it *checks*, never
a thread it manages. A surface (the REPL's `/stop` or Escape) flips the flag from
another thread. Two effects, one object:

  * **between steps** the run loop consults ``requested()`` at each boundary and, if
    set, stops cleanly (records a *stopped* run, not a *failed* one);
  * **mid-step** ``request()`` also tears down the child the current step is running
    (the cache subprocess for a shot, the executable for an executable step) -- the
    child runs in its own process group, so the teardown reaches its descendants.
    For a shot that child is gmlcache, which owns tearing the client down from there
    (capability-sinking, invariant 3); the engine never reaches past its own child.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Generator
from typing import Any


def _group_kwargs() -> dict[str, Any]:
    """Start the child in its own process group/session so one signal reaches the
    whole tree -- no orphaned grandchildren."""
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Best-effort teardown of a child and its group. A race where it already
    exited is fine -- that is the outcome we wanted."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass


class StopControl:
    """A run's stop flag, plus a handle to the child it is currently running so a
    stop can reach mid-step. Thread-safe: the surface calls ``request()`` from the
    prompt thread while the engine runs on a worker."""

    def __init__(self) -> None:
        self._requested = threading.Event()
        self._lock = threading.Lock()
        self._child: subprocess.Popen[str] | None = None

    def request(self) -> None:
        """Ask the run to stop. Sets the flag (the loop stops at the next boundary)
        and tears down any child a step is running right now (so a long step does
        not have to finish first)."""
        self._requested.set()
        with self._lock:
            child = self._child
        if child is not None:
            _terminate(child)

    def requested(self) -> bool:
        return self._requested.is_set()

    @contextlib.contextmanager
    def watching(self, child: subprocess.Popen[str]) -> Generator[None]:
        """While a step's child runs, register it so ``request()`` can reach it. If a
        stop was already requested before registration, tear it down at once."""
        with self._lock:
            self._child = child
        if self._requested.is_set():
            _terminate(child)
        try:
            yield
        finally:
            with self._lock:
                self._child = None


def run_supervised(
    argv: list[str],
    *,
    cwd: str | os.PathLike[str],
    timeout: float | None,
    stop: StopControl | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a child to completion, killable mid-flight by ``stop``. A drop-in for
    ``subprocess.run(capture_output=True, text=True)``: returns a CompletedProcess,
    raises ``FileNotFoundError`` for a missing executable and ``TimeoutExpired`` on
    timeout (killing the group first), so callers keep their existing error paths.
    ``env``, if given, is overlaid on the current environment for the child only.
    """
    use_stdin = input_text is not None
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.PIPE if use_stdin else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=({**os.environ, **env} if env else None),
        **_group_kwargs(),
    )
    register = stop.watching(proc) if stop is not None else contextlib.nullcontext()
    with register:
        try:
            out, err = proc.communicate(input=input_text if use_stdin else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            proc.communicate()  # reap the killed child
            raise
    # communicate() has reaped the child, so returncode is set (never None here).
    exit_code = proc.returncode if proc.returncode is not None else 0
    return subprocess.CompletedProcess(argv, exit_code, out or "", err or "")
