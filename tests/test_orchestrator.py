# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The orchestrator: warm-up, the context-fold, executable steps end to end, and
the events it emits (DESIGN.md SS4/SS7/SS11). Demo phase 1: two executable steps
wired by a binding, no model. POSIX (sh scripts); guarded on Windows."""

import sys
from pathlib import Path

import pytest

from generic_ml_workflow.core import eventtypes as et
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
    Workflow,
)
from generic_ml_workflow.core.events import EventStore
from generic_ml_workflow.core.orchestrator import (
    Orchestrator,
    OrchestratorError,
    RunPhase,
    RunProgress,
    warm_up,
)
from generic_ml_workflow.core.stamp import Stamp
from generic_ml_workflow.core.stopping import StopControl

STAMP = Stamp(commit="abc123", branch="main", engine_version="0.0.5.dev0")


def _out(name, filename):
    return OutputPort(name=name, lifespan=Lifespan.DURABLE, kind=OutputKind.FILE, filename=filename)


# --- warm-up (pure, cross-platform) ------------------------------------------


def _wf_needing_runinput():
    step = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint="true",
        inputs=(InputPort("url", Requirement.RUN_INPUT),),
        outputs=(_out("o", "o.txt"),),
    )
    return Workflow(name="w", input_type=InputType.URL, steps=(step,))


def test_warm_up_passes_when_run_input_provided():
    warm_up(_wf_needing_runinput(), {"url": "http://x"})  # no raise


def test_warm_up_fails_on_missing_run_input():
    with pytest.raises(OrchestratorError, match="run-input 'url' was not provided"):
        warm_up(_wf_needing_runinput(), {})


def test_warm_up_fails_on_missing_config_and_credential():
    step = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint="true",
        inputs=(
            InputPort("base_url", Requirement.CONFIG),
            InputPort("token", Requirement.CREDENTIAL),
        ),
        outputs=(_out("o", "o.txt"),),
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(step,))
    with pytest.raises(OrchestratorError) as exc:
        warm_up(wf, {}, config_values={}, credentials=set())
    msg = str(exc.value)
    assert "configuration 'base_url'" in msg and "credential role 'token'" in msg


# --- the demo phase-1 run (POSIX) --------------------------------------------


def _demo_workflow(scripts: Path) -> Workflow:
    """fetch: writes page.html from a run-input; extract: reads it, writes text."""
    fetch_sh = scripts / "fetch.sh"
    fetch_sh.write_text("printf '<h1>%s</h1>' \"$(cat url)\" > page.html\n", encoding="utf-8")
    extract_sh = scripts / "extract.sh"
    # strip the tags crudely -> page_text.txt
    extract_sh.write_text("sed 's/<[^>]*>//g' source > page_text.txt\n", encoding="utf-8")
    fetch = StepSpec(
        id="fetch",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(fetch_sh),
        inputs=(InputPort("url", Requirement.RUN_INPUT),),
        outputs=(_out("page", "page.html"),),
    )
    extract = StepSpec(
        id="extract",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(extract_sh),
        inputs=(InputPort("source", Requirement.ARTIFACT),),
        outputs=(_out("page_text", "page_text.txt"),),
    )
    return Workflow(
        name="demo",
        input_type=InputType.URL,
        steps=(fetch, extract),
        bindings=(Binding("extract", "source", "page"),),
    )


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_demo_phase1_runs_end_to_end(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    orch = Orchestrator(store, tmp_path / "ws")
    report = orch.run(wf, {"url": "hello world"}, STAMP)

    assert report.completed
    assert report.steps_run == ["fetch", "extract"]

    # the event story is real and complete
    events = store.replay(report.execution_id)
    types = [e.event_type for e in events]
    assert types[0] is et.EventType.WORKFLOW_EXECUTION_STARTED
    assert types[-1] is et.EventType.WORKFLOW_EXECUTION_COMPLETED
    assert et.EventType.RUN_INPUT_PROVIDED in types
    assert types.count(et.EventType.STEP_COMPLETED) == 2
    assert types.count(et.EventType.ARTIFACT_CREATED) == 2

    # the stamp is on the started event
    started = events[0].payload
    assert started.commit == "abc123" and started.engine_version == "0.0.5.dev0"

    # the projection reflects completion
    assert store.execution(report.execution_id)["status"] == "completed"

    # the final product exists and the context-fold actually carried the file through
    artifact_events = [e for e in events if e.event_type is et.EventType.ARTIFACT_CREATED]
    text_event = next(e for e in artifact_events if e.payload.name == "page_text")
    produced = Path(text_event.payload.path)
    assert produced.read_text().strip() == "hello world"  # tags stripped, value carried


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_step_failure_stops_the_run_and_marks_failed(tmp_path):
    store = EventStore(":memory:")
    bad = tmp_path / "bad.sh"
    bad.write_text("exit 2\n", encoding="utf-8")
    step = StepSpec(
        id="boom",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(bad),
        outputs=(_out("o", "o.txt"),),
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(step,))
    report = Orchestrator(store, tmp_path / "ws").run(wf, {}, STAMP)
    assert not report.completed and report.failed_step == "boom"
    assert store.execution(report.execution_id)["status"] == "failed"
    types = [e.event_type for e in store.replay(report.execution_id)]
    assert et.EventType.STEP_FAILED in types


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_interpretable_step_stops_honestly(tmp_path):
    store = EventStore(":memory:")
    shot = StepSpec(
        id="judge", nature=StepNature.INTERPRETABLE, cap="analyst", outputs=(_out("v", "v.md"),)
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot,))
    report = Orchestrator(store, tmp_path / "ws").run(wf, {}, STAMP)
    assert not report.completed
    assert "no client/model" in report.stopped_reason
    assert store.execution(report.execution_id)["status"] == "failed"


def test_invalid_workflow_never_runs(tmp_path):
    store = EventStore(":memory:")
    # interpretable step with no cap -> validation error
    bad = StepSpec(id="s", nature=StepNature.INTERPRETABLE, outputs=(_out("o", "o.md"),))
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(bad,))
    with pytest.raises(OrchestratorError, match="does not validate"):
        Orchestrator(store, tmp_path / "ws").run(wf, {}, STAMP)
    # nothing was recorded
    assert store.executions() == []


# --- 0.0.6: interpretable steps run through the gmlcache seam ------------------

from generic_ml_workflow.core import shotrunner  # noqa: E402
from generic_ml_workflow.core.contract import Tier  # noqa: E402
from generic_ml_workflow.core.orchestrator import ShotConfig  # noqa: E402


def _fake_shot_runner(produces: str):
    """A stand-in for shotrunner.run_shot that simulates gmlcache producing the
    step's declared output, without any real client."""

    def runner_fn(spec, envelope, resolution, run_dir, *, mode, **kw):
        from pathlib import Path as _P

        rd = _P(run_dir)
        if rd.exists():
            import shutil

            shutil.rmtree(rd)
        rd.mkdir(parents=True)
        out = spec.outputs[0]
        (rd / out.filename).write_text(produces, encoding="utf-8")
        import hashlib

        sha = hashlib.sha256(produces.encode()).hexdigest()
        return shotrunner.ShotResult(
            step_id=spec.id,
            attempt=1,
            exit_code=0,
            stdout=produces,
            stderr="",
            duration_seconds=0.01,
            outputs=(shotrunner.ProducedOutput(out.name, rd / out.filename, sha),),
        )

    return runner_fn


