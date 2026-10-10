import asyncio
from datetime import datetime, timezone

from converter_copilot.domain.schemas import (
    Claim,
    ConverterReport,
    Evidence,
    FaultAnalysisReport,
    TaskMode,
)
from converter_copilot.evaluation.metrics import compute_branch_metrics
from converter_copilot.evaluation.runner import evaluate_records


class StrategyGraph:
    def __init__(self, strategy, branch_status="qualified"):
        self.strategy = strategy
        self.branch_status = branch_status
        self.seen = []

    async def ainvoke(self, state, *, config):
        self.seen.append(state["inputs"])
        now = datetime.now(timezone.utc)
        evidence = Evidence(
            evidence_id="ev-1", task_id=state["task_id"], title="fixture manual",
            source_type="local_document", excerpt="synthetic excerpt", document_id="doc-1",
            locator="page 1", accessed_at=now, source_status="available",
        )
        report = ConverterReport(
            task_id=state["task_id"], mode=TaskMode.FAULT_ANALYSIS, result=FaultAnalysisReport(),
            claims=[Claim(claim_id="c1", text="fixture claim", claim_type="fact", evidence_ids=["ev-1"])],
            evidence=[evidence], completion_status="completed",
        )
        return {
            "report": report,
            "agent_runs": [{"role": "coordinator", "status": "completed", "started_at": now, "completed_at": now, "provider_usage": None}],
            "hypothesis_branches": [{
                "branch_id": f"{self.strategy}-branch", "supporting_evidence_ids": ["ev-1"],
                "opposing_evidence_ids": [], "applicability": "direct", "gate_status": self.branch_status,
            }],
            "agent_budget_remaining": 0,
        }


def test_branch_metrics_are_structural_and_keep_unlabeled_quality_null():
    result = compute_branch_metrics([
        {"supporting_evidence_ids": ["ev-1"], "opposing_evidence_ids": [], "applicability": "direct", "gate_status": "qualified"},
        {"supporting_evidence_ids": ["missing"], "opposing_evidence_ids": [], "applicability": "unknown", "gate_status": "review_incomplete"},
    ], reviewed_labels=[], evidence_ids=["ev-1"])
    assert result["evidence_id_integrity_rate"] == 0.5
    assert result["support_opposition_coverage"] == 1.0
    assert result["unsupported_candidate_share"] == 0.0
    assert result["applicability_warning_share"] == 0.5
    assert result["incomplete_review_share"] == 0.5
    assert result["human_labeled_branch_quality"] is None
    assert result["sample_counts"]["branches"] == 2


def test_branch_metrics_marks_references_invalid_when_known_inventory_is_empty():
    result = compute_branch_metrics([
        {"supporting_evidence_ids": ["ev-stale"], "opposing_evidence_ids": [], "gate_status": "blocked"}
    ], reviewed_labels=[], evidence_ids=[])
    assert result["evidence_id_integrity_rate"] == 0.0
    assert result["metric_reasons"]["evidence_id_integrity_rate"] is None


def test_strategy_comparison_runs_same_records_and_returns_paired_summaries_without_fake_quality():
    baseline = StrategyGraph("baseline")
    collaborative = StrategyGraph("collaborative", branch_status="review_incomplete")
    records = [
        {"example_id": "synth-1", "source_type": "synthetic_demo_fixture", "label_status": "unreviewed", "inputs": {"equipment_type": "CV-300", "symptom": "bus overvoltage"}},
        {"example_id": "synth-2", "source_type": "synthetic_demo_fixture", "label_status": "unreviewed", "inputs": {"equipment_type": "CV-500", "symptom": "start failure"}},
    ]
    result = asyncio.run(evaluate_records(
        mode="fault_analysis", records=records, graph=baseline,
        strategy_graphs={"baseline": baseline, "collaborative": collaborative},
    ))
    assert result["paired_comparison"]["paired_example_count"] == 2
    assert set(result["strategies"]) == {"baseline", "collaborative"}
    assert baseline.seen == collaborative.seen
    assert len(baseline.seen) == len(records)
    assert result["strategies"]["collaborative"]["agent_strategy"] == "collaborative"
    assert result["strategies"]["collaborative"]["branch_metrics"]["human_labeled_branch_quality"] is None
    assert result["strategies"]["collaborative"]["budget_exhaustion_count"] == 2
    assert result["strategies"]["collaborative"]["agent_run_counts"]["completed"] == 2
    assert result["strategies"]["collaborative"]["provider_usage"] is None
    assert result["strategies"]["collaborative"]["provider_usage_unavailable_reason"]
    assert "true" not in str(result["strategies"]["collaborative"]["branch_metrics"]).lower()
