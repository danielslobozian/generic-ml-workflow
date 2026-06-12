# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""loader.py -- read a workflow (declarative YAML data) into the validated
contract. The workflow is DATA: steps declare local ports; the workflow's
``bindings`` block is the only wiring (DESIGN.md SS7). A visual canvas, if ever
built, would be a view over exactly this file.

YAML shape::

    name: demo
    input_type: url
    steps:
      - id: fetch
        nature: executable
        entrypoint: fetch_page
        tier: low
        inputs:
          - {name: url, require: run_input}
        outputs:
          - {name: page, lifespan: durable, kind: file, filename: page.html}
      - id: summarize
        nature: interpretable
        cap: summarizer
        inputs:
          - {name: source_text, require: artifact}
        outputs:
          - {name: summary, lifespan: durable, kind: file, filename: summary.md}
    bindings:
      - {step: summarize, port: source_text, product: page_text}

Every malformed field raises ``WorkflowError`` with the file name and the reason.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from generic_ml_workflow.core.contract import (
    Binding,
    InputPort,
    InputType,
    Lifespan,
    OutputKind,
    OutputPort,
    Requirement,
    StepNature,
    StepSpec,
    Tier,
    Workflow,
    WorkflowError,
)


def load_workflow(path: str | Path) -> Workflow:
    """Parse a workflow YAML into the contract. Raises WorkflowError on any
    malformed field; does NOT run validate() -- the caller decides when to."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkflowError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError(f"{path}: a workflow must be a mapping")

    name = data.get("name") or path.stem
    raw_type = data.get("input_type", "freestyle")
    try:
        input_type = InputType(raw_type)
    except ValueError as exc:
        raise WorkflowError(
            f"{path}: unknown input_type '{raw_type}'"
            f" (expected one of: {', '.join(t.value for t in InputType)})"
        ) from exc

    steps = tuple(_step(path, s) for s in (data.get("steps") or []))
    bindings = tuple(_binding(path, b) for b in (data.get("bindings") or []))
    return Workflow(name=name, input_type=input_type, steps=steps, bindings=bindings)


def _enum(path: Path, cls, value, field: str):
    try:
        return cls(value)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in cls)
        raise WorkflowError(
            f"{path}: invalid {field} '{value}' (expected one of: {allowed})"
        ) from exc


def _require(path: Path, mapping: dict, key: str, ctx: str):
    if key not in mapping:
        raise WorkflowError(f"{path}: {ctx} is missing required field '{key}'")
    return mapping[key]


def _step(path: Path, s: dict) -> StepSpec:
    if not isinstance(s, dict):
        raise WorkflowError(f"{path}: each step must be a mapping, got {type(s).__name__}")
    sid = _require(path, s, "id", "a step")
    nature = _enum(path, StepNature, _require(path, s, "nature", f"step '{sid}'"), "nature")
    return StepSpec(
        id=sid,
        nature=nature,
        tier=_enum(path, Tier, s.get("tier", "medium"), "tier"),
        inputs=tuple(_input(path, sid, i) for i in (s.get("inputs") or [])),
        outputs=tuple(_output(path, sid, o) for o in (s.get("outputs") or [])),
        cap=s.get("cap"),
        methodology=s.get("methodology"),
        entrypoint=s.get("entrypoint"),
        unattended=bool(s.get("unattended", False)),
    )


def _input(path: Path, sid: str, i: dict) -> InputPort:
    name = _require(path, i, "name", f"an input of step '{sid}'")
    requirement = _enum(
        path,
        Requirement,
        _require(path, i, "require", f"input '{name}' of step '{sid}'"),
        "require",
    )
    return InputPort(name=name, requirement=requirement)


def _output(path: Path, sid: str, o: dict) -> OutputPort:
    name = _require(path, o, "name", f"an output of step '{sid}'")
    return OutputPort(
        name=name,
        lifespan=_enum(
            path, Lifespan, _require(path, o, "lifespan", f"output '{name}'"), "lifespan"
        ),
        kind=_enum(path, OutputKind, _require(path, o, "kind", f"output '{name}'"), "kind"),
        filename=_require(path, o, "filename", f"output '{name}' of step '{sid}'"),
    )


def _binding(path: Path, b: dict) -> Binding:
    if not isinstance(b, dict):
        raise WorkflowError(f"{path}: each binding must be a mapping")
    return Binding(
        step_id=_require(path, b, "step", "a binding"),
        port=_require(path, b, "port", "a binding"),
        product=_require(path, b, "product", "a binding"),
    )
