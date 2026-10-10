"""SQLite-backed task, event, report, and case metadata store."""

import hashlib
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


class IdempotencyConflictError(ValueError):
    """Raised when a client reuses a key for a different request body."""


class RetryLimitError(ValueError):
    """Raised when a task has exhausted its bounded retry budget."""


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

    def create_task(
        self, request: TaskCreate, *, idempotency_key: str | None = None
    ) -> tuple[TaskRecord, bool]:
        """Persist a queued task and event, reusing a matching request key."""
        request_hash = self._request_hash(request)
        self._validate_idempotency_key(idempotency_key)
        now = _now()
        record = self._new_task_record(request, now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflictError("Idempotency-Key was already used with a different request.")
                    return self._task_from_row(existing), True
            connection.execute(
                """INSERT INTO tasks(
                    task_id, mode, status, inputs_json, created_at, updated_at, idempotency_key, request_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.task_id,
                    record.mode.value,
                    record.status,
                    json.dumps(record.inputs, ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    idempotency_key,
                    request_hash,
                ),
            )
            self._insert_event(connection, record.task_id, "queued", "queued", "任务已排队。", now)
        return record, False

    def create_retry(
        self,
        original_task_id: str,
        request: TaskCreate,
        *,
        idempotency_key: str | None = None,
        max_retries: int = 2,
    ) -> tuple[TaskRecord, bool]:
        """Atomically create a bounded retry, its relationship, and events."""
        request_hash = self._request_hash(request)
        self._validate_idempotency_key(idempotency_key)
        now = _now()
        record = self._new_task_record(request, now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = connection.execute(
                    """SELECT t.* FROM task_retries r JOIN tasks t ON t.task_id = r.retry_task_id
                    WHERE r.idempotency_key = ?""",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflictError("Idempotency-Key was already used with a different request.")
                    return self._task_from_row(existing), True
            original = connection.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (original_task_id,)
            ).fetchone()
            if original is None:
                raise KeyError(original_task_id)
            if original["status"] not in {"failed", "partial"}:
                raise ValueError("Only failed or partial tasks can be retried.")
            retries = int(connection.execute(
                "SELECT COUNT(*) FROM task_retries WHERE original_task_id = ?", (original_task_id,)
            ).fetchone()[0])
            if retries >= max_retries:
                raise RetryLimitError(f"Task {original_task_id} has reached the retry limit.")
            connection.execute(
                """INSERT INTO tasks(
                    task_id, mode, status, inputs_json, created_at, updated_at, idempotency_key, request_hash
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    record.task_id, record.mode.value, record.status,
                    json.dumps(record.inputs, ensure_ascii=False), record.created_at.isoformat(),
                    record.updated_at.isoformat(), request_hash,
                ),
            )
            connection.execute(
                """INSERT INTO task_retries(retry_task_id, original_task_id, created_at, idempotency_key)
                VALUES (?, ?, ?, ?)""",
                (record.task_id, original_task_id, now.isoformat(), idempotency_key),
            )
            self._insert_event(connection, record.task_id, "queued", "queued", "任务已排队。", now)
            self._insert_event(
                connection, record.task_id, "retry", "queued", f"重试任务，来源任务 {original_task_id}。", now
            )
        return record, False

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

    def update_task_status(
        self, task_id: str, status: str, *, worker_id: str | None = None
    ) -> TaskRecord | None:
        """Update lifecycle status and its timestamp atomically."""
        updated_at = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE tasks SET status = ?, updated_at = ?,
                lease_owner = CASE WHEN ? = 'running' THEN ? WHEN ? IN ('completed', 'partial', 'failed') THEN NULL ELSE lease_owner END,
                lease_expires_at = CASE WHEN ? IN ('completed', 'partial', 'failed') THEN NULL ELSE lease_expires_at END
                WHERE task_id = ?""",
                (status, updated_at.isoformat(), status, worker_id, status, status, task_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_task(task_id)

    def finish_task(self, task_id: str, report: ConverterReport, *, worker_id: str) -> bool:
        """Atomically save a report and terminal event while the worker owns the task."""
        if report.task_id != task_id:
            raise ValueError("Report task_id does not match the task being completed.")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_owner FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None or row["lease_owner"] != worker_id:
                return False
            connection.execute(
                """INSERT INTO reports(task_id, payload_json) VALUES (?, ?)
                ON CONFLICT(task_id) DO UPDATE SET payload_json = excluded.payload_json""",
                (task_id, report.model_dump_json()),
            )
            connection.execute(
                """UPDATE tasks SET status = ?, updated_at = ?, lease_owner = NULL, lease_expires_at = NULL
                WHERE task_id = ? AND lease_owner = ?""",
                (report.completion_status, now.isoformat(), task_id, worker_id),
            )
            message = "报告已生成。" if report.completion_status == "completed" else "已生成部分报告，请检查提示和证据。"
            self._insert_event(connection, task_id, "complete", report.completion_status, message, now)
        return True

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

    @staticmethod
    def _new_task_record(request: TaskCreate, now: datetime) -> TaskRecord:
        return TaskRecord(
            task_id=str(uuid4()), mode=request.mode, status="queued",
            inputs=request.inputs.model_dump(mode="json"), created_at=now, updated_at=now,
        )

    @staticmethod
    def _request_hash(request: TaskCreate) -> str:
        canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_idempotency_key(key: str | None) -> None:
        if key is not None and (not key.strip() or len(key) > 255):
            raise ValueError("Idempotency-Key must contain 1 to 255 characters.")

    @staticmethod
    def _insert_event(connection, task_id: str, stage: str, status: str, message: str, created_at: datetime) -> None:
        connection.execute(
            "INSERT INTO task_events(task_id, stage, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, stage, status, message, created_at.isoformat()),
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
