"""Stable API response models."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class WorkOrderReviewRequest(BaseModel):
    """Validated local annotation for a completed work-order analysis."""

    reviewer: str = Field(min_length=1, max_length=120)
    verdict: Literal["accepted", "needs_more_information", "rejected"]
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("reviewer")
    @classmethod
    def reviewer_must_not_be_blank(cls, value: str) -> str:
        """Trim reviewer attribution and reject whitespace-only input."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Reviewer is required.")
        return normalized

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        """Normalize an empty optional note to null."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
