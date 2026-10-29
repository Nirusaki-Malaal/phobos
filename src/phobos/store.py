"""Transactional local state. Connections are short-lived and safe across processes."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from phobos.models import TicketCreate

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
 prompt TEXT NOT NULL, workflow TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', result TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
 claim TEXT, lease REAL, origin_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS queue ON tickets(status, id);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
 kind TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, content TEXT NOT NULL,
 source TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_project ON memories(project, id);
CREATE TABLE IF NOT EXISTS feedback (
 id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
 rating INTEGER NOT NULL CHECK(rating IN (-1,1)), comment TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 id INTEGER PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, body TEXT NOT NULL,
 recipient TEXT NOT NULL DEFAULT '',
 sent REAL, attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
 claim TEXT, lease REAL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS procedures (
 id INTEGER PRIMARY KEY, project TEXT NOT NULL, name TEXT NOT NULL,
 version INTEGER NOT NULL, instructions TEXT NOT NULL,
 source_ticket INTEGER NOT NULL REFERENCES tickets(id),
 status TEXT NOT NULL DEFAULT 'draft', created REAL NOT NULL,
 UNIQUE(project,name,version)
);
CREATE TABLE IF NOT EXISTS procedure_evaluations (
 id INTEGER PRIMARY KEY, procedure_id INTEGER NOT NULL REFERENCES procedures(id),
 ticket_id INTEGER NOT NULL REFERENCES tickets(id), passed INTEGER NOT NULL,
 notes TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_usage (
 ticket_id INTEGER NOT NULL REFERENCES tickets(id), name TEXT NOT NULL,
 digest TEXT NOT NULL, origin TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(ticket_id,name,digest)
);
PRAGMA user_version=3;
"""


