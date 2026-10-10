from converter_copilot.evaluation.dataset import validate_dataset
from converter_copilot.evaluation.metrics import (
    build_evaluation_summary,
    compute_citation_coverage,
    compute_evidence_support_rate,
    compute_retrieval_recall,
    compute_schema_pass_rate,
)


def test_retrieval_recall_at_k_is_macro_average():
    value = compute_retrieval_recall(
        expected_ids=[["a", "b"], ["c"]],
        retrieved_ids=[["a", "x"], ["c"]],
        k=2,
    )
    assert value == 0.75


def test_citation_coverage_counts_claims_with_at_least_one_reference():
    assert compute_citation_coverage([
        {"evidence_ids": ["ev-1"]},
        {"evidence_ids": []},
        {"evidence_ids": ["ev-2", "ev-3"]},
    ]) == 2 / 3


def test_schema_pass_rate_and_manual_support_rate_are_deterministic():
    assert compute_schema_pass_rate([True, False, True]) == 2 / 3
    assert compute_evidence_support_rate(["supported", "unsupported", "unverified", None]) == 0.5


def test_empty_inputs_return_null_metrics_not_invented_zeroes():
    assert compute_retrieval_recall([], [], k=5) is None
    assert compute_citation_coverage([]) is None
    assert compute_schema_pass_rate([]) is None
    assert compute_evidence_support_rate(["unverified", None]) is None


def test_summary_preserves_missing_cost_data_as_null():
    summary = build_evaluation_summary(
        mode="fault_analysis",
        example_count=2,
        retrieval_recall=None,
        citation_coverage=0.5,
        schema_pass_rate=1.0,
        evidence_support_rate=None,
        latencies_ms=[120.0, 180.0],
        costs_usd=[],
        failures=[],
    )
    assert summary["latency_ms"]["mean"] == 150.0
    assert summary["cost_usd"] is None
    assert summary["retrieval_recall_at_k"] is None
    assert summary["failures"] == []


def _valid_dataset_row(**updates):
    record = {
        "example_id": "fixture-1",
        "source_type": "synthetic_demo_fixture",
        "label_status": "unreviewed",
        "inputs": {"equipment_type": "inverter", "symptom": "overvoltage"},
        "expected_retrieval_ids": [],
        "manual_evidence_labels": [],
    }
    return {**record, **updates}


def test_reviewed_labels_require_reviewer_and_timestamp():
    issues = validate_dataset([_valid_dataset_row(label_status="reviewed")], mode="fault_analysis")

    assert {issue.field for issue in issues} == {"reviewer_id", "reviewed_at", "label_version"}


def test_duplicate_example_id_reports_location_without_source_text():
    issues = validate_dataset(
        [_valid_dataset_row(), _valid_dataset_row()],
        mode="fault_analysis",
    )

    duplicate = next(issue for issue in issues if issue.field == "example_id")
    assert duplicate.line_number == 2
    assert "fixture-1" not in duplicate.message
