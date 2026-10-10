"""Typed audit records for bounded agent collaboration and reviewed memory."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AgentRole = Literal[
    "coordinator",
    "evidence_researcher",
    "hypothesis_analyst",
    "evidence_auditor",
    "report_synthesizer",
]


class AgentRunRecord(BaseModel):
    """One role execution with safe references and a concise UI summary."""

    agent_run_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=120)
    role: AgentRole
    stage: str = Field(min_length=1, max_length=80)
    status: Literal["queued", "running", "completed", "failed", "skipped", "incomplete"]
    parent_run_id: str | None = Field(default=None, max_length=160)
    branch_id: str | None = Field(default=None, max_length=160)
    input_refs: list[str] = Field(default_factory=list, max_length=100)
    output_refs: list[str] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=4000)
    error_code: str | None = Field(default=None, max_length=80)
    retry_count: int = Field(default=0, ge=0, le=10)
    started_at: datetime
    completed_at: datetime | None = None
    provider_usage: dict[str, int | float] | None = None

    @field_validator("input_refs", "output_refs")
    @classmethod
    def refs_are_nonempty_strings(cls, values: list[str]) -> list[str]:
        """Reject blank references so UI links never silently disappear."""
        if any(not value.strip() for value in values):
            raise ValueError("Agent references must be non-empty strings.")
        return values


class BranchAuditResult(BaseModel):
    """Structured, evidence-ID-only review returned for one hypothesis branch."""

    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    opposing_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    missing_fields: list[str] = Field(default_factory=list, max_length=100)
    checks: list[str] = Field(default_factory=list, max_length=100)
    rationale_summary: str = Field(default="", max_length=2000)

    @field_validator("supporting_evidence_ids", "opposing_evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, values: list[str]) -> list[str]:
        """Reject blank or duplicate evidence IDs in one branch audit."""
        if any(not value.strip() for value in values):
            raise ValueError("Evidence IDs must be non-empty strings.")
        if len(values) != len(set(values)):
            raise ValueError("Evidence IDs must be unique within a branch audit.")
        return values


class HypothesisBranch(BaseModel):
    """A bounded fault candidate and its evidence audit outcome."""

    branch_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=120)
    parent_branch_id: str | None = Field(default=None, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=2000)
    mechanism_summary: str = Field(default="", max_length=4000)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    opposing_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    applicability: Literal["direct", "related", "cross_equipment", "unknown"] = "unknown"
    missing_fields: list[str] = Field(default_factory=list, max_length=100)
    checks: list[str] = Field(default_factory=list, max_length=100)
    gate_status: Literal["pending", "qualified", "blocked", "review_incomplete"] = "pending"
    disposition: Literal["retained", "omitted", "needs_more_information"] = "needs_more_information"
    ordinal: int = Field(ge=0, le=2)

    @field_validator("supporting_evidence_ids", "opposing_evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, values: list[str]) -> list[str]:
        """Reject blank or duplicate evidence IDs within one branch."""
        if any(not value.strip() for value in values):
            raise ValueError("Evidence IDs must be non-empty strings.")
        if len(values) != len(set(values)):
            raise ValueError("Evidence IDs must be unique within a branch.")
        return values


class MemoryNode(BaseModel):
    """Controlled entity in the locally stored engineering memory graph."""

    node_id: str = Field(min_length=1, max_length=240)
    node_type: Literal["equipment", "symptom", "condition", "cause", "work_order", "evidence"]
    canonical_key: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    synthetic_demo: bool = False


class MemoryEdge(BaseModel):
    """A provenance-linked relation projected from one accepted review."""

    edge_id: str = Field(min_length=1, max_length=240)
    source_node_id: str = Field(min_length=1, max_length=240)
    relation_type: Literal[
        "has_symptom",
        "occurred_under",
        "associated_with_cause",
        "belongs_to_work_order",
        "supported_by",
    ]
    target_node_id: str = Field(min_length=1, max_length=240)
    source_task_id: str = Field(min_length=1, max_length=120)
    source_work_order_id: str = Field(min_length=1, max_length=120)
    source_review_version: int = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    synthetic_demo: bool = False
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, values: list[str]) -> list[str]:
        """Reject blank or duplicate evidence IDs within one memory edge."""
        if any(not value.strip() for value in values):
            raise ValueError("Evidence IDs must be non-empty strings.")
        if len(values) != len(set(values)):
            raise ValueError("Evidence IDs must be unique within a memory edge.")
        return values
