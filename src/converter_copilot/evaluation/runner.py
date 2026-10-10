"""Async evaluation runner shared by the CLI and mocked harness tests."""

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from converter_copilot.config import tracing_run_config
from converter_copilot.domain.schemas import ConverterReport, TaskCreate
from converter_copilot.evaluation.metrics import (
    build_evaluation_summary,
    compute_branch_metrics,
    compute_citation_coverage,
    compute_citation_integrity_rate,
    compute_evidence_support_rate,
    compute_retrieval_recall,
    compute_schema_pass_rate,
)


async def evaluate_records(
    *,
    mode: str,
    records: Sequence[Mapping[str, Any]],
    graph: Any,
    recall_at_k: int = 5,
    dataset_version: str = "unspecified",
    strategy_graphs: Mapping[str, Any] | None = None,
    agent_strategy: str | None = None,
) -> dict[str, Any]:
    """Invoke a workflow for each curated record and return metrics plus outcomes."""
    if strategy_graphs is not None:
        if not strategy_graphs:
            raise ValueError("strategy_graphs must include at least one named workflow.")
        if len(strategy_graphs) < 2:
            raise ValueError("strategy_graphs comparison requires at least two named workflows.")
        if any(not name.strip() for name in strategy_graphs):
            raise ValueError("Workflow strategy names must be non-empty.")
        strategies = {
            name: await evaluate_records(
                mode=mode, records=records, graph=strategy_graph, recall_at_k=recall_at_k,
                dataset_version=dataset_version, agent_strategy=name,
            )
            for name, strategy_graph in strategy_graphs.items()
        }
        return _paired_strategy_comparison(mode, dataset_version, strategies)

    latencies: list[float] = []
    schema_results: list[bool] = []
    expected_ids: list[list[str]] = []
    retrieved_ids: list[list[str]] = []
    claims = []
    manual_labels: list[str | None] = []
    failures: list[dict[str, str]] = []
    outcomes: list[dict[str, Any]] = []
    review_examples: list[dict[str, Any]] = []
    branch_snapshots: list[dict[str, Any]] = []
    agent_durations: list[float] = []
    agent_run_counts: dict[str, int] = {}
    provider_usage_totals: dict[str, int | float] = {}
    budget_exhaustion_count = 0

    for index, record in enumerate(records, start=1):
        example_id = str(record.get("example_id", f"{mode}-{index}"))
        task_id = f"eval-{mode}-{uuid4().hex[:12]}"
        started = time.perf_counter()
        try:
            request = TaskCreate.model_validate({"mode": mode, "inputs": record["inputs"]})
            state = await graph.ainvoke(
                {"task_id": task_id, "inputs": request.inputs.model_dump()},
                config=tracing_run_config(task_id=task_id, mode=mode),
            )
            report = ConverterReport.model_validate(state["report"])
            if report.mode.value != mode:
                raise ValueError(f"Workflow returned mode {report.mode.value}, expected {mode}.")
            latency = (time.perf_counter() - started) * 1000
            latencies.append(latency)
            schema_results.append(True)
            claims.extend(report.claims)
            available_evidence_ids = [item.evidence_id for item in report.evidence]
            for raw_branch in state.get("hypothesis_branches", []):
                branch = raw_branch.model_dump(mode="json") if hasattr(raw_branch, "model_dump") else dict(raw_branch)
                branch["available_evidence_ids"] = available_evidence_ids
                branch_snapshots.append(branch)
            task_runs = state.get("agent_runs", [])
            task_budget_exhausted = False
            for raw_run in task_runs:
                run = raw_run.model_dump(mode="json") if hasattr(raw_run, "model_dump") else dict(raw_run)
                status = str(run.get("status", "unknown"))
                agent_run_counts[status] = agent_run_counts.get(status, 0) + 1
                usage = run.get("provider_usage")
                if isinstance(usage, Mapping):
                    for key, value in usage.items():
                        if isinstance(value, int | float):
                            provider_usage_totals[key] = provider_usage_totals.get(key, 0) + value
                started_at = _parse_datetime(run.get("started_at"))
                completed_at = _parse_datetime(run.get("completed_at"))
                if started_at and completed_at and completed_at >= started_at:
                    agent_durations.append((completed_at - started_at).total_seconds() * 1000)
                task_budget_exhausted = task_budget_exhausted or status == "skipped"
            if state.get("agent_budget_remaining") == 0 and (
                task_budget_exhausted
                or any(_value(branch, "gate_status", "") == "review_incomplete" for branch in state.get("hypothesis_branches", []))
            ):
                budget_exhaustion_count += 1
            reviewed = record.get("label_status") == "reviewed"
            claim_ids_by_evidence: dict[str, list[str]] = {}
            for claim in report.claims:
                for evidence_id in claim.evidence_ids:
                    claim_ids_by_evidence.setdefault(evidence_id, []).append(claim.claim_id)
            if reviewed:
                expected_ids.append(list(record.get("expected_retrieval_ids", [])))
                retrieved_ids.append([item.evidence_id for item in report.evidence])
                manual_labels.extend(
                    label.get("label") for label in record.get("manual_evidence_labels", [])
                    if isinstance(label, Mapping)
                )
            outcomes.append({
                "example_id": example_id,
                "task_id": task_id,
                "status": report.completion_status,
                "latency_ms": round(latency, 3),
                "claim_count": len(report.claims),
                "evidence_count": len(report.evidence),
                "quality_warnings": report.quality_warnings,
            })
            review_examples.append({
                "example_id": example_id,
                "source_type": str(record.get("source_type", "unknown")),
                "inputs": request.inputs.model_dump(mode="json"),
                "task_id": task_id,
                "output_context": {
                    "completion_status": report.completion_status,
                    "quality_warnings": report.quality_warnings,
                    "clarification_questions": report.clarification_questions,
                },
                "claims": [
                    {"claim_id": claim.claim_id, "text": claim.text, "evidence_ids": claim.evidence_ids}
                    for claim in report.claims
                ],
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "excerpt": item.excerpt,
                        "source_type": item.source_type,
                        "locator": item.locator,
                        "url": item.url,
                        "source_status": item.source_status,
                        "equipment_type": item.equipment_type,
                        "applicability": item.applicability,
                        "related_claim_ids": sorted(set(item.related_claim_ids + claim_ids_by_evidence.get(item.evidence_id, []))),
                    }
                    for item in report.evidence
                ],
            })
        except Exception as error:
            latency = (time.perf_counter() - started) * 1000
            latencies.append(latency)
            schema_results.append(False)
            if record.get("label_status") == "reviewed":
                expected_ids.append(list(record.get("expected_retrieval_ids", [])))
                retrieved_ids.append([])
                manual_labels.extend(
                    label.get("label") for label in record.get("manual_evidence_labels", [])
                    if isinstance(label, Mapping)
                )
            # Exception text can contain request details or provider secrets; keep the
            # persisted result useful for triage without serializing that payload.
            failure = {
                "example_id": example_id,
                "error_type": type(error).__name__,
                "message": "Evaluation example failed; inspect local process logs for details.",
            }
            error_code = "evaluation_schema_validation_failed" if type(error).__name__ == "ValidationError" else "evaluation_example_failed"
            output_context: dict[str, Any] = {
                "completion_status": "failed",
                "failure": {"error_code": error_code, "error_type": type(error).__name__},
            }
            if type(error).__name__ == "ValidationError" and hasattr(error, "errors"):
                try:
                    output_context["failure"]["field_paths"] = sorted({
                        ".".join(str(part) for part in item.get("loc", ()))
                        for item in error.errors(include_url=False, include_context=False, include_input=False)
                    })[:12]
                except (TypeError, ValueError):
                    pass
            failures.append(failure)
            outcomes.append({"example_id": example_id, "task_id": task_id, "status": "failed", "latency_ms": round(latency, 3), "error": failure})
            review_examples.append({
                "example_id": example_id,
                "source_type": str(record.get("source_type", "unknown")),
                "inputs": record.get("inputs", {}),
                "task_id": task_id,
                "output_context": output_context,
                "claims": [],
                "evidence": [],
            })

    reviewed_labels = [label for label in manual_labels if label in {"supported", "unsupported"}]
    bundle_payload = {
        "bundle_id": f"review-{uuid4().hex}",
        "mode": mode,
        "dataset_version": dataset_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "examples": review_examples,
    }
    bundle_hash = hashlib.sha256(
        json.dumps(bundle_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    bundle_payload["bundle_id"] = f"review-{bundle_hash[:32]}"

    summary = build_evaluation_summary(
        mode=mode,
        example_count=len(records),
        retrieval_recall=compute_retrieval_recall(expected_ids, retrieved_ids, k=recall_at_k),
        citation_coverage=compute_citation_coverage(claims),
        citation_integrity_rate=compute_citation_integrity_rate(claims),
        schema_pass_rate=compute_schema_pass_rate(schema_results),
        evidence_support_rate=compute_evidence_support_rate(manual_labels),
        latencies_ms=latencies,
        costs_usd=[],
        failures=failures,
        metric_sample_counts={
            "retrieval_labeled_examples": sum(bool(item) for item in expected_ids),
            "reviewed_evidence_labels": len(reviewed_labels),
            "citation_claims": len(claims),
            "citation_integrity_claims": sum(bool(claim.evidence_ids) for claim in claims),
        },
    )
    summary["recall_at_k"] = recall_at_k
    summary["dataset_version"] = dataset_version
    strategy_name = agent_strategy or "unspecified"
    summary["workflow_version"] = "agent-collaboration-v1" if strategy_name == "collaborative" else "workflow-v1"
    summary["agent_strategy"] = strategy_name
    summary["branch_metrics"] = compute_branch_metrics(
        branch_snapshots, reviewed_labels=[],
    )
    summary["agent_latency_ms"] = {
        "mean": sum(agent_durations) / len(agent_durations) if agent_durations else None,
        "sample_count": len(agent_durations),
        "reason": None if agent_durations else "Workflow returned no timed agent-run records.",
    }
    summary["agent_run_counts"] = agent_run_counts
    summary["budget_exhaustion_count"] = budget_exhaustion_count
    summary["provider_usage"] = provider_usage_totals or None
    summary["provider_usage_unavailable_reason"] = (
        None if provider_usage_totals else "The workflow provider adapters do not expose a consistent token-usage record."
    )
    summary["synthetic_example_count"] = sum(
        str(record.get("source_type", "")).startswith("synthetic") or record.get("label_status") == "unreviewed"
        for record in records
    )
    summary["outcomes"] = outcomes
    summary["review_bundle"] = bundle_payload
    return summary


def _paired_strategy_comparison(mode: str, dataset_version: str, strategies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pair outcomes by stable example IDs and expose numeric deltas only when measured."""
    names = list(strategies)
    first_name, second_name = names[0], names[1]
    first = {row["example_id"]: row for row in strategies[first_name]["outcomes"]}
    second = {row["example_id"]: row for row in strategies[second_name]["outcomes"]}
    shared = sorted(set(first) & set(second))
    metrics = ("schema_pass_rate", "retrieval_recall_at_k", "citation_coverage", "citation_integrity_rate", "manual_evidence_support_rate")
    deltas = {}
    for metric in metrics:
        left, right = strategies[first_name].get(metric), strategies[second_name].get(metric)
        deltas[metric] = round(right - left, 6) if isinstance(left, int | float) and isinstance(right, int | float) else None
    latency_deltas = [second[item].get("latency_ms", 0) - first[item].get("latency_ms", 0) for item in shared]
    return {
        "mode": mode,
        "dataset_version": dataset_version,
        "paired_comparison": {
            "strategy_order": names,
            "paired_example_count": len(shared),
            "shared_example_ids": shared,
            "metric_deltas_second_minus_first": deltas,
            "mean_latency_delta_ms_second_minus_first": sum(latency_deltas) / len(latency_deltas) if latency_deltas else None,
            "interpretation": "Synthetic or unreviewed fixtures validate workflow behavior only; quality deltas require human-reviewed labels.",
        },
        "strategies": strategies,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _value(item: Mapping[str, Any] | Any, key: str, default: Any) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)
