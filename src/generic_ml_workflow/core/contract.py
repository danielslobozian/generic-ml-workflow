# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""contract.py -- the engine's step and workflow contract (DESIGN.md SS5-SS7).

The rigid, exact structure every workflow must respect. Creation mode will
generate against it, the portability promise rests on it, and a "fetch a page"
step and a "fire a functional-analysis shot" step must both fit it without strain.

A workflow is DATA, not code.

== Step natures (invariant 4: one concept per step, no hybrid atom) ==
  * INTERPRETABLE -- a shot: pure ML judgment, compiled and fired once through
    gmlcache. Wears a cap; no step-specific code.
  * EXECUTABLE    -- a local invocation: deterministic code, declared inputs in,
    declared outputs out. The runtime cannot tell its origins apart (invariant 6).

== Ports, requirements, and bindings (DESIGN.md SS7) ==
A step declares its inputs and outputs as LOCAL PORT NAMES -- which is what makes
a step reusable across workflows untouched. A step never names another step.

Each input port also declares a REQUIREMENT KIND -- what satisfies it:
  * RUN_INPUT   -- asked at launch (the run interview is the union of these)
  * CONFIG      -- satisfied from the user's configuration (set once, shared)
  * CREDENTIAL  -- a credential role (the token never transits a model call; the
                   port carries the role's *presence*, not its value -- invariant 7)
  * ARTIFACT    -- a named product some earlier step must contribute

The WORKFLOW owns the wiring: a BINDING maps a consuming ARTIFACT port to a
context PRODUCT NAME (a launch input or an earlier step's durable output). The
engine derives the dependency graph from the bindings at compile time. This is
the only wiring; adding a step to a workflow *is* creating its bindings.

Outputs carry a LIFESPAN and a KIND:
  * lifespan TRANSPORT -- a courier (lifts into events, then the file is swept)
  * lifespan DURABLE   -- a keepsake: the substance, a later step's input
  * kind FILE          -- a document
  * kind QUESTIONS     -- a structured question-set that drives the gate

== Deduced correctness, before any token is spent (DESIGN.md SS7) ==
Workflow.validate() walks the steps in order, accumulating the context's product
names (run-inputs + each step's durable outputs), and raises on:
  - an unbound required ARTIFACT port
  - a binding naming a product nothing contributes
  - two products under one name (a step that *updates* an artifact contributes a
    NEW name that says why: x -> x_enriched, never a second x)
  - a binding to a product that only appears later (ordering)
and collects WARNINGS (not errors) for the dead-branch lint: a durable output no
later step consumes (terminal deliverables are legitimately unconsumed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class WorkflowError(Exception):
    """A workflow definition is invalid. Raised at load/validate, never mid-run."""


class StepNature(str, Enum):
    INTERPRETABLE = "interpretable"  # pure ML judgment via a headless shot; no step code
    EXECUTABLE = "executable"  # deterministic local invocation


class InputType(str, Enum):
    """The kind of subject a workflow works on (DESIGN.md SS4). Declared by the
    workflow; what it asks for at launch is computed from its steps."""

    FILE = "file"
    FOLDER = "folder"
    URL = "url"
    FREESTYLE = "freestyle"


class Tier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Requirement(str, Enum):
    RUN_INPUT = "run_input"  # asked at launch
    CONFIG = "config"  # from the user's configuration
    CREDENTIAL = "credential"  # a credential role (presence only; never the token)
    ARTIFACT = "artifact"  # a named product an earlier step must contribute
    ANSWER = "answer"  # a gate answer, by question id (filled mid-run, not at launch)
    PROVIDER = "provider"  # a named external dependency (issue tracker, db); see §10


class Lifespan(str, Enum):
    TRANSPORT = "transport"  # short-lived courier; substance lifts into events, file swept
    DURABLE = "durable"  # the kept artifact; a downstream step consumes it


class OutputKind(str, Enum):
    FILE = "file"  # a document
    QUESTIONS = "questions"  # a structured question-set; drives the gate


@dataclass(frozen=True)
class InputPort:
    """A step-local input name plus what kind of thing satisfies it. Reusable:
    the same step slots into any workflow because it names no other step."""

    name: str  # local port name
    requirement: Requirement


@dataclass(frozen=True)
class OutputPort:
    name: str  # local port name -> becomes a context product name when produced
    lifespan: Lifespan
    kind: OutputKind
    filename: str  # where the body writes it inside the step's output dir


@dataclass(frozen=True)
class StepSpec:
    """One step, declared as data. Validated on load; never trusted to be sane."""

    id: str
    nature: StepNature
    tier: Tier = Tier.MEDIUM
    inputs: tuple[InputPort, ...] = ()
    outputs: tuple[OutputPort, ...] = ()
    # interpretable steps:
    cap: str | None = None  # who the model is for this judgment
    methodology: str | None = None  # the briefing text/ref the cap reads
    # executable steps:
    entrypoint: str | None = None  # the declared body/invocation to run
    unattended: bool = False  # never blocks on a questions gate (invariant 10)

    def durable_outputs(self) -> tuple[OutputPort, ...]:
        return tuple(o for o in self.outputs if o.lifespan is Lifespan.DURABLE)

    def questions_output(self) -> OutputPort | None:
        for o in self.outputs:
            if o.kind is OutputKind.QUESTIONS:
                return o
        return None

    def artifact_ports(self) -> tuple[InputPort, ...]:
        return tuple(i for i in self.inputs if i.requirement is Requirement.ARTIFACT)

    def required(self, kind: Requirement) -> tuple[str, ...]:
        return tuple(i.name for i in self.inputs if i.requirement is kind)

    def validate(self) -> None:
        """Local checks only -- nothing cross-step (that is the workflow's job)."""
        if not self.id:
            raise WorkflowError("a step has no id")
        if self.nature is StepNature.INTERPRETABLE and not self.cap:
            raise WorkflowError(f"interpretable step '{self.id}' must declare a cap")
        if self.nature is StepNature.EXECUTABLE and not self.entrypoint:
            raise WorkflowError(f"executable step '{self.id}' must declare an entrypoint")
        in_names = [i.name for i in self.inputs]
        if len(in_names) != len(set(in_names)):
            raise WorkflowError(f"step '{self.id}' has duplicate input port names")
        out_names = [o.name for o in self.outputs]
        if len(out_names) != len(set(out_names)):
            raise WorkflowError(f"step '{self.id}' has duplicate output names")
        if sum(1 for o in self.outputs if o.kind is OutputKind.QUESTIONS) > 1:
            raise WorkflowError(f"step '{self.id}' declares more than one questions output")


class ProviderPlane(str, Enum):
    """Which plane a provider property lives on: ordinary config, or a secret."""

    CONFIG = "config"
    CREDENTIAL = "credential"


@dataclass(frozen=True)
class ProviderProperty:
    """One declared field of a provider: its name, which plane it lives on (config
    vs credential/secret), a human description, and whether it is required."""

    name: str
    plane: ProviderPlane
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class ProviderSpec:
    """A provider's self-describing shape -- its kind (e.g. ``issue_tracker``) and the
    properties an instance must supply. Meta-code: one file per provider, no values.
    The schema is what turns 'issue_tracker isn't configured' into 'your instance is
    missing base_url (config)', and is what a setup interview/UI reads (DESIGN.md §10)."""

    kind: str
    properties: tuple[ProviderProperty, ...] = ()

    def unmet(self, instance: dict[str, object]) -> list[str]:
        """The required properties absent from a configured ``instance`` (a flat
        key->value map merged across both planes), each as 'name (plane)'."""
        return [
            f"{p.name} ({p.plane.value})"
            for p in self.properties
            if p.required and p.name not in instance
        ]


@dataclass(frozen=True)
class Binding:
    """Wires one consuming artifact port to a context product name. Lives on the
    workflow, never on the step: '<step_id>.<port_name>' <- '<product_name>'."""

    step_id: str
    port: str  # the consuming step's local artifact port
    product: str  # the context product name it draws from


@dataclass(frozen=True)
class ProviderBinding:
    """Workflow-level choice of which configured instance (alias) a provider kind
    resolves to for this workflow, e.g. kind 'issue_tracker' -> alias 'acme'. A step
    declares only the kind; the workflow picks the instance, so the same step can hit
    a different instance in another workflow (DESIGN.md §10)."""

    kind: str
    alias: str


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of a token-free validation pass: hard errors and soft warnings."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Workflow:
    """A typed, ordered list of steps plus the binding block that wires them.
    Steps never name each other; the bindings are the only wiring."""

    name: str
    input_type: InputType
    steps: tuple[StepSpec, ...]
    bindings: tuple[Binding, ...] = ()
    provider_bindings: tuple[ProviderBinding, ...] = ()

    # --- computed requirements (for the run interview + warm-up, slice 0.0.5) ---
    def run_inputs(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.steps:
            for name in s.required(Requirement.RUN_INPUT):
                if name not in seen:
                    seen.append(name)
        return tuple(seen)

    def config_requirements(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.steps:
            for name in s.required(Requirement.CONFIG):
                if name not in seen:
                    seen.append(name)
        return tuple(seen)

    def credential_roles(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.steps:
            for name in s.required(Requirement.CREDENTIAL):
                if name not in seen:
                    seen.append(name)
        return tuple(seen)

    def provider_requirements(self) -> tuple[str, ...]:
        """The provider kinds this workflow needs -- the union of its steps' declared
        provider ports (e.g. 'issue_tracker'). Each must resolve to a configured
        instance before the run may open (§10)."""
        seen: list[str] = []
        for s in self.steps:
            for name in s.required(Requirement.PROVIDER):
                if name not in seen:
                    seen.append(name)
        return tuple(seen)

    def provider_aliases(self) -> dict[str, str]:
        """This workflow's instance choices: ``{kind: alias}`` from its provider
        bindings. An unbound kind resolves to its default/single instance."""
        return {b.kind: b.alias for b in self.provider_bindings}

    def validate(self) -> ValidationResult:
        """Deduced correctness, token-free. Returns errors + warnings rather than
        raising on the soft ones, so a caller (e.g. /validate) can show both."""
        errors: list[str] = []
        warnings: list[str] = []

        if not self.steps:
            return ValidationResult(errors=(f"workflow '{self.name}' has no steps",))

        # local step validity + unique step ids
        seen_ids: set[str] = set()
        for s in self.steps:
            try:
                s.validate()
            except WorkflowError as exc:
                errors.append(str(exc))
            if s.id in seen_ids:
                errors.append(f"workflow '{self.name}' repeats step id '{s.id}'")
            seen_ids.add(s.id)

        # index the bindings by (step_id, port); flag duplicates
        by_consumer: dict[tuple[str, str], str] = {}
        for b in self.bindings:
            if b.step_id not in seen_ids:
                errors.append(f"binding targets unknown step '{b.step_id}'")
            key = (b.step_id, b.port)
            if key in by_consumer:
                errors.append(f"step '{b.step_id}' port '{b.port}' is bound more than once")
            by_consumer[key] = b.product

        # walk in order: accumulate the context's product names; check as we go
        produced: dict[str, str] = {}  # product name -> step id that contributed it
        consumed: set[str] = set()  # product names some later step draws from
        for s in self.steps:
            # every ARTIFACT input port must be bound, and to an already-available product
            for port in s.artifact_ports():
                product = by_consumer.get((s.id, port.name))
                if product is None:
                    errors.append(
                        f"step '{s.id}' requires input '{port.name}' but nothing is bound to it"
                        " -- did you forget a binding?"
                    )
                    continue
                if product not in produced:
                    errors.append(
                        f"step '{s.id}' input '{port.name}' is bound to product '{product}',"
                        " which nothing contributes before it -- did you forget a step?"
                    )
                else:
                    consumed.add(product)
            # this step's durable outputs enter the context as products (unique names)
            for out in s.durable_outputs():
                if out.name in produced:
                    errors.append(
                        f"two steps contribute a product named '{out.name}'"
                        f" ('{produced[out.name]}' and '{s.id}') -- product names are unique;"
                        " an updating step must contribute a new name (e.g. x -> x_enriched)"
                    )
                else:
                    produced[out.name] = s.id

        # dead-branch lint: a durable output no later step consumes (warning only --
        # terminal deliverables are legitimately unconsumed)
        for name, step_id in produced.items():
            if name not in consumed:
                warnings.append(
                    f"product '{name}' (from step '{step_id}') is never consumed by a later step"
                    " -- a terminal deliverable, or a forgotten rebind?"
                )

        return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))


@dataclass(frozen=True)
class ResolvedStep:
    """What the orchestrator hands a body (slice 0.0.5+): all thinking already
    done. The body does not decide, discover, or scan -- it consumes resolved
    values and writes its declared outputs to ``output_dir``."""

    spec: StepSpec
    inputs: dict[str, object]  # port name -> resolved value
    output_dir: str
    output_paths: dict[str, str] = field(default_factory=dict[str, str])  # output name -> abs path