def _shot_config(tmp_path, produces="a summary", mode="cache"):
    return ShotConfig(
        resolutions={Tier.MEDIUM: shotrunner.Resolution("claude", "sonnet")},
        mode=mode,
        run_shot=_fake_shot_runner(produces),
    )


def test_interpretable_step_stops_when_no_shot_config(tmp_path):
    store = EventStore(":memory:")
    shot = StepSpec(
        id="judge", nature=StepNature.INTERPRETABLE, cap="analyst", outputs=(_out("v", "v.md"),)
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot,))
    report = Orchestrator(store, tmp_path / "ws").run(wf, {}, STAMP)  # no shot_config
    assert not report.completed and "no client/model" in report.stopped_reason


def test_shot_step_runs_through_the_seam(tmp_path):
    store = EventStore(":memory:")
    shot = StepSpec(
        id="summarize",
        nature=StepNature.INTERPRETABLE,
        cap="summarizer",
        tier=Tier.MEDIUM,
        outputs=(_out("summary", "summary.md"),),
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot,))
    report = Orchestrator(store, tmp_path / "ws").run(
        wf,
        {},
        STAMP,
        shot_config=_shot_config(tmp_path, "the summary"),
    )
    assert report.completed and report.steps_run == ["summarize"]
    events = store.replay(report.execution_id)
    types = [e.event_type for e in events]
    assert et.EventType.ARTIFACT_CREATED in types
    art = next(e for e in events if e.event_type is et.EventType.ARTIFACT_CREATED)
    assert Path(art.payload.path).read_text() == "the summary"
    assert store.execution(report.execution_id)["status"] == "completed"


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_mixed_executable_then_shot_carries_context(tmp_path):
    # fetch (executable) -> summarize (shot): the shot consumes the executable's product
    store = EventStore(":memory:")
    fetch_sh = tmp_path / "fetch.sh"
    fetch_sh.write_text("printf 'raw text' > page.txt\n", encoding="utf-8")
    fetch = StepSpec(
        id="fetch",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(fetch_sh),
        outputs=(_out("page", "page.txt"),),
    )
    summarize = StepSpec(
        id="summarize",
        nature=StepNature.INTERPRETABLE,
        cap="summarizer",
        tier=Tier.MEDIUM,
        inputs=(InputPort("source", Requirement.ARTIFACT),),
        outputs=(_out("summary", "summary.md"),),
    )
    wf = Workflow(
        name="w",
        input_type=InputType.FREESTYLE,
        steps=(fetch, summarize),
        bindings=(Binding("summarize", "source", "page"),),
    )
    report = Orchestrator(store, tmp_path / "ws").run(
        wf,
        {},
        STAMP,
        shot_config=_shot_config(tmp_path, "summarized"),
    )
    assert report.completed and report.steps_run == ["fetch", "summarize"]


