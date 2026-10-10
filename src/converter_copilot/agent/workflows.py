"""Compiled workflows for supported converter engineering task modes."""

from dataclasses import dataclass
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from converter_copilot.agent.nodes import (
    make_clarification_node,
    make_generate_node,
    make_intake_node,
    make_research_node,
    make_technical_research_generate_node,
    make_technical_research_intake_node,
)
from converter_copilot.agent.state import ConverterState
from converter_copilot.domain.schemas import TaskMode


class SearchAdapter(Protocol):
    """Web or external-source search adapter consumed by graph nodes."""

    def search(self, query: str, *, limit: int = 5): ...


class VectorSearch(Protocol):
    """Local vector index interface consumed by graph nodes."""

    def search(self, query: str, *, limit: int = 5, filters: dict[str, str] | None = None): ...


@dataclass
class WorkflowDeps:
    """Injectable model and retrieval dependencies for deterministic tests."""

    model: BaseChatModel
    search: SearchAdapter
    vector_index: VectorSearch


def build_workflow(mode: TaskMode, deps: WorkflowDeps):
    """Compile the requested workflow with explicitly injected dependencies."""
    if mode not in {TaskMode.FAULT_ANALYSIS, TaskMode.TECHNICAL_RESEARCH}:
        raise NotImplementedError(f"Workflow mode {mode.value} is not registered yet.")
    graph = StateGraph(ConverterState)
    graph.add_node("research", make_research_node(deps))
    if mode == TaskMode.FAULT_ANALYSIS:
        graph.add_node("intake", make_intake_node())
        graph.add_node("clarify", make_clarification_node())
        graph.add_node("generate", make_generate_node(deps))
        graph.add_edge(START, "intake")
        graph.add_conditional_edges(
            "intake",
            lambda state: "clarify" if not state["inputs"].get("operating_conditions") else "research",
            {"clarify": "clarify", "research": "research"},
        )
        graph.add_edge("clarify", END)
    else:
        graph.add_node("intake", make_technical_research_intake_node())
        graph.add_node("generate", make_technical_research_generate_node(deps))
        graph.add_edge(START, "intake")
        graph.add_edge("intake", "research")
    graph.add_edge("research", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
