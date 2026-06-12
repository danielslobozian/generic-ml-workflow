# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The event store: append/replay round-trip, project-on-append read-models,
typed payloads through the envelope, and rebuild-from-log. Runs in-memory."""

from generic_ml_workflow.core import eventtypes as et
from generic_ml_workflow.core.events import Event, EventStore, new_execution_id


def _store() -> EventStore:
    return EventStore(":memory:")


def test_new_execution_id_is_unique():
    assert new_execution_id() != new_execution_id()


def test_append_assigns_seq_and_round_trips_typed_payload():
    s = _store()
    x = new_execution_id()
    ev = s.emit(
        et.WorkflowExecutionStarted(
            workflow_name="feature",
            input_type="url",
            commit="abc",
            branch="main",
            engine_version="0.0.4.dev0",
            job_id="J-1",
        ),
        execution_id=x,
    )
    assert ev.seq == 1
    got = s.replay(x)
    assert len(got) == 1
    payload = got[0].payload
    assert isinstance(payload, et.WorkflowExecutionStarted)
    assert payload.workflow_name == "feature" and payload.commit == "abc"


def test_replay_is_scoped_and_ordered_by_execution():
    s = _store()
    a, b = new_execution_id(), new_execution_id()
    s.emit(et.RunInputProvided(name="t", value="1"), execution_id=a)
    s.emit(et.RunInputProvided(name="t", value="2"), execution_id=b)
    s.emit(et.StepStarted(step_name="fetch"), execution_id=a, step_name="fetch")
    assert [e.event_type for e in s.replay(a)] == [
        et.EventType.RUN_INPUT_PROVIDED,
        et.EventType.STEP_STARTED,
    ]
    assert len(s.replay(b)) == 1


def test_scope_keys_are_stored():
    s = _store()
    x = new_execution_id()
    s.emit(
        et.StepStarted(step_name="fetch", attempt=2), execution_id=x, step_name="fetch", attempt=2
    )
    ev = s.replay(x)[0]
    assert ev.step_name == "fetch" and ev.attempt == 2


def test_workflow_execution_projection_tracks_the_log():
    s = _store()
    x = new_execution_id()
    s.emit(
        et.WorkflowExecutionStarted(
            workflow_name="feature",
            input_type="url",
            commit="c1",
            branch="main",
            engine_version="0.0.4.dev0",
            job_id="J-1",
        ),
        execution_id=x,
    )
    row = s.execution(x)
    assert row["status"] == "running" and row["workflow_name"] == "feature"
    assert row["commit"] == "c1"  # the stamp is projected
    s.emit(et.WorkflowExecutionCompleted(), execution_id=x)
    assert s.execution(x)["status"] == "completed"


def test_job_projection_idempotent_upsert():
    s = _store()
    j = new_execution_id()
    s.emit(et.JobOpened(job_id="JOB-1", label="fix login"), execution_id=j)
    s.emit(et.JobOpened(job_id="JOB-1"), execution_id=j)  # re-open, no label
    jobs = s.jobs()
    assert len(jobs) == 1 and jobs[0]["label"] == "fix login"  # label preserved


def test_executions_regroup_by_job():
    s = _store()
    for sid in (new_execution_id(), new_execution_id()):
        s.emit(
            et.WorkflowExecutionStarted(
                workflow_name="feature",
                input_type="url",
                commit="c",
                branch="main",
                engine_version="v",
                job_id="J-1",
            ),
            execution_id=sid,
        )
    assert len(s.executions(job_id="J-1")) == 2
    assert s.executions(job_id="J-other") == []


def test_rebuild_projections_reconstructs_from_the_log():
    s = _store()
    x = new_execution_id()
    s.emit(
        et.WorkflowExecutionStarted(
            workflow_name="feature",
            input_type="url",
            commit="c",
            branch="main",
            engine_version="v",
        ),
        execution_id=x,
    )
    s.emit(et.WorkflowExecutionCompleted(), execution_id=x)
    # nuke the projection by hand, then rebuild purely from events
    s._conn.execute("DELETE FROM workflow_executions")
    s._conn.commit()
    assert s.execution(x) is None
    s.rebuild_projections()
    assert s.execution(x)["status"] == "completed"  # reconstructed identically


def test_event_carries_uuid_and_timestamp():
    s = _store()
    x = new_execution_id()
    ev = s.emit(et.RunInputProvided(name="t", value="1"), execution_id=x)
    assert ev.event_id and ev.occurred_at and "T" in ev.occurred_at  # ISO-8601 UTC


def test_append_directly_with_event_object():
    s = _store()
    x = new_execution_id()
    ev = Event(
        event_type=et.EventType.RUN_INPUT_PROVIDED,
        execution_id=x,
        payload=et.RunInputProvided(name="ticket", value="test-001"),
    )
    stored = s.append(ev)
    assert stored.seq == 1
    assert s.replay(x)[0].payload.value == "test-001"
