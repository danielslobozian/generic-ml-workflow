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

from enum import Enum
from pathlib import Path
from typing import Any, TypeVar, cast

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

_EnumT = TypeVar("_EnumT", bound=Enum)


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
    doc = cast(dict[str, Any], data)

    name = doc.get("name") or path.stem
    raw_type = doc.get("input_type", "freestyle")
    try:
        input_type = InputType(raw_type)
    except ValueError as exc:
        raise WorkflowError(
            f"{path}: unknown input_type '{raw_type}'"
            f" (expected one of: {', '.join(t.value for t in InputType)})"
        ) from exc

    steps = tuple(_step(path, s) for s in cast(list[Any], doc.get("steps") or []))
    bindings = tuple(_binding(path, b) for b in cast(list[Any], doc.get("bindings") or []))
    provider_bindings = tuple(
        _provider_binding(path, b) for b in cast(list[Any], doc.get("provider_bindings") or [])
    )
    return Workflow(
        name=name,
        input_type=input_type,
        steps=steps,
        bindings=bindings,
        provider_bindings=provider_bindings,
    )


def _enum(path: Path, cls: type[_EnumT], value: object, field: str) -> _EnumT:
    try:
        return cls(value)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in cls)
        raise WorkflowError(
            f"{path}: invalid {field} '{value}' (expected one of: {allowed})"
        ) from exc


def _require(path: Path, mapping: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in mapping:
        raise WorkflowError(f"{path}: {ctx} is missing required field '{key}'")
    return mapping[key]


def _step(path: Path, s: object) -> StepSpec:
    if not isinstance(s, dict):
        raise WorkflowError(f"{path}: each step must be a mapping, got {type(s).__name__}")
    step_map = cast(dict[str, Any], s)
    sid = _require(path, step_map, "id", "a step")
    nature = _enum(path, StepNature, _require(path, step_map, "nature", f"step '{sid}'"), "nature")
    raw_inputs = cast(list[Any], step_map.get("inputs") or [])
    raw_outputs = cast(list[Any], step_map.get("outputs") or [])
    return StepSpec(
        id=sid,
        nature=nature,
        tier=_enum(path, Tier, step_map.get("tier", "medium"), "tier"),
        inputs=tuple(_input(path, sid, i) for i in raw_inputs),
        outputs=tuple(_output(path, sid, o) for o in raw_outputs),
        cap=step_map.get("cap"),
        methodology=step_map.get("methodology"),
        entrypoint=step_map.get("entrypoint"),
        unattended=bool(step_map.get("unattended", False)),
    )


def _input(path: Path, sid: str, i: dict[str, Any]) -> InputPort:
    name = _require(path, i, "name", f"an input of step '{sid}'")
    requirement = _enum(
        path,
        Requirement,
        _require(path, i, "require", f"input '{name}' of step '{sid}'"),
        "require",
    )
    return InputPort(name=name, requirement=requirement)


def _output(path: Path, sid: str, o: dict[str, Any]) -> OutputPort:
    name = _require(path, o, "name", f"an output of step '{sid}'")
    return OutputPort(
        name=name,
        lifespan=_enum(
            path, Lifespan, _require(path, o, "lifespan", f"output '{name}'"), "lifespan"
        ),
        kind=_enum(path, OutputKind, _require(path, o, "kind", f"output '{name}'"), "kind"),
        filename=_require(path, o, "filename", f"output '{name}' of step '{sid}'"),
    )


def _binding(path: Path, b: object) -> Binding:
    if not isinstance(b, dict):
        raise WorkflowError(f"{path}: each binding must be a mapping")
    binding_map = cast(dict[str, Any], b)
    return Binding(
        step_id=_require(path, binding_map, "step", "a binding"),
        port=_require(path, binding_map, "port", "a binding"),
        product=_require(path, binding_map, "product", "a binding"),
    )


def _provider_binding(path: Path, b: object) -> ProviderBinding:
    if not isinstance(b, dict):
        raise WorkflowError(f"{path}: each provider binding must be a mapping")
    binding_map = cast(dict[str, Any], b)
    return ProviderBinding(
        kind=_require(path, binding_map, "kind", "a provider binding"),
        alias=_require(path, binding_map, "alias", "a provider binding"),
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
            loaded: Any = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowError(f"{path.name}: cannot read provider description: {exc}") from exc
    if not isinstance(loaded, dict):
        raise WorkflowError(f"{path.name}: provider description must be a mapping")
    doc = cast(dict[str, Any], loaded)
    kind = doc.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise WorkflowError(f"{path.name}: provider description needs a non-empty 'kind'")
    raw_props = doc.get("properties", [])
    if not isinstance(raw_props, list):
        raise WorkflowError(f"{path.name}: 'properties' must be a list")
    props: list[ProviderProperty] = []
    for entry in cast(list[Any], raw_props):
        if not isinstance(entry, dict):
            raise WorkflowError(f"{path.name}: each property needs a 'name'")
        property_map = cast(dict[str, Any], entry)
        if not isinstance(property_map.get("name"), str):
            raise WorkflowError(f"{path.name}: each property needs a 'name'")
        plane_raw = property_map.get("plane")
        try:
            plane = ProviderPlane(plane_raw)
        except ValueError:
            raise WorkflowError(
                f"{path.name}: property '{property_map['name']}' has invalid plane {plane_raw!r} "
                "(use 'config' or 'credential')"
            ) from None
        props.append(
            ProviderProperty(
                name=property_map["name"],
                plane=plane,
                description=str(property_map.get("description", "")),
                required=bool(property_map.get("required", True)),
            )
        )
    return ProviderSpec(kind=kind, properties=tuple(props))
