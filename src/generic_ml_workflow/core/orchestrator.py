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

from dataclasses import dataclass, field
from pathlib import Path

from generic_ml_workflow.core import runner, shotrunner
from generic_ml_workflow.core.contract import (
    Requirement,
    StepNature,
    StepSpec,
    Tier,
    Workflow,
)
from generic_ml_workflow.core.envelope import build_envelope
from generic_ml_workflow.core.events import EventStore, new_execution_id
from generic_ml_workflow.core.stamp import Stamp
from generic_ml_workflow.core import eventtypes as et


class OrchestratorError(Exception):
    """The execution could not proceed (unrunnable definition, unmet requirement)."""


@dataclass
class RunReport:
    """The outcome of one run, for the caller to render."""

    execution_id: str
    completed: bool
    steps_run: list[str] = field(default_factory=list)
    stopped_reason: str | None = None  # why a run stopped early (e.g. an ML step)
    failed_step: str | None = None


@dataclass
class ShotConfig:
    """How interpretable steps resolve and cache (DESIGN.md SS9). Tier -> concrete
    (client, model, effort); the cassette ``store`` and ``mode``. Full tier
    reconciliation against installed clients is slice 0.0.7; here the caller
    supplies the resolution map explicitly. ``run_shot`` is injectable for tests."""

    resolutions: dict[Tier, shotrunner.Resolution]
    store: Path
    mode: str = "cache"
    run_shot: object = staticmethod(shotrunner.run_shot)

    def resolve(self, tier: Tier) -> shotrunner.Resolution:
        if tier not in self.resolutions:
            raise OrchestratorError(
                f"no client/model configured for tier '{tier.value}' "
                "(tier reconciliation arrives in slice 0.0.7; supply a resolution)"
            )
        return self.resolutions[tier]


def warm_up(
    workflow: Workflow,
    run_inputs: dict[str, str],
    config_values: dict[str, str] | None = None,
    credentials: set[str] | None = None,
) -> None:
    """Token-free readiness check before step one fires (DESIGN.md SS7).

    Every computed run-input must be supplied; every configuration requirement must
    be satisfiable from config; every credential role must be present. Raises
    ``OrchestratorError`` listing what is missing, before any execution opens.
    """
    config_values = config_values or {}
    credentials = credentials or set()
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
    if missing:
        raise OrchestratorError("this workflow is not ready to run:\n  " + "\n  ".join(missing))


class Orchestrator:
    """Runs one workflow execution against an event store, in a workspace."""

    def __init__(self, store: EventStore, workspace: Path):
        self._store = store
        self._workspace = Path(workspace)

    def run(
        self,
        workflow: Workflow,
        run_inputs: dict[str, str],
        stamp: Stamp,
        *,
        job_id: str | None = None,
        config_values: dict[str, str] | None = None,
        credentials: set[str] | None = None,
        shot_config: ShotConfig | None = None,
    ) -> RunReport:
        # gate: the definition must be valid (warnings are fine, errors are not)
        result = workflow.validate()
        if not result.ok:
            raise OrchestratorError(
                "workflow does not validate; fix these before running:\n  "
                + "\n  ".join(result.errors)
            )
        # gate: requirements ready, token-free, before opening the execution
        warm_up(workflow, run_inputs, config_values, credentials)

        execution_id = new_execution_id()
        self._store.emit(
            et.WorkflowExecutionStarted(
                workflow_name=workflow.name,
                input_type=workflow.input_type.value,
                commit=stamp.commit,
                branch=stamp.branch,
                engine_version=stamp.engine_version,
                job_id=job_id,
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

        report = RunReport(execution_id=execution_id, completed=False)
        bindings = self._binding_map(workflow)

        for step in workflow.steps:
            if step.nature is StepNature.INTERPRETABLE:
                if shot_config is None:
                    # no shot configuration supplied -> stop honestly, do not fake it
                    report.stopped_reason = (
                        f"step '{step.id}' is a shot, but no client/model resolution was "
                        "provided (configure tiers -- slice 0.0.7 -- or pass a shot_config)."
                    )
                    self._store.emit(
                        et.WorkflowExecutionFailed(reason=report.stopped_reason),
                        execution_id=execution_id,
                    )
                    return report
                if not self._run_shot(step, execution_id, context, bindings, report, shot_config):
                    self._store.emit(
                        et.WorkflowExecutionFailed(reason=f"step '{step.id}' failed"),
                        execution_id=execution_id,
                    )
                    report.failed_step = step.id
                    return report
                continue

            if not self._run_step(step, execution_id, context, bindings, report):
                self._store.emit(
                    et.WorkflowExecutionFailed(reason=f"step '{step.id}' failed"),
                    execution_id=execution_id,
                )
                report.failed_step = step.id
                return report

        self._store.emit(et.WorkflowExecutionCompleted(), execution_id=execution_id)
        report.completed = True
        return report

    def _run_step(self, step, execution_id, context, bindings, report) -> bool:
        self._store.emit(
            et.StepStarted(step_name=step.id), execution_id=execution_id, step_name=step.id
        )
        # resolve each bound artifact port from the context
        inputs = self._resolve_inputs(step, context, bindings)
        run_dir = self._workspace / "executions" / execution_id / step.id
        try:
            result = runner.run_executable(step, run_dir, inputs)
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
            if out_spec.lifespan.value == "durable":
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

    def _run_shot(self, step, execution_id, context, bindings, report, shot_config) -> bool:
        self._store.emit(
            et.StepStarted(step_name=step.id), execution_id=execution_id, step_name=step.id
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
            resolution = shot_config.resolve(step.tier)
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
                store=shot_config.store,
                mode=shot_config.mode,
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
            if out_spec.lifespan.value == "durable":
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
            # CONFIG / CREDENTIAL resolution lands with their slices
        return resolved

    @staticmethod
    def _binding_map(workflow: Workflow) -> dict:
        return {(b.step_id, b.port): b.product for b in workflow.bindings}
