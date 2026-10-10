from datetime import datetime, timezone

import pytest

from converter_copilot.evaluation.review import (
    EvaluationReviewBundle,
    EvaluationReviewService,
    EvidenceLabel,
)
from converter_copilot.storage.sqlite_store import SQLiteStore


@pytest.fixture
def service(tmp_path):
    store = SQLiteStore(tmp_path / "evaluation.db")
    review_service = EvaluationReviewService(store)
    bundle = EvaluationReviewBundle.model_validate({
        "bundle_id": "bundle-1",
        "mode": "fault_analysis",
        "dataset_version": "abc123",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "examples": [{
            "example_id": "example-1", "source_type": "synthetic_demo_fixture", "task_id": "task-1",
            "inputs": {"equipment_type": "inverter", "symptom": "alarm"},
            "claims": [{"claim_id": "claim-1", "text": "Claim", "evidence_ids": ["evidence-1"]}],
            "evidence": [{"evidence_id": "evidence-1", "excerpt": "Excerpt", "locator": "page 1"}],
            "output_context": {"completion_status": "partial", "clarification_questions": ["请提供母线波形。"]},
        }],
    })
    imported = review_service.import_review_bundle(bundle)
    example = review_service.list_examples()[0][0]
    return review_service, imported, example


def test_bundle_import_is_idempotent_and_snapshots_are_immutable(service):
    reviews, imported, example = service
    again = reviews.import_review_bundle(EvaluationReviewBundle.model_validate({
        "bundle_id": "bundle-1", "mode": "fault_analysis", "dataset_version": "changed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
            "examples": [{"example_id": "different", "source_type": "x", "task_id": "t", "inputs": {"equipment_type": "inverter", "symptom": "alarm"}}],
    }))
    assert imported.imported_count == 1
    assert again.reused is True
    assert reviews.get_example(example.review_id).example_id == "example-1"
    assert reviews.get_example(example.review_id).output_context["clarification_questions"] == ["请提供母线波形。"]


def test_review_and_rereview_append_history(service):
    reviews, _, example = service
    first = reviews.save_review(
        example.review_id, reviewer_id="reviewer A",
        evidence_labels=[EvidenceLabel(evidence_id="evidence-1", claim_id="claim-1", label="supported")],
        expected_retrieval_ids=["evidence-1"],
    )
    second = reviews.save_review(
        example.review_id, reviewer_id="reviewer B",
        evidence_labels=[EvidenceLabel(evidence_id="evidence-1", claim_id="claim-1", label="unsupported")],
        expected_retrieval_ids=[],
    )
    assert first.label_version == 1
    assert second.label_version == 2
    assert [event.evidence_labels[0].label for event in reviews.history(example.review_id)] == ["supported", "unsupported"]
    assert reviews.get_example(example.review_id).evidence_labels[0].label == "unsupported"


@pytest.mark.parametrize("reviewer,labels,retrieval", [
    (" ", [], []),
    ("reviewer", [EvidenceLabel(evidence_id="unknown", claim_id="claim-1", label="supported")], []),
    ("reviewer", [EvidenceLabel(evidence_id="evidence-1", claim_id="unknown", label="supported")], []),
    ("reviewer", [], ["unknown"]),
])
def test_invalid_review_is_rejected_without_persisting_events(service, reviewer, labels, retrieval):
    reviews, _, example = service
    with pytest.raises(ValueError):
        reviews.save_review(example.review_id, reviewer_id=reviewer, evidence_labels=labels, expected_retrieval_ids=retrieval)
    assert reviews.history(example.review_id) == []
    assert reviews.get_example(example.review_id).status == "pending"


def test_filters_and_summary_keep_unreviewed_metrics_unavailable(service):
    reviews, _, example = service
    items, total = reviews.list_examples(status="pending", limit=1)
    assert total == 1
    assert items[0].review_id == example.review_id
    source_items, source_total = reviews.list_examples(source_type="synthetic_demo_fixture")
    assert source_total == 1
    assert source_items[0].source_type == "synthetic_demo_fixture"
    summary = reviews.summary()
    assert summary["modes"]["fault_analysis"]["pending"] == 1
    assert summary["metric_status"]["manual_evidence_support_rate"] == "insufficient_labels"

