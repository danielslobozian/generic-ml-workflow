# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""shotrunner.py -- run an INTERPRETABLE step (a shot) through gmlcache.

This engine executes no model call itself (invariant 3): every shot goes through
gmlcache as a subprocess. The shot runner builds *what* to call -- the
``[context, prompt, files]`` envelope (SS8), the concrete client/model/effort -- and
invokes ``gmlcache run`` in the step's isolated run folder, where gmlcache is the
caller, the cache, and the replayer.

We invoke ``gmlcache run --json``, so stdout is a machine-readable envelope (the
answer plus normalized usage and the run status), not the raw client output;
gmlcache still writes produced files into its working directory (which this runner
sets to the step's run folder) and exits with the client's exit code. So this
runner captures the envelope (lifting the answer and usage out of it), keeps
stderr/exit, and then collects the step's declared output files from the run folder
-- the same collection discipline as the executable runner. If the envelope is
absent or unparseable (an older gmlcache, or an error before it), the runner
degrades to treating stdout as the raw answer with usage unknown.

The cassette store makes runs cacheable and replayable, but as of gmlcache 0.0.7
the store is the cache's own (config-owned) concern: the engine passes neither a
``--store`` nor an ``--output-dir``, so it dictates no location. In offline mode a
missing cassette is gmlcache's error, surfaced verbatim (invariant: the run is the
truth). Building the argv is pure and unit-testable; the subprocess call is
exercised through an injected runner in tests and a recorded cassette in CI.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from generic_ml_workflow.core.contract import StepNature, StepSpec
from generic_ml_workflow.core.envelope import Envelope
from generic_ml_workflow.core.stopping import StopControl, run_supervised
from generic_ml_workflow.core.usage import Usage, usage_from_envelope


class ShotError(Exception):
    """The shot could not be run as declared (bad resolution, missing output)."""


@dataclass(frozen=True)
class Resolution:
    """The concrete client/model/effort a tier resolved to (SS9). Effort is
    optional -- omitted when the client uses its own default."""

    client: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class ProducedOutput:
    name: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class ShotResult:
    step_id: str
    attempt: int
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    outputs: tuple[ProducedOutput, ...] = field(default_factory=tuple)
    #: normalized usage lifted from gmlcache's --json envelope; None when the
    #: envelope carried no usage (or could not be parsed -- the run still stands).
    usage: "Usage | None" = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_argv(
    envelope: Envelope,
    resolution: Resolution,
    run_dir: Path,
    *,
    mode: str = "cache",
    gmlcache: str = "gmlcache",
) -> list[str]:
    """Construct the ``gmlcache run`` argv. Pure -- builds the command, runs
    nothing. The context and prompt are written to files in ``run_dir`` by the
    caller before launch; their paths are passed here.

    No ``--store`` or ``--output-dir``: as of gmlcache 0.0.7 the cassette store is
    the cache's own (config-owned) concern, and gmlcache writes produced files into
    its working directory -- which the caller sets to ``run_dir`` -- exactly as the
    real client would. The engine therefore dictates neither location.
    """
    context_file = run_dir / "_context.txt"
    prompt_file = run_dir / "_prompt.txt"
    argv = [
        gmlcache,
        "run",
        "--client",
        resolution.client,
        "--model",
        resolution.model,
        "--context-file",
        str(context_file),
        "--prompt-file",
        str(prompt_file),
        "--mode",
        mode,
        # Ask for the machine-readable envelope so we read back normalized usage
        # (and the answer) without parsing client output ourselves. gmlcache still
        # writes produced files into the run folder; --json only changes stdout.
        "--json",
    ]
    if resolution.effort:
        argv += ["--effort", resolution.effort]
    for f in envelope.files:
        argv += ["--input-file", str(f)]
    return argv


def run_shot(
    spec: StepSpec,
    envelope: Envelope,
    resolution: Resolution,
    run_dir: Path,
    *,
    mode: str = "cache",
    attempt: int = 1,
    timeout: float = 600.0,
    gmlcache: str = "gmlcache",
    stop: "StopControl | None" = None,
    _runner=None,
) -> ShotResult:
    """Run an interpretable step through gmlcache in an isolated run folder.

    ``_runner`` is injectable so the subprocess call can be exercised in tests
    without a real gmlcache; in CI a recorded cassette + offline mode drives the
    real binary deterministically. When ``_runner`` is left ``None`` the real,
    **supervised** runner is used: ``stop`` (if given) can tear down the gmlcache
    subprocess mid-shot, and gmlcache in turn tears down the client (capability
    sinks to the cache, invariant 3) -- the engine never reaches past its child.
    """
    if spec.nature is not StepNature.INTERPRETABLE:
        raise ShotError(f"step '{spec.id}' is not an interpretable (shot) step")

    # isolated, empty run folder (same discipline as the executable runner)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    # write the envelope's context + prompt into the run folder
    (run_dir / "_context.txt").write_text(envelope.context, encoding="utf-8")
    (run_dir / "_prompt.txt").write_text(envelope.prompt, encoding="utf-8")

    argv = build_argv(envelope, resolution, run_dir, mode=mode, gmlcache=gmlcache)

    start = time.monotonic()
    try:
        if _runner is None:
            proc = run_supervised(argv, cwd=run_dir, timeout=timeout, stop=stop)
        else:
            proc = _runner(argv, cwd=str(run_dir), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ShotError(
            f"step '{spec.id}': gmlcache not found -- it is the execution arm and must be installed"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ShotError(f"step '{spec.id}': shot timed out after {timeout}s") from exc
    duration = time.monotonic() - start

    # gmlcache ran with --json: stdout is a machine envelope (status / exit / files
    # / usage / stdout), not the raw answer. Lift the answer and the normalized
    # usage out of it. If it is not the expected JSON -- an older gmlcache, or an
    # error printed before the envelope -- degrade: treat stdout as the raw answer
    # and leave usage unknown, so the run still stands and the cost view stays quiet
    # rather than breaking.
    raw_stdout = proc.stdout or ""
    answer = raw_stdout
    usage: Usage | None = None
    try:
        envelope = json.loads(raw_stdout)
        if isinstance(envelope, dict) and "stdout" in envelope:
            answer = envelope.get("stdout") or ""
            usage = usage_from_envelope(envelope)
    except (json.JSONDecodeError, ValueError):
        pass

    produced: list[ProducedOutput] = []
    if proc.returncode == 0:
        for out in spec.outputs:
            target = run_dir / out.filename
            if not target.exists():
                raise ShotError(
                    f"step '{spec.id}' declared output '{out.name}' ({out.filename})"
                    " but the shot did not produce it"
                )
            produced.append(ProducedOutput(name=out.name, path=target, sha256=_sha256(target)))

    return ShotResult(
        step_id=spec.id,
        attempt=attempt,
        exit_code=proc.returncode,
        stdout=answer,
        stderr=proc.stderr or "",
        duration_seconds=duration,
        outputs=tuple(produced),
        usage=usage,
    )
