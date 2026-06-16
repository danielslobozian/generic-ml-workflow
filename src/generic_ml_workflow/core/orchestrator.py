# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""orchestrator.py -- run a workflow execution (DESIGN.md SS4, SS7, SS11, SS13).

The engine owns the session; it drives each step. This slice (0.0.5) runs the
**executable** nature end to end, zero ML. The flow:

  1. **Open** -- mint the execution id (the historization key), read the meta-code
     **stamp** (commit/branch/engine version), emit ``workflow_execution.started``.
  2. **Warm up the context** -- the run interview's answers (the workflow's
     computed run-inputs) are loaded in, each emitted as ``run_input.provided``.
  3. **Walk the steps in order**, maintaining the **context-fold**: a step never
     consumes "another step's output", it asks the context (resolution is uniform
     whether a value came from the launch or an earlier step). For each step:
       - resolve its bound artifact ports from the context (the workflow's
         bindings are the only wiring),
       - run the executable in an isolated per-step run folder (``core.runner``),
       - emit ``step.started`` / ``step.completed`` / ``step.failed``, and an
         ``artifact.created`` per durable output (a POINTER -- path + sha),
       - add each durable product to the context under its name.
  4. **Close** -- emit ``workflow_execution.completed`` (or ``…failed``).

This is pure core: the caller supplies resolved run-inputs (the REPL gathers them
at the prompt; tests pass a dict) and the orchestrator emits real events the
event store records. Interpretable steps are not runnable yet -- a workflow that
reaches one stops honestly (the gmlcache seam is slice 0.0.6).

Validation is the gate before any work: a workflow that does not pass
``validate()`` (errors) never runs. Warnings (the dead-branch lint) do not block.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from generic_ml_workflow.core import runner, shotrunner
from generic_ml_workflow.core.contract import (
    OutputKind,
    Requirement,
    StepNature,
    StepSpec,
    Tier,
    Workflow,
)
from generic_ml_workflow.core.envelope import build_envelope
from generic_ml_workflow.core.events import EventStore, new_execution_id
from generic_ml_workflow.core.stamp import Stamp
from generic_ml_workflow.core.stopping import StopControl
from generic_ml_workflow.core import eventtypes as et


class OrchestratorError(Exception):
    """The execution could not proceed (unrunnable definition, unmet requirement)."""


class RunMode(Enum):
    """How a run advances, chosen at launch and recorded in the run's start event so
    a ``/resume`` continues in the same mode and it survives a restart (DESIGN §7).
    ``FULL_AUTO`` walks straight through; ``FULL_MANUAL`` checkpoints after every
    step -- the run pauses (stop-and-resume) so it can be inspected, and ``/resume``
    advances one step. ``QUESTIONS_ONLY`` runs straight through but blocks whenever a
    step asks (produces its ``questions`` output), awaiting answers."""

    FULL_AUTO = "full-auto"
    FULL_MANUAL = "full-manual"
    QUESTIONS_ONLY = "questions-only"


class RunPhase(Enum):
    """The advancement boundaries the engine announces as a run walks its steps.
    These are *progress notifications* -- a side channel for a surface's live
    display -- and are not the event log (the source of truth, DESIGN.md SS11). A
    surface renders them; nothing about correctness depends on anyone listening."""

    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STOPPED = "run_stopped"
    RUN_PAUSED = "run_paused"  # full-manual checkpoint after a step; awaits /resume
    RUN_BLOCKED = "run_blocked"  # questions gate: a step asked; awaits answers


@dataclass(frozen=True)
class RunProgress:
    """One advancement notification. ``step_number`` is 1-based within
    ``step_count``; ``step_name`` and ``reason`` are filled only where they apply
    (a step boundary carries the name; a failure carries the reason)."""

    phase: RunPhase
    execution_id: str
    step_name: str | None = None
    step_number: int | None = None
    step_count: int | None = None
    reason: str | None = None
    questions: tuple = ()  # the gate's questions, carried on RUN_BLOCKED


