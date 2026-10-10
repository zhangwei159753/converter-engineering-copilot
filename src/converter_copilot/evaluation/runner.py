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
) -> dict[str, Any]:
    """Invoke a workflow for each curated record and return metrics plus outcomes."""
    latencies: list[float] = []
    schema_results: list[bool] = []
    expected_ids: list[list[str]] = []
    retrieved_ids: list[list[str]] = []
    claims = []
    manual_labels: list[str | None] = []
    failures: list[dict[str, str]] = []
    outcomes: list[dict[str, Any]] = []
    review_examples: list[dict[str, Any]] = []

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
    summary["outcomes"] = outcomes
    summary["review_bundle"] = bundle_payload
    return summary
