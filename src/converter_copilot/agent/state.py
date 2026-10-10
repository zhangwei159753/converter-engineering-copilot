"""Shared LangGraph state for converter copilot task execution."""

from operator import add
from typing import Annotated, TypedDict

from converter_copilot.domain.agent_audit import AgentRunRecord, HypothesisBranch
from converter_copilot.domain.schemas import ConverterReport, Evidence, FaultHypothesis


class ConverterState(TypedDict, total=False):
    """Inputs and accumulated outputs shared by workflow nodes."""

    task_id: str
    inputs: dict[str, object]
    queries: list[str]
    evidence: list[Evidence]
    agent_runs: Annotated[list[AgentRunRecord], add]
    hypothesis_branches: list[HypothesisBranch]
    hypothesis_candidates: list[FaultHypothesis]
    analysis_checks: list[str]
    agent_budget_remaining: int
    agent_warnings: Annotated[list[str], add]
    report: ConverterReport | None
    clarification_questions: list[str]
    quality_warnings: list[str]
