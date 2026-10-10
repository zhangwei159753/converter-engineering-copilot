import sqlite3

import pytest

from converter_copilot.domain.schemas import (
    CaseRecord,
    ConverterReport,
    TaskCreate,
    TaskMode,
)
from converter_copilot.storage.sqlite_store import SQLiteStore


def _request():
    return TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={"equipment_type": "光伏逆变器", "symptom": "直流过压"})


def test_create_read_and_list_tasks(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    created = store.create_task(_request())
    assert store.get_task(created.task_id) == created
    assert store.list_tasks() == [created]


def test_events_have_ordered_monotonic_ids_and_can_resume(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    task = store.create_task(_request())
    first = store.append_event(task.task_id, stage="retrieve", status="running", message="检索中")
    second = store.append_event(task.task_id, stage="compose", status="running", message="整理中")
    assert first.event_id < second.event_id
    assert store.events_after(task.task_id, after_id=first.event_id) == [second]


def test_report_persists_as_typed_payload(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    task = store.create_task(_request())
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
