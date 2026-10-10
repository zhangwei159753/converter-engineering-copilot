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
from converter_copilot.storage.migrations import CURRENT_SCHEMA_VERSION, migrate


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteStore:
    """Small synchronous SQLite repository used by the local single-user app."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        initialize: bool = True,
        backup_directory: str | Path | None = None,
    ):
        """Open the local database and optionally apply pending migrations."""
        self.database_path = Path(database_path)
        self.backup_directory = Path(backup_directory) if backup_directory else self.database_path.parent / "backups"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if initialize:
            self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> int:
        """Back up an existing schema and apply pending migrations."""
        existed = self.database_path.is_file() and self.database_path.stat().st_size > 0
        with self._connect() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_user_tables = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone() is not None
            if existed and has_user_tables and current_version < CURRENT_SCHEMA_VERSION:
                self._backup_before_migration()
            connection.execute("PRAGMA journal_mode = WAL")
            return migrate(connection)

    def _backup_before_migration(self) -> Path:
        """Create a consistent SQLite backup before changing an existing schema."""
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.backup_directory / f"{self.database_path.stem}-before-v{CURRENT_SCHEMA_VERSION}-{stamp}.db"
        with self._connect() as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)
        return backup_path

    def create_task(self, request: TaskCreate) -> TaskRecord:
        """Persist a task with an initial queued status."""
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
                """INSERT INTO tasks(task_id, mode, status, inputs_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
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
        """Return one persisted task, if it exists."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[TaskRecord]:
        """List the newest tasks up to the requested limit."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, task_id LIMIT ?", (limit,)
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def update_task_status(self, task_id: str, status: str) -> TaskRecord | None:
        """Update lifecycle status and its timestamp atomically."""
        updated_at = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, updated_at.isoformat(), task_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_task(task_id)

    def record_retry(self, original_task_id: str, retry_task_id: str) -> None:
        """Record a retry relationship without modifying the previous attempt."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO task_retries(retry_task_id, original_task_id, created_at) VALUES (?, ?, ?)",
                (retry_task_id, original_task_id, _now().isoformat()),
            )

    def retry_count(self, original_task_id: str) -> int:
        """Return how many retry attempts have been created for a task."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM task_retries WHERE original_task_id = ?",
                (original_task_id,),
            ).fetchone()
        return int(row["count"])

    def append_event(self, task_id: str, *, stage: str, status: str, message: str) -> TaskEvent:
        """Append one progress event and return its monotonic event ID."""
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
        """Return events for a task whose IDs follow the supplied cursor."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? AND event_id > ? ORDER BY event_id",
                (task_id, after_id),
            ).fetchall()
        return [TaskEvent.model_validate(dict(row)) for row in rows]

    def save_report(self, report: ConverterReport) -> None:
        """Insert or replace the report associated with one task."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO reports(task_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET payload_json = excluded.payload_json",
                (report.task_id, report.model_dump_json()),
            )

    def get_report(self, task_id: str) -> ConverterReport | None:
        """Return the typed report for a task, if it is available."""
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM reports WHERE task_id = ?", (task_id,)).fetchone()
        return ConverterReport.model_validate_json(row["payload_json"]) if row else None

    def save_case(self, case: CaseRecord) -> None:
        """Insert or replace one curated maintenance case."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO cases(case_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET payload_json = excluded.payload_json",
                (case.case_id, case.model_dump_json()),
            )

    def list_cases(self) -> list[CaseRecord]:
        """Return all curated cases in stable ID order."""
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
