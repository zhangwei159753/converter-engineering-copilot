import json
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient

from converter_copilot.agent.workflows import WorkflowDeps
from converter_copilot.api.main import create_app
from converter_copilot.api.runner import TaskRunner
from converter_copilot.domain.schemas import RetrievedChunk, TaskCreate
from converter_copilot.storage.sqlite_store import SQLiteStore


class EmptySearch:
    def search(self, query, *, limit=5):
        return []


class EmptyIndex:
    def search(self, query, *, limit=5, filters=None):
        return []

    def add(self, chunks):
        self.added = list(chunks)


class NeverModel:
    def with_structured_output(self, schema):
        raise AssertionError("model must not be called without evidence")


@contextmanager
def _client(tmp_path, deps=None):
    store = SQLiteStore(tmp_path / "api.db")
    deps = deps or WorkflowDeps(model=NeverModel(), search=EmptySearch(), vector_index=EmptyIndex())
    runner = TaskRunner(store=store, deps_provider=lambda: deps)
    with TestClient(create_app(runner=runner)) as client:
        yield client, store


def _fault_request():
    return {
        "mode": "fault_analysis",
        "inputs": {
            "equipment_type": "PV inverter",
            "symptom": "DC bus overvoltage",
            "operating_conditions": {"load": "50%"},
        },
    }


def _wait_for_terminal(client, task_id):
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"partial", "completed", "failed"}:
            return task
        time.sleep(0.02)
    return task


def test_valid_request_runs_and_report_becomes_available(tmp_path):
    with _client(tmp_path) as (client, _):
        response = client.post("/api/tasks", json=_fault_request())
        assert response.status_code == 202
        task_id = response.json()["task"]["task_id"]
        terminal = _wait_for_terminal(client, task_id)
        assert terminal["status"] == "partial"
        assert terminal["attempt"] == 1
        assert terminal["max_attempts"] == 3
        assert "lease_owner" not in terminal
        assert client.get(f"/api/tasks/{task_id}/report").status_code == 200


def test_invalid_mode_specific_input_returns_422(tmp_path):
    with _client(tmp_path) as (client, _):
        response = client.post("/api/tasks", json={"mode": "fault_analysis", "inputs": {"symptom": "overvoltage"}})
        assert response.status_code == 422


def test_unknown_task_and_report_not_ready_return_4xx(tmp_path):
    with _client(tmp_path) as (client, store):
        assert client.get("/api/tasks/missing").status_code == 404
        created, _ = store.create_task(TaskCreate.model_validate(_fault_request()))
        assert client.get(f"/api/tasks/{created.task_id}/report").status_code == 409


def test_retry_creates_new_attempt_and_preserves_original_report(tmp_path):
    with _client(tmp_path) as (client, store):
        response = client.post("/api/tasks", json=_fault_request())
        task_id = response.json()["task"]["task_id"]
        assert _wait_for_terminal(client, task_id)["status"] == "partial"
        original_report = client.get(f"/api/tasks/{task_id}/report").json()
        retry = client.post(f"/api/tasks/{task_id}/retry")
        assert retry.status_code == 202
        retry_id = retry.json()["task"]["task_id"]
        assert retry_id != task_id
        assert store.retry_count(task_id) == 1
        assert client.get(f"/api/tasks/{task_id}/report").json() == original_report


def test_unknown_mode_returns_validation_error(tmp_path):
    with _client(tmp_path) as (client, _):
        response = client.post(
            "/api/tasks",
            json={"mode": "unknown", "inputs": {"symptom": "startup overcurrent"}},
        )
        assert response.status_code == 422


def test_technical_research_mode_is_registered_and_runs(tmp_path):
    with _client(tmp_path) as (client, _):
        response = client.post(
            "/api/tasks",
            json={"mode": "technical_research", "inputs": {"question": "Compare two-level and three-level topologies"}},
        )
        assert response.status_code == 202
        task_id = response.json()["task"]["task_id"]
        assert _wait_for_terminal(client, task_id)["status"] == "partial"
        assert client.get(f"/api/tasks/{task_id}/report").json()["mode"] == "technical_research"


def test_case_retrieval_mode_is_registered_and_runs(tmp_path):
    with _client(tmp_path) as (client, _):
        response = client.post(
            "/api/tasks",
            json={"mode": "case_retrieval", "inputs": {"symptom": "startup overcurrent", "equipment_type": "PV inverter"}},
        )
        assert response.status_code == 202
        task_id = response.json()["task"]["task_id"]
        assert _wait_for_terminal(client, task_id)["status"] == "partial"
        assert client.get(f"/api/tasks/{task_id}/report").json()["mode"] == "case_retrieval"


