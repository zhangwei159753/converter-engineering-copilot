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
        self.messages = []

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        self.messages.append(_messages)
        return self.result


class SequenceStructuredModel(FakeStructuredModel):
    def __init__(self, results):
        super().__init__(None)
        self.results = list(results)

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages.append(messages)
        return self.results.pop(0)


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
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, vector=FakeVectorIndex([_chunk()])), agent_strategy="baseline")
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].result.hypotheses[0].hypothesis == "Braking unit failure"
    assert state["report"].claims[0].evidence_ids == ["chunk-1"]
    assert state["report"].claims[0].verification_status == "supported"
    assert state["report"].completion_status == "completed"


def test_uncited_hypotheses_are_omitted_but_their_checks_remain_visible():
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
                ),
                FaultHypothesis(
                    hypothesis="Unrelated coolant flow issue",
                    mechanism="Low flow could raise module temperature.",
                    confidence="low",
                    confidence_basis="No direct source was retrieved.",
                    checks=["Review the coolant flow indicator."],
                ),
            ],
            checks=["Measure the DC bus voltage."],
        )
    )
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, vector=FakeVectorIndex([_chunk()])), agent_strategy="baseline")
    report = asyncio.run(graph.ainvoke(_state()))["report"]

    assert [item.hypothesis for item in report.result.hypotheses] == ["Braking unit failure"]
    assert [claim.text for claim in report.claims] == ["Braking unit failure"]
    assert "Review the coolant flow indicator." in report.result.checks
    assert any("Omitted 1 hypothesis" in warning for warning in report.quality_warnings)
    assert report.completion_status == "partial"


def test_schema_validation_error_retries_once_and_marks_recovered_report_partial():
    valid = FaultAnalysisReport(
        hypotheses=[FaultHypothesis(
            hypothesis="Braking unit failure", mechanism="Possible energy dissipation issue.",
            confidence="low", confidence_basis="One matching source.",
            supporting_evidence_ids=["chunk-1"], checks=[],
        )]
    )
    model = SequenceStructuredModel([{"hypotheses": [{"confidence": "certain"}]}, valid])
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, vector=FakeVectorIndex([_chunk()])), agent_strategy="baseline")

    report = asyncio.run(graph.ainvoke(_state()))["report"]

    assert model.calls == 2
    assert report.result.hypotheses[0].hypothesis == "Braking unit failure"
    assert report.completion_status == "partial"
    assert any("schema" in warning.lower() and "retry" in warning.lower() for warning in report.quality_warnings)


def test_tentative_synthetic_source_cannot_support_definitive_cause():
    source = _chunk()
    source.text = "cause: 合成测试假设，冷却回路状态可能与温升相关；不是现场验证结论。"
    model = FakeStructuredModel(FaultAnalysisReport(hypotheses=[FaultHypothesis(
        hypothesis="冷却回路异常导致功率模块温升", mechanism="冷却不足导致温度升高",
        confidence="high", confidence_basis="单条合成假设。", supporting_evidence_ids=["chunk-1"],
        checks=["核对温度趋势。"],
    )]))
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, vector=FakeVectorIndex([source])), agent_strategy="baseline")

    report = asyncio.run(graph.ainvoke(_state()))["report"]

    assert report.result.hypotheses == []
    assert report.claims == []
    assert "核对温度趋势。" in report.result.checks
    assert any("causal" in warning.lower() for warning in report.quality_warnings)


def test_missing_operating_conditions_requests_clarification_without_model_call():
    model = FakeStructuredModel(FaultAnalysisReport())
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model), agent_strategy="baseline")
    request = _state(conditions={})
    request["inputs"]["fault_code"] = "DC_OV_017"
    state = asyncio.run(graph.ainvoke(request))
    assert state["clarification_questions"]
    assert any("母线波形" in item and "功率方向" in item for item in state["report"].clarification_questions)
    assert "operating_conditions" in state["report"].quality_warnings[0]
    assert state["report"].completion_status == "partial"
    assert model.calls == 0


def test_no_retrieval_results_returns_partial_report_without_unsupported_claims():
    model = FakeStructuredModel(FaultAnalysisReport())
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model), agent_strategy="baseline")
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
    graph = build_workflow(TaskMode.FAULT_ANALYSIS, _deps(model, search=FakeSearch([evidence])), agent_strategy="baseline")
    state = asyncio.run(graph.ainvoke(_state()))
    assert state["report"].claims[0].verification_status == "conflicting"
    assert state["report"].completion_status == "partial"