def test_unconfigured_tier_is_an_error(tmp_path):
    store = EventStore(":memory:")
    shot = StepSpec(
        id="s",
        nature=StepNature.INTERPRETABLE,
        cap="c",
        tier=Tier.HIGH,  # only MEDIUM configured
        outputs=(_out("o", "o.md"),),
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot,))
    report = Orchestrator(store, tmp_path / "ws").run(
        wf,
        {},
        STAMP,
        shot_config=_shot_config(tmp_path),
    )
    assert not report.completed and report.failed_step == "s"
    types = [e.event_type for e in store.replay(report.execution_id)]
    assert et.EventType.STEP_FAILED in types


# --- 0.0.7: per-step tier override at run time --------------------------------


def _recording_shot_runner(captured: list, produces="ok"):
    """Like _fake_shot_runner, but records the Resolution each shot received, so a
    test can assert which tier actually fired."""

    def runner_fn(spec, envelope, resolution, run_dir, *, mode, **kw):
        from pathlib import Path as _P
        import hashlib

        captured.append((spec.id, resolution.model))
        rd = _P(run_dir)
        if rd.exists():
            import shutil

            shutil.rmtree(rd)
        rd.mkdir(parents=True)
        out = spec.outputs[0]
        (rd / out.filename).write_text(produces, encoding="utf-8")
        sha = hashlib.sha256(produces.encode()).hexdigest()
        return shotrunner.ShotResult(
            step_id=spec.id,
            attempt=1,
            exit_code=0,
            stdout=produces,
            stderr="",
            duration_seconds=0.01,
            outputs=(shotrunner.ProducedOutput(out.name, rd / out.filename, sha),),
        )

    return runner_fn


def _two_tier_config(captured):
    return ShotConfig(
        resolutions={
            Tier.MEDIUM: shotrunner.Resolution("claude", "sonnet"),
            Tier.HIGH: shotrunner.Resolution("claude", "opus"),
        },
        run_shot=_recording_shot_runner(captured),
    )


def _one_shot_wf(tier=Tier.MEDIUM):
    shot = StepSpec(
        id="summarize",
        nature=StepNature.INTERPRETABLE,
        cap="summarizer",
        tier=tier,
        outputs=(_out("summary", "summary.md"),),
    )
    return Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot,))