def test_sse_replays_events_and_resumes_after_last_event_id(tmp_path):
    with _client(tmp_path) as (client, _):
        response = client.post("/api/tasks", json=_fault_request())
        task_id = response.json()["task"]["task_id"]
        assert _wait_for_terminal(client, task_id)["status"] == "partial"
        full = client.get(f"/api/tasks/{task_id}/events")
        assert full.status_code == 200
        events = [line for line in full.text.splitlines() if line.startswith("id: ")]
        assert len(events) >= 2
        last_id = int(events[0].split(": ", 1)[1])
        resumed = client.get(f"/api/tasks/{task_id}/events", headers={"Last-Event-ID": str(last_id)})
        resumed_ids = [int(line.split(": ", 1)[1]) for line in resumed.text.splitlines() if line.startswith("id: ")]
        assert resumed_ids
        assert min(resumed_ids) > last_id
        assert json.loads(resumed.text.split("data: ", 1)[1].splitlines()[0])["task_id"] == task_id


def test_client_disconnect_does_not_cancel_or_duplicate_background_run(tmp_path):
    with _client(tmp_path) as (client, _):
        created = client.post("/api/tasks", json=_fault_request()).json()["task"]["task_id"]
        with client.stream("GET", f"/api/tasks/{created}/events") as response:
            first_chunk = next(response.iter_text())
            assert "id: " in first_chunk
        task = _wait_for_terminal(client, created)
        assert task["status"] == "partial"
        event_text = client.get(f"/api/tasks/{created}/events").text
        assert event_text.count('"stage": "queued"') == 1


def test_create_idempotency_key_reuses_task_and_rejects_changed_payload(tmp_path):
    with _client(tmp_path) as (client, store):
        headers = {"Idempotency-Key": "create-once"}
        first = client.post("/api/tasks", json=_fault_request(), headers=headers)
        second = client.post("/api/tasks", json=_fault_request(), headers=headers)

        assert first.status_code == second.status_code == 202
        task_id = first.json()["task"]["task_id"]
        assert second.json()["task"]["task_id"] == task_id
        assert len([task for task in store.list_tasks() if task.task_id == task_id]) == 1

        changed_request = _fault_request()
        changed_request["inputs"]["symptom"] = "启动过流"
        conflict = client.post("/api/tasks", json=changed_request, headers=headers)
        assert conflict.status_code == 409
        assert len(store.list_tasks()) == 1


def test_retry_idempotency_key_returns_same_retry_task(tmp_path):
    with _client(tmp_path) as (client, store):
        task_id = client.post("/api/tasks", json=_fault_request()).json()["task"]["task_id"]
        assert _wait_for_terminal(client, task_id)["status"] == "partial"
        headers = {"Idempotency-Key": "retry-once"}

        first = client.post(f"/api/tasks/{task_id}/retry", headers=headers)
        second = client.post(f"/api/tasks/{task_id}/retry", headers=headers)

        assert first.status_code == second.status_code == 202
        assert first.json()["task"]["task_id"] == second.json()["task"]["task_id"]
        assert store.retry_count(task_id) == 1


def test_failed_workflow_status_and_events_remain_retrievable(tmp_path):
    class FailingModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("provider failed")

    class OneChunkIndex(EmptyIndex):
        def search(self, query, *, limit=5, filters=None):
            return [
                RetrievedChunk(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    filename="manual.pdf",
                    text="Inspect the converter DC bus.",
                    locator="page 1",
                    score=0.8,
                )
            ]

    deps = WorkflowDeps(model=FailingModel(), search=EmptySearch(), vector_index=OneChunkIndex())
    with _client(tmp_path, deps=deps) as (client, _):
        task_id = client.post("/api/tasks", json=_fault_request()).json()["task"]["task_id"]
        task = _wait_for_terminal(client, task_id)
        assert task["status"] == "failed"
        events = client.get(f"/api/tasks/{task_id}/events").text
        assert '"status": "failed"' in events


def test_migration_failure_keeps_liveness_and_marks_readiness_unavailable(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "migration-error.db")
    runner = TaskRunner(store=store, deps_provider=lambda: WorkflowDeps(model=NeverModel(), search=EmptySearch(), vector_index=EmptyIndex()))

    def fail_initialization():
        raise RuntimeError("private migration detail")

    monkeypatch.setattr(store, "initialize", fail_initialization)
    with TestClient(create_app(runner=runner)) as client:
        assert client.get("/api/health").status_code == 200
        readiness = client.get("/api/ready")
        assert readiness.status_code == 503
        assert readiness.json()["components"]["database"] == "unavailable"
        assert "private migration detail" not in readiness.text
