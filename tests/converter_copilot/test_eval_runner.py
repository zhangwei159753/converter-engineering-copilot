import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import evals.converter_copilot.run_eval as run_eval
from converter_copilot.config import tracing_run_config
from converter_copilot.domain.schemas import (
    Claim,
    ConverterReport,
    Evidence,
    FaultAnalysisReport,
    TaskMode,
)
from converter_copilot.evaluation.dataset import validate_dataset
from converter_copilot.evaluation.runner import evaluate_records
from converter_copilot.retrieval.document_loader import load_document


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
            "source_type": "reviewed_test_fixture",
            "label_status": "reviewed",
            "reviewer_id": "test-reviewer",
            "reviewed_at": "2026-10-10T00:00:00Z",
            "label_version": 1,
            "inputs": {"equipment_type": "inverter", "symptom": "alarm", "operating_conditions": {"load": "50%"}},
            "expected_retrieval_ids": ["ev-1"],
            "manual_evidence_labels": [{"evidence_id": "ev-1", "claim_id": "c1", "label": "supported"}],
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
    bundle = result["review_bundle"]
    assert bundle["dataset_version"] == "unspecified"
    assert bundle["examples"][0]["evidence"][0]["excerpt"] == "example"
    assert bundle["examples"][0]["evidence"][0]["related_claim_ids"] == ["c1"]
    assert bundle["examples"][0]["evidence"][0]["applicability"] == "unknown"
    assert bundle["examples"][0]["output_context"]["completion_status"] == "completed"
    assert "prompt" not in str(bundle).lower()
    assert "api_key" not in str(bundle).lower()


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
    assert result["review_bundle"]["examples"][0]["output_context"]["failure"]["error_code"] == "evaluation_schema_validation_failed"
    assert result["review_bundle"]["examples"][1]["output_context"]["failure"]["error_code"] == "evaluation_example_failed"
    assert [row["example_id"] for row in result["outcomes"]] == ["bad-1", "bad-2"]


def test_trace_config_contains_task_and_mode_but_no_secret_fields():
    config = tracing_run_config(task_id="eval-example", mode="fault_analysis")
    assert "task:eval-example" in config["tags"]
    assert "mode:fault_analysis" in config["tags"]
    assert config["metadata"] == {"task_id": "eval-example", "mode": "fault_analysis"}
    assert "api_key" not in str(config).lower()


def test_unreviewed_support_labels_are_not_scored_and_explain_insufficient_samples():
    graph = FakeGraph(report=_report())
    result = asyncio.run(evaluate_records(
        mode="fault_analysis",
        records=[{
            "example_id": "starter-1",
            "source_type": "synthetic_demo_fixture",
            "label_status": "unreviewed",
            "inputs": {"equipment_type": "inverter", "symptom": "alarm"},
            "expected_retrieval_ids": [],
            "manual_evidence_labels": [{"evidence_id": "ev-1", "claim_id": "c1", "label": "supported"}],
        }],
        graph=graph,
    ))

    assert result["manual_evidence_support_rate"] is None
    assert result["retrieval_recall_at_k"] is None
    assert result["metric_status"]["manual_evidence_support_rate"] == "insufficient_labels"
    assert result["metric_status"]["retrieval_recall_at_k"] == "insufficient_labels"
    assert result["metric_sample_counts"]["reviewed_evidence_labels"] == 0


def test_empty_gold_labels_report_insufficient_labels_even_for_reviewed_examples():
    result = asyncio.run(evaluate_records(
        mode="fault_analysis",
        records=[{
            "example_id": "reviewed-no-gold", "source_type": "human_reviewed",
            "label_status": "reviewed", "expected_retrieval_ids": [], "manual_evidence_labels": [],
            "inputs": {"equipment_type": "inverter", "symptom": "alarm"},
        }],
        graph=FakeGraph(report=_report()),
    ))
    assert result["retrieval_recall_at_k"] is None
    assert result["metric_status"]["retrieval_recall_at_k"] == "insufficient_labels"
    assert result["metric_sample_counts"]["retrieval_labeled_examples"] == 0


def test_custom_dataset_path_is_supported_for_one_mode():
    assert hasattr(run_eval, "_dataset_paths")
    dataset = run_eval._dataset_paths(["fault_analysis"], "evals/custom-faults.jsonl")
    assert dataset == {"fault_analysis": Path("evals/custom-faults.jsonl").resolve()}


def test_custom_dataset_path_rejects_multiple_modes():
    assert hasattr(run_eval, "_dataset_paths")
    try:
        run_eval._dataset_paths(["fault_analysis", "technical_research"], "evals/custom.jsonl")
    except ValueError as error:
        assert "one workflow mode" in str(error)
    else:
        raise AssertionError("A custom dataset must not be ambiguously applied to multiple modes.")


def test_synthetic_evidence_quality_pack_has_balanced_unreviewed_scenarios():
    root = Path(__file__).resolve().parents[2]
    dataset_path = root / "evals" / "converter_copilot" / "synthetic_fault_quality_v1.jsonl"
    records = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(records) == 20
    assert validate_dataset(records, mode="fault_analysis") == []
    assert all(record["label_status"] == "unreviewed" for record in records)
    assert all(record["expected_retrieval_ids"] == [] for record in records)
    assert all(record["manual_evidence_labels"] == [] for record in records)
    assert {category: sum(item["scenario_category"] == category for item in records) for category in {
        "direct_match", "cross_equipment_distractor", "missing_information", "conflicting_sources", "no_relevant_evidence"
    }} == {
        "direct_match": 4,
        "cross_equipment_distractor": 4,
        "missing_information": 4,
        "conflicting_sources": 4,
        "no_relevant_evidence": 4,
    }


def test_synthetic_evidence_corpus_has_twelve_attributed_fixture_rows():
    root = Path(__file__).resolve().parents[2]
    corpus_path = root / "evals" / "converter_copilot" / "synthetic_converter_evidence_v1.csv"
    with corpus_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    assert len(rows) == 12
    assert all(row["record_type"] == "synthetic_demo_fixture" for row in rows)
    assert all("不代表真实现场" in row["source_attribution"] for row in rows)
    chunks = load_document(corpus_path, task_id="synthetic-eval-test")
    assert len(chunks) == 12
    assert [chunk.locator for chunk in chunks[:2]] == ["row 2", "row 3"]
    assert all("synthetic_demo_fixture" in chunk.text for chunk in chunks)
