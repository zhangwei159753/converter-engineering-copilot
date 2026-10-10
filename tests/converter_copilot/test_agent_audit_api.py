from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from converter_copilot.api.main import create_app
from converter_copilot.api.runner import TaskRunner
from converter_copilot.domain.agent_audit import AgentRunRecord, HypothesisBranch
from converter_copilot.domain.schemas import TaskCreate, TaskMode
from converter_copilot.storage.sqlite_store import SQLiteStore


class UnusedModel:
    def with_structured_output(self, _schema):
        raise AssertionError("No workflow should execute in this test.")


class EmptyAdapter:
    def search(self, _query, *, limit=5, filters=None):
        return []


def _setup(tmp_path):
    store = SQLiteStore(tmp_path / "agent-api.db")
    runner = TaskRunner(store)
    task, _ = store.create_task(TaskCreate(
        mode=TaskMode.FAULT_ANALYSIS,
        inputs={
            "equipment_type": "PV inverter",
            "symptom": "DC bus overvoltage",
            "operating_conditions": {"load": "50%"},
        },
    ))
    store.claim_next_task("api-test-owner", lease_seconds=300)
    now = datetime.now(timezone.utc)
    store.save_agent_run(AgentRunRecord(
        agent_run_id="run-later",
        task_id=task.task_id,
        role="evidence_auditor",
        stage="audit",
        status="completed",
        branch_id="branch-0",
        input_refs=["evidence-1"],
        output_refs=["evidence-1"],
        summary="API token=never-return-this",
        started_at=now,
        completed_at=now + timedelta(seconds=1),
        provider_usage={"prompt_tokens": 12},
    ))
    store.save_agent_run(AgentRunRecord(
        agent_run_id="run-earlier",
        task_id=task.task_id,
        role="coordinator",
        stage="intake",
        status="completed",
        summary="Validated input.",
        started_at=now - timedelta(seconds=2),
        completed_at=now - timedelta(seconds=1),
    ))
    store.replace_hypothesis_branches(task.task_id, [
        HypothesisBranch(
            branch_id="branch-2",
            task_id=task.task_id,
            hypothesis="Second candidate",
            ordinal=1,
        ),
        HypothesisBranch(
            branch_id="branch-1",
            task_id=task.task_id,
            hypothesis="First candidate",
            ordinal=0,
        ),
    ])
    return store, runner, task.task_id


def test_agent_trace_and_hypothesis_routes_return_typed_ordered_safe_data(tmp_path):
    store, runner, task_id = _setup(tmp_path)

    with TestClient(create_app(runner)) as client:
        agents = client.get(f"/api/tasks/{task_id}/agents")
        hypotheses = client.get(f"/api/tasks/{task_id}/hypotheses")

    assert agents.status_code == 200
    assert agents.json()["task_id"] == task_id
    items = agents.json()["items"]
    assert [item["agent_run_id"] for item in items] == ["run-earlier", "run-later"]
    assert "provider_usage" not in items[1]
    assert "never-return-this" not in agents.text
    assert "prompt" not in agents.text.lower()
    assert hypotheses.status_code == 200
    assert [item["branch_id"] for item in hypotheses.json()["items"]] == ["branch-1", "branch-2"]
    assert store.get_task(task_id) is not None


def test_agent_trace_and_hypothesis_routes_return_404_for_unknown_tasks(tmp_path):
    _, runner, _ = _setup(tmp_path)

    with TestClient(create_app(runner)) as client:
        assert client.get("/api/tasks/missing/agents").status_code == 404
        assert client.get("/api/tasks/missing/hypotheses").status_code == 404