def test_tier_override_changes_resolution_and_records_event(tmp_path):
    store = EventStore(":memory:")
    captured: list = []
    wf = _one_shot_wf(tier=Tier.MEDIUM)
    report = Orchestrator(store, tmp_path / "ws").run(
        wf,
        {},
        STAMP,
        shot_config=_two_tier_config(captured),
        tier_overrides={"summarize": Tier.HIGH},
    )
    assert report.completed
    # the HIGH resolution fired, not the step's declared MEDIUM
    assert captured == [("summarize", "opus")]
    # and the decision is recorded, scoped to the step, as a user action
    events = store.replay(report.execution_id)
    ov = next(e for e in events if e.event_type is et.EventType.TIER_OVERRIDDEN)
    assert ov.payload.from_tier == "medium" and ov.payload.to_tier == "high"
    assert ov.step_name == "summarize" and ov.actor == "user"


def test_no_override_event_when_tier_unchanged(tmp_path):
    store = EventStore(":memory:")
    captured: list = []
    wf = _one_shot_wf(tier=Tier.MEDIUM)
    report = Orchestrator(store, tmp_path / "ws").run(
        wf,
        {},
        STAMP,
        shot_config=_two_tier_config(captured),
        tier_overrides={"summarize": Tier.MEDIUM},  # same as declared -> no change
    )
    assert report.completed and captured == [("summarize", "sonnet")]
    types = [e.event_type for e in store.replay(report.execution_id)]
    assert et.EventType.TIER_OVERRIDDEN not in types


# --- 0.0.8: the engine announces advancement through a progress reporter ------
# A side channel for a surface's live display, distinct from the event log. The
# engine stays synchronous; the reporter is called at each boundary (DESIGN SS11).


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_progress_reports_every_boundary_on_success(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    seen: list[RunProgress] = []
    report = Orchestrator(store, tmp_path / "ws").run(
        wf, {"url": "hello world"}, STAMP, progress=seen.append
    )
    assert report.completed
    assert [p.phase for p in seen] == [
        RunPhase.RUN_STARTED,
        RunPhase.STEP_STARTED,
        RunPhase.STEP_COMPLETED,
        RunPhase.STEP_STARTED,
        RunPhase.STEP_COMPLETED,
        RunPhase.RUN_COMPLETED,
    ]
    # step boundaries name the step and number it within the count
    started = [p for p in seen if p.phase is RunPhase.STEP_STARTED]
    assert [(p.step_name, p.step_number, p.step_count) for p in started] == [
        ("fetch", 1, 2),
        ("extract", 2, 2),
    ]
    # every notification carries the same run identity (the surface needs no lookup)
    assert {p.execution_id for p in seen} == {report.execution_id}


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_progress_reports_step_failure_then_run_failure(tmp_path):
    store = EventStore(":memory:")
    bad = tmp_path / "bad.sh"
    bad.write_text("exit 2\n", encoding="utf-8")
    step = StepSpec(
        id="boom", nature=StepNature.EXECUTABLE, entrypoint=str(bad), outputs=(_out("o", "o.txt"),)
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(step,))
    seen: list[RunProgress] = []
    Orchestrator(store, tmp_path / "ws").run(wf, {}, STAMP, progress=seen.append)
    assert [p.phase for p in seen] == [
        RunPhase.RUN_STARTED,
        RunPhase.STEP_STARTED,
        RunPhase.STEP_FAILED,
        RunPhase.RUN_FAILED,
    ]
    assert seen[-1].reason == "step 'boom' failed"


def test_progress_reports_run_failure_when_shot_unconfigured(tmp_path):
    store = EventStore(":memory:")
    shot = StepSpec(
        id="judge", nature=StepNature.INTERPRETABLE, cap="analyst", outputs=(_out("v", "v.md"),)
    )
    wf = Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot,))
    seen: list[RunProgress] = []
    Orchestrator(store, tmp_path / "ws").run(wf, {}, STAMP, progress=seen.append)  # no shot_config
    assert [p.phase for p in seen] == [RunPhase.RUN_STARTED, RunPhase.RUN_FAILED]
    assert "no client/model" in seen[-1].reason


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_default_reporter_is_a_noop(tmp_path):
    # omitting progress= must behave exactly as before (a run with no surface attached)
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    report = Orchestrator(store, tmp_path / "ws").run(wf, {"url": "x"}, STAMP)  # no progress=
    assert report.completed


