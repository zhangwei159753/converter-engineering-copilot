"""LangGraph nodes for fault intake, parallel retrieval, and report drafting."""

import asyncio
import hashlib
import inspect
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from converter_copilot.agent.prompts import FAULT_ANALYSIS_SYSTEM, TECHNICAL_RESEARCH_SYSTEM, format_research_prompt
from converter_copilot.agent.state import ConverterState
from converter_copilot.domain.evidence import validate_report_evidence
from converter_copilot.domain.schemas import (
    Claim,
    CaseMatch,
    CaseRecord,
    CaseRetrievalInput,
    CaseRetrievalReport,
    ConverterReport,
    Evidence,
    FaultAnalysisInput,
    FaultAnalysisReport,
    TaskMode,
    TechnicalResearchInput,
    TechnicalResearchReport,
)


def make_intake_node():
    """Create the request validation and bounded query planning node."""

    def intake(state: ConverterState) -> dict[str, Any]:
        request = FaultAnalysisInput.model_validate(state["inputs"])
        queries = [
            f"{request.equipment_type} {request.symptom} power stage device behavior {request.fault_code or ''}",
            f"{request.equipment_type} {request.symptom} control sensing protection behavior",
            f"{request.equipment_type} {request.symptom} operating conditions {request.operating_conditions}",
        ]
        return {"inputs": request.model_dump(mode="json"), "queries": queries, "evidence": []}

    return intake


def make_technical_research_intake_node():
    """Create a bounded technical-research plan from the user's topic and scope."""

    def intake(state: ConverterState) -> dict[str, Any]:
        request = TechnicalResearchInput.model_validate(state["inputs"])
        context = " ".join(filter(None, [request.scope, request.time_range]))
        dimensions = ", ".join(request.comparison_dimensions)
        base = f"{request.question} {context}"
        queries = [
            f"{base} published methods and comparative studies",
            f"{base} experimental conditions datasets metrics {dimensions}",
            f"{base} limitations reproducibility contradictory findings",
        ]
        return {"inputs": request.model_dump(mode="json"), "queries": queries, "evidence": []}

    return intake


