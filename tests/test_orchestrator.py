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
from generic_ml_workflow.core.orchestrator import Orchestrator, OrchestratorError, warm_up
from generic_ml_workflow.core.stamp import Stamp

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
    assert "0.0.6" in report.stopped_reason
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