# A surface supplies this to receive live advancement; the engine calls it at each
# boundary and otherwise knows nothing of screens or threads (DESIGN.md invariant 24).
ProgressReporter = Callable[[RunProgress], None]


def _ignore_progress(_progress: RunProgress) -> None:
    """The default reporter -- a run with no surface attached announces to no one."""


@dataclass
class RunReport:
    """The outcome of one run, for the caller to render."""

    execution_id: str
    completed: bool
    steps_run: list[str] = field(default_factory=list)
    stopped_reason: str | None = None  # why a run stopped early (e.g. an ML step)
    failed_step: str | None = None
    paused_after: str | None = None  # full-manual checkpoint: the step we paused after
    awaiting: tuple = ()  # questions gate: the questions the run is blocked on
    awaiting_file: Path | None = None  # internal: the produced questions file (to parse/sweep)


@dataclass
class ShotConfig:
    """How interpretable steps resolve and cache (DESIGN.md SS9). Tier -> concrete
    (client, model, effort), plus the resolution ``mode``. The cassette store is
    the cache's own (config-owned) concern as of gmlcache 0.0.7 -- the engine
    dictates no store location. Full tier reconciliation against installed clients
    (detection-assisted seeding) is a later slice; here the caller supplies the
    resolution map (from [tiers]). ``run_shot`` is injectable for tests."""

    resolutions: dict[Tier, shotrunner.Resolution]
    mode: str = "cache"
    run_shot: object = staticmethod(shotrunner.run_shot)

    def resolve(self, tier: Tier) -> shotrunner.Resolution:
        if tier not in self.resolutions:
            raise OrchestratorError(
                f"no client/model configured for tier '{tier.value}' "
                f"-- add a [tiers.{tier.value}] table (client + model) to your config"
            )
        return self.resolutions[tier]


def warm_up(
    workflow: Workflow,
    run_inputs: dict[str, str],
    config_values: dict[str, str] | None = None,
    credentials: set[str] | None = None,
    providers: set[str] | None = None,
    provider_specs: dict | None = None,
    provider_instances: dict[str, dict[str, object]] | None = None,
) -> None:
    """Token-free readiness check before step one fires (DESIGN.md SS7).

    Every computed run-input must be supplied; every configuration requirement must
    be satisfiable from config; every credential role must be present; every provider
    a step needs must resolve to a configured instance that satisfies the provider's
    schema. Raises ``OrchestratorError`` listing what is missing, before any execution
    opens.
    """
    config_values = config_values or {}
    credentials = credentials or set()
    providers = providers or set()
    provider_specs = provider_specs or {}
    provider_instances = provider_instances or {}
    missing: list[str] = []
    for name in workflow.run_inputs():
        if name not in run_inputs:
            missing.append(f"run-input '{name}' was not provided")
    for name in workflow.config_requirements():
        if name not in config_values:
            missing.append(f"configuration '{name}' is required but not set")
    for role in workflow.credential_roles():
        if role not in credentials:
            missing.append(f"credential role '{role}' is required but not configured")
    for kind in workflow.provider_requirements():
        if kind not in providers:
            missing.append(
                f"provider '{kind}' is required but no instance is configured "
                "(set it up in config + credentials)"
            )
            continue
        spec = provider_specs.get(kind)
        if spec is not None:
            for unmet in spec.unmet(provider_instances.get(kind, {})):
                missing.append(f"provider '{kind}' instance is missing {unmet}")
    if missing:
        raise OrchestratorError("this workflow is not ready to run:\n  " + "\n  ".join(missing))


