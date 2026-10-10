"""Deterministic metrics used by the local converter copilot evaluation harness."""

from collections.abc import Mapping, Sequence
from statistics import fmean
from typing import Any


def compute_retrieval_recall(
    expected_ids: Sequence[Sequence[str]],
    retrieved_ids: Sequence[Sequence[str]],
    *,
    k: int,
) -> float | None:
    """Compute macro Recall@K over examples with at least one expected ID."""
    if k < 1:
        raise ValueError("k must be at least 1")
    scores = []
    for expected, retrieved in zip(expected_ids, retrieved_ids, strict=True):
        relevant = set(expected)
        if not relevant:
            continue
        scores.append(len(relevant.intersection(retrieved[:k])) / len(relevant))
    return fmean(scores) if scores else None


def compute_citation_coverage(claims: Sequence[Mapping[str, Any] | Any]) -> float | None:
    """Return the share of claims that cite at least one evidence item.

    This measures citation presence only; it does not check citation validity or
    whether the cited excerpt semantically supports the claim.
    """
    if not claims:
        return None
    cited = sum(bool(_value(claim, "evidence_ids", [])) for claim in claims)
    return cited / len(claims)


def compute_citation_integrity_rate(claims: Sequence[Mapping[str, Any] | Any]) -> float | None:
    """Return the share of cited claims whose IDs passed deterministic checks.

    The workflow assigns ``supported`` only when all cited IDs are unique,
    traceable, and point to available sources; ``conflicting`` links are also
    structurally valid. Neither status verifies semantic entailment.
    """
    cited = [claim for claim in claims if _value(claim, "evidence_ids", [])]
    if not cited:
        return None
    valid = sum(_value(claim, "verification_status", "unverified") in {"supported", "conflicting"} for claim in cited)
    return valid / len(cited)


def compute_schema_pass_rate(results: Sequence[bool]) -> float | None:
    """Return the fraction of examples that passed structured schema validation."""
    if not results:
        return None
    return sum(results) / len(results)


def compute_evidence_support_rate(labels: Sequence[str | None]) -> float | None:
    """Compute manually labeled support rate, excluding unverified labels."""
    scored = [label for label in labels if label in {"supported", "unsupported"}]
    if not scored:
        return None
    return sum(label == "supported" for label in scored) / len(scored)


def compute_branch_metrics(
    branches: Sequence[Mapping[str, Any] | Any],
    reviewed_labels: Mapping[str, str] | Sequence[Mapping[str, Any]],
    *,
    evidence_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Measure deterministic branch structure; keep human quality null without branch labels."""
    count = len(branches)
    inventory = set(evidence_ids) if evidence_ids is not None else None
    refs: list[tuple[str, set[str] | None]] = []
    support_coverage = unsupported = applicability_warning = incomplete = 0
    for branch in branches:
        supporting = list(_value(branch, "supporting_evidence_ids", []))
        opposing = list(_value(branch, "opposing_evidence_ids", []))
        available = _value(branch, "available_evidence_ids", None)
        allowed = set(available) if available is not None else inventory
        refs.extend((ref, allowed) for ref in supporting + opposing)
        support_coverage += bool(supporting or opposing)
        unsupported += not bool(supporting)
        applicability_warning += _value(branch, "applicability", "unknown") != "direct"
        incomplete += _value(branch, "gate_status", "pending") in {"pending", "review_incomplete"}

    if isinstance(reviewed_labels, Mapping):
        labels = list(reviewed_labels.values())
    else:
        labels = [item.get("label") for item in reviewed_labels if isinstance(item, Mapping)]
    scored_labels = [label for label in labels if label in {"supported", "unsupported"}]
    integrity = (
        sum(reference in allowed for reference, allowed in refs) / len(refs)
        if refs and all(allowed is not None for _, allowed in refs) else None
    )
    def ratio(value: int) -> float | None:
        return value / count if count else None
    return {
        "evidence_id_integrity_rate": integrity,
        "support_opposition_coverage": ratio(support_coverage),
        "unsupported_candidate_share": ratio(unsupported),
        "applicability_warning_share": ratio(applicability_warning),
        "incomplete_review_share": ratio(incomplete),
        "human_labeled_branch_quality": (
            sum(label == "supported" for label in scored_labels) / len(scored_labels)
            if scored_labels else None
        ),
        "sample_counts": {"branches": count, "evidence_references": len(refs), "human_labeled_branches": len(scored_labels)},
        "metric_reasons": {
            "evidence_id_integrity_rate": "No evidence references or evidence inventory are available."
            if integrity is None else None,
            "human_labeled_branch_quality": "No human-labeled branch-quality set is available."
            if not scored_labels else None,
        },
    }


def build_evaluation_summary(
    *,
    mode: str,
    example_count: int,
    retrieval_recall: float | None,
    citation_coverage: float | None,
    schema_pass_rate: float | None,
    evidence_support_rate: float | None,
    latencies_ms: Sequence[float],
    costs_usd: Sequence[float],
    failures: Sequence[Mapping[str, str]],
    citation_integrity_rate: float | None = None,
    metric_sample_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Build a JSON-ready summary while keeping unavailable measurements null."""
    latency = {
        "mean": fmean(latencies_ms) if latencies_ms else None,
        "min": min(latencies_ms) if latencies_ms else None,
        "max": max(latencies_ms) if latencies_ms else None,
        "sample_count": len(latencies_ms),
    }
    sample_counts = {
        "retrieval_labeled_examples": 0,
        "reviewed_evidence_labels": 0,
        "citation_claims": 0,
        "citation_integrity_claims": 0,
        "schema_examples": example_count,
        **dict(metric_sample_counts or {}),
    }
    metric_status = {
        "retrieval_recall_at_k": "measured" if retrieval_recall is not None else "insufficient_labels",
        "citation_coverage": "measured" if citation_coverage is not None else "insufficient_outputs",
        "citation_integrity_rate": "measured" if citation_integrity_rate is not None else "insufficient_outputs",
        "schema_pass_rate": "measured" if schema_pass_rate is not None else "insufficient_outputs",
        "manual_evidence_support_rate": "measured" if evidence_support_rate is not None else "insufficient_labels",
    }
    metric_reasons = {
        key: (
            "No human-reviewed gold labels are available."
            if status == "insufficient_labels"
            else "No eligible generated outputs are available."
        )
        for key, status in metric_status.items()
        if status.startswith("insufficient")
    }
    return {
        "mode": mode,
        "example_count": example_count,
        "retrieval_recall_at_k": retrieval_recall,
        "citation_coverage": citation_coverage,
        "citation_integrity_rate": citation_integrity_rate,
        "schema_pass_rate": schema_pass_rate,
        "manual_evidence_support_rate": evidence_support_rate,
        "metric_status": metric_status,
        "metric_sample_counts": sample_counts,
        "metric_reasons": metric_reasons,
        "latency_ms": latency,
        "cost_usd": fmean(costs_usd) if costs_usd else None,
        "cost_sample_count": len(costs_usd),
        "failures": list(failures),
    }


def _value(item: Mapping[str, Any] | Any, key: str, default: Any) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)
