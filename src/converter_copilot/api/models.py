"""Stable API response models."""

from pydantic import BaseModel

from converter_copilot.domain.schemas import TaskRecord


class TaskAccepted(BaseModel):
    """Task creation response returned before background execution finishes."""

    task: TaskRecord


class CaseImportResult(BaseModel):
    """Summary of an imported local document and extracted cases."""

    document_ids: list[str]
    chunk_count: int
    imported_case_count: int
