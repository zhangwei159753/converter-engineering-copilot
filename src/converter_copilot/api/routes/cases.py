"""Case and source-document import endpoints."""

import hashlib
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from converter_copilot.api.models import CaseImportResult
from converter_copilot.api.runner import TaskRunner
from converter_copilot.config import ImportLimits, import_source_directory
from converter_copilot.domain.schemas import CaseRecord, ImportStatus
from converter_copilot.retrieval.case_extractor import extract_case_records
from converter_copilot.retrieval.import_validation import (
    ImportValidationError,
    retain_source,
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
        source_path = retain_source(validated, import_source_directory(runner.store.database_path.parent / "sources"))
        import_record, reused = runner.store.create_import(
            validated.import_id,
            content_sha256=validated.content_sha256,
            source_name=validated.filename,
            file_type=validated.file_type,
            parser_version=validated.parser_version,
            chunker_version=validated.chunker_version,
            expected_chunk_count=len(chunks),
            source_path=str(source_path),
        )
        if reused:
            if import_record.status.value == "complete":
                return CaseImportResult(
                    document_ids=[import_record.document_id],
                    chunk_count=import_record.indexed_chunks,
                    imported_case_count=import_record.imported_cases,
                )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "import_incomplete",
                    "message": "This file already has an incomplete import. Use the repair action before retrying.",
                    "filename": filename,
                    "row": None,
                },
            )

        candidate_cases: list[CaseRecord] = []
        if Path(filename).suffix.lower() in {".pdf", ".md"}:
            try:
                deps = runner.deps_provider()
                for chunk in chunks[:8]:
                    extracted = await extract_case_records(chunk.text, evidence_id=chunk.chunk_id, model=deps.model)
                    for index, case in enumerate(extracted, start=1):
                        stable_id = hashlib.sha256(
                            f"{validated.import_id}:{chunk.chunk_id}:{index}".encode()
                        ).hexdigest()[:24]
                        candidate_cases.append(case.model_copy(update={"case_id": f"case-{stable_id}"}))
            except RuntimeError as error:
                runner.store.update_import_status(
                    validated.import_id, ImportStatus.FAILED, indexed_chunks=0, imported_cases=0,
                    error_code="case_extraction_failed",
                )
                raise HTTPException(status_code=503, detail="Case extraction service is unavailable.") from error
        elif Path(filename).suffix.lower() == ".csv":
            for row_number, row in enumerate(validated.case_rows, start=2):
                stable_id = hashlib.sha256(f"{validated.import_id}:{row_number}".encode()).hexdigest()[:24]
                candidate_cases.append(CaseRecord(
                    case_id=f"case-{stable_id}",
                    equipment_type=row.get("equipment_type") or None,
                    symptom=row["symptom"],
                    cause=row.get("cause") or None,
                    validation_result=row.get("validation_result") or None,
                    handling_result=row.get("handling_result") or None,
                    evidence_ids=[chunk.chunk_id for chunk in chunks if chunk.locator == f"row {row_number}"],
                ))

        runner.store.save_import_chunks(validated.import_id, chunks)
        runner.store.save_import_case_candidates(validated.import_id, candidate_cases)
        runner.store.update_import_status(
            validated.import_id, ImportStatus.INDEXING, indexed_chunks=0, imported_cases=0
        )
        try:
            deps = runner.deps_provider()
            deps.vector_index.add(chunks)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail="Vector index service is unavailable.") from error
        imported_cases = runner.store.promote_import_case_candidates(validated.import_id)
        runner.store.update_import_status(
            validated.import_id,
            ImportStatus.COMPLETE,
            indexed_chunks=len(chunks),
            imported_cases=imported_cases,
        )
        return CaseImportResult(
            document_ids=sorted({chunk.document_id for chunk in chunks}),
            chunk_count=len(chunks),
            imported_case_count=imported_cases,
        )

    @router.get("")
    def list_cases():
        return runner.store.list_cases()

    return router
