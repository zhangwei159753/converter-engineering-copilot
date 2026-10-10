"""SQLite-backed task, event, report, and case metadata store."""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from converter_copilot.domain.schemas import (
    CaseRecord,
    ConverterReport,
    DocumentChunk,
    ImportRecord,
    ImportStatus,
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
                    """SELECT t.*, r.original_task_id AS retry_original_task_id
                    FROM task_retries r JOIN tasks t ON t.task_id = r.retry_task_id
                    WHERE r.idempotency_key = ?""",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["request_hash"] != request_hash
                        or existing["retry_original_task_id"] != original_task_id
                    ):
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

    def check_writable(self, *, timeout_seconds: float = 1.0) -> None:
        """Check bounded SQLite write-lock availability without changing data."""
        if timeout_seconds <= 0:
            raise ValueError("Readiness write-check timeout must be positive.")
        connection = sqlite3.connect(self.database_path, timeout=timeout_seconds)
        try:
            connection.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)}")
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        finally:
            connection.close()

    def create_import(
        self,
        import_id: str,
        *,
        content_sha256: str,
        source_name: str,
        file_type: str,
        parser_version: str,
        chunker_version: str,
        expected_chunk_count: int,
        source_path: str,
    ) -> tuple[ImportRecord, bool]:
        """Create or return the ledger row for the same content and parser versions."""
        if expected_chunk_count < 0:
            raise ValueError("Expected chunk count cannot be negative.")
        now = _now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM imports WHERE content_sha256 = ? AND file_type = ?
                AND parser_version = ? AND chunker_version = ?""",
                (content_sha256, file_type, parser_version, chunker_version),
            ).fetchone()
            if row is not None:
                if row["import_id"] != import_id:
                    raise ValueError("Import identity does not match its content and parser versions.")
                return self._import_from_row(row), True
            connection.execute(
                """INSERT INTO imports(
                    import_id, content_sha256, source_name, file_type, source_path,
                    parser_version, chunker_version, expected_chunk_count, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'validated', ?, ?)""",
                (
                    import_id, content_sha256, source_name, file_type, source_path,
                    parser_version, chunker_version, expected_chunk_count, now, now,
                ),
            )
            row = connection.execute("SELECT * FROM imports WHERE import_id = ?", (import_id,)).fetchone()
        return self._import_from_row(row), False

    def get_import(self, import_id: str) -> ImportRecord | None:
        """Return one private import-ledger record, if it exists."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM imports WHERE import_id = ?", (import_id,)).fetchone()
        return self._import_from_row(row) if row else None

    def update_import_status(
        self,
        import_id: str,
        status: ImportStatus,
        *,
        indexed_chunks: int,
        imported_cases: int,
        error_code: str | None = None,
    ) -> ImportRecord:
        """Persist import progress using safe counts and a stable error code."""
        if indexed_chunks < 0 or imported_cases < 0:
            raise ValueError("Import counts cannot be negative.")
        now = _now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE imports SET status = ?, indexed_chunks = ?, imported_cases = ?,
                error_code = ?, updated_at = ? WHERE import_id = ?""",
                (status.value, indexed_chunks, imported_cases, error_code, now, import_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(import_id)
        record = self.get_import(import_id)
        assert record is not None
        return record

    def save_import_chunks(self, import_id: str, chunks: list[DocumentChunk]) -> None:
        """Record deterministic expected chunk IDs before external vector writes."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM imports WHERE import_id = ?", (import_id,)).fetchone() is None:
                raise KeyError(import_id)
            connection.executemany(
                """INSERT OR IGNORE INTO import_chunks(import_id, chunk_id, document_id, locator)
                VALUES (?, ?, ?, ?)""",
                [(import_id, chunk.chunk_id, chunk.document_id, chunk.locator) for chunk in chunks],
            )

    def save_import_case_candidates(self, import_id: str, candidates: list[CaseRecord]) -> None:
        """Persist candidate extraction once so recovery does not rerun an LLM."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM imports WHERE import_id = ?", (import_id,)).fetchone() is None:
                raise KeyError(import_id)
            connection.executemany(
                """INSERT OR IGNORE INTO import_case_candidates(import_id, case_id, payload_json)
                VALUES (?, ?, ?)""",
                [(import_id, case.case_id, case.model_dump_json()) for case in candidates],
            )

    def get_import_case_candidates(self, import_id: str) -> list[CaseRecord]:
        """Return saved case-extraction candidates in deterministic order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM import_case_candidates WHERE import_id = ? ORDER BY case_id",
                (import_id,),
            ).fetchall()
        return [CaseRecord.model_validate_json(row["payload_json"]) for row in rows]

    def promote_import_case_candidates(self, import_id: str) -> int:
        """Idempotently upsert saved candidates and link their cases to an import."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT case_id, payload_json FROM import_case_candidates WHERE import_id = ?",
                (import_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """INSERT INTO cases(case_id, payload_json) VALUES (?, ?)
                    ON CONFLICT(case_id) DO UPDATE SET payload_json = excluded.payload_json""",
                    (row["case_id"], row["payload_json"]),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO import_cases(import_id, case_id) VALUES (?, ?)",
                    (import_id, row["case_id"]),
                )
        return len(rows)

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
                "SELECT status, lease_owner, lease_expires_at FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            lease_expires_at = (
                datetime.fromisoformat(row["lease_expires_at"])
                if row is not None and row["lease_expires_at"]
                else None
            )
            if (
                row is None
                or row["status"] != "running"
                or row["lease_owner"] != worker_id
                or lease_expires_at is None
                or lease_expires_at <= now
            ):
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

    def claim_next_task(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        max_attempts: int = 3,
    ) -> TaskRecord | None:
        """Atomically claim the oldest queued task and increment its attempt."""
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("Lease and attempt limits must be positive.")
        now = _now()
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._requeue_expired(connection, now)
            row = connection.execute(
                "SELECT * FROM tasks WHERE status = 'queued' AND attempt < max_attempts ORDER BY created_at, task_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            attempt = int(row["attempt"]) + 1
            task_id = row["task_id"]
            connection.execute(
                """UPDATE tasks SET status = 'running', updated_at = ?, attempt = ?, max_attempts = ?,
                lease_owner = ?, lease_expires_at = ?, error_code = NULL WHERE task_id = ? AND status = 'queued'""",
                (now.isoformat(), attempt, max_attempts, worker_id, expires_at.isoformat(), task_id),
            )
            self._insert_event(
                connection, task_id, "workflow", "running", f"工作流开始执行（第 {attempt}/{max_attempts} 次）。", now
            )
        return self.get_task(task_id)

    def renew_task_lease(self, task_id: str, worker_id: str, *, lease_seconds: int) -> bool:
        """Extend a running task lease only for its current owner."""
        if lease_seconds <= 0:
            raise ValueError("Lease duration must be positive.")
        now = _now()
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE tasks SET lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND status = 'running' AND lease_owner = ? AND lease_expires_at > ?""",
                (expires_at.isoformat(), now.isoformat(), task_id, worker_id, now.isoformat()),
            )
        return cursor.rowcount == 1

    def requeue_expired_tasks(self) -> list[str]:
        """Recover expired leases once, failing tasks that exhausted attempts."""
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._requeue_expired(connection, now)

    def _requeue_expired(self, connection: sqlite3.Connection, now: datetime) -> list[str]:
        rows = connection.execute(
            """SELECT task_id, attempt, max_attempts FROM tasks
            WHERE status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
            ORDER BY created_at, task_id""",
            (now.isoformat(),),
        ).fetchall()
        recovered: list[str] = []
        for row in rows:
            task_id = row["task_id"]
            exhausted = int(row["attempt"]) >= int(row["max_attempts"])
            if exhausted:
                connection.execute(
                    """UPDATE tasks SET status = 'failed', updated_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, error_code = 'attempt_limit' WHERE task_id = ? AND status = 'running'""",
                    (now.isoformat(), task_id),
                )
                self._insert_event(
                    connection, task_id, "failed", "failed", "任务达到最大执行次数，已停止自动恢复。", now
                )
            else:
                connection.execute(
                    """UPDATE tasks SET status = 'queued', updated_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL WHERE task_id = ? AND status = 'running'""",
                    (now.isoformat(), task_id),
                )
                self._insert_event(
                    connection, task_id, "recovered", "queued", "检测到执行租约过期，任务已重新排队。", now
                )
            recovered.append(task_id)
        return recovered

    def fail_task(self, task_id: str, *, worker_id: str, error_code: str) -> bool:
        """Persist a safe terminal failure only while the caller owns the lease."""
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE tasks SET status = 'failed', updated_at = ?, lease_owner = NULL,
                lease_expires_at = NULL, error_code = ?
                WHERE task_id = ? AND status = 'running' AND lease_owner = ? AND lease_expires_at > ?""",
                (now.isoformat(), error_code, task_id, worker_id, now.isoformat()),
            )
            if cursor.rowcount != 1:
                return False
            self._insert_event(
                connection, task_id, "failed", "failed",
                f"工作流执行失败（{error_code}）。可检查服务状态后重试。", now,
            )
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
    def _import_from_row(row: sqlite3.Row) -> ImportRecord:
        """Build the typed import record without exposing it as an API model."""
        return ImportRecord(
            import_id=row["import_id"],
            document_id=row["import_id"],
            content_sha256=row["content_sha256"],
            source_name=row["source_name"],
            file_type=row["file_type"],
            source_path=row["source_path"],
            parser_version=row["parser_version"],
            chunker_version=row["chunker_version"],
            expected_chunk_count=row["expected_chunk_count"],
            indexed_chunks=row["indexed_chunks"],
            imported_cases=row["imported_cases"],
            status=ImportStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error_code=row["error_code"],
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            mode=TaskMode(row["mode"]),
            status=row["status"],
            inputs=json.loads(row["inputs_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            lease_expires_at=row["lease_expires_at"],
            error_code=row["error_code"],
        )
