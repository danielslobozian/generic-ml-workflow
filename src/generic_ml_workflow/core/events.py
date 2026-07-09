# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""events.py -- the event store: the append-only log (the sole source of truth)
plus project-on-append read-models, on one local SQLite file (DESIGN.md SS11).

The log is authoritative and never updated or deleted. Projections are derived,
disposable, and written *in the same transaction* as the event they reflect
(immediate consistency, no refresh, no staleness). Drop every projection and
``rebuild_projections`` replays the log to reconstruct them identically.

The event envelope is uniform columns (queryable: seq, event_id, event_type,
occurred_at, execution_id, actor, and the nested scope keys step_name / attempt)
plus one opaque ``payload`` (JSON) -- the type-specific body, declared by its bean
in ``eventtypes``. The store stays generic; types live in the vocabulary.

Scope keys nest: ``execution_id`` is mandatory on every run event; ``step_name``
and ``attempt`` narrow within it (null when N/A). Loading a run is one indexed
query, ``WHERE execution_id = ? ORDER BY seq``.

Kept minimal: only the projections the event types that exist today justify
(``jobs`` and ``workflow_executions``). Step-executions, artifacts, the context
fold, and the gate read-model arrive with the slices that emit their events.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from generic_ml_workflow.core import eventtypes
from generic_ml_workflow.core.eventtypes import EventType, Payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_execution_id() -> str:
    """Mint a run's historization key -- in code, before the first event."""
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Event:
    """One envelope + typed payload. ``execution_id`` is the historization key
    (mandatory for run events). ``step_name`` / ``attempt`` are optional nested
    scope. ``event_id``, ``seq``, ``occurred_at`` are assigned by the store."""

    event_type: EventType
    execution_id: str
    payload: Payload
    step_name: str | None = None
    attempt: int | None = None
    actor: str = "system"  # system | user | ml_client
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: str = field(default_factory=_now)
    seq: int | None = None  # assigned on append


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,   -- total order, append-only
    event_id      TEXT NOT NULL UNIQUE,
    event_type    TEXT NOT NULL,
    occurred_at   TEXT NOT NULL,                        -- UTC ISO-8601, mandatory
    execution_id  TEXT NOT NULL,                        -- the historization key
    step_name     TEXT,                                 -- nested scope (optional)
    attempt       INTEGER,
    actor         TEXT NOT NULL,
    payload_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_exec ON events(execution_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- projection: jobs -- the regroup unit (history, cost, documents regroup by job)
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    label       TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
-- projection: workflow executions -- the run, with its stamp (commit/branch/version)
CREATE TABLE IF NOT EXISTS workflow_executions (
    execution_id   TEXT PRIMARY KEY,
    workflow_name  TEXT,
    input_type     TEXT,
    job_id         TEXT,
    status         TEXT,                                -- running | completed | failed | stopped
    commit_hash    TEXT,
    branch         TEXT,
    engine_version TEXT,
    created_at     TEXT,
    updated_at     TEXT
);
-- projection: the gate read-model -- one row per question per run, the queryable
-- "what's asked / answered / still pending" state, rebuilt from questions.asked
-- (inserts pending rows) and answer.submitted (updates a row to answered/skipped).
CREATE TABLE IF NOT EXISTS gate_questions (
    execution_id  TEXT NOT NULL,
    step_name     TEXT,                                -- the step that asked
    question_id   TEXT NOT NULL,
    text          TEXT,
    blocking      INTEGER,                             -- 1 = must be answered to proceed
    status        TEXT,                                -- pending | answered | skipped
    answer        TEXT,
    asked_at      TEXT,
    answered_at   TEXT,
    PRIMARY KEY (execution_id, question_id)
);
"""


class EventStore:
    """Append-only log + project-on-append read-models on one SQLite file."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- the one write path: append the event + project it, atomically ---
    def append(self, event: Event) -> Event:
        with self._conn:  # one transaction covers log + projections
            cur = self._conn.execute(
                "INSERT INTO events(event_id,event_type,occurred_at,execution_id,"
                "step_name,attempt,actor,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.event_type.value,
                    event.occurred_at,
                    event.execution_id,
                    event.step_name,
                    event.attempt,
                    event.actor,
                    json.dumps(event.payload.to_json(), ensure_ascii=False),
                ),
            )
            self._project(event)
        return Event(
            event_type=event.event_type,
            execution_id=event.execution_id,
            payload=event.payload,
            step_name=event.step_name,
            attempt=event.attempt,
            actor=event.actor,
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            seq=cur.lastrowid,
        )

    def emit(
        self,
        payload: Payload,
        execution_id: str,
        *,
        step_name: str | None = None,
        attempt: int | None = None,
        actor: str = "system",
    ) -> Event:
        """Convenience: build the envelope around a typed payload and append it.
        The event type is taken from the payload bean -- they cannot disagree."""
        return self.append(
            Event(
                event_type=payload.event_type,
                execution_id=execution_id,
                payload=payload,
                step_name=step_name,
                attempt=attempt,
                actor=actor,
            )
        )

    # --- projections (pure function of the event; rebuilt by replaying) ---
    def _project(self, e: Event) -> None:
        c = self._conn
        # The event_type <-> payload-bean correspondence is a system invariant
        # (emit() sets event_type from the bean), so each branch casts to its bean.
        if e.event_type is EventType.JOB_OPENED:
            opened = cast(eventtypes.JobOpened, e.payload)
            c.execute(
                "INSERT INTO jobs(job_id,label,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET label=COALESCE(excluded.label, jobs.label),"
                "updated_at=excluded.updated_at",
                (opened.job_id, opened.label, e.occurred_at, e.occurred_at),
            )
        elif e.event_type is EventType.WORKFLOW_EXECUTION_STARTED:
            started = cast(eventtypes.WorkflowExecutionStarted, e.payload)
            c.execute(
                "INSERT INTO workflow_executions(execution_id,workflow_name,input_type,job_id,"
                "status,commit_hash,branch,engine_version,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(execution_id) DO UPDATE SET "
                "workflow_name=excluded.workflow_name,status=excluded.status,"
                "updated_at=excluded.updated_at",
                (
                    e.execution_id,
                    started.workflow_name,
                    started.input_type,
                    started.job_id,
                    "running",
                    started.commit,
                    started.branch,
                    started.engine_version,
                    e.occurred_at,
                    e.occurred_at,
                ),
            )
        elif e.event_type is EventType.WORKFLOW_EXECUTION_COMPLETED:
            c.execute(
                "UPDATE workflow_executions SET status='completed',updated_at=? "
                "WHERE execution_id=?",
                (e.occurred_at, e.execution_id),
            )
        elif e.event_type is EventType.WORKFLOW_EXECUTION_FAILED:
            c.execute(
                "UPDATE workflow_executions SET status='failed',updated_at=? WHERE execution_id=?",
                (e.occurred_at, e.execution_id),
            )
        elif e.event_type is EventType.WORKFLOW_EXECUTION_STOPPED:
            c.execute(
                "UPDATE workflow_executions SET status='stopped',updated_at=? WHERE execution_id=?",
                (e.occurred_at, e.execution_id),
            )
        elif e.event_type is EventType.WORKFLOW_EXECUTION_RESUMED:
            c.execute(
                "UPDATE workflow_executions SET status='running',updated_at=? WHERE execution_id=?",
                (e.occurred_at, e.execution_id),
            )
        elif e.event_type is EventType.QUESTIONS_ASKED:
            asked = cast(eventtypes.QuestionsAsked, e.payload)
            for q in asked.questions:
                c.execute(
                    "INSERT INTO gate_questions(execution_id,step_name,question_id,text,"
                    "blocking,status,answer,asked_at,answered_at) "
                    "VALUES(?,?,?,?,?,'pending',NULL,?,NULL) "
                    "ON CONFLICT(execution_id,question_id) DO UPDATE SET "
                    "text=excluded.text,blocking=excluded.blocking,status='pending',"
                    "answer=NULL,asked_at=excluded.asked_at,answered_at=NULL",
                    (
                        e.execution_id,
                        e.step_name,
                        q["id"],
                        q["text"],
                        1 if q.get("blocking", True) else 0,
                        e.occurred_at,
                    ),
                )
        elif e.event_type is EventType.ANSWER_SUBMITTED:
            submitted = cast(eventtypes.AnswerSubmitted, e.payload)
            c.execute(
                "UPDATE gate_questions SET status=?,answer=?,answered_at=? "
                "WHERE execution_id=? AND question_id=?",
                (
                    submitted.status,
                    submitted.answer,
                    e.occurred_at,
                    e.execution_id,
                    submitted.question_id,
                ),
            )

    # --- reads ---
    def gate_questions(self, execution_id: str) -> list[dict[str, Any]]:
        """The gate read-model for one run: every question with its current status
        (pending / answered / skipped). The surface reads it to know what to ask;
        resume reads it to know whether the gate is satisfied."""
        rows = self._conn.execute(
            "SELECT question_id,step_name,text,blocking,status,answer FROM gate_questions "
            "WHERE execution_id=? ORDER BY rowid",
            (execution_id,),
        )
        return [
            {
                "question_id": r[0],
                "step_name": r[1],
                "text": r[2],
                "blocking": bool(r[3]),
                "status": r[4],
                "answer": r[5],
            }
            for r in rows
        ]

    def replay(self, execution_id: str) -> list[Event]:
        """Every event of one run, in order -- the basis of /replay and of the
        deterministic fold that rebuilds in-memory state."""
        rows = self._conn.execute(
            "SELECT event_type,occurred_at,execution_id,step_name,attempt,actor,"
            "payload_json,event_id,seq FROM events WHERE execution_id=? ORDER BY seq",
            (execution_id,),
        )
        return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(r: tuple[Any, ...]) -> Event:
        et = EventType(r[0])
        return Event(
            event_type=et,
            occurred_at=r[1],
            execution_id=r[2],
            step_name=r[3],
            attempt=r[4],
            actor=r[5],
            payload=eventtypes.parse_payload(et, json.loads(r[6])),
            event_id=r[7],
            seq=r[8],
        )

    def jobs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT job_id,label,created_at FROM jobs ORDER BY created_at, job_id"
        )
        return [{"job_id": r[0], "label": r[1], "created_at": r[2]} for r in rows]

    def executions(self, job_id: str | None = None) -> list[dict[str, Any]]:
        if job_id is None:
            rows = self._conn.execute(
                "SELECT execution_id,workflow_name,input_type,job_id,status,commit_hash,"
                "created_at FROM workflow_executions ORDER BY created_at"
            )
        else:
            rows = self._conn.execute(
                "SELECT execution_id,workflow_name,input_type,job_id,status,commit_hash,"
                "created_at FROM workflow_executions WHERE job_id=? ORDER BY created_at",
                (job_id,),
            )
        return [
            {
                "execution_id": r[0],
                "workflow_name": r[1],
                "input_type": r[2],
                "job_id": r[3],
                "status": r[4],
                "commit": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def execution(self, execution_id: str) -> dict[str, Any] | None:
        r = self._conn.execute(
            "SELECT execution_id,workflow_name,input_type,job_id,status,commit_hash,branch,"
            "engine_version,created_at FROM workflow_executions WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
        if not r:
            return None
        return {
            "execution_id": r[0],
            "workflow_name": r[1],
            "input_type": r[2],
            "job_id": r[3],
            "status": r[4],
            "commit": r[5],
            "branch": r[6],
            "engine_version": r[7],
            "created_at": r[8],
        }

    # --- rebuild (disaster recovery / projection schema change) ---
    def rebuild_projections(self) -> None:
        """Drop and replay: projections are derived, so they can always be
        reconstructed from the log. The log itself is never touched."""
        with self._conn:
            self._conn.execute("DELETE FROM jobs")
            self._conn.execute("DELETE FROM workflow_executions")
            rows = self._conn.execute(
                "SELECT event_type,occurred_at,execution_id,step_name,attempt,actor,"
                "payload_json,event_id,seq FROM events ORDER BY seq"
            ).fetchall()
            for r in rows:
                self._project(self._row_to_event(r))
