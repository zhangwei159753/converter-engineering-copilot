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
