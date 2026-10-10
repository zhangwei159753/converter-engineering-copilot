"""Failure-injection tests for the SQLite/Chroma import reconciliation boundary."""

import sqlite3
from types import SimpleNamespace

import pytest

from converter_copilot.api.runner import TaskRunner
from converter_copilot.config import ImportLimits
from converter_copilot.domain.agent_audit import MemoryEdge, MemoryNode
from converter_copilot.domain.schemas import (
    CaseRecord,
    ImportStatus,
    TaskCreate,
    TaskMode,
)
from converter_copilot.retrieval.import_validation import retain_source, validate_upload
from converter_copilot.storage.sqlite_store import SQLiteStore


class MemoryIndex:
    def __init__(self):
        self.chunks = {}
        self.fail_add = False
        self.fail_delete = False

    def add(self, chunks):
        if self.fail_add:
            self.fail_add = False
            raise RuntimeError("injected vector write failure")
        self.chunks.update({chunk.chunk_id: chunk for chunk in chunks})

    def contains_chunks(self, chunk_ids):
        return set(chunk_ids) <= self.chunks.keys()

    def delete_chunks(self, chunk_ids):
        if self.fail_delete:
            self.fail_delete = False
            raise RuntimeError("injected vector delete failure")
        for chunk_id in chunk_ids:
            self.chunks.pop(chunk_id, None)


class NeverModel:
    def with_structured_output(self, schema):
        raise AssertionError("CSV recovery must use its persisted deterministic candidates")


def _create_incomplete_import(tmp_path, index):
    store = SQLiteStore(tmp_path / "imports.db")
    payload = b"symptom,equipment_type\nDC overvoltage,PV inverter\n"
    source = validate_upload("cases.csv", payload, ImportLimits())
    source_path = retain_source(source, tmp_path / "sources")
    store.create_import(
        source.import_id,
        content_sha256=source.content_sha256,
        source_name=source.filename,
        file_type=source.file_type,
        parser_version=source.parser_version,
        chunker_version=source.chunker_version,
        expected_chunk_count=len(source.chunks),
        source_path=str(source_path),
    )
    store.save_import_chunks(source.import_id, source.chunks)
    store.save_import_case_candidates(
        source.import_id,
        [CaseRecord(case_id="case-saved", symptom="DC overvoltage", evidence_ids=[source.chunks[0].chunk_id])],
    )
    store.update_import_status(source.import_id, ImportStatus.INDEXING, indexed_chunks=0, imported_cases=0)
    deps = SimpleNamespace(model=NeverModel(), search=object(), vector_index=index, case_store=store)
    runner = TaskRunner(store, deps_provider=lambda: deps)
    return store, runner, source, source_path


@pytest.mark.asyncio
async def test_vector_failure_replays_without_duplicate_cases_or_model_extraction(tmp_path):
    index = MemoryIndex()
    index.fail_add = True
    store, runner, source, _ = _create_incomplete_import(tmp_path, index)

    failed = await runner.reconcile_imports(source.import_id)
    repaired = await runner.reconcile_imports(source.import_id)

    assert failed[0].status == ImportStatus.FAILED
    assert repaired[0].status == ImportStatus.COMPLETE
    assert list(index.chunks) == [source.chunks[0].chunk_id]
    assert [case.case_id for case in store.list_cases()] == ["case-saved"]


@pytest.mark.asyncio
async def test_database_case_failure_after_vector_success_reconciles_to_one_record_set(tmp_path, monkeypatch):
    index = MemoryIndex()
    store, runner, source, _ = _create_incomplete_import(tmp_path, index)
    promote = store.promote_import_case_candidates
    failed_once = True

    def fail_promotion_once(import_id):
        nonlocal failed_once
        if failed_once:
            failed_once = False
            raise sqlite3.OperationalError("injected case transaction failure")
        return promote(import_id)

    monkeypatch.setattr(store, "promote_import_case_candidates", fail_promotion_once)
    first = await runner.reconcile_imports(source.import_id)
    second = await runner.reconcile_imports(source.import_id)

    assert first[0].status == ImportStatus.FAILED
    assert second[0].status == ImportStatus.COMPLETE
    assert len(index.chunks) == 1
    assert len(store.list_cases()) == 1