class Orchestrator:
    """Runs one workflow execution against an event store, in a workspace."""

    def __init__(self, store: EventStore, workspace: Path):
        self._store = store
        self._workspace = Path(workspace)
        self._provider_instances: dict[str, dict[str, str]] = {}

    def run(
        self,
        workflow: Workflow,
        run_inputs: dict[str, str],
        stamp: Stamp,
        *,
        job_id: str | None = None,
        config_values: dict[str, str] | None = None,
        credentials: set[str] | None = None,
        providers: set[str] | None = None,
        provider_instances: dict[str, dict[str, str]] | None = None,
        provider_specs: dict | None = None,
        shot_config: ShotConfig | None = None,
        tier_overrides: dict[str, Tier] | None = None,
        mode: RunMode = RunMode.FULL_AUTO,
        progress: ProgressReporter = _ignore_progress,
        stop: StopControl | None = None,
    ) -> RunReport:
        # gate: the definition must be valid (warnings are fine, errors are not)
        self._provider_instances = provider_instances or {}
        result = workflow.validate()
        if not result.ok:
            raise OrchestratorError(
                "workflow does not validate; fix these before running:\n  "
                + "\n  ".join(result.errors)
            )
        # gate: requirements ready, token-free, before opening the execution
        warm_up(
            workflow,
            run_inputs,
            config_values,
            credentials,
            providers,
            provider_specs,
            self._provider_instances,
        )

        execution_id = new_execution_id()
        self._store.emit(
            et.WorkflowExecutionStarted(
                workflow_name=workflow.name,
                input_type=workflow.input_type.value,
                commit=stamp.commit,
                branch=stamp.branch,
                engine_version=stamp.engine_version,
                job_id=job_id,
                mode=mode.value,
            ),
            execution_id=execution_id,
        )

        # the workflow context: the run-fold. Seed with the interview answers.
        context: dict[str, object] = {}
        for name, value in run_inputs.items():
            self._store.emit(
                et.RunInputProvided(name=name, value=value),
                execution_id=execution_id,
                actor="user",
            )
            context[name] = value

        return self._walk(
            workflow,
            execution_id,
            context,
            completed=set(),
            tier_overrides=tier_overrides,
            shot_config=shot_config,
            mode=mode,
            progress=progress,
            stop=stop,
        )

    def _walk(
        self,
        workflow,
        execution_id,
        context,
        *,
        completed,
        tier_overrides,
        shot_config,
        mode,
        progress,
        stop,
    ) -> RunReport:
        """Walk the steps once -- shared by a fresh ``run`` (empty context, nothing
        completed) and a ``resume`` (context rebuilt from the log, prior steps in
        ``completed`` and skipped). In ``FULL_MANUAL`` it pauses after each step that
        has an unfinished step behind it (a checkpoint -- stop-and-resume). Emits the
        terminal event (completed / failed / stopped) and the matching progress, then
        returns the report."""
        report = RunReport(execution_id=execution_id, completed=False)
        bindings = self._binding_map(workflow)
        tier_overrides = tier_overrides or {}
        step_count = len(workflow.steps)
        progress(RunProgress(RunPhase.RUN_STARTED, execution_id, step_count=step_count))

        def record_stopped(step_name: str | None) -> RunReport:
            reason = "stopped by request"
            report.stopped_reason = reason
            self._store.emit(
                et.WorkflowExecutionStopped(reason=reason, step_name=step_name),
                execution_id=execution_id,
            )
            progress(
                RunProgress(RunPhase.RUN_STOPPED, execution_id, step_name=step_name, reason=reason)
            )
            return report

        def record_checkpoint(step_name: str) -> RunReport:
            # full-manual pause: a checkpoint awaiting /resume. Recorded as a stopped
            # event (resumable) with a checkpoint reason; rendered as a pause, not a
            # stop. The run's mode is in its start event, so the resume keeps stepping.
            reason = f"checkpoint after '{step_name}' (full-manual)"
            report.paused_after = step_name
            report.stopped_reason = reason
            self._store.emit(
                et.WorkflowExecutionStopped(reason=reason, step_name=step_name),
                execution_id=execution_id,
            )
            progress(
                RunProgress(RunPhase.RUN_PAUSED, execution_id, step_name=step_name, reason=reason)
            )
            return report

        for step_number, step in enumerate(workflow.steps, start=1):
            if step.id in completed:  # already done in a prior segment (resume)
                continue
            if stop is not None and stop.requested():  # asked to stop before this step
                return record_stopped(None)

            if step.nature is StepNature.INTERPRETABLE and shot_config is None:
                # no shot configuration supplied -> stop honestly, do not fake it
                report.stopped_reason = (
                    f"step '{step.id}' is a shot, but no client/model resolution was "
                    "provided -- configure the step's tier in your [tiers] config."
                )
                self._store.emit(
                    et.WorkflowExecutionFailed(reason=report.stopped_reason),
                    execution_id=execution_id,
                )
                progress(
                    RunProgress(RunPhase.RUN_FAILED, execution_id, reason=report.stopped_reason)
                )
                return report

            progress(
                RunProgress(
                    RunPhase.STEP_STARTED,
                    execution_id,
                    step_name=step.id,
                    step_number=step_number,
                    step_count=step_count,
                )
            )
            if step.nature is StepNature.INTERPRETABLE:
                step_ok = self._run_shot(
                    step, execution_id, context, bindings, report, shot_config, tier_overrides, stop
                )
            else:
                step_ok = self._run_step(step, execution_id, context, bindings, report, stop)

            if stop is not None and stop.requested():
                # the step was cut short by our stop (its child was torn down):
                # record a stopped run, not a failure.
                return record_stopped(step.id)

            if not step_ok:
                reason = f"step '{step.id}' failed"
                self._store.emit(
                    et.WorkflowExecutionFailed(reason=reason), execution_id=execution_id
                )
                report.failed_step = step.id
                progress(
                    RunProgress(
                        RunPhase.STEP_FAILED,
                        execution_id,
                        step_name=step.id,
                        step_number=step_number,
                        step_count=step_count,
                    )
                )
                progress(RunProgress(RunPhase.RUN_FAILED, execution_id, reason=reason))
                return report

            progress(
                RunProgress(
                    RunPhase.STEP_COMPLETED,
                    execution_id,
                    step_name=step.id,
                    step_number=step_number,
                    step_count=step_count,
                )
            )

            # the questions gate: did this step ask? (produce its `questions` output)
            if report.awaiting_file is not None:
                questions_file = report.awaiting_file
                report.awaiting_file = None
                gate_honored = mode is not RunMode.FULL_AUTO and not step.unattended
                if gate_honored:
                    try:
                        questions = self._read_questions(questions_file)
                    except OrchestratorError as exc:
                        self._store.emit(
                            et.WorkflowExecutionFailed(reason=str(exc)), execution_id=execution_id
                        )
                        report.failed_step = step.id
                        progress(RunProgress(RunPhase.RUN_FAILED, execution_id, reason=str(exc)))
                        return report
                    report.awaiting = questions
                    self._store.emit(
                        et.QuestionsAsked(step_name=step.id, questions=questions),
                        execution_id=execution_id,
                        step_name=step.id,
                    )
                    # a gate block is a resumable pause: record the halt so the run
                    # leaves 'running' (status -> stopped, resumable), distinct from a
                    # failure. Why it halted is in the questions.asked event + the rows.
                    report.stopped_reason = f"awaiting answers to {len(questions)} question(s)"
                    self._store.emit(
                        et.WorkflowExecutionStopped(
                            reason=report.stopped_reason, step_name=step.id
                        ),
                        execution_id=execution_id,
                    )
                    progress(
                        RunProgress(
                            RunPhase.RUN_BLOCKED,
                            execution_id,
                            step_name=step.id,
                            reason=f"{len(questions)} question(s) await answers",
                            questions=questions,
                        )
                    )
                    return report
                # full-auto / unattended: the gate is bypassed -- proceed as if unasked

            if mode is RunMode.FULL_MANUAL:
                # checkpoint, unless this was the last unfinished step (then complete)
                remaining = [s for s in workflow.steps[step_number:] if s.id not in completed]
                if remaining:
                    return record_checkpoint(step.id)
        self._store.emit(et.WorkflowExecutionCompleted(), execution_id=execution_id)
        report.completed = True
        progress(RunProgress(RunPhase.RUN_COMPLETED, execution_id, step_count=step_count))
        return report

    def resume(
        self,
        execution_id: str,
        workflow: Workflow,
        *,
        shot_config: ShotConfig | None = None,
        tier_overrides: dict[str, Tier] | None = None,
        mode: RunMode | None = None,
        provider_instances: dict[str, dict[str, str]] | None = None,
        progress: ProgressReporter = _ignore_progress,
        stop: StopControl | None = None,
    ) -> RunReport:
        """Continue a stopped or interrupted execution. Rebuilds the context-fold and
        the set of completed steps from the run's own events (the log is the
        authority; DESIGN §11), marks it running again, and walks the unfinished
        steps on the same execution id. An interrupted step (started, never
        completed) simply runs again -- the step is the unit of resume. Continues in
        the run's recorded mode (so a full-manual run keeps checkpointing) unless an
        explicit ``mode`` overrides it. Uses the currently-loaded workflow/config, not
        the originally-stamped commit (strict same-commit resume is the 0.1.5
        time-travel slice)."""
        row = self._store.execution(execution_id)
        if row is None:
            raise OrchestratorError(f"no execution '{execution_id}' to resume")
        self._provider_instances = provider_instances or {}
        if row["status"] in ("completed", "failed"):
            raise OrchestratorError(
                f"execution '{execution_id}' is {row['status']} -- nothing to resume"
            )
        result = workflow.validate()
        if not result.ok:
            raise OrchestratorError(
                "workflow does not validate; fix these before resuming:\n  "
                + "\n  ".join(result.errors)
            )
        context, completed, recorded_mode = self._rebuild(execution_id)
        # a blocking gate must be answered before the run can move past it
        unanswered = [
            q
            for q in self._store.gate_questions(execution_id)
            if q["status"] == "pending" and q["blocking"]
        ]
        if unanswered:
            raise OrchestratorError(
                f"{len(unanswered)} blocking question(s) still unanswered -- "
                "answer them first ('/answer'), then resume."
            )
        first_unfinished = next((s.id for s in workflow.steps if s.id not in completed), None)
        self._store.emit(
            et.WorkflowExecutionResumed(from_step=first_unfinished),
            execution_id=execution_id,
        )
        return self._walk(
            workflow,
            execution_id,
            context,
            completed=completed,
            tier_overrides=tier_overrides or {},
            shot_config=shot_config,
            mode=mode or recorded_mode,
            progress=progress,
            stop=stop,
        )

    def _rebuild(self, execution_id: str) -> tuple[dict[str, object], set[str], RunMode]:
        """Rebuild the context-fold (run-inputs + artifact pointers), the set of
        completed step names, and the run's recorded mode from its own events -- a
        read-model, not a re-execution (DESIGN §11)."""
        context: dict[str, object] = {}
        completed: set[str] = set()
        mode = RunMode.FULL_AUTO
        for event in self._store.replay(execution_id):
            if event.event_type is et.EventType.WORKFLOW_EXECUTION_STARTED:
                mode = RunMode(getattr(event.payload, "mode", RunMode.FULL_AUTO.value))
            elif event.event_type is et.EventType.RUN_INPUT_PROVIDED:
                context[event.payload.name] = event.payload.value
            elif event.event_type is et.EventType.ARTIFACT_CREATED:
                context[event.payload.name] = Path(event.payload.path)
            elif event.event_type is et.EventType.STEP_COMPLETED:
                completed.add(event.payload.step_name)
            elif event.event_type is et.EventType.ANSWER_SUBMITTED:
                # a gate answer re-enters the context under its question id (B):
                # a downstream ANSWER port reads it by that name.
                if event.payload.status == "answered":
                    context[event.payload.question_id] = event.payload.answer
        return context, completed, mode

    @staticmethod
    def _read_questions(path: Path) -> tuple[dict, ...]:
        """Parse a step's `questions` output into the structured set the gate records:
        a JSON list of ``{id?, text, blocking?}``. Fails loudly on a malformed file --
        a gate the engine can't read is an authoring error, not something to paper
        over. ``id`` defaults to a positional ``q1``/``q2``…; ``blocking`` to true."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OrchestratorError(f"questions file '{path}' is not valid JSON: {exc}") from exc
        if not isinstance(raw, list) or not raw:
            raise OrchestratorError(f"questions file '{path}' must be a non-empty JSON list")
        questions: list[dict] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict) or "text" not in item:
                raise OrchestratorError(
                    f"questions file '{path}': item {index} needs at least a 'text' field"
                )
            questions.append(
                {
                    "id": str(item.get("id", f"q{index}")),
                    "text": str(item["text"]),
                    "blocking": bool(item.get("blocking", True)),
                }
            )
        return tuple(questions)

    def _run_step(self, step, execution_id, context, bindings, report, stop=None) -> bool:
        self._store.emit(
            et.StepStarted(step_name=step.id), execution_id=execution_id, step_name=step.id
        )
        # resolve each bound artifact port from the context
        inputs = self._resolve_inputs(step, context, bindings)
        run_dir = self._workspace / "executions" / execution_id / step.id
        try:
            prov_kinds = step.required(Requirement.PROVIDER)
            provider_instance = self._provider_instances.get(prov_kinds[0]) if prov_kinds else None
            result = runner.run_executable(
                step,
                run_dir,
                inputs,
                stop=stop,
                env=self._provider_env(step),
                provider_instance=provider_instance,
            )
        except runner.RunnerError as exc:
            self._store.emit(
                et.StepFailed(step_name=step.id, reason=str(exc)),
                execution_id=execution_id,
                step_name=step.id,
            )
            return False
        if not result.ok:
            self._store.emit(
                et.StepFailed(
                    step_name=step.id, reason=f"exit {result.exit_code}: {result.stderr.strip()}"
                ),
                execution_id=execution_id,
                step_name=step.id,
            )
            return False
        # collect durable products into the context + emit pointers
        for produced in result.outputs:
            out_spec = next(o for o in step.outputs if o.name == produced.name)
            if out_spec.kind is OutputKind.QUESTIONS:
                # a courier, not a keepsake: the gate reads it (parsed in _walk), it
                # is not added to the context nor recorded as an artifact pointer.
                report.awaiting_file = produced.path
            elif out_spec.lifespan.value == "durable":
                self._store.emit(
                    et.ArtifactCreated(
                        name=produced.name,
                        path=str(produced.path),
                        sha256=produced.sha256,
                    ),
                    execution_id=execution_id,
                    step_name=step.id,
                )
                context[produced.name] = produced.path
        self._store.emit(
            et.StepCompleted(step_name=step.id), execution_id=execution_id, step_name=step.id
        )
        report.steps_run.append(step.id)
        return True

    def _run_shot(
        self,
        step,
        execution_id,
        context,
        bindings,
        report,
        shot_config,
        tier_overrides=None,
        stop=None,
    ) -> bool:
        tier_overrides = tier_overrides or {}
        self._store.emit(
            et.StepStarted(step_name=step.id), execution_id=execution_id, step_name=step.id
        )
        # a per-step, run-time tier override is a user decision -- recorded only
        # when it actually changes the tier (a no-op override is not a change).
        effective_tier = tier_overrides.get(step.id, step.tier)
        if effective_tier != step.tier:
            self._store.emit(
                et.TierOverridden(
                    step_name=step.id,
                    from_tier=step.tier.value,
                    to_tier=effective_tier.value,
                ),
                execution_id=execution_id,
                step_name=step.id,
                actor="user",
            )
        # context prefix: the cap/methodology -- run-agnostic by construction
        prefix_parts = []
        if step.cap:
            prefix_parts.append(f"You are: {step.cap}.")
        if step.methodology:
            prefix_parts.append(step.methodology)
        context_text = "\n".join(prefix_parts) or "You are a careful assistant."
        # files: the bound artifact ports (resolved from the context as paths)
        files: list[str] = []
        for port in step.artifact_ports():
            product = bindings[(step.id, port.name)]
            value = context[product]
            if isinstance(value, Path):
                files.append(str(value))
        prompt = f"Perform the step '{step.id}'. Produce its declared outputs."

        try:
            envelope = build_envelope(context_text, prompt, tuple(files))
        except Exception as exc:  # PurityError -> a definition/builder problem
            self._store.emit(
                et.StepFailed(step_name=step.id, reason=str(exc)),
                execution_id=execution_id,
                step_name=step.id,
            )
            return False

        try:
            resolution = shot_config.resolve(effective_tier)
        except OrchestratorError as exc:
            self._store.emit(
                et.StepFailed(step_name=step.id, reason=str(exc)),
                execution_id=execution_id,
                step_name=step.id,
            )
            return False
        run_dir = self._workspace / "executions" / execution_id / step.id
        try:
            result = shot_config.run_shot(
                step,
                envelope,
                resolution,
                run_dir,
                mode=shot_config.mode,
                stop=stop,
            )
        except shotrunner.ShotError as exc:
            self._store.emit(
                et.StepFailed(step_name=step.id, reason=str(exc)),
                execution_id=execution_id,
                step_name=step.id,
            )
            return False
        if not result.ok:
            self._store.emit(
                et.StepFailed(
                    step_name=step.id, reason=f"exit {result.exit_code}: {result.stderr.strip()}"
                ),
                execution_id=execution_id,
                step_name=step.id,
            )
            return False
        for produced in result.outputs:
            out_spec = next(o for o in step.outputs if o.name == produced.name)
            if out_spec.kind is OutputKind.QUESTIONS:
                report.awaiting_file = produced.path
            elif out_spec.lifespan.value == "durable":
                self._store.emit(
                    et.ArtifactCreated(
                        name=produced.name, path=str(produced.path), sha256=produced.sha256
                    ),
                    execution_id=execution_id,
                    step_name=step.id,
                )
                context[produced.name] = produced.path
        self._store.emit(
            et.StepCompleted(step_name=step.id), execution_id=execution_id, step_name=step.id
        )
        report.steps_run.append(step.id)
        return True

    def _resolve_inputs(self, step: StepSpec, context: dict, bindings: dict) -> dict:
        """Map each input port to a concrete value from the context. Artifact ports
        follow their binding to a context product; run-input ports read their own
        name (seeded at warm-up). Credential ports never carry a token -- presence
        only (the executable side gets the token elsewhere, SS10); deferred here."""
        resolved: dict[str, object] = {}
        for port in step.inputs:
            if port.requirement is Requirement.ARTIFACT:
                product = bindings[(step.id, port.name)]
                resolved[port.name] = context[product]
            elif port.requirement is Requirement.RUN_INPUT:
                resolved[port.name] = context[port.name]
            elif port.requirement is Requirement.ANSWER:
                # a gate answer, by question id -- folded into the context from
                # answer.submitted. Missing means the gate wasn't answered: fail loud.
                if port.name not in context:
                    raise OrchestratorError(
                        f"step '{step.id}' needs the answer to '{port.name}', "
                        "but no answer was provided at the gate."
                    )
                resolved[port.name] = context[port.name]
            # CONFIG / CREDENTIAL resolution lands with their slices
        return resolved

    def _provider_env(self, step: StepSpec) -> dict[str, str] | None:
        """Env vars for a step's declared providers: each instance value (config plane
        and token) exposed as ``<KIND>_<KEY>`` (e.g. ``ISSUE_TRACKER_BASE_URL``,
        ``ISSUE_TRACKER_TOKEN``). The token reaches only the executable's process this
        way -- never the context, events, prompts, or cassettes (§10). Warm-up already
        guaranteed each declared provider has a configured instance."""
        env: dict[str, str] = {}
        for kind in step.required(Requirement.PROVIDER):
            instance = self._provider_instances.get(kind, {})
            prefix = kind.upper()
            for key, value in instance.items():
                env[f"{prefix}_{key.upper()}"] = str(value)
        return env or None

    @staticmethod
    def _binding_map(workflow: Workflow) -> dict:
        return {(b.step_id, b.port): b.product for b in workflow.bindings}
