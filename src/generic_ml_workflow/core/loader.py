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
    ProviderBinding,
    ProviderPlane,
    ProviderProperty,
    ProviderSpec,
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
    provider_bindings = tuple(
        _provider_binding(path, b) for b in (data.get("provider_bindings") or [])
    )
    return Workflow(
        name=name,
        input_type=input_type,
        steps=steps,
        bindings=bindings,
        provider_bindings=provider_bindings,
    )


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


def _provider_binding(path: Path, b: dict) -> ProviderBinding:
    if not isinstance(b, dict):
        raise WorkflowError(f"{path}: each provider binding must be a mapping")
    return ProviderBinding(
        kind=_require(path, b, "kind", "a provider binding"),
        alias=_require(path, b, "alias", "a provider binding"),
    )


def load_provider(path: str | Path) -> ProviderSpec:
    """Parse a provider description (meta-code YAML) into a :class:`ProviderSpec`.

    Shape::

        kind: issue_tracker
        properties:
          - {name: base_url, plane: config, description: "API base URL"}
          - {name: token, plane: credential, description: "API token"}

    Holds only the schema, never values. Raises ``WorkflowError`` on any malformed
    field, named with the file."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowError(f"{path.name}: cannot read provider description: {exc}") from exc
    if not isinstance(doc, dict):
        raise WorkflowError(f"{path.name}: provider description must be a mapping")
    kind = doc.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise WorkflowError(f"{path.name}: provider description needs a non-empty 'kind'")
    raw_props = doc.get("properties", [])
    if not isinstance(raw_props, list):
        raise WorkflowError(f"{path.name}: 'properties' must be a list")
    props: list[ProviderProperty] = []
    for entry in raw_props:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise WorkflowError(f"{path.name}: each property needs a 'name'")
        plane_raw = entry.get("plane")
        try:
            plane = ProviderPlane(plane_raw)
        except ValueError:
            raise WorkflowError(
                f"{path.name}: property '{entry['name']}' has invalid plane {plane_raw!r} "
                "(use 'config' or 'credential')"
            ) from None
        props.append(
            ProviderProperty(
                name=entry["name"],
                plane=plane,
                description=str(entry.get("description", "")),
                required=bool(entry.get("required", True)),
            )
        )
    return ProviderSpec(kind=kind, properties=tuple(props))
