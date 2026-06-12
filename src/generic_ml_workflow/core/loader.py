# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""loader.py -- read a workflow (declarative YAML data) into validated contract
objects. The workflow is DATA: the wiring between steps lives in each input's
``from``, so a workflow is reconfigurable without touching code. A visual canvas,
if ever built, would be a view over exactly this file.

Groundwork note: this module is converted salvage. Its real slice is 0.0.3, which
adds the typed-input contract, the ports-and-bindings wiring of DESIGN.md §7 with
precise load errors and the dead-branch lint, ``/list`` and ``/validate``, and
the bundled demo definition.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from generic_ml_workflow.core.contract import (
    InputSpec,
    Lifespan,
    OutputKind,
    OutputSpec,
    StepNature,
    StepSpec,
    Tier,
    Workflow,
)


def load_workflow(path: str | Path) -> Workflow:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: workflow must be a mapping")
    name = data.get("name") or path.stem
    steps = tuple(_step(s) for s in (data.get("steps") or []))
    wf = Workflow(name=name, steps=steps)
    wf.validate()
    return wf


def _step(s: dict) -> StepSpec:
    nature = StepNature(s["nature"])
    inputs = tuple(_input(i) for i in (s.get("inputs") or []))
    outputs = tuple(_output(o) for o in (s.get("outputs") or []))
    return StepSpec(
        id=s["id"],
        nature=nature,
        tier=Tier(s.get("tier", "medium")),
        inputs=inputs,
        outputs=outputs,
        needs=tuple(s.get("needs") or ()),
        cap=s.get("cap"),
        methodology=s.get("methodology"),
        entrypoint=s.get("entrypoint"),
        unattended=bool(s.get("unattended", False)),
    )


def _input(i: dict) -> InputSpec:
    src = i.get("from")
    if src == "literal":
        return InputSpec(name=i["name"], from_literal=True)
    return InputSpec(name=i["name"], from_step=src)  # "<step>.<output>"


def _output(o: dict) -> OutputSpec:
    return OutputSpec(
        name=o["name"],
        lifespan=Lifespan(o["lifespan"]),
        kind=OutputKind(o["kind"]),
        filename=o["filename"],
    )