class Conflict(ValueError):
    """The requested ticket transition is no longer valid."""


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = data_dir / "phobos.db"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("database is newer than this Phobos version")
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _event(self, db, ticket_id: int, kind: str, detail: str = "") -> None:
        db.execute(
            "INSERT INTO events(ticket_id,kind,detail,created) VALUES(?,?,?,?)",
            (ticket_id, kind, detail, time.time()),
        )
        event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        body = f"Phobos #{ticket_id} · {kind}\n{ticket['project']}: {ticket['title']}"
        if detail:
            body += "\n" + detail[:1000]
        db.execute("INSERT INTO outbox(dedupe,body) VALUES(?,?)", (f"event:{event_id}", body))

    def create(self, ticket: TicketCreate, origin_key: str | None = None) -> dict:
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if origin_key:
                existing = db.execute(
                    "SELECT * FROM tickets WHERE origin_key=?", (origin_key,)
                ).fetchone()
                if existing:
                    return dict(existing)
            cursor = db.execute(
                "INSERT INTO tickets(project,title,prompt,workflow,created,updated,origin_key) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    ticket.project,
                    ticket.title,
                    ticket.prompt,
                    ticket.workflow,
                    now,
                    now,
                    origin_key,
                ),
            )
            self._event(db, cursor.lastrowid, "queued")
            return dict(
                db.execute("SELECT * FROM tickets WHERE id=?", (cursor.lastrowid,)).fetchone()
            )

    def get(self, ticket_id: int) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                raise KeyError(ticket_id)
            return dict(row)

    def list(self, project: str | None = None, limit: int = 50) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM tickets WHERE (? IS NULL OR project=?) ORDER BY id DESC LIMIT ?",
                (project, project, min(max(limit, 1), 100)),
            ).fetchall()
            return [dict(row) for row in rows]

    def events(self, ticket_id: int) -> list[dict]:
        self.get(ticket_id)
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events WHERE ticket_id=? ORDER BY id", (ticket_id,)
                )
            ]

    def claim(
        self, lease_seconds: float = 120, workflows: tuple[str, ...] | None = None
    ) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            expired = db.execute(
                "SELECT id FROM tickets WHERE status='running' AND lease<?", (now,)
            ).fetchall()
            for row in expired:
                db.execute(
                    "UPDATE tickets SET status='failed',error=?,updated=?,claim=NULL,lease=NULL "
                    "WHERE id=?",
                    ("Worker interrupted; review side effects before retry", now, row[0]),
                )
                self._event(db, row[0], "failed", "Worker lease expired; automatic replay disabled")
            if workflows is not None and not workflows:
                return None
            filter_sql = (
                " AND workflow IN (" + ",".join("?" for _ in workflows) + ")"
                if workflows is not None
                else ""
            )
            row = db.execute(
                "SELECT id FROM tickets WHERE status='queued'"
                + filter_sql
                + " ORDER BY id LIMIT 1",
                workflows or (),
            ).fetchone()
            if row is None:
                return None
            claim = uuid.uuid4().hex
            db.execute(
                "UPDATE tickets SET status='running',claim=?,lease=?,updated=? WHERE id=?",
                (claim, now + lease_seconds, now, row[0]),
            )
            self._event(db, row[0], "running")
            return dict(db.execute("SELECT * FROM tickets WHERE id=?", (row[0],)).fetchone())

    def heartbeat(self, ticket_id: int, claim: str) -> bool:
        with self.connection() as db:
            return (
                db.execute(
                    "UPDATE tickets SET lease=? WHERE id=? AND claim=? "
                    "AND status='running' AND lease>=?",
                    (time.time() + 120, ticket_id, claim, time.time()),
                ).rowcount
                == 1
            )

    def finish(self, ticket_id: int, claim: str, result: str = "", error: str = "") -> bool:
        status = "failed" if error else "completed"
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE tickets SET status=?,result=?,error=?,updated=?,claim=NULL,lease=NULL "
                "WHERE id=? AND claim=? AND status='running' AND lease>=?",
                (status, result, error, time.time(), ticket_id, claim, time.time()),
            ).rowcount
            if changed:
                self._event(db, ticket_id, status, error or "Output delivered; see ticket result")
            return bool(changed)

    def transition(self, ticket_id: int, action: str) -> dict:
        transitions = {
            "pause": ({"queued"}, "paused"),
            "resume": ({"paused"}, "queued"),
            "cancel": ({"queued", "paused", "running"}, "cancelled"),
            "retry": ({"failed", "cancelled"}, "queued"),
        }
        if action not in transitions:
            raise ValueError("unknown action")
        allowed, target = transitions[action]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                raise KeyError(ticket_id)
            if row[0] not in allowed:
                raise Conflict(f"cannot {action} a {row[0]} ticket")
            db.execute(
                "UPDATE tickets SET status=?,updated=?,claim=NULL,lease=NULL,error='' WHERE id=?",
                (target, time.time(), ticket_id),
            )
            self._event(db, ticket_id, target)
        return self.get(ticket_id)

    def remember(self, project: str, content: str, source: str = "owner") -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if source.startswith("telegram:"):
                row = db.execute(
                    "SELECT * FROM memories WHERE project=? AND source=?", (project, source)
                ).fetchone()
                if row:
                    return dict(row)
            cursor = db.execute(
                "INSERT INTO memories(project,content,source,created) VALUES(?,?,?,?)",
                (project, content, source, time.time()),
            )
            return dict(
                db.execute("SELECT * FROM memories WHERE id=?", (cursor.lastrowid,)).fetchone()
            )

    def memories(self, project: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM memories WHERE project=? ORDER BY id DESC LIMIT 100", (project,)
                )
            ]

    def forget(self, project: str, memory_id: int) -> bool:
        with self.connection() as db:
            removed = bool(
                db.execute(
                    "DELETE FROM memories WHERE id=? AND project=?", (memory_id, project)
                ).rowcount
            )
        if removed:
            (self.path.parent / "graphs" / project / "graph.json").unlink(missing_ok=True)
        return removed

    def feedback(self, ticket_id: int, rating: int, comment: str) -> None:
        self.get(ticket_id)
        with self.connection() as db:
            db.execute(
                "INSERT INTO feedback(ticket_id,rating,comment,created) VALUES(?,?,?,?)",
                (ticket_id, rating, comment, time.time()),
            )

    def record_skill(self, ticket_id: int, name: str, digest: str, origin: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO skill_usage VALUES(?,?,?,?,?)",
                (ticket_id, name, digest, origin, time.time()),
            )

    def skill_scores(self, project: str) -> dict:
        with self.connection() as db:
            rows = db.execute(
                "SELECT u.name, count(DISTINCT t.id) AS uses, "
                "sum(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS completed, "
                "sum(coalesce((SELECT rating FROM feedback f WHERE f.ticket_id=t.id "
                "ORDER BY f.id DESC LIMIT 1),0)) AS feedback "
                "FROM skill_usage u JOIN tickets t ON t.id=u.ticket_id "
                "WHERE t.project=? GROUP BY u.name",
                (project,),
            )
            return {row["name"]: dict(row) for row in rows}

    def report(self, since: float | None = None) -> dict:
        since = time.time() - 86400 if since is None else since
        with self.connection() as db:
            totals = {
                row[0]: row[1]
                for row in db.execute("SELECT status,count(*) FROM tickets GROUP BY status")
            }
            completed = db.execute(
                "SELECT count(*) FROM tickets WHERE status='completed' AND updated>=?", (since,)
            ).fetchone()[0]
            failed = db.execute(
                "SELECT count(*) FROM tickets WHERE status='failed' AND updated>=?", (since,)
            ).fetchone()[0]
        return {"since": since, "completed": completed, "failed": failed, "current": totals}

    def procedures(self, project: str, active_only: bool = False) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM procedures WHERE project=? AND (?=0 OR status='active') "
                    "ORDER BY id DESC LIMIT 100",
                    (project, active_only),
                )
            ]

    def procedure(self, project: str, procedure_id: int) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM procedures WHERE project=? AND id=?", (project, procedure_id)
            ).fetchone()
            if row is None:
                raise KeyError(procedure_id)
            result = dict(row)
            result["evaluations"] = [
                dict(item)
                for item in db.execute(
                    "SELECT * FROM procedure_evaluations WHERE procedure_id=? ORDER BY id",
                    (procedure_id,),
                )
            ]
            return result

    def create_procedure(
        self, project: str, name: str, instructions: str, source_ticket: int
    ) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            ticket = db.execute("SELECT * FROM tickets WHERE id=?", (source_ticket,)).fetchone()
            if ticket is None or ticket["project"] != project or ticket["status"] != "completed":
                raise Conflict("Source must be a completed ticket in this project")
            version = db.execute(
                "SELECT coalesce(max(version),0)+1 FROM procedures WHERE project=? AND name=?",
                (project, name),
            ).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO procedures(project,name,version,instructions,source_ticket,created) "
                "VALUES(?,?,?,?,?,?)",
                (project, name, version, instructions, source_ticket, time.time()),
            )
            procedure_id = cursor.lastrowid
        return self.procedure(project, procedure_id)

    def evaluate_procedure(
        self, project: str, procedure_id: int, ticket_id: int, passed: bool, notes: str
    ) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            procedure = db.execute(
                "SELECT * FROM procedures WHERE id=? AND project=?", (procedure_id, project)
            ).fetchone()
            if procedure is None:
                raise KeyError(procedure_id)
            ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if (
                ticket is None
                or ticket["project"] != project
                or ticket["status"] != "completed"
                or ticket_id == procedure["source_ticket"]
            ):
                raise Conflict("Evaluation needs a different completed ticket in this project")
            db.execute(
                "INSERT INTO procedure_evaluations(procedure_id,ticket_id,passed,notes,created) "
                "VALUES(?,?,?,?,?)",
                (procedure_id, ticket_id, passed, notes, time.time()),
            )
            if not passed:
                db.execute("UPDATE procedures SET status='draft' WHERE id=?", (procedure_id,))
        return self.procedure(project, procedure_id)

    def transition_procedure(self, project: str, procedure_id: int, action: str) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM procedures WHERE id=? AND project=?", (procedure_id, project)
            ).fetchone()
            if row is None:
                raise KeyError(procedure_id)
            if action == "activate":
                evaluation = db.execute(
                    "SELECT passed FROM procedure_evaluations WHERE procedure_id=? "
                    "ORDER BY id DESC LIMIT 1",
                    (procedure_id,),
                ).fetchone()
                if evaluation is None or not evaluation[0]:
                    raise Conflict("Latest owner evaluation must pass before activation")
                db.execute(
                    "UPDATE procedures SET status='retired' "
                    "WHERE project=? AND name=? AND status='active'",
                    (project, row["name"]),
                )
                status = "active"
            elif action == "retire":
                status = "retired"
            else:
                raise ValueError("Unknown procedure action")
            db.execute("UPDATE procedures SET status=? WHERE id=?", (status, procedure_id))
        return self.procedure(project, procedure_id)

    def state(self, key: str, value: str | None = None) -> str | None:
        with self.connection() as db:
            if value is not None:
                db.execute(
                    "INSERT INTO state VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
                return value
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def enqueue_message(self, dedupe: str, body: str, recipient: str = "") -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO outbox(dedupe,body,recipient) VALUES(?,?,?)",
                (dedupe, body, recipient),
            )

    def claim_message(self, include_channel: bool = True) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            row = db.execute(
                "SELECT * FROM outbox WHERE sent IS NULL AND due<=? "
                "AND (lease IS NULL OR lease<?) AND (? OR recipient!='') ORDER BY id LIMIT 1",
                (now, now, include_channel),
            ).fetchone()
            if row is None:
                return None
            claim = uuid.uuid4().hex
            db.execute(
                "UPDATE outbox SET claim=?,lease=? WHERE id=?", (claim, now + 120, row["id"])
            )
            return {**dict(row), "claim": claim}

    def ack_message(self, message: dict, success: bool) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE outbox SET sent=?,attempts=attempts+1,due=?,lease=NULL,claim=NULL "
                "WHERE id=? AND claim=?",
                (
                    time.time() if success else None,
                    time.time() + min(3600, 2 ** min(message["attempts"] + 1, 12)),
                    message["id"],
                    message["claim"],
                ),
            )

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db, sqlite3.connect(destination) as target:
            db.backup(target)


def public_ticket(ticket: dict) -> dict:
    return {
        key: value for key, value in ticket.items() if key not in {"claim", "lease", "origin_key"}
    }


def report_text(report: dict) -> str:
    return (
        f"Phobos · last 24 hours\nCompleted: {report['completed']}\n"
        f"Failed: {report['failed']}\n"
        f"Current tickets: {json.dumps(report['current'], sort_keys=True)}\n"
        "Counts represent delivered workflow outputs, not estimated hours saved."
    )
