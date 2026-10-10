import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from converter_copilot.domain.agent_audit import AgentRunRecord, HypothesisBranch
from converter_copilot.domain.schemas import ConverterReport, TaskCreate, TaskMode
from converter_copilot.storage.migrations import CURRENT_SCHEMA_VERSION
from converter_copilot.storage.sqlite_store import SQLiteStore


def _task(store: SQLiteStore):
    return store.create_task(
        TaskCreate(
            mode=TaskMode.FAULT_ANALYSIS,
            inputs={"equipment_type": "光伏逆变器", "symptom": "直流母线过压"},
        )
    )[0]


def _run(task_id: str, run_id: str, seconds: int) -> AgentRunRecord:
    started = datetime(2026, 10, 10, 12, 0, seconds, tzinfo=timezone.utc)
    return AgentRunRecord(
        agent_run_id=run_id,
        task_id=task_id,
        role="evidence_researcher",
        stage="retrieve",
        status="completed",
        input_refs=["task-input"],
        output_refs=["ev-1"],
        summary="检索到一条可追溯资料。",
        retry_count=0,
        started_at=started,
        completed_at=started + timedelta(milliseconds=30),
    )


def _branch(task_id: str, branch_id: str, ordinal: int) -> HypothesisBranch:
    return HypothesisBranch(
        branch_id=branch_id,
        task_id=task_id,
        hypothesis=f"候选原因 {branch_id}",
        mechanism_summary="来源中描述了相应机理。",
        supporting_evidence_ids=["ev-1"],
        opposing_evidence_ids=[],
        applicability="direct",
        missing_fields=[],
        checks=["核对保护前的母线波形"],
        gate_status="qualified",
        disposition="retained",
        ordinal=ordinal,
    )


def test_agent_runs_round_trip_and_are_listed_in_stable_start_order(tmp_path):
    store = SQLiteStore(tmp_path / "audit.db")
    task = _task(store)
    later = _run(task.task_id, "run-later", 2)
    earlier = _run(task.task_id, "run-earlier", 1)

    store.save_agent_run(later)
    store.save_agent_run(earlier)

    assert store.list_agent_runs(task.task_id) == [earlier, later]


def test_agent_run_rejects_orphan_task(tmp_path):
    store = SQLiteStore(tmp_path / "audit.db")

    with pytest.raises(sqlite3.IntegrityError):
        store.save_agent_run(_run("missing-task", "orphan-run", 1))


def test_hypothesis_branches_round_trip_in_ordinal_order(tmp_path):
    store = SQLiteStore(tmp_path / "audit.db")
    task = _task(store)
    branches = [_branch(task.task_id, "branch-2", 2), _branch(task.task_id, "branch-1", 1)]

    store.replace_hypothesis_branches(task.task_id, branches)

    assert store.list_hypothesis_branches(task.task_id) == [branches[1], branches[0]]


def test_duplicate_branch_ids_are_rejected_before_writing(tmp_path):
    store = SQLiteStore(tmp_path / "audit.db")
    task = _task(store)
    branches = [_branch(task.task_id, "duplicate", 1), _branch(task.task_id, "duplicate", 2)]

    with pytest.raises(ValueError, match="duplicate"):
        store.replace_hypothesis_branches(task.task_id, branches)

    assert store.list_hypothesis_branches(task.task_id) == []


def test_schema_eight_migration_preserves_existing_tasks_and_reports(tmp_path):
    database = tmp_path / "legacy-v7.db"
    store = SQLiteStore(database)
    task = _task(store)
    report = ConverterReport(
        task_id=task.task_id,
        mode=TaskMode.FAULT_ANALYSIS,
        result={"hypotheses": [], "checks": []},
        claims=[],
        evidence=[],
        quality_warnings=[],
        completion_status="partial",
    )
    store.save_report(report)

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE memory_edges")
        connection.execute("DROP TABLE memory_nodes")
        connection.execute("DROP TABLE hypothesis_branches")
        connection.execute("DROP TABLE agent_runs")
        connection.execute("PRAGMA user_version = 7")

    upgraded = SQLiteStore(database)

    assert CURRENT_SCHEMA_VERSION == 8
    assert upgraded.get_task(task.task_id) == task
    assert upgraded.get_report(task.task_id) == report
    assert upgraded.list_agent_runs(task.task_id) == []