# --- 0.0.8: clean stop -- a stopped run is recorded distinctly from a failed one


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_stop_before_a_step_records_a_stopped_run(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    stop = StopControl()
    stop.request()  # already requested -> the run stops before step one
    seen: list[RunProgress] = []
    report = Orchestrator(store, tmp_path / "ws").run(
        wf, {"url": "x"}, STAMP, progress=seen.append, stop=stop
    )
    assert not report.completed
    assert report.stopped_reason == "stopped by request"
    assert seen[-1].phase is RunPhase.RUN_STOPPED
    assert store.execution(report.execution_id)["status"] == "stopped"
    types = [e.event_type for e in store.replay(report.execution_id)]
    assert et.EventType.WORKFLOW_EXECUTION_STOPPED in types
    assert et.EventType.WORKFLOW_EXECUTION_FAILED not in types  # stopped != failed


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_stop_after_first_step_does_not_start_the_second(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    stop = StopControl()
    seen: list[RunProgress] = []

    def progress(p: RunProgress) -> None:
        seen.append(p)
        # ask to stop the moment the first step completes
        if p.phase is RunPhase.STEP_COMPLETED and p.step_name == "fetch":
            stop.request()

    report = Orchestrator(store, tmp_path / "ws").run(
        wf, {"url": "x"}, STAMP, progress=progress, stop=stop
    )
    assert not report.completed
    assert report.stopped_reason == "stopped by request"
    assert "fetch" in report.steps_run and "extract" not in report.steps_run
    assert RunPhase.RUN_STOPPED in [p.phase for p in seen]
    assert store.execution(report.execution_id)["status"] == "stopped"


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_a_run_with_no_stop_completes_normally(tmp_path):
    # the stop path must not perturb an ordinary run (stop defaults to None)
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    report = Orchestrator(store, tmp_path / "ws").run(wf, {"url": "x"}, STAMP)
    assert report.completed


# --- 0.0.8: resume -- continue a stopped run, rebuilt from its own log -----------


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_resume_continues_a_stopped_run_from_the_log(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    orch = Orchestrator(store, tmp_path / "ws")

    # run, but stop the moment the first step completes
    stop = StopControl()

    def progress(p: RunProgress) -> None:
        if p.phase is RunPhase.STEP_COMPLETED and p.step_name == "fetch":
            stop.request()

    first = orch.run(wf, {"url": "hello world"}, STAMP, progress=progress, stop=stop)
    assert not first.completed
    assert first.steps_run == ["fetch"]  # extract never started
    assert store.execution(first.execution_id)["status"] == "stopped"

    # resume the same execution: skip fetch, run extract, complete
    second = orch.resume(first.execution_id, wf)
    assert second.execution_id == first.execution_id  # same run, continued
    assert second.completed
    assert second.steps_run == ["extract"]  # only the unfinished step ran this segment
    assert store.execution(first.execution_id)["status"] == "completed"

    events = store.replay(first.execution_id)
    types = [e.event_type for e in events]
    assert et.EventType.WORKFLOW_EXECUTION_RESUMED in types
    assert types[-1] is et.EventType.WORKFLOW_EXECUTION_COMPLETED

    # the resumed step saw the first step's product -> the context-fold was rebuilt
    artifacts = [e for e in events if e.event_type is et.EventType.ARTIFACT_CREATED]
    text_event = next(e for e in artifacts if e.payload.name == "page_text")
    assert Path(text_event.payload.path).read_text().strip() == "hello world"


def test_resume_refuses_a_completed_run(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    orch = Orchestrator(store, tmp_path / "ws")
    report = orch.run(wf, {"url": "x"}, STAMP)
    assert report.completed
    with pytest.raises(OrchestratorError, match="nothing to resume"):
        orch.resume(report.execution_id, wf)


def test_resume_unknown_execution_is_loud(tmp_path):
    store = EventStore(":memory:")
    wf = _demo_workflow(tmp_path)
    with pytest.raises(OrchestratorError, match="no execution"):
        Orchestrator(store, tmp_path / "ws").resume("does-not-exist", wf)
