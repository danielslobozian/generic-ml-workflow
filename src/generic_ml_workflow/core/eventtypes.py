# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""eventtypes.py -- the engine's event vocabulary: a closed, typed, self-describing
registry (DESIGN.md SS11).

Event types are *engine* concepts -- the engine records how it ran things; they
are never authored as meta-code. So the set is a **closed enum owned by the engine
version** that reads the log. Each type has a **typed payload bean** (a dataclass):
the bean is the single declaration of that type's body shape, used by both the
writer (constructing an event) and the projection-rebuilder (reading it back), so
the two cannot drift, and a payload that does not fit fails loudly -- exactly where
a schema-evolution problem should surface.

A small **registry** maps type -> bean, from which the engine offers a
self-description capability (`event_types()`, `describe()`): the Swagger-for-events
backbone that `/replay`, the companion, and the future versioned schema doc consume
to narrate any event without hard-coding each type.

The payload carries **scalars, references (names), and pointers (path + sha)** --
never embedded definitions, never blobs (invariant 11). In particular, meta-code is
referenced by **name + the run's stamped commit**, never by a database id, so that
events + git are self-sufficient to rebuild every projection.

This module is *vocabulary only*: the envelope columns (seq, event_id, occurred_at,
execution_id, actor, scope keys) and the store live in ``events.py``. Kept minimal
on purpose -- types are added as the slices that emit them arrive.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class EventType(str, Enum):
    """The closed set of engine event types. Add a member only with its payload
    bean (below) and a registry entry."""

    JOB_OPENED = "job.opened"
    WORKFLOW_EXECUTION_STARTED = "workflow_execution.started"
    WORKFLOW_EXECUTION_COMPLETED = "workflow_execution.completed"
    WORKFLOW_EXECUTION_FAILED = "workflow_execution.failed"
    WORKFLOW_EXECUTION_STOPPED = "workflow_execution.stopped"
    RUN_INPUT_PROVIDED = "run_input.provided"
    TIER_OVERRIDDEN = "tier.overridden"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    ARTIFACT_CREATED = "artifact.created"
    QUESTIONS_ASKED = "questions.asked"
    ANSWER_SUBMITTED = "answer.submitted"


class Payload:
    """Base for typed event payloads. Subclasses are frozen dataclasses whose
    fields *are* the declared schema of one event type. ``event_type`` ties the
    bean to its enum member; ``to_json``/``from_json`` cross the envelope boundary."""

    event_type: ClassVar[EventType]

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict):
        fields = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - fields
        if unknown:
            raise ValueError(f"{cls.__name__}: unexpected payload fields {sorted(unknown)}")
        try:
            return cls(**data)
        except TypeError as exc:  # missing required field
            raise ValueError(f"{cls.__name__}: malformed payload -- {exc}") from exc


@dataclass(frozen=True)
class JobOpened(Payload):
    event_type = EventType.JOB_OPENED
    job_id: str
    label: str | None = None


@dataclass(frozen=True)
class WorkflowExecutionStarted(Payload):
    """The stamp: meta-code referenced by name + commit, plus the engine version.
    Never the workflow object -- the definition lives in git."""

    event_type = EventType.WORKFLOW_EXECUTION_STARTED
    workflow_name: str
    input_type: str
    commit: str | None  # the flows-repo commit the run was stamped against
    branch: str | None
    engine_version: str
    job_id: str | None = None


@dataclass(frozen=True)
class WorkflowExecutionCompleted(Payload):
    event_type = EventType.WORKFLOW_EXECUTION_COMPLETED


@dataclass(frozen=True)
class WorkflowExecutionFailed(Payload):
    event_type = EventType.WORKFLOW_EXECUTION_FAILED
    reason: str | None = None


@dataclass(frozen=True)
class WorkflowExecutionStopped(Payload):
    """The user stopped the run (via the surface) before it finished. Distinct from
    failed: nothing went wrong, the run was halted on request. The interrupted step
    (if one was mid-flight) is named so `/replay` can show where it stopped."""

    event_type = EventType.WORKFLOW_EXECUTION_STOPPED
    reason: str | None = None
    step_name: str | None = None


