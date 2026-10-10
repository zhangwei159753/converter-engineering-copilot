"""Validated request, evidence, and report schemas."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskMode(str, Enum):
    """Supported converter copilot workflows."""

    FAULT_ANALYSIS = "fault_analysis"
    TECHNICAL_RESEARCH = "technical_research"
    CASE_RETRIEVAL = "case_retrieval"


class FaultAnalysisInput(BaseModel):
    """Inputs for evidence-grounded fault analysis."""

    equipment_type: str = Field(min_length=1)
    symptom: str = Field(min_length=1)
    operating_conditions: dict[str, object] = Field(default_factory=dict)
    fault_code: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)


class TechnicalResearchInput(BaseModel):
    """Inputs for technical literature and experiment research."""

    question: str = Field(min_length=1)
    scope: str | None = None
    time_range: str | None = None
    comparison_dimensions: list[str] = Field(default_factory=list)


class CaseRetrievalInput(BaseModel):
    """Inputs for finding similar maintenance cases."""

    symptom: str = Field(min_length=1)
    equipment_type: str | None = None
    operating_conditions: dict[str, object] = Field(default_factory=dict)
    case_collection_ids: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """Source excerpt used to support or qualify a report claim."""

    evidence_id: str
    task_id: str
    title: str
    source_type: str
    excerpt: str
    url: str | None = None
    document_id: str | None = None
    locator: str | None = None
    published_at: datetime | None = None
    accessed_at: datetime
    related_claim_ids: list[str] = Field(default_factory=list)
    source_status: Literal["available", "conflicting", "unavailable"]


class DocumentChunk(BaseModel):
    """A source document segment stored in the local retrieval index."""

    chunk_id: str
    document_id: str
    filename: str
    text: str
    locator: str
    metadata: dict[str, str] = Field(default_factory=dict)


class RetrievedChunk(DocumentChunk):
    """A document chunk with its retrieval similarity score."""

    score: float


class Claim(BaseModel):
    """A fact, inference, or recommendation in a generated report."""

    claim_id: str
    text: str
    claim_type: Literal["fact", "inference", "recommendation"]
    evidence_ids: list[str] = Field(default_factory=list)
    verification_status: Literal["supported", "conflicting", "unverified"] = "unverified"


class FaultHypothesis(BaseModel):
    """Candidate cause and checks for a reported fault symptom."""

    hypothesis: str
    mechanism: str
    confidence: Literal["low", "medium", "high"]
    confidence_basis: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    opposing_evidence_ids: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class FaultAnalysisReport(BaseModel):
    """Structured fault analysis result."""

    hypotheses: list[FaultHypothesis] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class ResearchComparison(BaseModel):
    """One comparable method, result, or technical approach."""

    item: str
    method_or_value: str
    experimental_conditions: str | None = None
    datasets: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class TechnicalResearchReport(BaseModel):
    """Structured technical research result."""

    comparisons: list[ResearchComparison] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    experiment_steps: list[str] = Field(default_factory=list)


class CaseMatch(BaseModel):
    """A prior maintenance case and its applicability limits."""

    case_id: str
    similarity_score: float = Field(ge=0, le=1)
    matching_fields: list[str] = Field(default_factory=list)
    differing_fields: list[str] = Field(default_factory=list)
    applicability_limitations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    equipment_type: str | None = None
    operating_conditions: dict[str, object] = Field(default_factory=dict)
    symptom: str | None = None
    cause: str | None = None
    validation_result: str | None = None
    handling_result: str | None = None


class CaseRetrievalReport(BaseModel):
    """Structured similar-case retrieval result."""

    matches: list[CaseMatch] = Field(default_factory=list)


class CaseRecord(BaseModel):
    """Curated maintenance-case metadata stored locally."""

    case_id: str
    equipment_type: str | None = None
    operating_conditions: dict[str, object] = Field(default_factory=dict)
    symptom: str
    cause: str | None = None
    validation_result: str | None = None
    handling_result: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ImportStatus(str, Enum):
    """Durable lifecycle state for one validated source import."""

    VALIDATED = "validated"
    INDEXING = "indexing"
    COMPLETE = "complete"
    FAILED = "failed"
    NEEDS_REPAIR = "needs_repair"
    DELETING = "deleting"
    DELETED = "deleted"


class ImportRecord(BaseModel):
    """Private import ledger record; source_path must not be returned by APIs."""

    import_id: str
    document_id: str
    content_sha256: str
    source_name: str
    file_type: str
    source_path: str
    parser_version: str
    chunker_version: str
    expected_chunk_count: int
    indexed_chunks: int = 0
    imported_cases: int = 0
    status: ImportStatus
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    case_candidates_ready: bool = False


class ImportRepairResult(BaseModel):
    """Safe result of replaying or inspecting one durable source import."""

    import_id: str
    status: ImportStatus
    expected_chunk_count: int
    indexed_chunks: int
    imported_cases: int
    error_code: str | None = None


_INPUT_MODELS = {
    TaskMode.FAULT_ANALYSIS: FaultAnalysisInput,
    TaskMode.TECHNICAL_RESEARCH: TechnicalResearchInput,
    TaskMode.CASE_RETRIEVAL: CaseRetrievalInput,
}
_REPORT_MODELS = {
    TaskMode.FAULT_ANALYSIS: FaultAnalysisReport,
    TaskMode.TECHNICAL_RESEARCH: TechnicalResearchReport,
    TaskMode.CASE_RETRIEVAL: CaseRetrievalReport,
}


class TaskCreate(BaseModel):
    """Validated task request, with inputs constrained to the selected mode."""

    model_config = ConfigDict(use_enum_values=False)

    mode: TaskMode
    inputs: FaultAnalysisInput | TechnicalResearchInput | CaseRetrievalInput

    @model_validator(mode="before")
    @classmethod
    def validate_mode_inputs(cls, value):
        """Coerce request inputs through the schema selected by task mode."""
        if not isinstance(value, dict) or "mode" not in value or "inputs" not in value:
            return value
        mode = TaskMode(value["mode"])
        value = dict(value)
        value["inputs"] = _INPUT_MODELS[mode].model_validate(value["inputs"])
        return value


class TaskRecord(BaseModel):
    """Persisted task lifecycle state."""

    task_id: str
    mode: TaskMode
    status: Literal["queued", "running", "completed", "partial", "failed"]
    inputs: dict[str, object]
    created_at: datetime
    updated_at: datetime
    attempt: int = 0
    max_attempts: int = 3
    lease_expires_at: datetime | None = None
    error_code: str | None = None


class TaskEvent(BaseModel):
    """Append-only progress event with a per-database monotonic ID."""

    event_id: int
    task_id: str
    stage: str
    status: str
    message: str
    created_at: datetime


class EvidenceCheckResult(BaseModel):
    """Deterministic evidence-link validation summary for a report."""

    supported_claim_ids: list[str] = Field(default_factory=list)
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    unverified_claim_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConverterReport(BaseModel):
    """Mode-specific result plus traceable claims and evidence."""

    task_id: str
    mode: TaskMode
    result: FaultAnalysisReport | TechnicalResearchReport | CaseRetrievalReport
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    completion_status: Literal["completed", "partial", "failed"]

    @model_validator(mode="before")
    @classmethod
    def validate_mode_result(cls, value):
        """Coerce report results through the schema selected by task mode."""
        if not isinstance(value, dict) or "mode" not in value or "result" not in value:
            return value
        mode = TaskMode(value["mode"])
        value = dict(value)
        value["result"] = _REPORT_MODELS[mode].model_validate(value["result"])
        return value
