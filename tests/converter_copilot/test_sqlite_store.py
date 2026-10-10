import sqlite3

import pytest

from converter_copilot.domain.schemas import (
    CaseRecord,
    ConverterReport,
    TaskCreate,
    TaskMode,
)
from converter_copilot.storage.sqlite_store import IdempotencyConflictError, SQLiteStore


def _request():
    return TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={"equipment_type": "光伏逆变器", "symptom": "直流过压"})


def test_create_read_and_list_tasks(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    created, reused = store.create_task(_request())
    assert reused is False
    assert store.get_task(created.task_id) == created
    assert store.list_tasks() == [created]


def test_events_have_ordered_monotonic_ids_and_can_resume(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    task, _ = store.create_task(_request())
    first = store.append_event(task.task_id, stage="retrieve", status="running", message="检索中")
    second = store.append_event(task.task_id, stage="compose", status="running", message="整理中")
    assert first.event_id < second.event_id
    assert store.events_after(task.task_id, after_id=first.event_id) == [second]


def test_report_persists_as_typed_payload(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    task, _ = store.create_task(_request())
    report = ConverterReport(
        task_id=task.task_id,
        mode="fault_analysis",
        result={"hypotheses": [], "checks": []},
        claims=[],
        evidence=[],
        quality_warnings=[],
        completion_status="partial",
    )
    store.save_report(report)
    assert store.get_report(report.task_id) == report


def test_case_metadata_round_trip(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    case = CaseRecord(case_id="case-1", symptom="启动后过流", evidence_ids=["ev-1"])
    store.save_case(case)
    assert store.list_cases() == [case]


def test_foreign_keys_reject_orphan_events(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    with pytest.raises(sqlite3.IntegrityError):
        store.append_event("missing-task", stage="run", status="failed", message="orphan")


def test_empty_database_migrates_to_current_version(tmp_path):
    database = tmp_path / "empty.db"
    SQLiteStore(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}

    assert version == 2
    assert {"task_id", "mode", "status", "inputs_json", "created_at", "updated_at"} <= columns


def test_existing_v0_rows_survive_migration(tmp_path):
    database = tmp_path / "existing.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY, mode TEXT NOT NULL, status TEXT NOT NULL,
                inputs_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                stage TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE reports (
                task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE cases (case_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            CREATE TABLE task_retries (
                retry_task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE CASCADE,
                original_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );
            INSERT INTO tasks VALUES ('task-old', 'fault_analysis', 'completed', '{}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00');
            INSERT INTO task_events VALUES (41, 'task-old', 'complete', 'completed', 'old event', '2026-01-01T00:01:00+00:00');
            INSERT INTO reports VALUES ('task-old', '{"report":"kept"}');
            INSERT INTO cases VALUES ('case-old', '{"case":"kept"}');
            INSERT INTO tasks VALUES ('task-retry-old', 'fault_analysis', 'failed', '{}', '2026-01-01T00:02:00+00:00', '2026-01-01T00:03:00+00:00');
            INSERT INTO task_retries VALUES ('task-retry-old', 'task-old', '2026-01-01T00:02:00+00:00');
            """
        )

    SQLiteStore(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT task_id, status FROM tasks ORDER BY task_id").fetchall() == [
            ("task-old", "completed"),
            ("task-retry-old", "failed"),
        ]
        assert connection.execute("SELECT event_id, message FROM task_events").fetchall() == [(41, "old event")]
        assert connection.execute("SELECT payload_json FROM reports WHERE task_id = 'task-old'").fetchone() == (
            '{"report":"kept"}',
        )
        assert connection.execute("SELECT payload_json FROM cases WHERE case_id = 'case-old'").fetchone() == (
            '{"case":"kept"}',
        )
        assert connection.execute("SELECT retry_task_id, original_task_id FROM task_retries").fetchall() == [
            ("task-retry-old", "task-old")
        ]


def test_failed_migration_does_not_advance_user_version(tmp_path, monkeypatch):
    from converter_copilot.storage import migrations

    def fail_after_ddl(connection):
        connection.execute("CREATE TABLE transient_v2 (value TEXT)")
        raise sqlite3.OperationalError("simulated migration failure")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [
            (1, lambda connection: connection.execute("CREATE TABLE stable_v1 (value TEXT)")),
            (2, fail_after_ddl),
        ],
    )
    with sqlite3.connect(tmp_path / "migration.db") as connection:
        with pytest.raises(sqlite3.OperationalError, match="simulated migration failure"):
            migrations.migrate(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'stable_v1'"
        ).fetchone() == ("stable_v1",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'transient_v2'"
        ).fetchone() is None


def test_backup_failure_prevents_migration(tmp_path, monkeypatch):
    database = tmp_path / "existing.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT)")
        connection.execute("INSERT INTO legacy_marker VALUES ('preserve')")

    def fail_backup(self):
        raise OSError("simulated backup failure")

    monkeypatch.setattr(SQLiteStore, "_backup_before_migration", fail_backup, raising=False)
    with pytest.raises(OSError, match="simulated backup failure"):
        SQLiteStore(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT value FROM legacy_marker").fetchone() == ("preserve",)


def test_same_idempotency_key_and_request_reuses_task_and_queue_event(tmp_path):
    store = SQLiteStore(tmp_path / "idempotent.db")
    first, first_reused = store.create_task(_request(), idempotency_key="request-1")
    second, second_reused = store.create_task(_request(), idempotency_key="request-1")

    assert first_reused is False
    assert second_reused is True
    assert second.task_id == first.task_id
    assert [event.stage for event in store.events_after(first.task_id)] == ["queued"]


def test_same_idempotency_key_with_changed_request_conflicts(tmp_path):
    store = SQLiteStore(tmp_path / "idempotent.db")
    store.create_task(_request(), idempotency_key="request-1")
    changed = TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={"equipment_type": "储能变流器", "symptom": "过温"})

    with pytest.raises(IdempotencyConflictError):
        store.create_task(changed, idempotency_key="request-1")
    assert len(store.list_tasks()) == 1


def test_retry_key_reuses_retry_and_retry_link_is_atomic(tmp_path):
    store = SQLiteStore(tmp_path / "retry.db")
    original, _ = store.create_task(_request())
    store.update_task_status(original.task_id, "failed")

    first, first_reused = store.create_retry(original.task_id, _request(), idempotency_key="retry-1")
    second, second_reused = store.create_retry(original.task_id, _request(), idempotency_key="retry-1")

    assert first_reused is False
    assert second_reused is True
    assert second.task_id == first.task_id
    assert store.retry_count(original.task_id) == 1
    assert [event.stage for event in store.events_after(first.task_id)] == ["queued", "retry"]


def test_retry_idempotency_key_is_bound_to_original_task(tmp_path):
    store = SQLiteStore(tmp_path / "retry-scope.db")
    first_original, _ = store.create_task(_request())
    second_original, _ = store.create_task(_request())
    store.update_task_status(first_original.task_id, "failed")
    store.update_task_status(second_original.task_id, "failed")
    store.create_retry(first_original.task_id, _request(), idempotency_key="retry-shared")

    with pytest.raises(IdempotencyConflictError):
        store.create_retry(second_original.task_id, _request(), idempotency_key="retry-shared")

    assert store.retry_count(first_original.task_id) == 1
    assert store.retry_count(second_original.task_id) == 0


def test_finish_task_rolls_back_report_status_and_event_together(tmp_path):
    database = tmp_path / "finish.db"
    store = SQLiteStore(database)
    task, _ = store.create_task(_request())
    store.claim_next_task("worker-1", lease_seconds=30)
    report = ConverterReport(
        task_id=task.task_id,
        mode="fault_analysis",
        result={"hypotheses": [], "checks": []},
        claims=[],
        evidence=[],
        quality_warnings=[],
        completion_status="completed",
    )
    with store._connect() as connection:
        connection.execute(
            """CREATE TRIGGER reject_complete_event BEFORE INSERT ON task_events
            WHEN NEW.stage = 'complete' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        store.finish_task(task.task_id, report, worker_id="worker-1")

    assert store.get_report(task.task_id) is None
    assert store.get_task(task.task_id).status == "running"
    assert [event.stage for event in store.events_after(task.task_id)] == ["queued", "workflow"]
