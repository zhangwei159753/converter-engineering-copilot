"""Human-reviewed engineering memory projection and retrieval contracts."""

import hashlib
import json
import re
import unicodedata

from pydantic import BaseModel, Field

from converter_copilot.domain.agent_audit import MemoryEdge, MemoryNode
from converter_copilot.domain.evidence import validate_report_evidence
from converter_copilot.domain.schemas import ConverterReport
from converter_copilot.domain.work_orders import WorkOrderRecord, get_work_order
from converter_copilot.storage.sqlite_store import SQLiteStore


class MemoryMatch(BaseModel):
    """An advisory case relation with its accepted human review provenance."""

    equipment_type: str
    symptom: str
    cause: str
    source_work_order_id: str
    source_task_id: str
    source_review_version: int
    evidence_ids: list[str]
    applicability_differences: list[str] = Field(default_factory=list)
    synthetic_demo: bool = False


def project_accepted_review(
    store: SQLiteStore, work_order_id: str, task_id: str, review_version: int
) -> int:
    """Idempotently project the latest accepted review when its provenance is eligible."""
    reviews = store.list_work_order_reviews(work_order_id, task_id)
    latest = reviews[-1] if reviews else None
    if latest is None or latest.version != review_version:
        store.replace_reviewed_memory_projection(work_order_id, task_id, review_version, [], [])
        return 0
    work_order = get_work_order(work_order_id)
    task = store.get_task(task_id)
    report = store.get_report(task_id)
    if (
        latest.verdict != "accepted"
        or work_order is None
        or task is None
        or report is None
        or report.completion_status != "completed"
        or task.status != "completed"
        or bool((task.inputs.get("source_context") or {}).get("synthetic_demo"))
        or not store.is_work_order_task(work_order_id, task_id)
    ):
        store.replace_reviewed_memory_projection(work_order_id, task_id, review_version, [], [])
        return 0
    nodes, edges = build_review_projection(work_order, task_id, review_version, report)
    return store.replace_reviewed_memory_projection(work_order_id, task_id, review_version, nodes, edges)


def build_review_projection(
    work_order: WorkOrderRecord, task_id: str, review_version: int, report: ConverterReport
) -> tuple[list[MemoryNode], list[MemoryEdge]]:
    """Build a graph only from human-reviewed claims with available, task-local citations."""
    if work_order.synthetic_demo or report.task_id != task_id or report.completion_status != "completed":
        return [], []
    evidence_by_id = {
        item.evidence_id: item
        for item in report.evidence
        if item.task_id == task_id
        and item.source_status == "available"
        and item.source_type.casefold() not in {"synthetic", "synthetic_demo"}
        and not re.search(r"synthetic[_ -]?demo|合成测试假设", item.excerpt, re.IGNORECASE)
    }
    evidence_check = validate_report_evidence(report)
    supported_claim_ids = set(evidence_check.supported_claim_ids)
    accepted_claims = [
        claim for claim in report.claims
        if claim.verification_status == "supported"
        and claim.claim_id in supported_claim_ids
        and claim.evidence_ids
        and all(evidence_id in evidence_by_id for evidence_id in claim.evidence_ids)
    ]
    if not accepted_claims:
        return [], []
    evidence_ids = sorted({item for claim in accepted_claims for item in claim.evidence_ids})
    equipment = _normalized(work_order.equipment_model)
    symptom = _normalized(work_order.symptom)
    nodes: dict[str, MemoryNode] = {}

    def add_node(node_type: str, canonical: str, display_name: str) -> str:
        node_id = _stable_id("node", node_type, canonical)
        nodes[node_id] = MemoryNode(
            node_id=node_id,
            node_type=node_type,
            canonical_key=canonical,
            display_name=display_name,
        )
        return node_id

    equipment_id = add_node("equipment", equipment, work_order.equipment_model)
    symptom_id = add_node("symptom", symptom, work_order.symptom)
    work_order_id = add_node("work_order", _normalized(work_order.work_order_id), work_order.work_order_id)
    edges: list[MemoryEdge] = []

    def add_edge(source: str, relation: str, target: str, citations: list[str]) -> None:
        edge_id = _stable_id(
            "edge", work_order.work_order_id, task_id, str(review_version), source, relation, target
        )
        edges.append(MemoryEdge(
            edge_id=edge_id,
            source_node_id=source,
            relation_type=relation,
            target_node_id=target,
            source_task_id=task_id,
            source_work_order_id=work_order.work_order_id,
            source_review_version=review_version,
            evidence_ids=citations,
        ))

    add_edge(equipment_id, "has_symptom", symptom_id, evidence_ids)
    add_edge(equipment_id, "belongs_to_work_order", work_order_id, evidence_ids)
    conditions = {
        key: value for key, value in sorted(work_order.operating_conditions.items()) if value is not None
    }
    if conditions:
        condition_text = json.dumps(conditions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        condition_id = add_node("condition", condition_text, condition_text)
        add_edge(work_order_id, "occurred_under", condition_id, evidence_ids)

    for claim in accepted_claims:
        cause_text = claim.text.strip()
        cause_id = add_node("cause", _normalized(cause_text), cause_text)
        add_edge(equipment_id, "associated_with_cause", cause_id, claim.evidence_ids)
        for evidence_id in claim.evidence_ids:
            source = evidence_by_id[evidence_id]
            evidence_node_id = add_node("evidence", _normalized(evidence_id), source.title)
            add_edge(cause_id, "supported_by", evidence_node_id, [evidence_id])
    return list(nodes.values()), edges


def search_memory(
    store: SQLiteStore, query: str | None = None, equipment_type: str | None = None, limit: int = 10
) -> list[MemoryMatch]:
    """Retrieve only active relations supported by each source's latest accepted review."""
    if not 1 <= limit <= 50:
        raise ValueError("Memory query limit must be between 1 and 50.")
    if query is not None and len(query) > 120:
        raise ValueError("Memory query must be 120 characters or fewer.")
    rows = store.search_memory_rows(query=query, equipment_type=equipment_type, limit=limit)
    requested_equipment = _normalized(equipment_type) if equipment_type else None
    matches = []
    for row in rows:
        differences = []
        if requested_equipment and requested_equipment != _normalized(row["equipment_type"]):
            differences.append("Equipment model differs from the reviewed source case.")
        matches.append(MemoryMatch(
            equipment_type=row["equipment_type"],
            symptom=row["symptom"],
            cause=row["cause"],
            source_work_order_id=row["source_work_order_id"],
            source_task_id=row["source_task_id"],
            source_review_version=row["source_review_version"],
            evidence_ids=json.loads(row["evidence_ids_json"]),
            applicability_differences=differences,
            synthetic_demo=False,
        ))
    return matches


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{parts[0]}-{digest}"
