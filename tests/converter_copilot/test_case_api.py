from fastapi.testclient import TestClient

from converter_copilot.agent.workflows import WorkflowDeps
from converter_copilot.api.main import create_app
from converter_copilot.api.runner import TaskRunner
from converter_copilot.storage.sqlite_store import SQLiteStore


class EmptySearch:
    def search(self, query, *, limit=5):
        return []


class CaptureIndex:
    def __init__(self):
        self.added = []

    def search(self, query, *, limit=5, filters=None):
        return []

    def add(self, chunks):
        self.added.extend(chunks)


class NeverModel:
    def with_structured_output(self, schema):
        raise AssertionError("not used")


def _client(tmp_path):
    store = SQLiteStore(tmp_path / "cases.db")
    index = CaptureIndex()
    runner = TaskRunner(store=store, deps_provider=lambda: WorkflowDeps(NeverModel(), EmptySearch(), index))
    return TestClient(create_app(runner=runner)), store, index


def test_csv_case_import_indexes_chunks_and_persists_cases(tmp_path):
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import",
        files={"file": ("cases.csv", "symptom,equipment_type,cause\nDC overvoltage,PV inverter,braking unit fault\n", "text/csv")},
    )
    assert response.status_code == 201
    assert response.json()["document_ids"]
    assert len(index.added) == 1
    assert index.added[0].document_id == response.json()["document_ids"][0]
    assert client.get("/api/cases").json()[0]["symptom"] == "DC overvoltage"
    assert len(store.list_cases()) == 1


def test_case_import_rejects_unsupported_extension(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.post("/api/cases/import", files={"file": ("cases.xlsx", b"not supported")})
    assert response.status_code == 415


def test_case_import_returns_400_for_malformed_csv(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.post("/api/cases/import", files={"file": ("cases.csv", b'"unterminated')})
    assert response.status_code == 400
