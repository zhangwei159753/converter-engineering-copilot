"""Bounded role-based fault analysis and auditable branch aggregation."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from converter_copilot.agent.audit_prompts import (
    BRANCH_AUDITOR_SYSTEM,
    HYPOTHESIS_ANALYST_SYSTEM,
)
from converter_copilot.agent.nodes import (
    make_intake_node,
    make_research_node,
)
from converter_copilot.agent.prompts import format_research_prompt
from converter_copilot.agent.state import ConverterState
from converter_copilot.config import AgentBudget
from converter_copilot.domain.agent_audit import (
    AgentRunRecord,
    BranchAuditResult,
    HypothesisBranch,
)
from converter_copilot.domain.evidence import validate_report_evidence
from converter_copilot.domain.schemas import (
    Claim,
    ConverterReport,
    FaultAnalysisReport,
    FaultHypothesis,
    TaskMode,
)


def _start_run(
    deps,
    state: ConverterState,
    *,
    role: str,
    stage: str,
    summary: str,
    refs: list[str],
    branch_id: str | None = None,
) -> AgentRunRecord:
    """Create and emit a safe started event for one role execution."""
    record = AgentRunRecord(
        agent_run_id=str(uuid4()),
        task_id=state["task_id"],
        role=role,
        stage=stage,
        status="running",
        branch_id=branch_id,
        input_refs=refs[:100],
        summary=summary,
        retry_count=0,
        started_at=datetime.now(timezone.utc),
    )
    if deps.agent_event_sink is not None:
        deps.agent_event_sink(record)
    return record


def _finish_run(
    deps,
    started: AgentRunRecord,
    *,
    status: str,
    summary: str,
    output_refs: list[str] | None = None,
    error_code: str | None = None,
) -> AgentRunRecord:
    """Complete a role event without retaining private prompt or exception text."""
    record = started.model_copy(
        update={
            "status": status,
            "summary": summary[:4000],
            "output_refs": (output_refs or [])[:100],
            "error_code": error_code,
            "completed_at": datetime.now(timezone.utc),
        }
    )
    if deps.agent_event_sink is not None:
        deps.agent_event_sink(record)
    return record


def make_collaborative_intake_node(deps, budget: AgentBudget):
    """Validate and plan bounded evidence queries as the coordinator role."""
    intake = make_intake_node()

    def run(state: ConverterState):
        started = _start_run(
            deps,
            state,
            role="coordinator",
            stage="intake",
            summary="检查故障输入并生成有限检索计划。",
            refs=["task-input"],
        )
        try:
            result = intake(state)
        except Exception:
            record = _finish_run(
                deps, started, status="failed", summary="输入校验失败。", error_code="agent_intake_failed"
            )
            return {"agent_runs": [record]}
        record = _finish_run(
            deps,
            started,
            status="completed",
            summary=f"生成 {len(result['queries'])} 条有界检索查询。",
            output_refs=[f"query:{index}" for index, _ in enumerate(result["queries"], start=1)],
        )
        return {
            **result,
            "agent_runs": [record],
            "agent_budget_remaining": budget.max_model_calls,
            "agent_warnings": [],
        }

    return run


def make_collaborative_research_node(deps):
    """Run the existing parallel retrieval under the evidence-researcher role."""
    research = make_research_node(deps)

    async def run(state: ConverterState):
        started = _start_run(
            deps,
            state,
            role="evidence_researcher",
            stage="research",
            summary="并发检索公开来源和本地资料。",
            refs=[f"query:{index}" for index, _ in enumerate(state.get("queries", []), start=1)],
        )
        try:
            result = await research(state)
        except Exception:
            record = _finish_run(
                deps, started, status="failed", summary="证据检索未能完成。", error_code="agent_research_failed"
            )
            return {"agent_runs": [record], "agent_warnings": ["证据检索阶段未能完成，请检查本地服务和来源配置。"]}
        evidence = result.get("evidence", [])
        record = _finish_run(
            deps,
            started,
            status="completed",
            summary=f"检索并归一化 {len(evidence)} 条候选证据。",
            output_refs=[item.evidence_id for item in evidence],
        )
        return {**result, "agent_runs": [record]}

    return run


def make_hypothesis_node(deps, budget: AgentBudget):
    """Generate at most the configured number of independently auditable candidates."""

    async def run(state: ConverterState):
        evidence = state.get("evidence", [])
        if not evidence:
            record = AgentRunRecord(
                agent_run_id=str(uuid4()),
                task_id=state["task_id"],
                role="hypothesis_analyst",
                stage="hypothesize",
                status="skipped",
                input_refs=[],
                output_refs=[],
                summary="没有可用证据，未生成故障候选。",
                retry_count=0,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
            if deps.agent_event_sink is not None:
                deps.agent_event_sink(record)
            return {"agent_runs": [record], "hypothesis_candidates": [], "hypothesis_branches": []}

        remaining = state.get("agent_budget_remaining", budget.max_model_calls)
        if remaining < 1:
            warning = "模型调用预算已耗尽，未生成故障候选。"
            record = AgentRunRecord(
                agent_run_id=str(uuid4()),
                task_id=state["task_id"],
                role="hypothesis_analyst",
                stage="hypothesize",
                status="skipped",
                input_refs=[item.evidence_id for item in evidence],
                output_refs=[],
                summary=warning,
                retry_count=0,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
            if deps.agent_event_sink is not None:
                deps.agent_event_sink(record)
            return {
                "agent_runs": [record],
                "hypothesis_candidates": [],
                "hypothesis_branches": [],
                "agent_warnings": [warning],
            }

        started = _start_run(
            deps,
            state,
            role="hypothesis_analyst",
            stage="hypothesize",
            summary="基于现有证据生成最多三个候选原因。",
            refs=[item.evidence_id for item in evidence],
        )
        messages = [
            SystemMessage(content=HYPOTHESIS_ANALYST_SYSTEM),
            HumanMessage(
                content=format_research_prompt(
                    f"Fault inputs: {state['inputs']}",
                    [_evidence_text(item) for item in evidence],
                )
            ),
        ]
        try:
            raw = await deps.model.with_structured_output(FaultAnalysisReport).ainvoke(messages)
            result = FaultAnalysisReport.model_validate(raw)
        except Exception as error:
            error_code = "agent_output_invalid" if isinstance(error, ValidationError) else "agent_hypothesis_failed"
            record = _finish_run(
                deps,
                started,
                status="failed",
                summary="故障候选生成失败，未将错误内容写入审计记录。",
                error_code=error_code,
            )
            return {
                "agent_runs": [record],
                "hypothesis_candidates": [],
                "hypothesis_branches": [],
                "agent_budget_remaining": max(0, remaining - 1),
                "agent_warnings": ["故障候选生成失败；当前结果不能视为完成审查。"],
            }

        candidates = result.hypotheses[: budget.max_hypotheses]
        branches = [
            HypothesisBranch(
                branch_id=f"{state['task_id']}-branch-{index}",
                task_id=state["task_id"],
                hypothesis=candidate.hypothesis,
                mechanism_summary=candidate.mechanism,
                supporting_evidence_ids=[],
                opposing_evidence_ids=[],
                applicability="unknown",
                missing_fields=[],
                checks=candidate.checks,
                gate_status="pending",
                disposition="needs_more_information",
                ordinal=index - 1,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]
        record = _finish_run(
            deps,
            started,
            status="completed",
            summary=f"生成 {len(candidates)} 个候选；超出上限的候选已丢弃。",
            output_refs=[branch.branch_id for branch in branches],
        )
        return {
            "hypothesis_candidates": candidates,
            "analysis_checks": result.checks,
            "hypothesis_branches": branches,
            "agent_runs": [record],
            "agent_budget_remaining": max(0, remaining - 1),
            "agent_warnings": [],
        }

    return run


def make_branch_audit_node(deps, budget: AgentBudget):
    """Audit candidate branches concurrently within the task's model-call budget."""

    async def call_model(branch: HypothesisBranch, candidate: FaultHypothesis, evidence, inputs, started):
        evidence_by_id = {item.evidence_id: item for item in evidence}
        messages = [
            SystemMessage(content=BRANCH_AUDITOR_SYSTEM),
            HumanMessage(
                content=format_research_prompt(
                    f"Review candidate: {candidate.hypothesis}\nMechanism: {candidate.mechanism}\n"
                    f"Operating inputs: {inputs}\n"
                    "Return only exact evidence IDs that support or oppose this candidate.",
                    [_evidence_text(item) for item in evidence],
                )
            ),
        ]
        try:
            raw = await deps.model.with_structured_output(BranchAuditResult).ainvoke(messages)
            audit = BranchAuditResult.model_validate(raw)
            valid_support = [item for item in audit.supporting_evidence_ids if item in evidence_by_id]
            valid_opposing = [item for item in audit.opposing_evidence_ids if item in evidence_by_id]
            applicable_support = [
                evidence_by_id[item]
                for item in valid_support
                if evidence_by_id[item].applicability not in {"related", "cross_equipment"}
                and evidence_by_id[item].source_status != "unavailable"
            ]
            support_ids = [item.evidence_id for item in applicable_support]
            applicability = _branch_applicability(applicable_support)
            updated = branch.model_copy(
                update={
                    "supporting_evidence_ids": support_ids,
                    "opposing_evidence_ids": valid_opposing,
                    "applicability": applicability,
                    "missing_fields": audit.missing_fields,
                    "checks": list(dict.fromkeys(branch.checks + audit.checks)),
                }
            )
            status = "completed"
            summary = audit.rationale_summary or f"检查了 {len(support_ids)} 条支持证据和 {len(valid_opposing)} 条反证。"
            warning = None
            unknown_ids = sorted((set(audit.supporting_evidence_ids) | set(audit.opposing_evidence_ids)) - set(evidence_by_id))
            if unknown_ids:
                warning = f"候选 {branch.ordinal + 1} 引用了不存在的证据 ID，已从支持列表移除。"
            return updated, _finish_run(
                deps,
                started,
                status=status,
                summary=summary,
                output_refs=support_ids + valid_opposing,
            ), warning
        except Exception as error:
            error_code = "agent_audit_invalid" if isinstance(error, ValidationError) else "agent_audit_failed"
            incomplete = branch.model_copy(update={"gate_status": "review_incomplete", "disposition": "needs_more_information"})
            return incomplete, _finish_run(
                deps,
                started,
                status="failed",
                summary="该候选的证据审查未完成。",
                error_code=error_code,
            ), "至少一个候选未完成独立证据审查。"

    async def audit(state: ConverterState):
        branches = state.get("hypothesis_branches", [])
        candidates = state.get("hypothesis_candidates", [])
        if not branches:
            return {"hypothesis_branches": [], "agent_runs": [], "agent_budget_remaining": state.get("agent_budget_remaining", budget.max_model_calls)}
        remaining = state.get("agent_budget_remaining", budget.max_model_calls)
        authorized = min(len(branches), budget.max_hypotheses, remaining, budget.max_hypotheses * budget.max_audit_passes)
        semaphore = asyncio.Semaphore(budget.max_parallel_auditors)

        async def one(branch: HypothesisBranch):
            candidate = candidates[branch.ordinal]
            if branch.ordinal >= authorized:
                record = AgentRunRecord(
                    agent_run_id=str(uuid4()),
                    task_id=state["task_id"],
                    role="evidence_auditor",
                    stage="audit",
                    status="skipped",
                    branch_id=branch.branch_id,
                    input_refs=branch.supporting_evidence_ids,
                    output_refs=[],
                    summary="模型调用预算不足，候选未完成独立审查。",
                    retry_count=0,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                )
                if deps.agent_event_sink is not None:
                    deps.agent_event_sink(record)
                return branch.model_copy(update={"gate_status": "review_incomplete"}), record, "候选审查因预算耗尽而跳过。"
            async with semaphore:
                started = _start_run(
                    deps,
                    state,
                    role="evidence_auditor",
                    stage="audit",
                    summary=f"独立审查候选 {branch.ordinal + 1} 的支持证据、反证和适用性。",
                    refs=[branch.branch_id, *[item.evidence_id for item in state.get("evidence", [])]],
                    branch_id=branch.branch_id,
                )
                return await call_model(branch, candidate, state.get("evidence", []), state.get("inputs", {}), started)

        results = await asyncio.gather(*(one(branch) for branch in branches))
        reviewed = [item[0] for item in results]
        records = [item[1] for item in results]
        warnings = [item[2] for item in results if item[2]]
        return {
            "hypothesis_branches": reviewed,
            "agent_runs": records,
            "agent_warnings": warnings,
            "agent_budget_remaining": max(0, remaining - authorized),
        }

    return audit


