# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""probe.py -- rung 2 of the validation ladder: the recorded probe.

Rung 1 (``reconcile``) is free and only name-deep: it confirms a configured client
is present and its model is listed. It cannot tell you the triple actually *runs*
(auth, access, the model accepting the effort). Rung 2 finds out the only way there
is -- by running it -- but as cheaply as possible: **one tiny shot per unique
``(client, model, effort)`` triple**, taken through the same gmlcache seam every
other shot uses (so it inherits run-folder isolation and supervision). Because a
probe is a real spend, its verdict is meant to be *remembered* as an event and the
triple probed once; this module produces that verdict, the orchestration layer
stores and dedupes it.

The verdict is deliberately thin: did it run, and -- when it did not -- the client's
own error, kept **verbatim**. The list is advisory; the run is the truth, and a
failed probe's truth is whatever the client said, not our gloss on it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from generic_ml_workflow.core import eventtypes as et
from generic_ml_workflow.core.contract import StepNature, StepSpec
from generic_ml_workflow.core.envelope import build_envelope
from generic_ml_workflow.core.shotrunner import Resolution, ShotError, run_shot

#: The probe's prompt is intentionally trivial -- the point is the verdict, not the
#: answer. A single-word reply keeps the spend to the floor.
_PROBE_CONTEXT = "You are a readiness probe. Answer with a single word."
_PROBE_PROMPT = "Reply with the single word READY."


def probe_stream_key(client: str, model: str, effort: str | None) -> str:
    """The stable per-triple stream key a verdict is recorded under. A triple's
    probes form their own event stream, so 'the latest verdict for this triple' is
    just the last event on this key -- dedup and re-probe history fall out of replay,
    and probe streams never appear among workflow executions."""
    return f"probe:{client}/{model}/{effort or '-'}"


def run_probe(
    resolution: Resolution,
    run_dir: Path,
    *,
    mode: str = "cache",
    timeout: float = 120.0,
    gmlcache: str = "gmlcache",
    _runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> et.ProbeRecorded:
    """Take one tiny shot for ``resolution`` and return its verdict.

    Never raises for a *client* failure -- a nonzero exit, or a gmlcache/seam error,
    is exactly what a probe is here to discover, so it becomes ``ok=False`` with the
    client's message kept verbatim. ``_runner`` is injectable for tests, mirroring
    the shot runner.
    """
    spec = StepSpec(
        id="__probe__",
        nature=StepNature.INTERPRETABLE,
        cap="readiness probe",
        outputs=(),  # a probe declares no outputs: its product is the verdict
    )
    envelope = build_envelope(_PROBE_CONTEXT, _PROBE_PROMPT, ())

    try:
        result = run_shot(
            spec,
            envelope,
            resolution,
            run_dir,
            mode=mode,
            timeout=timeout,
            gmlcache=gmlcache,
            _runner=_runner,
        )
    except ShotError as exc:
        # the shot could not even be taken (gmlcache missing, timeout, bad seam) --
        # a failed verdict carrying the reason verbatim, not an exception upward.
        return et.ProbeRecorded(
            client=resolution.client,
            model=resolution.model,
            effort=resolution.effort,
            ok=False,
            error=str(exc),
        )

    error = None if result.ok else (result.stderr.strip() or f"exit {result.exit_code}")
    return et.ProbeRecorded(
        client=resolution.client,
        model=resolution.model,
        effort=resolution.effort,
        ok=result.ok,
        error=error,
    )
