"""Stable API response models."""

from pydantic import BaseModel

from converter_copilot.domain.schemas import ImportStatus, TaskRecord


class TaskAccepted(BaseModel):
    """Task creation response returned before background execution finishes."""

    task: TaskRecord


class CaseImportResult(BaseModel):
    """Summary of an imported local document and extracted cases."""

    document_ids: list[str]
    chunk_count: int
    imported_case_count: int


class ImportSummary(BaseModel):
    """Safe import status returned without exposing local source paths."""

    import_id: str
    document_id: str
    source_name: str
    file_type: str
    parser_version: str
    chunker_version: str
    expected_chunk_count: int
    indexed_chunks: int
    imported_cases: int
    status: ImportStatus
    created_at: str
    updated_at: str
    error_code: str | None = None