def make_evidence_gate_node(deps):
    """Apply deterministic citation and equipment-applicability gates per branch."""

    def gate(state: ConverterState):
        evidence_by_id = {item.evidence_id: item for item in state.get("evidence", [])}
        branches = []
        warnings = []
        for branch in state.get("hypothesis_branches", []):
            if branch.gate_status == "review_incomplete":
                branches.append(branch)
                continue
            support_ids = [item for item in branch.supporting_evidence_ids if item in evidence_by_id]
            support = [evidence_by_id[item] for item in support_ids]
            if not support:
                branches.append(branch.model_copy(update={"gate_status": "blocked", "disposition": "omitted"}))
                warnings.append(f"候选 {branch.ordinal + 1} 没有可解析的直接支持证据，已从原因结论中移除。")
                continue
            if any(item.applicability in {"related", "cross_equipment"} for item in support):
                branches.append(branch.model_copy(update={"gate_status": "blocked", "disposition": "omitted"}))
                warnings.append(f"候选 {branch.ordinal + 1} 仅有跨设备或相关设备材料支撑，不能用于目标设备结论。")
                continue
            if branch.applicability == "unknown":
                warnings.append(f"候选 {branch.ordinal + 1} 的来源设备适用性未知，请人工核对。")
            branches.append(
                branch.model_copy(update={"gate_status": "qualified", "disposition": "retained"})
            )
        if deps.agent_result_sink is not None:
            deps.agent_result_sink(branches)
        return {"hypothesis_branches": branches, "agent_warnings": warnings}

    return gate


