import asyncio

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.domain.schemas import (
    Evidence,
    FaultAnalysisReport,
    FaultHypothesis,
    RetrievedChunk,
    TaskMode,
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


class FakeVectorIndex:
    def __init__(self, results=None):
        self.results = results or []

    def search(self, _query, *, limit=5, filters=None):
        return self.results[:limit]


def _chunk(chunk_id="chunk-1"):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        filename="manual.pdf",
        text="The DC bus overvoltage can be related to braking unit failure.",
        locator="page 4",
        score=0.9,
    )


def _deps(model, search=None, vector=None):
    return WorkflowDeps(model=model, search=search or FakeSearch(), vector_index=vector or FakeVectorIndex())


def _state(conditions=None):
    return {
        "task_id": "task-1",
        "inputs": {
            "equipment_type": "PV inverter",
            "symptom": "DC bus overvoltage",
            "operating_conditions": conditions if conditions is not None else {"load": "50%"},
        },
    }


def test_fault_analysis_generates_typed_report_with_citations():
    model = FakeStructuredModel(
        FaultAnalysisReport(
            hypotheses=[
                FaultHypothesis(
                    hypothesis="Braking unit failure",
                    mechanism="The DC bus cannot dissipate regenerative energy.",
                    confidence="medium",
                    confidence_basis="One manual excerpt matched the symptom.",
                    supporting_evidence_ids=["chunk-1"],
                    checks=["Inspect the braking resistor circuit."],
                )
            ],
            checks=["Measure the DC bus voltage."],
        )
    )
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, vector=FakeVectorIndex([_chunk()])))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].result.hypotheses[0].hypothesis == "Braking unit failure"
    assert state["report"].claims[0].evidence_ids == ["chunk-1"]
    assert state["report"].claims[0].verification_status == "supported"
    assert state["report"].completion_status == "completed"


def test_missing_operating_conditions_requests_clarification_without_model_call():
    model = FakeStructuredModel(FaultAnalysisReport())
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model))
    state = asyncio.run(graph.ainvoke(_state(conditions={})))
    assert state["clarification_questions"]
    assert "operating_conditions" in state["report"].quality_warnings[0]
    assert state["report"].completion_status == "partial"
    assert model.calls == 0


def test_no_retrieval_results_returns_partial_report_without_unsupported_claims():
    model = FakeStructuredModel(FaultAnalysisReport())
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].result.hypotheses == []
    assert state["report"].claims == []
    assert state["report"].completion_status == "partial"
    assert model.calls == 0


def test_conflicting_web_source_marks_generated_claim_conflicting():
    evidence = Evidence(
        evidence_id="web-1",
        task_id="task-1",
        title="Conflicting technical bulletin",
        source_type="web",
        excerpt="This bulletin lists a different cause.",
        url="https://example.test/bulletin",
        accessed_at="2026-10-10T10:00:00Z",
        source_status="conflicting",
    )
    model = FakeStructuredModel(
        FaultAnalysisReport(
            hypotheses=[
                FaultHypothesis(
                    hypothesis="Check braking unit",
                    mechanism="Possible energy dissipation issue.",
                    confidence="low",
                    confidence_basis="Conflicting source exists.",
                    supporting_evidence_ids=["web-1"],
                    opposing_evidence_ids=[],
                    checks=[],
                )
            ]
        )
    )
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, search=FakeSearch([evidence])))
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].claims[0].verification_status == "conflicting"
    assert state["report"].completion_status == "partial"

