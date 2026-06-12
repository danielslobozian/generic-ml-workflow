# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The event store (converted salvage groundwork): append/replay round-trip,
projection correctness, pointer-only payloads, and the Job entity. The store runs
in-memory; nothing touches a real file. The full event-spine slice is 0.0.4."""

from generic_ml_workflow.core import events as ev
from generic_ml_workflow.core.events import Event, EventStore


def _mem() -> EventStore:
    return EventStore(":memory:")


# --- append / replay --------------------------------------------------------


def test_append_replay_round_trip_preserves_order_and_payload():
    s = _mem()
    s.append(Event(event_type=ev.WORKFLOW_STARTED, session_id="x1", payload={"workflow": "demo"}))
    s.append(Event(event_type=ev.STEP_STARTED, session_id="x1", step_id="fetch"))
    s.append(
        Event(
            event_type=ev.ARTIFACT_CREATED,
            session_id="x1",
            step_id="fetch",
            payload={"name": "page", "path": "/runs/x1/page.html", "sha256": "ab" * 32},
        )
    )
    s.append(Event(event_type=ev.WORKFLOW_COMPLETED, session_id="x1"))
    got = s.replay("x1")
    assert [e.event_type for e in got] == [
        ev.WORKFLOW_STARTED,
        ev.STEP_STARTED,
        ev.ARTIFACT_CREATED,
        ev.WORKFLOW_COMPLETED,
    ]
    assert got[2].payload["sha256"] == "ab" * 32  # the pointer, not the content


def test_replay_is_scoped_to_one_session():
    s = _mem()
    s.append(Event(event_type=ev.WORKFLOW_STARTED, session_id="a", payload={"workflow": "w"}))
    s.append(Event(event_type=ev.WORKFLOW_STARTED, session_id="b", payload={"workflow": "w"}))
    assert len(s.replay("a")) == 1


# --- projections ------------------------------------------------------------


def test_session_projection_follows_the_log():
    s = _mem()
    s.append(
        Event(
            event_type=ev.WORKFLOW_STARTED,
            session_id="x1",
            payload={"workflow": "demo", "job_id": "J-1"},
        )
    )
    s.append(Event(event_type=ev.STEP_STARTED, session_id="x1", step_id="analyze"))
    row = s.session_row("x1")
    assert row["status"] == "running" and row["current_step"] == "analyze"
    assert row["job_id"] == "J-1"
    s.append(Event(event_type=ev.WORKFLOW_FAILED, session_id="x1"))
    assert s.session_row("x1")["status"] == "failed"


def test_artifact_projection_upserts_by_name():
    s = _mem()
    for sha in ("aa", "bb"):
        s.append(
            Event(
                event_type=ev.ARTIFACT_CREATED,
                session_id="x1",
                step_id="fetch",
                payload={"name": "page", "path": "/p", "sha256": sha},
            )
        )
    arts = s.artifacts("x1")
    assert len(arts) == 1 and arts[0]["sha256"] == "bb"


def test_questions_projection_open_then_answered():
    s = _mem()
    s.append(
        Event(
            event_type=ev.QUESTIONS_ASKED,
            session_id="x1",
            step_id="analyze",
            payload={"questions": [{"id": "q1", "text": "which variant?", "blocking": True}]},
        )
    )
    assert s.open_blocking_questions("x1", "analyze") == [{"id": "q1", "text": "which variant?"}]
    s.append(
        Event(
            event_type=ev.USER_ANSWER_SUBMITTED,
            session_id="x1",
            step_id="analyze",
            actor="user",
            payload={"question_id": "q1", "answer": "the second", "status": "answered"},
        )
    )
    assert s.open_blocking_questions("x1", "analyze") == []


# --- jobs (the organizing unit) ----------------------------------------------


def test_open_job_creates_then_selects_idempotently():
    s = _mem()
    row = s.open_job("JOB-1", "fix login")
    assert row["job_id"] == "JOB-1" and row["label"] == "fix login" and row["status"] == "open"
    s.open_job("JOB-1")  # selecting again
    assert len(s.jobs()) == 1  # not duplicated
    opened = [e for e in s.replay("JOB-1") if e.event_type == ev.JOB_OPENED]
    assert len(opened) == 1  # and records no second event


def test_jobs_list_in_creation_order():
    s = _mem()
    s.open_job("A", "first")
    s.open_job("B", "second")
    assert [j["job_id"] for j in s.jobs()] == ["A", "B"]


def test_executions_regroup_by_job():
    s = _mem()
    s.open_job("J-1")
    for sid in ("x1", "x2"):
        s.append(
            Event(
                event_type=ev.WORKFLOW_STARTED,
                session_id=sid,
                payload={"workflow": "demo", "job_id": "J-1"},
            )
        )
    assert [e["session_id"] for e in s.executions_for_job("J-1")] == ["x1", "x2"]
