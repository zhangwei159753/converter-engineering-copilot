from datetime import datetime, timezone

from fastapi.testclient import TestClient

from converter_copilot.agent.workflows import WorkflowDeps
from converter_copilot.api.main import create_app
from converter_copilot.api.runner import TaskRunner
from converter_copilot.storage.sqlite_store import SQLiteStore


class EmptySearch:
    def search(self, query, *, limit=5):
        return []


class EmptyIndex:
    def search(self, query, *, limit=5, filters=None):
        return []


class NeverModel:
    def with_structured_output(self, schema):
        raise AssertionError("evaluation review does not call a model")


def _client(tmp_path):
    store = SQLiteStore(tmp_path / "eval-api.db")
    runner = TaskRunner(store=store, deps_provider=lambda: WorkflowDeps(NeverModel(), EmptySearch(), EmptyIndex()))
    return TestClient(create_app(runner=runner)), store


def _bundle():
    return {
        "bundle_id": "api-bundle-1", "mode": "fault_analysis", "dataset_version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "examples": [{
            "example_id": "api-example", "source_type": "synthetic_demo_fixture", "task_id": "task-1",
            "inputs": {"equipment_type": "inverter", "symptom": "alarm"},
            "claims": [{"claim_id": "claim-1", "text": "Claim", "evidence_ids": ["evidence-1"]}],
            "evidence": [{"evidence_id": "evidence-1", "excerpt": "Excerpt", "locator": "page 1"}],
        }],
    }


def test_evaluation_api_import_filter_review_history_and_null_metrics(tmp_path):
    client, _ = _client(tmp_path)
    first = client.post("/api/evaluations/review-bundles", json=_bundle())
    again = client.post("/api/evaluations/review-bundles", json=_bundle())
    assert first.status_code == again.status_code == 200
    assert first.json()["imported_count"] == 1
    assert again.json()["reused"] is True

    pending = client.get("/api/evaluations/examples?mode=fault_analysis&status=pending&limit=1")
    assert pending.status_code == 200
    assert pending.json()["total"] == 1
    review_id = pending.json()["items"][0]["review_id"]
    summary = client.get("/api/evaluations").json()
    assert summary["metric_status"]["retrieval_recall_at_k"] == "insufficient_labels"
    assert summary["metric_sample_counts"]["reviewed_evidence_labels"] == 0

    saved = client.put(f"/api/evaluations/examples/{review_id}/review", json={
        "reviewer_id": "local reviewer",
        "evidence_labels": [{"evidence_id": "evidence-1", "claim_id": "claim-1", "label": "supported"}],
        "expected_retrieval_ids": ["evidence-1"], "note": "Reviewed snapshot.",
    })
    assert saved.status_code == 200
    assert saved.json()["label_version"] == 1
    history = client.get(f"/api/evaluations/examples/{review_id}/history")
    assert history.json()[0]["reviewer_id"] == "local reviewer"
    assert client.get("/api/evaluations/examples?status=reviewed").json()["total"] == 1


def test_evaluation_api_rejects_invalid_bundle_and_review_ids(tmp_path):
    client, _ = _client(tmp_path)
    invalid_bundle = _bundle()
    invalid_bundle["examples"][0]["evidence"] = [{"excerpt": "missing ID"}]
    assert client.post("/api/evaluations/review-bundles", json=invalid_bundle).status_code == 422
    client.post("/api/evaluations/review-bundles", json=_bundle())
    review_id = client.get("/api/evaluations/examples").json()["items"][0]["review_id"]
    bad = client.put(f"/api/evaluations/examples/{review_id}/review", json={
        "reviewer_id": "reviewer", "evidence_labels": [], "expected_retrieval_ids": ["not-in-snapshot"],
    })
    assert bad.status_code == 422
    assert client.get(f"/api/evaluations/examples/{review_id}/history").json() == []
    assert client.get("/api/evaluations/examples/missing/history").status_code == 404