def make_synthesis_node(deps):
    """Build the legacy-compatible report from branches that passed the evidence gate."""

    def synthesize(state: ConverterState):
        started = _start_run(
            deps,
            state,
            role="report_synthesizer",
            stage="synthesize",
            summary="汇总通过证据门禁的候选及其来源。",
            refs=[branch.branch_id for branch in state.get("hypothesis_branches", [])],
        )
        evidence = state.get("evidence", [])
        candidates = state.get("hypothesis_candidates", [])
        branches = state.get("hypothesis_branches", [])
        accepted = [branch for branch in branches if branch.gate_status == "qualified" and branch.disposition == "retained"]
        hypotheses = []
        for branch in accepted:
            candidate = candidates[branch.ordinal]
            hypotheses.append(
                candidate.model_copy(
                    update={
                        "supporting_evidence_ids": branch.supporting_evidence_ids,
                        "opposing_evidence_ids": branch.opposing_evidence_ids,
                        "checks": branch.checks,
                        "confidence_basis": candidate.confidence_basis + (
                            f" Evidence audit: {branch.mechanism_summary}" if branch.mechanism_summary else ""
                        ),
                    }
                )
            )
        result = FaultAnalysisReport(
            hypotheses=hypotheses,
            checks=list(dict.fromkeys([
                *state.get("analysis_checks", []),
                *[check for branch in branches for check in branch.checks],
            ])),
        )
        claims = [
            Claim(
                claim_id=f"claim-{index}",
                text=hypothesis.hypothesis,
                claim_type="inference",
                evidence_ids=hypothesis.supporting_evidence_ids,
            )
            for index, hypothesis in enumerate(hypotheses, start=1)
        ]
        provisional = ConverterReport(
            task_id=state["task_id"],
            mode=TaskMode.FAULT_ANALYSIS,
            result=result,
            claims=claims,
            evidence=evidence,
            quality_warnings=list(state.get("agent_warnings", [])),
            completion_status="completed",
        )
        check = validate_report_evidence(provisional)
        status_by_id = {
            **{claim_id: "supported" for claim_id in check.supported_claim_ids},
            **{claim_id: "conflicting" for claim_id in check.conflicting_claim_ids},
            **{claim_id: "unverified" for claim_id in check.unverified_claim_ids},
        }
        final_claims = []
        final_hypotheses = []
        for hypothesis, claim in zip(hypotheses, claims, strict=True):
            status = status_by_id.get(claim.claim_id, "unverified")
            if status == "unverified":
                continue
            final_claims.append(claim.model_copy(update={"claim_id": f"claim-{len(final_claims) + 1}", "verification_status": status}))
            final_hypotheses.append(hypothesis)
        warnings = list(provisional.quality_warnings) + check.warnings
        omitted = len([branch for branch in branches if branch.disposition != "retained"])
        if omitted:
            warnings.append(f"Omitted {omitted} candidate branch(es) that lacked completed, applicable evidence review.")
        if check.conflicting_claim_ids:
            warnings.append("Conflicting evidence remains linked; qualified engineering review is required.")
        result = result.model_copy(update={"hypotheses": final_hypotheses})
        report = provisional.model_copy(
            update={
                "result": result,
                "claims": final_claims,
                "quality_warnings": list(dict.fromkeys(warnings)),
                "completion_status": "partial" if (
                    state.get("agent_warnings") or omitted or check.conflicting_claim_ids or check.warnings
                ) else "completed",
            }
        )
        record = _finish_run(
            deps,
            started,
            status="completed",
            summary=f"报告保留 {len(final_hypotheses)} 个通过引用校验的候选。",
            output_refs=[claim.claim_id for claim in final_claims],
        )
        return {"report": report, "agent_runs": [record], "clarification_questions": []}

    return synthesize


def _evidence_text(item) -> str:
    """Render only the evidence fields required by the downstream role."""
    return (
        f"[{item.evidence_id}] {item.title} ({item.locator or item.url or 'source'})\n"
        f"设备：{item.equipment_type or '未标注'}；适用性：{item.applicability}；状态：{item.source_status}\n"
        f"{item.excerpt}"
    )


def _branch_applicability(support) -> str:
    """Summarize support applicability without upgrading unknown sources to direct."""
    labels = {item.applicability for item in support}
    if "cross_equipment" in labels:
        return "cross_equipment"
    if "related" in labels:
        return "related"
    if support and labels == {"direct"}:
        return "direct"
    return "unknown"