@dataclass(frozen=True)
class RunInputProvided(Payload):
    """A launch-interview answer entering the context. Scalars only -- a small
    value lives inline; a file input would enter as an artifact pointer instead."""

    event_type = EventType.RUN_INPUT_PROVIDED
    name: str
    value: str


@dataclass(frozen=True)
class TierOverridden(Payload):
    """A run-time, per-step decision: the user ran this step at a tier other than
    the one its spec declares. Scalars only -- the chosen tier is a reference, the
    concrete client/model it resolves to is gmlcache's and is captured in the shot,
    not here. Emitted (actor=user) only when the chosen tier actually differs."""

    event_type = EventType.TIER_OVERRIDDEN
    step_name: str
    from_tier: str  # the tier the step's spec declared
    to_tier: str  # the tier the user chose for this run


@dataclass(frozen=True)
class StepStarted(Payload):
    event_type = EventType.STEP_STARTED
    step_name: str  # the authored step code, resolved against the run's commit
    attempt: int = 1


@dataclass(frozen=True)
class StepCompleted(Payload):
    event_type = EventType.STEP_COMPLETED
    step_name: str
    attempt: int = 1


@dataclass(frozen=True)
class StepFailed(Payload):
    event_type = EventType.STEP_FAILED
    step_name: str
    attempt: int = 1
    reason: str | None = None


@dataclass(frozen=True)
class ArtifactCreated(Payload):
    """A durable product entering the context -- a POINTER, never content."""

    event_type = EventType.ARTIFACT_CREATED
    name: str  # the product name (a context key)
    path: str
    sha256: str | None = None


@dataclass(frozen=True)
class QuestionsAsked(Payload):
    event_type = EventType.QUESTIONS_ASKED
    step_name: str
    questions: tuple  # list of {id, text, blocking}; kept generic for the gate


@dataclass(frozen=True)
class AnswerSubmitted(Payload):
    event_type = EventType.ANSWER_SUBMITTED
    question_id: str
    answer: str | None = None
    status: str = "answered"  # answered | skipped


# --- the registry + self-description -----------------------------------------

_REGISTRY: dict[EventType, type[Payload]] = {
    p.event_type: p
    for p in (
        JobOpened,
        WorkflowExecutionStarted,
        WorkflowExecutionCompleted,
        WorkflowExecutionFailed,
        WorkflowExecutionStopped,
        RunInputProvided,
        TierOverridden,
        StepStarted,
        StepCompleted,
        StepFailed,
        ArtifactCreated,
        QuestionsAsked,
        AnswerSubmitted,
    )
}

assert set(_REGISTRY) == set(EventType), (
    "every EventType must have a registered payload bean: "
    f"missing {set(EventType) - set(_REGISTRY)}"
)


def bean_for(event_type: EventType | str) -> type[Payload]:
    """The payload bean class for a type. Raises KeyError on an unknown type."""
    if isinstance(event_type, str):
        event_type = EventType(event_type)
    return _REGISTRY[event_type]


def parse_payload(event_type: EventType | str, data: dict) -> Payload:
    """JSON dict -> the typed bean for the type. Fails loudly on a bad shape."""
    return bean_for(event_type).from_json(data)


def event_types() -> list[str]:
    """Self-description: every event type the engine knows (the 'list' endpoint)."""
    return [t.value for t in EventType]


def describe(event_type: EventType | str) -> dict:
    """Self-description: the structure of one event type (the 'get schema' endpoint).
    Returns the type and its payload fields with their declared annotations."""
    bean = bean_for(event_type)
    fields = [
        {
            "name": f.name,
            "type": f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type)),
            "required": f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING,  # type: ignore[misc]
        }
        for f in dataclasses.fields(bean)
    ]
    et = event_type if isinstance(event_type, EventType) else EventType(event_type)
    return {"event_type": et.value, "payload": fields}
