"""Async evaluation runner shared by the CLI and mocked harness tests."""

import time
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from converter_copilot.config import tracing_run_config
from converter_copilot.domain.schemas import ConverterReport, TaskCreate
from converter_copilot.evaluation.metrics import (
    build_evaluation_summary,
    compute_citation_coverage,
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
            expected_ids.append(list(record.get("expected_retrieval_ids", [])))
            retrieved_ids.append([item.evidence_id for item in report.evidence])
            manual_labels.extend(record.get("manual_evidence_labels", []))
            outcomes.append({
                "example_id": example_id,
                "task_id": task_id,
                "status": report.completion_status,
                "latency_ms": round(latency, 3),
                "claim_count": len(report.claims),
                "evidence_count": len(report.evidence),
                "quality_warnings": report.quality_warnings,
            })
        except Exception as error:
            latency = (time.perf_counter() - started) * 1000
            latencies.append(latency)
            schema_results.append(False)
            expected_ids.append(list(record.get("expected_retrieval_ids", [])))
            retrieved_ids.append([])
            manual_labels.extend(record.get("manual_evidence_labels", []))
            # Exception text can contain request details or provider secrets; keep the
            # persisted result useful for triage without serializing that payload.
            failure = {
                "example_id": example_id,
                "error_type": type(error).__name__,
                "message": "Evaluation example failed; inspect local process logs for details.",
            }
            failures.append(failure)
            outcomes.append({"example_id": example_id, "task_id": task_id, "status": "failed", "latency_ms": round(latency, 3), "error": failure})

    summary = build_evaluation_summary(
        mode=mode,
        example_count=len(records),
        retrieval_recall=compute_retrieval_recall(expected_ids, retrieved_ids, k=recall_at_k),
        citation_coverage=compute_citation_coverage(claims),
        schema_pass_rate=compute_schema_pass_rate(schema_results),
        evidence_support_rate=compute_evidence_support_rate(manual_labels),
        latencies_ms=latencies,
        costs_usd=[],
        failures=failures,
    )
    summary["recall_at_k"] = recall_at_k
    summary["outcomes"] = outcomes
    return summary
