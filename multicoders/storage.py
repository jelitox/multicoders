"""SQLite storage for the Multicoders Tracker.

Three tables: tasks, artifacts, verdicts. No ORM, no migrations engine.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

TASK_STATUSES = {
    "pending",
    "approved",
    "running",
    "done",
    "failed",
    "rejected",
    "paused_quota",
}


@dataclass(slots=True)
class TaskRecord:
    id: int
    repo_path: str
    task_type: str
    task_text: str
    status: str
    lead_provider: Optional[str]
    created_at: str
    updated_at: Optional[str]
    approved_at: Optional[str]
    run_id: Optional[str]
    result_json: Optional[str]
    requester: Optional[str]
    request_update_id: Optional[int]


class Storage:
    def __init__(self, db_path: str = "multicoders.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._get_connection() as conn:
            conn.executescript(schema)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)")}
            if "workdir" not in cols:
                conn.execute("ALTER TABLE artifacts ADD COLUMN workdir TEXT")
            conn.commit()

    def create_task(self, task_id: str, payload: Dict[str, Any]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks (id, status, payload) VALUES (?, ?, ?)",
                (task_id, "pending", json.dumps(payload)),
            )
            conn.commit()

    def update_task_status(self, task_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id),
            )
            conn.commit()

    def add_artifact(
        self,
        task_id: str,
        author: str,
        content: str,
        passed_filter: bool = False,
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO artifacts (task_id, author, content, passed_filter)"
                " VALUES (?, ?, ?, ?)",
                (task_id, author, content, 1 if passed_filter else 0),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_artifact_workdir(self, artifact_id: int, workdir: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE artifacts SET workdir = ? WHERE id = ?",
                (workdir, artifact_id),
            )
            conn.commit()

    def update_artifact_filter_status(self, artifact_id: int, passed: bool) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE artifacts SET passed_filter = ? WHERE id = ?",
                (1 if passed else 0, artifact_id),
            )
            conn.commit()

    def add_verdict(
        self,
        task_id: str,
        judge: str,
        artifact_id: int,
        vote: str,
        reasoning: str,
    ) -> None:
        if vote not in {"approve", "reject"}:
            raise ValueError(f"invalid vote: {vote!r}")
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO verdicts (task_id, judge, artifact_id, vote, reasoning)"
                " VALUES (?, ?, ?, ?, ?)",
                (task_id, judge, artifact_id, vote, reasoning),
            )
            conn.commit()

    def get_task_summary(self, task_id: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            task = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            artifacts = conn.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
            verdicts = conn.execute(
                "SELECT * FROM verdicts WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
            return {
                "task": dict(task) if task else None,
                "artifacts": [dict(a) for a in artifacts],
                "verdicts": [dict(v) for v in verdicts],
            }

    def get_artifact(self, artifact_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_checkpoint(
        self,
        task_id: str,
        node: str,
        state: Dict[str, Any],
        attempt: int = 0,
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO checkpoints (task_id, node, attempt, state)"
                " VALUES (?, ?, ?, ?)",
                (task_id, node, attempt, json.dumps(state)),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def get_latest_checkpoint(
        self, task_id: str, node: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if node is None:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE task_id = ?"
                    " ORDER BY id DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE task_id = ? AND node = ?"
                    " ORDER BY id DESC LIMIT 1",
                    (task_id, node),
                ).fetchone()
            if not row:
                return None
            data = dict(row)
            data["state"] = json.loads(data["state"])
            return data

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("payload"):
                data["payload"] = json.loads(data["payload"])
            return data


def connect_db(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_path TEXT NOT NULL,
            task_type TEXT NOT NULL,
            task_text TEXT NOT NULL,
            status TEXT NOT NULL,
            lead_provider TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            approved_at TEXT,
            run_id TEXT,
            result_json TEXT,
            requester TEXT,
            request_update_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_tasks_status_id "
        "ON service_tasks(status, id)"
    )
    conn.commit()


def _task_from_row(row: sqlite3.Row | None) -> Optional[TaskRecord]:
    if row is None:
        return None
    return TaskRecord(
        id=int(row["id"]),
        repo_path=str(row["repo_path"]),
        task_type=str(row["task_type"]),
        task_text=str(row["task_text"]),
        status=str(row["status"]),
        lead_provider=row["lead_provider"],
        created_at=str(row["created_at"]),
        updated_at=row["updated_at"],
        approved_at=row["approved_at"],
        run_id=row["run_id"],
        result_json=row["result_json"],
        requester=row["requester"],
        request_update_id=row["request_update_id"],
    )


def create_task(
    conn: sqlite3.Connection,
    *,
    repo_path: str,
    task_type: str,
    task_text: str,
    lead_provider: Optional[str],
    created_at: str,
    requester: Optional[str] = None,
    request_update_id: Optional[int] = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO service_tasks (
            repo_path, task_type, task_text, status, lead_provider,
            created_at, requester, request_update_id
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            repo_path,
            task_type,
            task_text,
            lead_provider,
            created_at,
            requester,
            request_update_id,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_task(conn: sqlite3.Connection, task_id: int) -> Optional[TaskRecord]:
    row = conn.execute(
        "SELECT * FROM service_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return _task_from_row(row)


def list_recent_tasks(
    conn: sqlite3.Connection, *, limit: int = 10
) -> list[TaskRecord]:
    rows = conn.execute(
        "SELECT * FROM service_tasks ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [task for row in rows if (task := _task_from_row(row)) is not None]


def list_paused_quota_tasks(conn: sqlite3.Connection) -> list[TaskRecord]:
    rows = conn.execute(
        "SELECT * FROM service_tasks WHERE status = 'paused_quota' ORDER BY id"
    ).fetchall()
    return [task for row in rows if (task := _task_from_row(row)) is not None]


def update_task_status(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    status: str,
    updated_at: str,
    approved_at: Optional[str] = None,
    run_id: Optional[str] = None,
    result_json: Optional[str] = None,
    lead_provider: Optional[str] = None,
) -> None:
    if status not in TASK_STATUSES:
        raise ValueError(f"unknown task status: {status}")

    assignments = ["status = ?", "updated_at = ?"]
    values: list[object] = [status, updated_at]
    optional_fields = {
        "approved_at": approved_at,
        "run_id": run_id,
        "result_json": result_json,
        "lead_provider": lead_provider,
    }
    for field, value in optional_fields.items():
        if value is not None:
            assignments.append(f"{field} = ?")
            values.append(value)
    values.append(task_id)
    conn.execute(
        f"UPDATE service_tasks SET {', '.join(assignments)} WHERE id = ?",
        values,
    )
    conn.commit()


def retry_task(
    conn: sqlite3.Connection, *, task_id: int, updated_at: str
) -> bool:
    cursor = conn.execute(
        """
        UPDATE service_tasks
        SET status = 'pending',
            updated_at = ?,
            approved_at = NULL,
            run_id = NULL,
            result_json = NULL
        WHERE id = ? AND status IN ('failed', 'rejected')
        """,
        (updated_at, task_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def resume_paused_task(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    updated_at: str,
    lead_provider: Optional[str] = None,
) -> bool:
    assignments = ["status = 'approved'", "updated_at = ?"]
    values: list[object] = [updated_at]
    if lead_provider is not None:
        assignments.append("lead_provider = ?")
        values.append(lead_provider)
    values.append(task_id)
    cursor = conn.execute(
        f"""
        UPDATE service_tasks
        SET {', '.join(assignments)}
        WHERE id = ? AND status = 'paused_quota'
        """,
        values,
    )
    conn.commit()
    return cursor.rowcount > 0


def claim_next_approved_task(
    conn: sqlite3.Connection, *, updated_at: str
) -> Optional[TaskRecord]:
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT * FROM service_tasks WHERE status = 'approved' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        conn.commit()
        return None
    task_id = int(row["id"])
    conn.execute(
        "UPDATE service_tasks SET status = 'running', updated_at = ? WHERE id = ?",
        (updated_at, task_id),
    )
    conn.commit()
    return get_task(conn, task_id)
