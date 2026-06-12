# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""runner.py -- run an EXECUTABLE step (user-supplied origin) in isolation.

The executable nature: deterministic code the engine runs, declared inputs in,
declared outputs out (DESIGN.md SS5). This slice (0.0.5) handles the
**user-supplied** origin -- a script or binary the step config names as its
``entrypoint``. The runtime cannot tell the origins apart (invariant 6); generated
bodies and the pure-ML fallback arrive in later slices.

Isolation (mirrors gmlcache's discipline): each step runs in its **own** run
folder, created empty. Declared inputs are materialized into it before launch;
declared outputs are collected from it after. The engine attributes every file in
that folder to this run -- nothing leaks from or into the user's space.

This module executes; it does not decide. The caller resolves inputs to concrete
values and tells the runner where each declared output should land. The runner:
  1. makes the run folder,
  2. writes each input value to a file (or copies an input file) inside it,
  3. runs the entrypoint with the run folder as cwd,
  4. verifies each declared output file exists, fingerprints it (sha256),
  5. returns the result (exit code, stdout/stderr, produced output pointers).

Timing is split (DESIGN SS4): the runner measures client/execution time; user
reaction time is the caller's concern.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from generic_ml_workflow.core.contract import StepNature, StepSpec


class RunnerError(Exception):
    """The step could not be run as declared (bad entrypoint, missing output)."""


@dataclass(frozen=True)
class ProducedOutput:
    name: str  # the output port / product name
    path: Path  # absolute path in the run folder
    sha256: str


@dataclass(frozen=True)
class StepResult:
    step_id: str
    attempt: int
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    outputs: tuple[ProducedOutput, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_executable(
    spec: StepSpec,
    run_dir: Path,
    inputs: dict[str, object],
    *,
    attempt: int = 1,
    timeout: float = 300.0,
) -> StepResult:
    """Run a user-supplied executable step in an isolated ``run_dir``.

    ``inputs`` maps each declared input port name to a concrete value:
      * a ``Path`` -> the file is copied into the run folder under the port name,
      * anything else -> ``str(value)`` is written to a file named after the port.
    The entrypoint is launched with ``run_dir`` as cwd. Each declared output's
    ``filename`` must exist in ``run_dir`` afterwards, or the step failed.
    """
    if spec.nature is not StepNature.EXECUTABLE:
        raise RunnerError(f"step '{spec.id}' is not executable")
    if not spec.entrypoint:
        raise RunnerError(f"step '{spec.id}' declares no entrypoint")

    # 1. fresh, empty, isolated run folder
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    # 2. materialize declared inputs into the run folder
    for name, value in inputs.items():
        dest = run_dir / name
        if isinstance(value, Path):
            shutil.copyfile(value, dest)
        else:
            dest.write_text(str(value), encoding="utf-8")

    # 3. run the entrypoint with the run folder as cwd
    argv = _resolve_entrypoint(spec.entrypoint)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RunnerError(f"step '{spec.id}': entrypoint not found: {spec.entrypoint}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"step '{spec.id}': timed out after {timeout}s") from exc
    duration = time.monotonic() - start

    # 4. collect declared outputs (only if the process succeeded)
    produced: list[ProducedOutput] = []
    if proc.returncode == 0:
        for out in spec.outputs:
            target = run_dir / out.filename
            if not target.exists():
                raise RunnerError(
                    f"step '{spec.id}' declared output '{out.name}' ({out.filename})"
                    " but the file was not produced"
                )
            produced.append(ProducedOutput(name=out.name, path=target, sha256=_sha256(target)))

    return StepResult(
        step_id=spec.id,
        attempt=attempt,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_seconds=duration,
        outputs=tuple(produced),
    )


def _resolve_entrypoint(entrypoint: str) -> list[str]:
    """Turn the declared entrypoint into an argv. A path to an existing file is run
    directly (via sh for a script, or as a binary); otherwise it is split as a
    command line. Kept simple for 0.0.5; the adapter for richer invocation grows
    later."""
    p = Path(entrypoint)
    if p.exists():
        if p.suffix == ".py":
            import sys

            return [sys.executable, str(p)]
        if p.suffix in (".sh", ""):
            return ["sh", str(p)]
        return [str(p)]
    # not a file path: treat as a shell-style command line
    return ["sh", "-c", entrypoint]
