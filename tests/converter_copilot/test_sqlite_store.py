import sqlite3

import pytest

from converter_copilot.domain.schemas import (
    CaseRecord,
    ConverterReport,
    TaskCreate,
    TaskEvent,
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
