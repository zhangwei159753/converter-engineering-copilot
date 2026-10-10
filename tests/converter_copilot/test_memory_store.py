from datetime import datetime, timezone

import pytest

from converter_copilot.domain.agent_audit import MemoryEdge, MemoryNode
from converter_copilot.domain.schemas import TaskCreate, TaskMode
from converter_copilot.storage.sqlite_store import SQLiteStore


def test_memory_graph_round_trip_preserves_provenance_and_stable_order(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    task, _ = store.create_task(
        TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={"equipment_type": "逆变器", "symptom": "母线过压"}),
        work_order_id="WO-MEM-1",
    )
    with store._connect() as connection:
        connection.execute("UPDATE tasks SET status = 'completed' WHERE task_id = ?", (task.task_id,))
    review = store.save_work_order_review(
        "WO-MEM-1", task.task_id, reviewer="engineer", verdict="accepted", note="证据已核对"
    )
    nodes = [
        MemoryNode(node_id="equip:inv-1", node_type="equipment", canonical_key="inv-1", display_name="逆变器 INV-1"),
        MemoryNode(node_id="symptom:dc-overvoltage", node_type="symptom", canonical_key="dc-overvoltage", display_name="直流母线过压"),
    ]
    edge = MemoryEdge(
        edge_id="edge:source:1",
        source_node_id="equip:inv-1",
        relation_type="has_symptom",
        target_node_id="symptom:dc-overvoltage",
        source_task_id=task.task_id,
        source_work_order_id="WO-MEM-1",
        source_review_version=review.version,
        evidence_ids=["ev-1"],
        synthetic_demo=False,
        active=True,
        created_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
    )

    store.upsert_memory_graph(nodes, [edge])

    assert store.list_memory_edges(active_only=True) == [edge]


def test_memory_graph_rejects_orphan_source_task(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    node = MemoryNode(node_id="symptom:dc-overvoltage", node_type="symptom", canonical_key="dc-overvoltage", display_name="直流母线过压")
    edge = MemoryEdge(
        edge_id="edge:orphan",
        source_node_id=node.node_id,
        relation_type="has_symptom",
        target_node_id=node.node_id,
        source_task_id="missing-task",
        source_work_order_id="WO-MISSING",
        source_review_version=1,
        evidence_ids=["ev-1"],
        synthetic_demo=False,
        active=True,
    )

    with pytest.raises(ValueError, match="accepted work-order review"):
        store.upsert_memory_graph([node], [edge])

    assert store.list_memory_edges(active_only=True) == []
