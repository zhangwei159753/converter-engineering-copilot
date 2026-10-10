from datetime import datetime, timezone
import asyncio

from converter_copilot.domain.schemas import (
    Claim,
    ConverterReport,
    Evidence,
    FaultAnalysisReport,
    TaskMode,
)
from converter_copilot.config import tracing_run_config
from converter_copilot.evaluation.runner import evaluate_records


class FakeGraph:
    def __init__(self, report=None, error=None):
        self.report = report
        self.error = error
        self.configs = []

    async def ainvoke(self, state, *, config):
        self.configs.append(config)
        if self.error:
            raise self.error
        report = self.report.model_copy(update={"task_id": state["task_id"]})
        return {"report": report}


def _report():
    now = datetime.now(timezone.utc)
    return ConverterReport(
        task_id="placeholder",
        mode=TaskMode.FAULT_ANALYSIS,
        result=FaultAnalysisReport(),
        claims=[Claim(claim_id="c1", text="example", claim_type="fact", evidence_ids=["ev-1"])],
        evidence=[Evidence(
            evidence_id="ev-1", task_id="placeholder", title="manual", source_type="local_document",
            excerpt="example", document_id="doc-1", locator="page 1", accessed_at=now,
            source_status="available",
        )],
        completion_status="completed",
    )


def test_evaluation_runs_with_tagged_task_and_reports_metrics():
    graph = FakeGraph(report=_report())
    result = asyncio.run(evaluate_records(
        mode="fault_analysis",
        records=[{
            "example_id": "fixture-1",
            "inputs": {"equipment_type": "inverter", "symptom": "alarm", "operating_conditions": {"load": "50%"}},
            "expected_retrieval_ids": ["ev-1"],
            "manual_evidence_labels": ["supported"],
        }],
        graph=graph,
    ))

    assert result["schema_pass_rate"] == 1.0
    assert result["retrieval_recall_at_k"] == 1.0
    assert result["citation_coverage"] == 1.0
    assert result["manual_evidence_support_rate"] == 1.0
    assert result["cost_usd"] is None
    assert graph.configs[0]["metadata"]["mode"] == "fault_analysis"
    assert result["outcomes"][0]["status"] == "completed"


def test_evaluation_records_individual_failures_and_continues():
    graph = FakeGraph(error=RuntimeError("mock failure"))
    result = asyncio.run(evaluate_records(
        mode="fault_analysis",
        records=[
            {"example_id": "bad-1", "inputs": {"equipment_type": "", "symptom": "alarm"}},
            {"example_id": "bad-2", "inputs": {"equipment_type": "inverter", "symptom": "alarm"}},
        ],
        graph=graph,
    ))
    assert result["example_count"] == 2
    assert result["schema_pass_rate"] == 0.0
    assert len(result["failures"]) == 2
    assert [row["example_id"] for row in result["outcomes"]] == ["bad-1", "bad-2"]


def test_trace_config_contains_task_and_mode_but_no_secret_fields():
    config = tracing_run_config(task_id="eval-example", mode="fault_analysis")
    assert "task:eval-example" in config["tags"]
    assert "mode:fault_analysis" in config["tags"]
    assert config["metadata"] == {"task_id": "eval-example", "mode": "fault_analysis"}
    assert "api_key" not in str(config).lower()
