"""Case and source-document import endpoints."""

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from converter_copilot.api.models import CaseImportResult
from converter_copilot.api.runner import TaskRunner
from converter_copilot.config import ImportLimits
from converter_copilot.domain.schemas import CaseRecord
from converter_copilot.retrieval.case_extractor import extract_case_records
from converter_copilot.retrieval.import_validation import (
    ImportValidationError,
    validate_upload,
)


def build_case_router(runner: TaskRunner) -> APIRouter:
    """Build source and maintenance-case endpoints."""
    router = APIRouter(prefix="/api/cases", tags=["cases"])

    @router.post("/import", response_model=CaseImportResult, status_code=201)
    async def import_cases(file: UploadFile = File(...)):
        filename = Path((file.filename or "upload").replace("\\", "/")).name
        limits = ImportLimits.from_env()
        body: list[bytes] = []
        total_bytes = 0
        while True:
            read_size = min(64 * 1024, limits.max_bytes + 1 - total_bytes)
            if read_size <= 0:
                read_size = 1
            chunk = await file.read(read_size)
            if not chunk:
                break
            body.append(chunk)
            total_bytes += len(chunk)
            if total_bytes > limits.max_bytes:
                error = ImportValidationError(
                    "file_too_large",
                    f"File exceeds the {limits.max_bytes}-byte upload limit.",
                    filename,
                    status_code=413,
                )
                raise HTTPException(status_code=error.status_code, detail=error.detail) from error
        payload = b"".join(body)
        try:
            validated = validate_upload(filename, payload, limits)
        except ImportValidationError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error

        filename = validated.filename
        chunks = validated.chunks
        try:
            deps = runner.deps_provider()
            deps.vector_index.add(chunks)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        imported_cases = 0
        if Path(filename).suffix.lower() in {".pdf", ".md"}:
            try:
                for chunk in chunks[:8]:
                    extracted = await extract_case_records(chunk.text, evidence_id=chunk.chunk_id, model=deps.model)
                    for case in extracted:
                        runner.store.save_case(case)
                        imported_cases += 1
            except RuntimeError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        if Path(filename).suffix.lower() == ".csv":
            for row_number, row in enumerate(validated.case_rows, start=2):
                case = CaseRecord(
                    case_id=f"case-{uuid4().hex[:12]}",
                    equipment_type=row.get("equipment_type") or None,
                    symptom=row["symptom"],
                    cause=row.get("cause") or None,
                    validation_result=row.get("validation_result") or None,
                    handling_result=row.get("handling_result") or None,
                    evidence_ids=[chunk.chunk_id for chunk in chunks if chunk.locator == f"row {row_number}"],
                )
                runner.store.save_case(case)
                imported_cases += 1
        return CaseImportResult(
            document_ids=sorted({chunk.document_id for chunk in chunks}),
            chunk_count=len(chunks),
            imported_case_count=imported_cases,
        )

    @router.get("")
    def list_cases():
        return runner.store.list_cases()

    return router
