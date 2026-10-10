"""Case and source-document import endpoints."""

import csv
import io
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from converter_copilot.api.models import CaseImportResult
from converter_copilot.api.runner import TaskRunner
from converter_copilot.domain.schemas import CaseRecord
from converter_copilot.retrieval.document_loader import DocumentLoadError, load_document


def build_case_router(runner: TaskRunner) -> APIRouter:
    """Build source and maintenance-case endpoints."""
    router = APIRouter(prefix="/api/cases", tags=["cases"])

    @router.post("/import", response_model=CaseImportResult, status_code=201)
    async def import_cases(file: UploadFile = File(...)):
        filename = Path(file.filename or "").name
        if Path(filename).suffix.lower() not in {".pdf", ".md", ".csv"}:
            raise HTTPException(status_code=415, detail="Supported formats are PDF, Markdown, and CSV.")
        payload = await file.read()
        try:
            with tempfile.TemporaryDirectory(prefix="converter-import-") as directory:
                path = Path(directory) / filename
                path.write_bytes(payload)
                chunks = load_document(path, task_id="case-import")
            deps = runner.deps_provider()
            deps.vector_index.add(chunks)
        except DocumentLoadError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        imported_cases = 0
        if Path(filename).suffix.lower() == ".csv":
            try:
                rows = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
                for row_number, row in enumerate(rows, start=2):
                    symptom = (row.get("symptom") or "").strip()
                    if not symptom:
                        continue
                    case = CaseRecord(
                        case_id=f"case-{uuid4().hex[:12]}",
                        equipment_type=(row.get("equipment_type") or "").strip() or None,
                        symptom=symptom,
                        cause=(row.get("cause") or "").strip() or None,
                        validation_result=(row.get("validation_result") or "").strip() or None,
                        handling_result=(row.get("handling_result") or "").strip() or None,
                        evidence_ids=[chunk.chunk_id for chunk in chunks if chunk.locator == f"row {row_number}"],
                    )
                    runner.store.save_case(case)
                    imported_cases += 1
            except (UnicodeDecodeError, csv.Error) as error:
                raise HTTPException(status_code=400, detail=f"Could not parse CSV: {error}") from error
        return CaseImportResult(
            document_ids=sorted({chunk.document_id for chunk in chunks}),
            chunk_count=len(chunks),
            imported_case_count=imported_cases,
        )

    @router.get("")
    def list_cases():
        return runner.store.list_cases()

    return router
