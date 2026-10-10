from fastapi.testclient import TestClient

from converter_copilot.api.main import create_app
from converter_copilot.api.routes import work_orders as work_order_routes
from converter_copilot.api.runner import TaskRunner
from converter_copilot.domain.agent_audit import MemoryEdge, MemoryNode
from converter_copilot.domain.schemas import (
    Claim,
    ConverterReport,
    Evidence,
    TaskCreate,
    TaskMode,
)
from converter_copilot.domain.work_orders import WorkOrderRecord
from converter_copilot.storage.sqlite_store import SQLiteStore


class EmptyAdapter:
    def search(self, _query, *, limit=5, filters=None):
        return []


class UnusedModel:
    def with_structured_output(self, _schema):
        raise AssertionError("Memory API does not run an agent.")


def _setup(tmp_path):
    store = SQLiteStore(tmp_path / "memory-api.db")
    runner = TaskRunner(store)
    task, _ = store.create_task(TaskCreate(
        mode=TaskMode.FAULT_ANALYSIS,
        inputs={"equipment_type": "CPC-2500", "symptom": "Overvoltage"},
    ), work_order_id="WO-REAL-1")
    store.update_task_status(task.task_id, "completed")
    store.save_work_order_review(
        "WO-REAL-1", task.task_id, reviewer="engineer", verdict="accepted", note="Verified."
    )
    nodes = [
        MemoryNode(node_id="equipment-1", node_type="equipment", canonical_key="cpc-2500", display_name="CPC-2500"),
        MemoryNode(node_id="cause-1", node_type="cause", canonical_key="braking-circuit", display_name="Inspect braking circuit"),
    ]
    store.upsert_memory_graph(nodes, [MemoryEdge(
        edge_id="edge-1",
        source_node_id="equipment-1",
        relation_type="associated_with_cause",
        target_node_id="cause-1",
        source_task_id=task.task_id,
        source_work_order_id="WO-REAL-1",
        source_review_version=1,
        evidence_ids=["ev-1"],
    )])
    return runner


def test_memory_api_returns_bounded_matches_and_non_synthetic_flag(tmp_path):
    runner = _setup(tmp_path)

    with TestClient(create_app(runner)) as client:
        response = client.get("/api/memory", params={"q": "braking", "equipment_type": "CPC-2500", "limit": 1})

    assert response.status_code == 200
    assert response.json()["synthetic_demo"] is False
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["source_work_order_id"] == "WO-REAL-1"
    assert response.json()["items"][0]["evidence_ids"] == ["ev-1"]


def test_memory_api_rejects_out_of_range_limit(tmp_path):
    runner = _setup(tmp_path)

    with TestClient(create_app(runner)) as client:
        assert client.get("/api/memory", params={"limit": 51}).status_code == 422


def test_work_order_review_and_memory_projection_commit_together(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "review-projection.db")
    runner = TaskRunner(store)
    task, _ = store.create_task(TaskCreate(
        mode=TaskMode.FAULT_ANALYSIS,
        inputs={"equipment_type": "CPC-2500", "symptom": "DC bus overvoltage"},
    ), work_order_id="WO-2026-0418")
    store.update_task_status(task.task_id, "completed")
    store.save_report(ConverterReport(
        task_id=task.task_id,
        mode=TaskMode.FAULT_ANALYSIS,
        result={"hypotheses": []},
        claims=[Claim(
            claim_id="claim-1",
            text="Inspect the braking circuit.",
            claim_type="inference",
            evidence_ids=["ev-1"],
            verification_status="supported",
        )],
        evidence=[Evidence(
            evidence_id="ev-1",
            task_id=task.task_id,
            title="Service manual",
            source_type="web",
            excerpt="Regenerative energy must be dissipated.",
            url="https://example.test/manual",
            accessed_at="2026-10-10T10:00:00Z",
            source_status="available",
            applicability="direct",
        )],
        completion_status="completed",
    ))
    monkeypatch.setattr(
        work_order_routes,
        "get_work_order",
        lambda _work_order_id: WorkOrderRecord(
            work_order_id="WO-2026-0418",
            equipment_model="CPC-2500",
            serial_number="SERIAL-1",
            alarm_code="DC_OV_017",
            symptom="DC bus overvoltage",
            occurred_at="2026-10-08T09:42:00+08:00",
            operating_conditions={"active_power_kw": 1800},
            severity="high",
            status="resolved",
            synthetic_demo=False,
        ),
    )

    with TestClient(create_app(runner)) as client:
        response = client.put(
            f"/api/work-orders/WO-2026-0418/analyses/{task.task_id}/review",
            json={"reviewer": "engineer", "verdict": "accepted", "note": "Validated."},
        )

    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert len(store.list_memory_edges()) > 0
