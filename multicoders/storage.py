"""SQLite storage for the Multicoders Tracker.

Three tables: tasks, artifacts, verdicts. No ORM, no migrations engine.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


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