def make_research_node(deps):
    """Create a three-branch concurrent local and web retrieval node."""

    async def call(method, *args, **kwargs):
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        result = await asyncio.to_thread(method, *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def research(state: ConverterState) -> dict[str, Any]:
        task_id = state["task_id"]

        async def one_query(query: str) -> list[Evidence]:
            web_results, local_results = await asyncio.gather(
                call(deps.search.search, query, limit=4),
                call(deps.vector_index.search, query, limit=4),
            )
            normalized: list[Evidence] = []
            for item in web_results or []:
                normalized.append(_web_evidence(item, task_id))
            for chunk in local_results or []:
                normalized.append(
                    Evidence(
                        evidence_id=chunk.chunk_id,
                        task_id=task_id,
                        title=chunk.filename,
                        source_type="local_document",
                        excerpt=chunk.text,
                        document_id=chunk.document_id,
                        locator=chunk.locator,
                        accessed_at=datetime.now(timezone.utc),
                        source_status="available",
                    )
                )
            return normalized

        branch_results = await asyncio.gather(*(one_query(query) for query in state["queries"]))
        evidence_by_id = {}
        for item in (source for branch in branch_results for source in branch):
            evidence_by_id.setdefault(item.evidence_id, item)
        return {"evidence": list(evidence_by_id.values())}

    return research


def make_clarification_node():
    """Create a safe partial report when essential operating context is missing."""

    def clarify(state: ConverterState) -> dict[str, Any]:
        warning = "Missing required operating_conditions; gather the missing context before drawing fault conclusions."
        report = ConverterReport(
            task_id=state["task_id"],
            mode=TaskMode.FAULT_ANALYSIS,
            result=FaultAnalysisReport(),
            claims=[],
            evidence=[],
            quality_warnings=[warning],
            completion_status="partial",
        )
        return {
            "report": report,
            "clarification_questions": [
                "故障发生时的负载、电网电压和环境温度分别是多少？",
                "故障前后是否发生启停、功率突变或保护动作？",
                "请提供故障码、设备型号及可安全获取的波形或告警记录。",
            ],
        }

    return clarify


def make_generate_node(deps):
    """Create structured fault analysis generation and evidence quality checking."""

    async def generate(state: ConverterState) -> dict[str, Any]:
        evidence = state.get("evidence", [])
        if not evidence:
            report = ConverterReport(
                task_id=state["task_id"],
                mode=TaskMode.FAULT_ANALYSIS,
                result=FaultAnalysisReport(),
                claims=[],
                evidence=[],
                quality_warnings=["No supporting sources were found; no fault hypothesis was generated."],
                completion_status="partial",
            )
            return {"report": report, "clarification_questions": []}

        source_text = [
            f"[{item.evidence_id}] {item.title} ({item.locator or item.url or 'source'})\n{item.excerpt}"
            for item in evidence
        ]
        question = f"Analyze fault inputs: {state['inputs']}"
        messages = [
            SystemMessage(content=FAULT_ANALYSIS_SYSTEM),
            HumanMessage(content=format_research_prompt(question, source_text)),
        ]
        structured_model = deps.model.with_structured_output(FaultAnalysisReport)
        result = await structured_model.ainvoke(messages)
        result = FaultAnalysisReport.model_validate(result)
        claims = [
            Claim(
                claim_id=f"claim-{index}",
                text=hypothesis.hypothesis,
                claim_type="inference",
                evidence_ids=hypothesis.supporting_evidence_ids,
            )
            for index, hypothesis in enumerate(result.hypotheses, start=1)
        ]
        provisional = ConverterReport(
            task_id=state["task_id"],
            mode=TaskMode.FAULT_ANALYSIS,
            result=result,
            claims=claims,
            evidence=evidence,
            quality_warnings=[],
            completion_status="completed",
        )
        check = validate_report_evidence(provisional)
        status_by_claim = {
            **{claim_id: "supported" for claim_id in check.supported_claim_ids},
            **{claim_id: "conflicting" for claim_id in check.conflicting_claim_ids},
            **{claim_id: "unverified" for claim_id in check.unverified_claim_ids},
        }
        claims = [claim.model_copy(update={"verification_status": status_by_claim[claim.claim_id]}) for claim in claims]
        completion_status = "partial" if check.conflicting_claim_ids or check.unverified_claim_ids else "completed"
        report = provisional.model_copy(
            update={
                "claims": claims,
                "quality_warnings": check.warnings,
                "completion_status": completion_status,
            }
        )
        return {"report": report, "clarification_questions": []}

    return generate


def make_technical_research_generate_node(deps):
    """Create the structured, source-backed technical comparison report node."""

    async def generate(state: ConverterState) -> dict[str, Any]:
        evidence = state.get("evidence", [])
        if not evidence:
            report = ConverterReport(
                task_id=state["task_id"],
                mode=TaskMode.TECHNICAL_RESEARCH,
                result=TechnicalResearchReport(),
                claims=[],
                evidence=[],
                quality_warnings=["No supporting sources were found; no comparison was generated."],
                completion_status="partial",
            )
            return {"report": report, "clarification_questions": []}

        source_text = [
            f"[{item.evidence_id}] {item.title} ({item.locator or item.url or 'source'})\n{item.excerpt}"
            for item in evidence
        ]
        prompt = format_research_prompt(f"Research question: {state['inputs']}", source_text)
        structured_model = deps.model.with_structured_output(TechnicalResearchReport)
        result = await structured_model.ainvoke(
            [SystemMessage(content=TECHNICAL_RESEARCH_SYSTEM), HumanMessage(content=prompt)]
        )
        result = TechnicalResearchReport.model_validate(result)
        claims = [
            Claim(
                claim_id=f"claim-{index}",
                text=f"{comparison.item}: {comparison.method_or_value}",
                claim_type="fact",
                evidence_ids=comparison.evidence_ids,
            )
            for index, comparison in enumerate(result.comparisons, start=1)
        ]
        provisional = ConverterReport(
            task_id=state["task_id"],
            mode=TaskMode.TECHNICAL_RESEARCH,
            result=result,
            claims=claims,
            evidence=evidence,
            quality_warnings=[],
            completion_status="completed",
        )
        check = validate_report_evidence(provisional)
        status_by_claim = {
            **{claim_id: "supported" for claim_id in check.supported_claim_ids},
            **{claim_id: "conflicting" for claim_id in check.conflicting_claim_ids},
            **{claim_id: "unverified" for claim_id in check.unverified_claim_ids},
        }
        claims = [claim.model_copy(update={"verification_status": status_by_claim[claim.claim_id]}) for claim in claims]
        partial = not result.comparisons or bool(check.conflicting_claim_ids or check.unverified_claim_ids)
        report = provisional.model_copy(
            update={
                "claims": claims,
                "quality_warnings": check.warnings,
                "completion_status": "partial" if partial else "completed",
            }
        )
        return {"report": report, "clarification_questions": []}

    return generate


def make_case_retrieval_node(deps):
    """Create local case ranking, structured comparison, and citation node."""

    async def retrieve(state: ConverterState) -> dict[str, Any]:
        request = CaseRetrievalInput.model_validate(state["inputs"])
        query = " ".join(
            part
            for part in [request.equipment_type or "", request.symptom, str(request.operating_conditions)]
            if part
        )
        chunks = await asyncio.to_thread(deps.vector_index.search, query, limit=10)
        cases = deps.case_store.list_cases() if deps.case_store is not None else []
        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        ranked: dict[str, tuple[CaseRecord, float, Any]] = {}

        for case in cases:
            linked_chunks = [chunks_by_id[evidence_id] for evidence_id in case.evidence_ids if evidence_id in chunks_by_id]
            for chunk in linked_chunks:
                if float(chunk.score) < 0.20:
                    continue
                equipment_score = _field_similarity(request.equipment_type, case.equipment_type, missing_neutral=0.5)
                symptom_score = SequenceMatcher(None, request.symptom.casefold(), case.symptom.casefold()).ratio()
                similarity = 0.55 * max(0.0, min(float(chunk.score), 1.0)) + 0.2 * equipment_score + 0.25 * symptom_score
                if similarity < 0.30:
                    continue
                previous = ranked.get(case.case_id)
                if previous is None or similarity > previous[1]:
                    ranked[case.case_id] = (case, similarity, chunk)

        matches: list[CaseMatch] = []
        evidence: list[Evidence] = []
        claims: list[Claim] = []
        incomplete = False
        for case_id, (case, similarity, chunk) in sorted(ranked.items(), key=lambda item: item[1][1], reverse=True):
            matching_fields, differing_fields, limitations = _compare_case_fields(request, case)
            matches.append(
                CaseMatch(
                    case_id=case_id,
                    similarity_score=round(similarity, 4),
                    matching_fields=matching_fields,
                    differing_fields=differing_fields,
                    applicability_limitations=limitations,
                    evidence_ids=[chunk.chunk_id],
                    equipment_type=case.equipment_type,
                    operating_conditions=case.operating_conditions,
                    symptom=case.symptom,
                    cause=case.cause,
                    validation_result=case.validation_result,
                    handling_result=case.handling_result,
                )
            )
            evidence.append(
                Evidence(
                    evidence_id=chunk.chunk_id,
                    task_id=state["task_id"],
                    title=chunk.filename,
                    source_type="local_document",
                    excerpt=chunk.text,
                    document_id=chunk.document_id,
                    locator=chunk.locator,
                    accessed_at=datetime.now(timezone.utc),
                    source_status="available",
                )
            )
            claims.append(
                Claim(
                    claim_id=f"claim-{len(claims) + 1}",
                    text=f"Case {case.case_id} shares some structured fields with the reported symptom; similarity is a retrieval cue, not proof of cause.",
                    claim_type="inference",
                    evidence_ids=[chunk.chunk_id],
                )
            )
            incomplete = incomplete or bool(limitations)

        result = CaseRetrievalReport(matches=matches)
        warning = "Case similarity helps locate prior records; it does not establish causality or guarantee the same repair outcome."
        quality_warnings = [warning]
        if not matches:
            quality_warnings.append("No linked maintenance case exceeded the minimum similarity threshold.")
        provisional = ConverterReport(
            task_id=state["task_id"],
            mode=TaskMode.CASE_RETRIEVAL,
            result=result,
            claims=claims,
            evidence=evidence,
            quality_warnings=quality_warnings,
            completion_status="partial",
        )
        check = validate_report_evidence(provisional)
        statuses = {
            **{claim_id: "supported" for claim_id in check.supported_claim_ids},
            **{claim_id: "conflicting" for claim_id in check.conflicting_claim_ids},
            **{claim_id: "unverified" for claim_id in check.unverified_claim_ids},
        }
        claims = [claim.model_copy(update={"verification_status": statuses[claim.claim_id]}) for claim in claims]
        report = provisional.model_copy(
            update={
                "claims": claims,
                "quality_warnings": quality_warnings + check.warnings,
                "completion_status": "partial" if incomplete or not matches or check.warnings else "completed",
            }
        )
        return {"report": report, "evidence": evidence, "clarification_questions": []}

    return retrieve


def _field_similarity(query_value: str | None, case_value: str | None, *, missing_neutral: float = 0.0) -> float:
    if not query_value or not case_value:
        return missing_neutral
    return SequenceMatcher(None, query_value.casefold().strip(), case_value.casefold().strip()).ratio()


def _compare_case_fields(request: CaseRetrievalInput, case: CaseRecord) -> tuple[list[str], list[str], list[str]]:
    matching: list[str] = []
    differing: list[str] = []
    limitations: list[str] = []

    if case.equipment_type:
        if _field_similarity(request.equipment_type, case.equipment_type) >= 0.9:
            matching.append("equipment_type")
        elif request.equipment_type:
            differing.append(f"equipment_type: {request.equipment_type} vs {case.equipment_type}")
    else:
        limitations.append("Prior case does not record equipment type.")

    symptom_similarity = _field_similarity(request.symptom, case.symptom)
    if symptom_similarity >= 0.7:
        matching.append("symptom")
    else:
        differing.append(f"symptom: {request.symptom} vs {case.symptom}")

    condition_keys = set(request.operating_conditions) | set(case.operating_conditions)
    for key in sorted(condition_keys):
        query_value = request.operating_conditions.get(key)
        case_value = case.operating_conditions.get(key)
        field = f"operating_conditions.{key}"
        if query_value is None or case_value is None:
            limitations.append(f"Prior case has incomplete operating condition '{key}'.")
        elif str(query_value).strip().casefold() == str(case_value).strip().casefold():
            matching.append(field)
        else:
            differing.append(f"{field}: current {query_value} vs case {case_value}")

    if not case.cause:
        limitations.append("Prior case does not record a validated cause.")
    if not case.validation_result:
        limitations.append("Prior case does not record a validation result.")
    if not case.handling_result:
        limitations.append("Prior case does not record a handling outcome.")
    return matching, differing, limitations


def _web_evidence(item: Any, task_id: str) -> Evidence:
    if isinstance(item, Evidence):
        return item.model_copy(update={"task_id": task_id})
    payload = item if isinstance(item, dict) else {"content": str(item)}
    url = payload.get("url")
    excerpt = payload.get("content") or payload.get("raw_content") or ""
    evidence_id = "web-" + hashlib.sha256(f"{url}|{excerpt}".encode("utf-8")).hexdigest()[:16]
    return Evidence(
        evidence_id=evidence_id,
        task_id=task_id,
        title=payload.get("title") or url or "Web search result",
        source_type="web",
        excerpt=excerpt,
        url=url,
        accessed_at=datetime.now(timezone.utc),
        source_status="available",
    )
