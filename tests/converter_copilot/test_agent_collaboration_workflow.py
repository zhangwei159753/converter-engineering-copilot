import asyncio

import pytest

from converter_copilot.agent.workflows import WorkflowDeps, build_workflow
from converter_copilot.config import AgentBudget
from converter_copilot.domain.agent_audit import BranchAuditResult
from converter_copilot.domain.schemas import (
    Evidence,
    FaultAnalysisReport,
    FaultHypothesis,
    TaskMode,
)


class FakeSearch:
    def search(self, _query, *, limit=5):
        return [
            Evidence(
                evidence_id="ev-1",
                task_id="source-task",
                title="Converter service manual",
                source_type="web",
                excerpt="A DC bus overvoltage can occur when regenerative energy is not dissipated.",
                url="https://example.test/manual",
                accessed_at="2026-10-10T10:00:00Z",
                source_status="available",
            )][:limit]


class EmptyVector:
    def search(self, _query, *, limit=5, filters=None):
        return []


class SchemaModel:
    def __init__(self, *, audit_results, fail_audit_indexes=()):
        self.audit_results = list(audit_results)
        self.fail_audit_indexes = set(fail_audit_indexes)
        self.audit_calls = 0
        self.analysis_calls = 0

    def with_structured_output(self, schema):
        model = self

        class BoundModel:
            async def ainvoke(self, _messages):
                if schema is FaultAnalysisReport:
                    model.analysis_calls += 1
                    return FaultAnalysisReport(
                        hypotheses=[
                            FaultHypothesis(
                                hypothesis=f"Candidate {index}",
                                mechanism="Regenerative energy is not dissipated.",
                                confidence="low",
                                confidence_basis="One retrieved source is available for review.",
                                supporting_evidence_ids=["ev-1"],
                                checks=[f"Check waveform {index}"],
                            )
                            for index in range(1, 5)
                        ],
                        checks=["Record the alarm timeline."],
                    )
                if schema is BranchAuditResult:
                    index = model.audit_calls
                    model.audit_calls += 1
                    if index in model.fail_audit_indexes:
                        raise RuntimeError("private provider error")
                    return model.audit_results[index]
                raise AssertionError(f"Unexpected structured schema: {schema}")

        return BoundModel()


def _state(conditions=None):
    return {
        "task_id": "task-collaboration",
        "inputs": {
            "equipment_type": "PV inverter",
            "symptom": "DC bus overvoltage",
            "operating_conditions": conditions if conditions is not None else {"load": "50%"},
        },
    }


def _audit(support=("ev-1",), oppose=()):
    return BranchAuditResult(
        supporting_evidence_ids=list(support),
        opposing_evidence_ids=list(oppose),
        missing_fields=[],
        checks=["Verify the measured DC bus waveform."],
        rationale_summary="The candidate was checked against the retrieved source excerpt.",
    )


def _graph(model, *, budget=None, events=None, branches=None, search=None):
    deps = WorkflowDeps(
        model=model,
        search=search or FakeSearch(),
        vector_index=EmptyVector(),
        agent_event_sink=events.append if events is not None else None,
        agent_result_sink=branches.append if branches is not None else None,
        agent_budget=budget or AgentBudget(),
    )
    return build_workflow(TaskMode.FAULT_ANALYSIS, deps)


def test_collaborative_workflow_caps_candidates_and_emits_role_runs():
    model = SchemaModel(audit_results=[_audit(), _audit(), _audit()])
    events = []
    branch_snapshots = []

    state = asyncio.run(_graph(model, events=events, branches=branch_snapshots).ainvoke(_state()))

    assert model.analysis_calls == 1
    assert model.audit_calls == 3
    assert len(state["hypothesis_branches"]) == 3
    assert len(state["report"].result.hypotheses) == 3
    assert {run.role for run in state["agent_runs"]} == {
        "coordinator", "evidence_researcher", "hypothesis_analyst", "evidence_auditor", "report_synthesizer"
    }
    assert branch_snapshots[-1] == state["hypothesis_branches"]
    assert any(record.status == "running" for record in events)
    assert all(record.status in {"running", "completed", "failed", "incomplete", "skipped"} for record in events)


def test_hypothesis_gate_blocks_unknown_evidence_ids():
    model = SchemaModel(audit_results=[_audit(support=("not-in-task",))] * 3)

    state = asyncio.run(_graph(model).ainvoke(_state()))

    assert state["report"].result.hypotheses == []
    assert state["hypothesis_branches"][0].gate_status == "blocked"
    assert state["hypothesis_branches"][0].disposition == "omitted"
    assert state["report"].completion_status == "partial"


def test_opposing_evidence_is_preserved_but_branch_is_not_promoted():
    model = SchemaModel(audit_results=[_audit(oppose=("ev-1",))] * 3)

    state = asyncio.run(_graph(model).ainvoke(_state()))

    branch = state["hypothesis_branches"][0]
    assert branch.opposing_evidence_ids == ["ev-1"]
    assert branch.gate_status == "blocked"
    assert branch.disposition == "needs_more_information"
    assert not state["report"].result.hypotheses
    assert state["report"].completion_status == "partial"


def test_source_marked_conflicting_cannot_qualify_supporting_branch():
    class ConflictingSearch(FakeSearch):
        def search(self, query, *, limit=5):
            return [item.model_copy(update={"source_status": "conflicting"}) for item in super().search(query, limit=limit)]

    model = SchemaModel(audit_results=[_audit()] * 3)
    state = asyncio.run(_graph(model, search=ConflictingSearch()).ainvoke(_state()))

    assert state["hypothesis_branches"][0].gate_status == "blocked"
    assert state["hypothesis_branches"][0].disposition == "needs_more_information"
    assert not state["report"].result.hypotheses
    assert state["report"].completion_status == "partial"


def test_auditor_failure_marks_only_that_branch_incomplete_and_sanitizes_error():
    model = SchemaModel(audit_results=[_audit()] * 3, fail_audit_indexes={0})
    events = []

    state = asyncio.run(_graph(model, events=events).ainvoke(_state()))

    failed = next(run for run in state["agent_runs"] if run.role == "evidence_auditor" and run.status == "failed")
    assert failed.error_code == "agent_audit_failed"
    assert "private provider error" not in failed.summary
    assert state["hypothesis_branches"][0].gate_status == "review_incomplete"
    assert state["report"].completion_status == "partial"
    assert model.audit_calls == 3


def test_task_model_call_budget_skips_unfunded_auditors():
    model = SchemaModel(audit_results=[_audit()])
    budget = AgentBudget(max_model_calls=2)

    state = asyncio.run(_graph(model, budget=budget).ainvoke(_state()))

    assert model.analysis_calls == 1
    assert model.audit_calls == 1
    assert [branch.gate_status for branch in state["hypothesis_branches"]] == [
        "qualified", "review_incomplete", "review_incomplete"
    ]
    assert state["report"].completion_status == "partial"


def test_missing_operating_conditions_skips_collaborative_model_calls():
    model = SchemaModel(audit_results=[])

    state = asyncio.run(_graph(model).ainvoke(_state(conditions={})))

    assert state["report"].completion_status == "partial"
    assert state["clarification_questions"]
    assert model.analysis_calls == model.audit_calls == 0
    assert state.get("hypothesis_branches", []) == []


def test_agent_budget_rejects_values_above_hard_ceiling():
    with pytest.raises(ValueError, match="hard maximum"):
        AgentBudget(max_hypotheses=4)
