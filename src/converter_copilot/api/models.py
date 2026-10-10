"""Stable API response models."""

from pydantic import BaseModel, Field

from converter_copilot.domain.schemas import ImportStatus, TaskRecord


class TaskAccepted(BaseModel):
    """Task creation response returned before background execution finishes."""

    task: TaskRecord


class CaseImportResult(BaseModel):
    """Summary of an imported local document and extracted cases."""

    document_ids: list[str]
    chunk_count: int
    imported_case_count: int
    import_id: str
    reused: bool = False


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
    synthetic_demo: bool = False


class EvaluationReviewRequest(BaseModel):
    """Human review payload accepted by the local evaluation API."""

    reviewer_id: str = Field(min_length=1, max_length=120)
    evidence_labels: list[dict[str, str]] = Field(default_factory=list)
    expected_retrieval_ids: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)
