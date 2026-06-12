# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""contract.py -- the engine's step contract.

This is the rigid, exact structure every step must respect. It is the load-bearing
artifact of the whole system: creation mode will generate against it, the
portability promise rests on it, and a "fetch a page" step and a "fire a
functional-analysis shot" step must both fit it without strain.

A step is DATA, not code. At runtime a step has exactly one of two natures
(design invariant 4 -- one concept per step, no hybrid atom):

  * INTERPRETABLE -- a shot: pure ML judgment, compiled by the engine and fired
    once through gmlcache. No step-specific code at all.
  * EXECUTABLE    -- a local invocation: deterministic code run by the engine,
    declared inputs in, declared outputs out. The runtime cannot distinguish its
    origins (user-supplied / generated / --), per design invariant 6.

A step declares, in plain data:

  - id            its name (also the folder name of its spec)
  - nature        INTERPRETABLE or EXECUTABLE
  - tier          the abstract capability class (high/medium/low) a shot resolves
                  through; a workflow never names a vendor (invariant 8)
  - cap/methodology   (interpretable only) who the model is, and the briefing it reads
  - entrypoint        (executable only) the declared body/invocation to run
  - inputs        named things the engine resolves to concrete values BEFORE the
                  body runs -- a prior step's durable output, a literal, or nothing.
                  The step never discovers; it consumes a resolved input.
  - needs         credential ROLES the body's work requires (never tokens; secrets
                  never transit a model call -- invariant 7)
  - outputs       each awaited output: a name, a LIFESPAN, and a KIND.
                    lifespan TRANSPORT = a courier (extracted to events, then swept)
                    lifespan DURABLE   = a keepsake (the substance; a later step's input)
                    kind FILE          = a document
                    kind QUESTIONS     = a structured question-set that drives the gate

The engine's completeness check follows from ``outputs``: a step is finished when
its declared DURABLE outputs are present (or a blocking QUESTIONS output appeared);
a step that produced neither failed.

Groundwork note: this module is converted salvage. Its real slice is 0.0.3
(workflow definitions load and validate), which finalizes the contract against
DESIGN.md §7 — in particular, the explicit step-addressed wiring below
(``from_step="<step>.<output>"``) is the old chain model and will be replaced by
**ports and bindings**: steps declare local port names only; the workflow's
binding block maps consuming ports to workflow-context product names, and the
dependency graph is derived from the bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepNature(str, Enum):
    INTERPRETABLE = "interpretable"  # pure ML judgment via a headless shot; no step code
    EXECUTABLE = "executable"  # deterministic local invocation


class Tier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Lifespan(str, Enum):
    TRANSPORT = "transport"  # short-lived courier; substance lifts into events, file swept
    DURABLE = "durable"  # the kept artifact; a downstream step consumes it


class OutputKind(str, Enum):
    FILE = "file"  # a document
    QUESTIONS = "questions"  # a structured question-set; drives the gate


@dataclass(frozen=True)
class InputSpec:
    """A named input the engine resolves to a concrete value before the body runs.

    Exactly one source:
      literal     - supplied at run time (e.g. a URL, a key, a path)
      step        - a prior step's durable output, referenced as "<step_id>.<output>"
    The wiring lives in the workflow data, so steps stay decoupled -- this is the
    same line a visual canvas would draw between two boxes.
    """

    name: str
    from_literal: bool = False
    from_step: str | None = None  # "<step_id>.<output_name>"

    def __post_init__(self) -> None:
        if self.from_literal == bool(self.from_step):
            raise ValueError(
                f"input '{self.name}' must have exactly one source "
                f"(literal XOR step), got literal={self.from_literal} step={self.from_step}"
            )


@dataclass(frozen=True)
class OutputSpec:
    name: str
    lifespan: Lifespan
    kind: OutputKind
    filename: str  # where the body writes it inside the step's output dir


@dataclass(frozen=True)
class StepSpec:
    """One step, declared as data. Validated on load; never trusted to be sane."""

    id: str
    nature: StepNature
    tier: Tier = Tier.MEDIUM
    inputs: tuple[InputSpec, ...] = ()
    outputs: tuple[OutputSpec, ...] = ()
    needs: tuple[str, ...] = ()  # credential roles, e.g. ("issue_tracker",)
    # interpretable steps:
    cap: str | None = None  # who the model is for this judgment
    methodology: str | None = None  # the briefing text/ref the cap reads
    # executable steps:
    entrypoint: str | None = None  # the declared body/invocation to run
    unattended: bool = False  # never blocks on a questions gate (invariant 10)

    def durable_outputs(self) -> tuple[OutputSpec, ...]:
        return tuple(o for o in self.outputs if o.lifespan is Lifespan.DURABLE)

    def questions_output(self) -> OutputSpec | None:
        for o in self.outputs:
            if o.kind is OutputKind.QUESTIONS:
                return o
        return None

    def validate(self) -> None:
        """Fail loud at load time, never mid-run."""
        if not self.id:
            raise ValueError("step has no id")
        if self.nature is StepNature.INTERPRETABLE and not self.cap:
            raise ValueError(f"interpretable step '{self.id}' must declare a cap")
        if self.nature is StepNature.EXECUTABLE and not self.entrypoint:
            raise ValueError(f"executable step '{self.id}' must declare an entrypoint")
        names = [o.name for o in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError(f"step '{self.id}' has duplicate output names")
        if sum(1 for o in self.outputs if o.kind is OutputKind.QUESTIONS) > 1:
            raise ValueError(f"step '{self.id}' declares more than one questions output")


@dataclass(frozen=True)
class Workflow:
    """An ordered list of steps. The wiring between them lives in each step's
    InputSpec.from_step, so the workflow is reconfigurable as pure data."""

    name: str
    steps: tuple[StepSpec, ...]

    def validate(self) -> None:
        if not self.steps:
            raise ValueError(f"workflow '{self.name}' has no steps")
        seen: set[str] = set()
        for s in self.steps:
            s.validate()
            if s.id in seen:
                raise ValueError(f"workflow '{self.name}' repeats step id '{s.id}'")
            seen.add(s.id)
        # every step-sourced input must reference a step that runs earlier
        order = {s.id: i for i, s in enumerate(self.steps)}
        for i, s in enumerate(self.steps):
            for inp in s.inputs:
                if inp.from_step is None:
                    continue
                ref_step = inp.from_step.split(".", 1)[0]
                if ref_step not in order:
                    raise ValueError(
                        f"step '{s.id}' input '{inp.name}' references unknown step '{ref_step}'"
                    )
                if order[ref_step] >= i:
                    raise ValueError(
                        f"step '{s.id}' input '{inp.name}' references '{ref_step}' "
                        f"which does not run earlier"
                    )


@dataclass(frozen=True)
class ResolvedStep:
    """What the orchestrator hands a body: all thinking already done. The body
    does not decide, discover, or scan -- it consumes resolved values and writes
    its declared outputs to ``output_dir``."""

    spec: StepSpec
    inputs: dict[str, object]  # name -> resolved value (text, path, ...)
    output_dir: str  # where to write declared outputs
    output_paths: dict[str, str] = field(default_factory=dict)  # output name -> abs path