@pytest.mark.asyncio
async def test_missing_retained_source_is_visible_as_needs_repair(tmp_path):
    index = MemoryIndex()
    store, runner, source, source_path = _create_incomplete_import(tmp_path, index)
    source_path.unlink()

    result = await runner.reconcile_imports(source.import_id)

    assert result[0].status == ImportStatus.NEEDS_REPAIR
    assert result[0].error_code == "source_missing"
    assert store.list_cases() == []
    assert index.chunks == {}


@pytest.mark.asyncio
async def test_consistency_inspection_flags_missing_vectors_and_repair_restores_them(tmp_path):
    index = MemoryIndex()
    store, runner, source, _ = _create_incomplete_import(tmp_path, index)
    assert (await runner.reconcile_imports(source.import_id))[0].status == ImportStatus.COMPLETE
    index.chunks.clear()

    inspected = await runner.inspect_imports()
    repaired = await runner.reconcile_imports(source.import_id)

    assert inspected[0].status == ImportStatus.NEEDS_REPAIR
    assert inspected[0].error_code == "vectors_missing"
    assert repaired[0].status == ImportStatus.COMPLETE
    assert list(index.chunks) == [source.chunks[0].chunk_id]
    assert len(store.list_cases()) == 1


@pytest.mark.asyncio
async def test_consistency_inspection_flags_missing_case_projection_and_repairs_it(tmp_path):
    index = MemoryIndex()
    store, runner, source, _ = _create_incomplete_import(tmp_path, index)
    assert (await runner.reconcile_imports(source.import_id))[0].status == ImportStatus.COMPLETE
    with store._connect() as connection:
        connection.execute("DELETE FROM cases WHERE case_id = 'case-saved'")

    inspected = await runner.inspect_imports()
    repaired = await runner.reconcile_imports(source.import_id)

    assert inspected[0].status == ImportStatus.NEEDS_REPAIR
    assert inspected[0].error_code == "cases_incomplete"
    assert repaired[0].status == ImportStatus.COMPLETE
    assert [case.case_id for case in store.list_cases()] == ["case-saved"]


@pytest.mark.asyncio
async def test_runner_start_reconciles_incomplete_imports_before_polling(tmp_path):
    index = MemoryIndex()
    store, runner, source, _ = _create_incomplete_import(tmp_path, index)

    await runner.start()
    try:
        assert store.get_import(source.import_id).status == ImportStatus.COMPLETE
        assert runner.is_running
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_interrupted_delete_can_be_retried_without_leaving_source_or_case_data(tmp_path):
    index = MemoryIndex()
    store, runner, source, source_path = _create_incomplete_import(tmp_path, index)
    await runner.reconcile_imports(source.import_id)
    index.fail_delete = True

    first = await runner.delete_import(source.import_id)
    second = await runner.reconcile_imports(source.import_id)

    assert first.status == ImportStatus.DELETING
    assert second[0].status == ImportStatus.DELETED
    assert not source_path.exists()
    assert store.list_cases() == []
    assert store.get_import_chunk_ids(source.import_id) == []
    assert index.chunks == {}


@pytest.mark.asyncio
async def test_deleting_import_deactivates_memory_citing_its_chunks(tmp_path):
    index = MemoryIndex()
    store, runner, source, _ = _create_incomplete_import(tmp_path, index)
    await runner.reconcile_imports(source.import_id)
    task, _ = store.create_task(
        TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={"equipment_type": "PV inverter", "symptom": "DC overvoltage"}),
        work_order_id="WO-IMPORT-MEMORY",
    )
    with store._connect() as connection:
        connection.execute("UPDATE tasks SET status = 'completed' WHERE task_id = ?", (task.task_id,))
    review = store.save_work_order_review(
        "WO-IMPORT-MEMORY", task.task_id, reviewer="engineer", verdict="accepted", note="reviewed"
    )
    equipment = MemoryNode(node_id="equip:import", node_type="equipment", canonical_key="pv", display_name="PV inverter")
    cause = MemoryNode(node_id="cause:import", node_type="cause", canonical_key="overvoltage", display_name="Overvoltage cause")
    edge = MemoryEdge(
        edge_id="edge:import-source", source_node_id=equipment.node_id, relation_type="associated_with_cause",
        target_node_id=cause.node_id, source_task_id=task.task_id, source_work_order_id="WO-IMPORT-MEMORY",
        source_review_version=review.version, evidence_ids=[source.chunks[0].chunk_id],
    )
    store.upsert_memory_graph([equipment, cause], [edge])

    await runner.delete_import(source.import_id)

    assert store.list_memory_edges(active_only=True) == []
    assert store.list_memory_edges(active_only=False)[0].active is False
