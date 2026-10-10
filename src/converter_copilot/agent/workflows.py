"""Compiled workflows for supported converter engineering task modes."""

from dataclasses import dataclass, field
from typing import Callable, Protocol

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from converter_copilot.agent.collaboration import (
    make_branch_audit_node,
    make_collaborative_intake_node,
    make_collaborative_research_node,
    make_evidence_gate_node,
    make_hypothesis_node,
    make_synthesis_node,
)
from converter_copilot.agent.nodes import (
    make_case_retrieval_node,
    make_clarification_node,
    make_generate_node,
    make_intake_node,
    make_research_node,
    make_technical_research_generate_node,
    make_technical_research_intake_node,
)
from converter_copilot.agent.state import ConverterState
from converter_copilot.config import AgentBudget
from converter_copilot.domain.agent_audit import AgentRunRecord, HypothesisBranch
from converter_copilot.domain.schemas import TaskMode


class SearchAdapter(Protocol):
    """Web or external-source search adapter consumed by graph nodes."""

    def search(self, query: str, *, limit: int = 5):
        """Return up to the requested number of external source results."""
        ...


class VectorSearch(Protocol):
    """Local vector index interface consumed by graph nodes."""

    def search(self, query: str, *, limit: int = 5, filters: dict[str, str] | None = None):
        """Search local vectors with optional metadata filters."""
        ...


@dataclass
class WorkflowDeps:
    """Injectable model and retrieval dependencies for deterministic tests."""

    model: BaseChatModel
    search: SearchAdapter
    vector_index: VectorSearch
    case_store: object | None = None
    agent_event_sink: Callable[[AgentRunRecord], None] | None = None
    agent_result_sink: Callable[[list[HypothesisBranch]], None] | None = None
    agent_budget: AgentBudget = field(default_factory=AgentBudget)


def build_workflow(mode: TaskMode, deps: WorkflowDeps, *, agent_strategy: str = "collaborative"):
    """Compile the requested workflow with explicitly injected dependencies."""
    if mode not in {TaskMode.FAULT_ANALYSIS, TaskMode.TECHNICAL_RESEARCH, TaskMode.CASE_RETRIEVAL}:
        raise NotImplementedError(f"Workflow mode {mode.value} is not registered yet.")
    graph = StateGraph(ConverterState)
    if mode == TaskMode.CASE_RETRIEVAL:
        if agent_strategy != "collaborative":
            raise ValueError("agent_strategy is only configurable for fault-analysis workflows.")
        graph.add_node("case_retrieval", make_case_retrieval_node(deps))
        graph.add_edge(START, "case_retrieval")
        graph.add_edge("case_retrieval", END)
        return graph.compile()
    if mode == TaskMode.FAULT_ANALYSIS:
        if agent_strategy not in {"collaborative", "baseline"}:
            raise ValueError("agent_strategy must be 'collaborative' or 'baseline'.")
        if agent_strategy == "collaborative":
            graph.add_node("intake", make_collaborative_intake_node(deps, deps.agent_budget))
            graph.add_node("research", make_collaborative_research_node(deps))
            graph.add_node("hypothesize", make_hypothesis_node(deps, deps.agent_budget))
            graph.add_node("audit", make_branch_audit_node(deps, deps.agent_budget))
            graph.add_node("evidence_gate", make_evidence_gate_node(deps))
            graph.add_node("synthesize", make_synthesis_node(deps))
            graph.add_node("clarify", make_clarification_node())
            graph.add_edge(START, "intake")
            graph.add_conditional_edges(
                "intake",
                lambda state: "clarify" if not state["inputs"].get("operating_conditions") else "research",
                {"clarify": "clarify", "research": "research"},
            )
            graph.add_edge("clarify", END)
            graph.add_edge("research", "hypothesize")
            graph.add_edge("hypothesize", "audit")
            graph.add_edge("audit", "evidence_gate")
            graph.add_edge("evidence_gate", "synthesize")
            graph.add_edge("synthesize", END)
            return graph.compile()

        graph.add_node("research", make_research_node(deps))
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
        if agent_strategy != "collaborative":
            raise ValueError("agent_strategy is only configurable for fault-analysis workflows.")
        graph.add_node("research", make_research_node(deps))
        graph.add_node("intake", make_technical_research_intake_node())
        graph.add_node("generate", make_technical_research_generate_node(deps))
        graph.add_edge(START, "intake")
        graph.add_edge("intake", "research")
    graph.add_edge("research", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
