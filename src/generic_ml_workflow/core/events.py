# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""events.py -- the spine.

An append-only event log is the truth; projections are convenient views rebuilt
from it. This is the lightweight, hand-rolled form (stdlib sqlite3) -- no
framework, the right weight for a local single-user app.

Two disciplines hold the founding rule (design invariant 11):

  * The event payload is small: metadata + POINTERS (path + sha256), never file
    contents. The substance stays in files on disk; the event references it.
  * One generic envelope, typed-by-event_type payloads. Not one table per type.

``replay(session_id)`` returns the ordered events -- the basis of the `/replay`
story view, and the same keys (session/step/execution/cap) give cost attribution
for free.

Groundwork note: this module is converted salvage. Its real slice is 0.0.4 (the
event spine), which wires it into the REPL, settles the Run/StepExecution naming,
and adds the pointer-only payload enforcement to the gate. Until then nothing on
the launch path opens a database.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --- event-type namespace (draw from this; implement only what a slice needs) ---
WORKFLOW_STARTED = "workflow.started"
WORKFLOW_COMPLETED = "workflow.completed"
WORKFLOW_FAILED = "workflow.failed"
STEP_STARTED = "step.started"
STEP_BLOCKED = "step.blocked"
STEP_UNBLOCKED = "step.unblocked"
STEP_COMPLETED = "step.completed"
STEP_FAILED = "step.failed"
EXECUTION_COMPLETED = "execution.completed"  # one shot or executable invocation finished
ARTIFACT_CREATED = "artifact.created"  # a declared output landed (path + sha256)
QUESTIONS_ASKED = "questions.asked"  # a transport questions-set was extracted
USER_ANSWER_SUBMITTED = "user.answer_submitted"
JOB_OPENED = "job.opened"  # a job was created (the regroup key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Event:
    event_type: str
    session_id: str
    payload: dict = field(default_factory=dict)
    step_id: str | None = None
    execution_id: str | None = None
    cap: str | None = None
    actor: str = "system"  # system | user | ml_client
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: str = field(default_factory=_now)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,   -- total order, append-only
    event_id      TEXT NOT NULL UNIQUE,
    event_type    TEXT NOT NULL,
    occurred_at   TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    step_id       TEXT,
    execution_id  TEXT,
    cap           TEXT,
    actor         TEXT NOT NULL,
    payload_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_step    ON events(step_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type    ON events(event_type);

-- projection: current session state (a convenient read, rebuildable from events)
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    workflow    TEXT,
    status      TEXT,
    current_step TEXT,
    job_id      TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
-- projection: artifacts the workflow produced (the durable substance index)
CREATE TABLE IF NOT EXISTS artifacts (
    session_id  TEXT NOT NULL,
    step_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT,
    created_at  TEXT,
    PRIMARY KEY (session_id, step_id, name)
);
-- projection: open/answered questions (the gate's read model)
CREATE TABLE IF NOT EXISTS questions (
    question_id TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    step_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    blocking    INTEGER NOT NULL,
    status      TEXT NOT NULL,          -- open | answered | skipped
    answer      TEXT,
    created_at  TEXT,
    answered_at TEXT
);
-- projection: jobs -- the unit everything regroups by (history, cost, documents)
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    label       TEXT,
    status      TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
"""


class EventStore:
    """Append-only event log + projections, on one SQLite file.

    The store is the only writer of truth. Projections are updated in the same
    transaction as the append, so a reader never sees an event without its view.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- the one write path ---
    def append(self, event: Event) -> Event:
        """Append an event and update any projection it implies, atomically."""
        with self._conn:  # transaction
            self._conn.execute(
                "INSERT INTO events(event_id,event_type,occurred_at,session_id,"
                "step_id,execution_id,cap,actor,payload_json) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.event_type,
                    event.occurred_at,
                    event.session_id,
                    event.step_id,
                    event.execution_id,
                    event.cap,
                    event.actor,
                    json.dumps(event.payload, ensure_ascii=False),
                ),
            )
            self._project(event)
        return event

    # --- projections (rebuildable; kept in step with the log) ---
    def _project(self, e: Event) -> None:
        c = self._conn
        if e.event_type == WORKFLOW_STARTED:
            c.execute(
                "INSERT INTO sessions(session_id,workflow,status,current_step,job_id,"
                "created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "workflow=excluded.workflow,status=excluded.status,job_id=excluded.job_id,"
                "updated_at=excluded.updated_at",
                (
                    e.session_id,
                    e.payload.get("workflow"),
                    "running",
                    None,
                    e.payload.get("job_id"),
                    e.occurred_at,
                    e.occurred_at,
                ),
            )
        elif e.event_type in (WORKFLOW_COMPLETED, WORKFLOW_FAILED):
            status = "completed" if e.event_type == WORKFLOW_COMPLETED else "failed"
            c.execute(
                "UPDATE sessions SET status=?,updated_at=? WHERE session_id=?",
                (status, e.occurred_at, e.session_id),
            )
        elif e.event_type == STEP_STARTED:
            c.execute(
                "UPDATE sessions SET current_step=?,updated_at=? WHERE session_id=?",
                (e.step_id, e.occurred_at, e.session_id),
            )
        elif e.event_type == ARTIFACT_CREATED:
            c.execute(
                "INSERT INTO artifacts(session_id,step_id,name,path,sha256,created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,step_id,name) DO UPDATE SET "
                "path=excluded.path,sha256=excluded.sha256,created_at=excluded.created_at",
                (
                    e.session_id,
                    e.step_id,
                    e.payload.get("name"),
                    e.payload.get("path"),
                    e.payload.get("sha256"),
                    e.occurred_at,
                ),
            )
        elif e.event_type == QUESTIONS_ASKED:
            for q in e.payload.get("questions", []):
                c.execute(
                    "INSERT OR REPLACE INTO questions("
                    "question_id,session_id,step_id,text,blocking,status,answer,"
                    "created_at,answered_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        q["id"],
                        e.session_id,
                        e.step_id,
                        q["text"],
                        int(bool(q.get("blocking", True))),
                        "open",
                        None,
                        e.occurred_at,
                        None,
                    ),
                )
        elif e.event_type == USER_ANSWER_SUBMITTED:
            status = e.payload.get("status", "answered")  # answered | skipped
            c.execute(
                "UPDATE questions SET status=?,answer=?,answered_at=? WHERE question_id=?",
                (status, e.payload.get("answer"), e.occurred_at, e.payload.get("question_id")),
            )
        elif e.event_type == JOB_OPENED:
            c.execute(
                "INSERT INTO jobs(job_id,label,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "label=COALESCE(excluded.label, jobs.label),updated_at=excluded.updated_at",
                (
                    e.payload.get("job_id") or e.session_id,
                    e.payload.get("label"),
                    "open",
                    e.occurred_at,
                    e.occurred_at,
                ),
            )

    # --- reads ---
    def replay(self, session_id: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT event_type,occurred_at,session_id,step_id,execution_id,cap,actor,"
            "payload_json,event_id FROM events WHERE session_id=? ORDER BY seq",
            (session_id,),
        )
        return [
            Event(
                event_type=r[0],
                occurred_at=r[1],
                session_id=r[2],
                step_id=r[3],
                execution_id=r[4],
                cap=r[5],
                actor=r[6],
                payload=json.loads(r[7]),
                event_id=r[8],
            )
            for r in rows
        ]

    def open_blocking_questions(self, session_id: str, step_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT question_id,text FROM questions WHERE session_id=? AND step_id=? "
            "AND status='open' AND blocking=1",
            (session_id, step_id),
        )
        return [{"id": r[0], "text": r[1]} for r in rows]

    def session_row(self, session_id: str) -> dict | None:
        r = self._conn.execute(
            "SELECT session_id,workflow,status,current_step,job_id FROM sessions "
            "WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not r:
            return None
        return {
            "session_id": r[0],
            "workflow": r[1],
            "status": r[2],
            "current_step": r[3],
            "job_id": r[4],
        }

    def artifacts(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT step_id,name,path,sha256 FROM artifacts WHERE session_id=? ORDER BY rowid",
            (session_id,),
        )
        return [{"step_id": r[0], "name": r[1], "path": r[2], "sha256": r[3]} for r in rows]

    def executions_for_job(self, job_id: str) -> list[dict]:
        """Every workflow execution bound to a job, oldest first -- the job's run
        history (the new-vs-continue read)."""
        rows = self._conn.execute(
            "SELECT session_id,workflow,status,created_at FROM sessions "
            "WHERE job_id=? ORDER BY created_at",
            (job_id,),
        )
        return [
            {"session_id": r[0], "workflow": r[1], "status": r[2], "created_at": r[3]} for r in rows
        ]

    # --- jobs (the organizing unit) ---
    def open_job(self, job_id: str, label: str | None = None, actor: str = "user") -> dict:
        """Create the job if new (append JOB_OPENED); else just return it. Selecting an
        existing job records nothing -- existence + label is the state, set once."""
        if self.job_row(job_id) is None:
            self.append(
                Event(
                    event_type=JOB_OPENED,
                    session_id=job_id,
                    actor=actor,
                    payload={"job_id": job_id, "label": label},
                )
            )
        return self.job_row(job_id)

    def jobs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT job_id,label,status,created_at FROM jobs ORDER BY created_at, job_id"
        )
        return [{"job_id": r[0], "label": r[1], "status": r[2], "created_at": r[3]} for r in rows]

    def job_row(self, job_id: str) -> dict | None:
        r = self._conn.execute(
            "SELECT job_id,label,status,created_at FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return {"job_id": r[0], "label": r[1], "status": r[2], "created_at": r[3]} if r else None
