import json
import time

from fastapi.testclient import TestClient

from converter_copilot.agent.workflows import WorkflowDeps
from converter_copilot.api.main import create_app
from converter_copilot.api.runner import TaskRunner
from converter_copilot.domain.agent_audit import BranchAuditResult
from converter_copilot.domain.schemas import (
    FaultAnalysisReport,
    FaultHypothesis,
    RetrievedChunk,
)
from converter_copilot.storage.sqlite_store import SQLiteStore


class FakeStructuredModel:
    def with_structured_output(self, _schema):
        return _FakeStructuredAdapter(self, _schema)

    @staticmethod
    def _fault_report():
        return FaultAnalysisReport(hypotheses=[FaultHypothesis(
            hypothesis="检查制动单元回路",
            mechanism="演示用：直流侧能量可能无法正常释放。",
            confidence="low",
            confidence_basis="单条模拟来源，不构成已验证的故障结论。",
            supporting_evidence_ids=["e2e-chunk-1"],
            checks=["按设备规程停机隔离后检查制动单元。"],
        )])


class _FakeStructuredAdapter:
    def __init__(self, model, schema):
        self.model = model
        self.schema = schema

    async def ainvoke(self, _messages):
        if self.schema is BranchAuditResult:
            return BranchAuditResult(supporting_evidence_ids=["e2e-chunk-1"])
        return FakeStructuredModel._fault_report()


class EmptySearch:
    def search(self, _query, *, limit=5):
        return []


class OneChunkIndex:
    def search(self, _query, *, limit=5, filters=None):
        return [RetrievedChunk(
            chunk_id="e2e-chunk-1",
            document_id="e2e-doc-1",
            filename="synthetic-demo-manual.md",
            text="Synthetic fixture: inspect the braking circuit for DC bus overvoltage.",
            locator="heading: Demo fixture",
            metadata={"equipment_type": "演示变流器"},
            score=0.9,
        )]


def test_health_readiness_and_fault_task_complete_with_traceable_evidence(tmp_path):
    store = SQLiteStore(tmp_path / "e2e.db")
    deps = WorkflowDeps(model=FakeStructuredModel(), search=EmptySearch(), vector_index=OneChunkIndex())
    runner = TaskRunner(store=store, deps_provider=lambda: deps)
    with TestClient(create_app(runner=runner)) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        readiness = client.get("/api/ready")
        assert readiness.status_code == 200
        assert readiness.json() == {
            "status": "ready",
            "components": {"database": "ready", "worker": "ready"},
        }

        response = client.post("/api/tasks", json={
            "mode": "fault_analysis",
            "inputs": {
                "equipment_type": "演示变流器",
                "symptom": "升载过程中直流母线过压",
                "operating_conditions": {"load": "60%"},
            },
        })
        assert response.status_code == 202
        task_id = response.json()["task"]["task_id"]

        for _ in range(100):
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["status"] in {"completed", "partial", "failed"}:
                break
            time.sleep(0.02)
        assert task["status"] == "completed", client.get(f"/api/tasks/{task_id}/report").json()

        events_response = client.get(f"/api/tasks/{task_id}/events")
        event_rows = [json.loads(line[6:]) for line in events_response.text.splitlines() if line.startswith("data: ")]
        assert any(event["stage"] == "complete" for event in event_rows)

        report = client.get(f"/api/tasks/{task_id}/report").json()
        assert report["evidence"][0]["evidence_id"] == "e2e-chunk-1"
        assert report["claims"][0]["evidence_ids"] == ["e2e-chunk-1"]
        assert report["claims"][0]["verification_status"] == "supported"
