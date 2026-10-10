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
    """Return the share of claims that cite at least one evidence item."""
    if not claims:
        return None
    cited = sum(bool(_value(claim, "evidence_ids", [])) for claim in claims)
    return cited / len(claims)


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
) -> dict[str, Any]:
    """Build a JSON-ready summary while keeping unavailable measurements null."""
    latency = {
        "mean": fmean(latencies_ms) if latencies_ms else None,
        "min": min(latencies_ms) if latencies_ms else None,
        "max": max(latencies_ms) if latencies_ms else None,
        "sample_count": len(latencies_ms),
    }
    return {
        "mode": mode,
        "example_count": example_count,
        "retrieval_recall_at_k": retrieval_recall,
        "citation_coverage": citation_coverage,
        "schema_pass_rate": schema_pass_rate,
        "manual_evidence_support_rate": evidence_support_rate,
        "latency_ms": latency,
        "cost_usd": fmean(costs_usd) if costs_usd else None,
        "cost_sample_count": len(costs_usd),
        "failures": list(failures),
    }


def _value(item: Mapping[str, Any] | Any, key: str, default: Any) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)
