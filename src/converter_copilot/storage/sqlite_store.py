"""SQLite-backed task, event, report, and case metadata store."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from converter_copilot.domain.schemas import (
    CaseRecord,
    ConverterReport,
    TaskCreate,
    TaskEvent,
    TaskMode,
    TaskRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteStore:
    """Small synchronous SQLite repository used by the local single-user app."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task_event
                    ON task_events(task_id, event_id);
                CREATE TABLE IF NOT EXISTS reports (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def create_task(self, request: TaskCreate) -> TaskRecord:
        now = _now()
        record = TaskRecord(
            task_id=str(uuid4()),
            mode=request.mode,
            status="queued",
            inputs=request.inputs.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.task_id,
                    record.mode.value,
                    record.status,
                    json.dumps(record.inputs, ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[TaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, task_id LIMIT ?", (limit,)
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def append_event(self, task_id: str, *, stage: str, status: str, message: str) -> TaskEvent:
        created_at = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO task_events(task_id, stage, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, stage, status, message, created_at.isoformat()),
            )
            event_id = cursor.lastrowid
        return TaskEvent(
            event_id=event_id,
            task_id=task_id,
            stage=stage,
            status=status,
            message=message,
            created_at=created_at,
        )

    def events_after(self, task_id: str, after_id: int = 0) -> list[TaskEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? AND event_id > ? ORDER BY event_id",
                (task_id, after_id),
            ).fetchall()
        return [TaskEvent.model_validate(dict(row)) for row in rows]

    def save_report(self, report: ConverterReport) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO reports(task_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET payload_json = excluded.payload_json",
                (report.task_id, report.model_dump_json()),
            )

    def get_report(self, task_id: str) -> ConverterReport | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM reports WHERE task_id = ?", (task_id,)).fetchone()
        return ConverterReport.model_validate_json(row["payload_json"]) if row else None

    def save_case(self, case: CaseRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO cases(case_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET payload_json = excluded.payload_json",
                (case.case_id, case.model_dump_json()),
            )

    def list_cases(self) -> list[CaseRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM cases ORDER BY case_id").fetchall()
        return [CaseRecord.model_validate_json(row["payload_json"]) for row in rows]

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            mode=TaskMode(row["mode"]),
            status=row["status"],
            inputs=json.loads(row["inputs_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
