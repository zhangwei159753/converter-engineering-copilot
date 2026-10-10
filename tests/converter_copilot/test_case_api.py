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


def test_identical_csv_upload_reuses_import_case_and_vector_identity(tmp_path):
    client, store, index = _client(tmp_path)
    upload = ("cases.csv", "symptom,equipment_type\nDC overvoltage,PV inverter\n", "text/csv")

    first = client.post("/api/cases/import", files={"file": upload})
    second = client.post("/api/cases/import", files={"file": upload})

    assert first.status_code == second.status_code == 201
    assert first.json()["document_ids"] == second.json()["document_ids"]
    assert len(index.added) == 1
    assert len(store.list_cases()) == 1


def test_case_import_rejects_unsupported_extension(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.post("/api/cases/import", files={"file": ("cases.xlsx", b"not supported")})
    assert response.status_code == 415


def test_case_import_returns_400_for_malformed_csv(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.post("/api/cases/import", files={"file": ("cases.csv", b'"unterminated')})
    assert response.status_code == 400


def test_invalid_csv_has_no_import_side_effects(tmp_path):
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import",
        files={"file": ("bad.csv", b'symptom,cause\n"unterminated', "text/csv")},
    )
    assert response.status_code == 400
    assert store.list_cases() == []
    assert index.added == []


def test_missing_symptom_reports_csv_row(tmp_path):
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import",
        files={"file": ("cases.csv", b"symptom,equipment_type,cause\n,PV inverter,overheat\n", "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "missing_required_field",
        "message": "CSV row 2 is missing the required symptom field.",
        "filename": "cases.csv",
        "row": 2,
    }
    assert store.list_cases() == []
    assert index.added == []


def test_duplicate_headers_are_rejected_before_import(tmp_path):
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import",
        files={"file": ("cases.csv", b"symptom,cause,symptom\nfirst,cause,second\n", "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "duplicate_csv_headers"
    assert store.list_cases() == []
    assert index.added == []


def test_file_byte_limit_is_enforced_before_unbounded_read(tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_BYTES", "8")
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import",
        files={"file": ("notes.md", b"# too large\ncontent", "text/markdown")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"
    assert store.list_cases() == []
    assert index.added == []


def test_pdf_page_limit_is_enforced(tmp_path, monkeypatch):
    import fitz

    monkeypatch.setenv("IMPORT_MAX_PDF_PAGES", "1")
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "page one")
    pdf.new_page().insert_text((72, 72), "page two")
    payload = pdf.tobytes()
    pdf.close()
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import", files={"file": ("manual.pdf", payload, "application/pdf")}
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "pdf_page_limit_exceeded"
    assert store.list_cases() == []
    assert index.added == []


def test_chunk_limit_is_enforced_before_import(tmp_path, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_CHUNKS", "1")
    client, store, index = _client(tmp_path)
    response = client.post(
        "/api/cases/import",
        files={"file": ("notes.md", b"first paragraph\n\nsecond paragraph", "text/markdown")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "chunk_limit_exceeded"
    assert store.list_cases() == []
    assert index.added == []


def test_markdown_case_import_extracts_structured_record_with_source_locator(tmp_path):
    class ExtractModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            return {"cases": [{
                "equipment_type": "PV inverter",
                "symptom": "DC bus overvoltage",
                "cause": "Braking resistor open circuit",
                "validation_result": "Resistance measurement was open",
                "handling_result": "Replaced resistor",
                "operating_conditions": {"load": "75%"},
            }]}

    store = SQLiteStore(tmp_path / "extracted-cases.db")
    index = CaptureIndex()
    deps = WorkflowDeps(model=ExtractModel(), search=EmptySearch(), vector_index=index)
    runner = TaskRunner(store=store, deps_provider=lambda: deps)
    with TestClient(create_app(runner=runner)) as client:
        response = client.post(
            "/api/cases/import",
            files={"file": ("service-note.md", "# Repair log\n\nDC bus overvoltage after startup; inspect braking resistor.\n", "text/markdown")},
        )
    assert response.status_code == 201
    saved = store.list_cases()[0]
    assert saved.symptom == "DC bus overvoltage"
    assert saved.evidence_ids == [index.added[0].chunk_id]
    assert response.json()["imported_case_count"] == 1
