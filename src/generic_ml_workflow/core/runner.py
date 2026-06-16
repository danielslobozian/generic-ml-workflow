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
from generic_ml_workflow.core import builtin_bodies
from generic_ml_workflow.core.stopping import StopControl, run_supervised


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
    stop: StopControl | None = None,
    env: dict[str, str] | None = None,
    provider_instance: dict[str, object] | None = None,
) -> StepResult:
    """Run a user-supplied executable step in an isolated ``run_dir``.

    ``inputs`` maps each declared input port name to a concrete value:
      * a ``Path`` -> the file is copied into the run folder under the port name,
      * anything else -> ``str(value)`` is written to a file named after the port.
    The entrypoint is launched with ``run_dir`` as cwd. Each declared output's
    ``filename`` must exist in ``run_dir`` afterwards, or the step failed. When
    ``stop`` is given and a stop is requested mid-run, the child is torn down and
    the step returns a non-zero (killed) result -- the orchestrator reads the stop
    and records the run as stopped, not failed.
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

    # 3. run -- either a built-in body the engine ships, or a user subprocess
    start = time.monotonic()
    if builtin_bodies.is_builtin(spec.entrypoint):
        returncode, stdout, stderr = _run_builtin(spec, run_dir, inputs, provider_instance)
    else:
        argv = _resolve_entrypoint(spec.entrypoint)
        try:
            proc = run_supervised(argv, cwd=run_dir, timeout=timeout, stop=stop, env=env)
        except FileNotFoundError as exc:
            raise RunnerError(f"step '{spec.id}': entrypoint not found: {spec.entrypoint}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(f"step '{spec.id}': timed out after {timeout}s") from exc
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    duration = time.monotonic() - start

    # 4. collect declared outputs (only if the run succeeded)
    produced: list[ProducedOutput] = []
    if returncode == 0:
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
        exit_code=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        outputs=tuple(produced),
    )


def _run_builtin(
    spec: StepSpec,
    run_dir: Path,
    inputs: dict[str, object],
    provider_instance: dict[str, object] | None,
) -> tuple[int, str, str]:
    """Run an engine-shipped body. Today only ``fetch``: read the step's ``path``
    input from the bound provider instance, host-pinned, and write the response to
    the step's first declared output. A misconfigured step raises ``RunnerError`` (a
    setup error); a refused/failed fetch returns a non-zero result (a runtime
    failure), both surfacing as a failed step. The token stays in-process."""
    name = builtin_bodies.builtin_name(spec.entrypoint)
    if name != "fetch":
        raise RunnerError(f"step '{spec.id}': unknown builtin '{name}'")
    path_value = inputs.get("path")
    if path_value is None:
        raise RunnerError(f"step '{spec.id}': builtin fetch needs a 'path' input")
    if not spec.outputs:
        raise RunnerError(f"step '{spec.id}': builtin fetch declares no output to write")
    out_spec = spec.outputs[0]
    try:
        body = builtin_bodies.run_fetch(str(path_value), provider_instance or {})
    except builtin_bodies.BuiltinError as exc:
        return 1, "", str(exc)
    (run_dir / out_spec.filename).write_bytes(body)
    return 0, f"fetched {len(body)} bytes", ""


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
