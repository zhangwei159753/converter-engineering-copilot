"""Shared LangGraph state for converter copilot task execution."""

from typing import TypedDict

from converter_copilot.domain.schemas import ConverterReport, Evidence


class ConverterState(TypedDict, total=False):
    """Inputs and accumulated outputs shared by workflow nodes."""

    task_id: str
    inputs: dict[str, object]
    queries: list[str]
    evidence: list[Evidence]
    report: ConverterReport | None
    clarification_questions: list[str]
    quality_warnings: list[str]
