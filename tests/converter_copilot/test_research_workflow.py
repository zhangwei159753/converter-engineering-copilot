import asyncio

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.domain.schemas import (
    Evidence,
    ResearchComparison,
    RetrievedChunk,
    TaskMode,
    TechnicalResearchReport,
)


class FakeStructuredModel:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        return self.result


class FakeSearch:
    def __init__(self, results=None):
        self.results = results or []

    def search(self, _query, *, limit=5):
        return self.results[:limit]


class FakeVector:
    def __init__(self, results=None):
        self.results = results or []

    def search(self, _query, *, limit=5, filters=None):
        return self.results[:limit]


def _deps(model, search=None, vector=None):
    return WorkflowDeps(model=model, search=search or FakeSearch(), vector_index=vector or FakeVector())


def _state():
    return {
        "task_id": "research-1",
        "inputs": {
            "question": "Compare two-level and three-level inverter topologies",
            "scope": "grid-connected PV",
            "comparison_dimensions": ["efficiency", "switching loss"],
        },
    }


def test_research_comparison_retains_methods_conditions_and_citations():
    model = FakeStructuredModel(
        TechnicalResearchReport(
            comparisons=[
                ResearchComparison(
                    item="NPC three-level inverter",
                    method_or_value="Lower output voltage THD under matched filtering.",
                    experimental_conditions="400 V DC bus, 10 kHz switching frequency",
                    datasets=["simulation benchmark"],
                    evidence_ids=["paper-1"],
                )
            ],
            limitations=["Results depend on modulation strategy."],
            experiment_steps=["Match switching frequency", "Measure efficiency and THD"],
        )
    )
    evidence = Evidence(
        evidence_id="paper-1", task_id="research-1", title="Topology comparison paper", source_type="web",
        excerpt="Matched simulation conditions.", url="https://example.test/paper", accessed_at="2026-10-10T10:00:00Z", source_status="available",
    )
    graph = build_workflow(TaskMode.TECHNICAL_RESEARCH, _deps(model, search=FakeSearch([evidence])))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].result.comparisons[0].experimental_conditions.startswith("400 V")
    assert state["report"].claims[0].verification_status == "supported"
    assert state["report"].result.experiment_steps


def test_missing_source_metadata_leaves_comparison_unverified():
    model = FakeStructuredModel(
        TechnicalResearchReport(comparisons=[ResearchComparison(item="NPC", method_or_value="Compared", evidence_ids=["paper-no-url"])])
    )
    evidence = Evidence(
        evidence_id="paper-no-url", task_id="research-1", title="Unknown page", source_type="web",
        excerpt="A claim without a traceable URL.", accessed_at="2026-10-10T10:00:00Z", source_status="available",
    )
    graph = build_workflow(TaskMode.TECHNICAL_RESEARCH, _deps(model, search=FakeSearch([evidence])))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].claims[0].verification_status == "unverified"
    assert any("URL" in warning for warning in state["report"].quality_warnings)


def test_contradictory_findings_are_flagged_and_report_is_partial():
    model = FakeStructuredModel(
        TechnicalResearchReport(comparisons=[ResearchComparison(item="NPC", method_or_value="Higher loss", evidence_ids=["paper-conflict"])])
    )
    evidence = Evidence(
        evidence_id="paper-conflict", task_id="research-1", title="Conflicting paper", source_type="web",
        excerpt="Opposing efficiency result.", url="https://example.test/conflict", accessed_at="2026-10-10T10:00:00Z", source_status="conflicting",
    )
    graph = build_workflow(TaskMode.TECHNICAL_RESEARCH, _deps(model, search=FakeSearch([evidence])))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].claims[0].verification_status == "conflicting"
    assert state["report"].completion_status == "partial"


def test_empty_search_returns_partial_report_without_model_call():
    model = FakeStructuredModel(TechnicalResearchReport())
    graph = build_workflow(TaskMode.TECHNICAL_RESEARCH, _deps(model))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].result.comparisons == []
    assert state["report"].completion_status == "partial"
    assert model.calls == 0
