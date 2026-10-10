import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Download, FileCheck2, FileWarning } from "lucide-react";
import { getAgentRuns, getHypothesisBranches, getReport } from "../api/client";
import AgentTimeline from "../components/AgentTimeline";
import EvidenceDrawer from "../components/EvidenceDrawer";
import HypothesisTree from "../components/HypothesisTree";
import ReportViews from "../components/ReportViews";
import type { AgentRun, ConverterReport, Evidence, HypothesisBranch } from "../types";

function reportMarkdown(report: ConverterReport) {
  const clarificationQuestions = report.clarification_questions ?? [];
  const hypotheses = report.result.hypotheses ?? [];
  const comparisons = report.result.comparisons ?? [];
  const cases = report.result.matches ?? [];
  return [
    `# 变流器工程分析报告`,
    ``,
    `- 任务：${report.task_id}`,
    `- 完成状态：${report.completion_status}`,
    ``,
    `## 故障假设`,
    ...hypotheses.flatMap((item, index) => [`### ${index + 1}. ${item.hypothesis}`, item.mechanism, `置信度：${item.confidence}；依据：${item.confidence_basis}`, `建议检查：${item.checks.join("；")}`, `引用：${item.supporting_evidence_ids.join(", ") || "无"}`, ``]),
    `## 核验清单`,
    ...(report.result.checks ?? []).map((item) => `- ${item}`),
    ...(clarificationQuestions.length ? [``, `## 需要补充的信息`, ...clarificationQuestions.map((item) => `- ${item}`)] : []),
    ...(report.mode === "technical_research" ? [``, `## 技术路线对照`, ...comparisons.map((item) => `- **${String(item.item)}**：${String(item.method_or_value)}；条件：${String(item.experimental_conditions ?? "未报告")}；来源：${(item.evidence_ids as string[] | undefined)?.join(", ") ?? "无"}`), ``, `## 研究局限`, ...(report.result.limitations ?? []).map((item) => `- ${item}`), ``, `## 可复现实验步骤`, ...(report.result.experiment_steps ?? []).map((item) => `- ${item}`)] : []),
    ...(report.mode === "case_retrieval" ? [``, `## 相似检修案例`, ...cases.map((item) => `- **${String(item.case_id)}**（相似度 ${Math.round(Number(item.similarity_score ?? 0) * 100)}%）：${String(item.symptom ?? "未记录现象")}；设备：${String(item.equipment_type ?? "未记录")}；原因：${String(item.cause ?? "未记录")}；验证：${String(item.validation_result ?? "未记录")}；处置：${String(item.handling_result ?? "未记录")}；差异：${(item.differing_fields as string[] | undefined)?.join("；") ?? "无"}；来源：${(item.evidence_ids as string[] | undefined)?.join(", ") ?? "无"}`)] : []),
    ``,
    `## 质量提示`,
    ...report.quality_warnings.map((item) => `- ${item}`),
    ``,
    `## 来源`,
    ...report.evidence.map((item) => `- [${item.evidence_id}] ${item.title} — ${item.locator ?? ""} ${item.url ?? ""}`),
  ].join("\n");
}

export default function ReportPage() {
  const { taskId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const workOrderId = searchParams.get("workOrderId");
  const [report, setReport] = useState<ConverterReport | null>(null);
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [branches, setBranches] = useState<HypothesisBranch[]>([]);
  const [agentError, setAgentError] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getReport(taskId).then((value) => { if (active) setReport(value); }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "报告暂不可用。");
    });
    Promise.all([getAgentRuns(taskId), getHypothesisBranches(taskId)]).then(([runs, tree]) => {
      if (!active) return;
      setAgentRuns(runs.items);
      setBranches(tree.items);
    }).catch((reason) => {
      if (active) setAgentError(reason instanceof Error ? reason.message : "无法读取 Agent 协作记录。");
    });
    return () => { active = false; };
  }, [taskId]);
  const evidenceById = useMemo(() => new Map((report?.evidence ?? []).map((item) => [item.evidence_id, item])), [report]);

  function exportMarkdown() {
    if (!report) return;
    const blob = new Blob([reportMarkdown(report)], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `converter-report-${report.task_id.slice(0, 8)}.md`;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (error) return <div className="loading-panel"><FileWarning size={18} />{error}</div>;
  if (!report) return <div className="loading-panel">正在整理证据与结论……</div>;
  const clarificationQuestions = report.clarification_questions ?? [];
  return <div className="report-page"><Link className="back-link" to={`/tasks/${taskId}${workOrderId ? `?workOrderId=${encodeURIComponent(workOrderId)}` : ""}`}><ArrowLeft size={15} />返回任务进度</Link>{workOrderId && <Link className="back-link report-work-order-link" to={`/work-orders?selected=${encodeURIComponent(workOrderId)}`}><ArrowLeft size={15} />返回来源工单 {workOrderId}</Link>}<div className="report-heading"><div><span className="section-kicker">REPORT / {taskId.slice(0, 8).toUpperCase()}</span><h1>工程分析报告<span>.</span></h1><p>结论与证据相互关联，点击来源编号查看原文摘录。</p></div><div className="report-actions"><span className={`completion-badge ${report.completion_status}`}><FileCheck2 size={14} />{report.completion_status === "completed" ? "完整报告" : "部分报告"}</span><button className="secondary-button" onClick={exportMarkdown}><Download size={15} />导出 Markdown</button></div></div>{clarificationQuestions.length > 0 && <section className="clarification-questions report-clarifications"><strong>形成结论前建议补充的信息</strong><p>当前工况信息不足，以下问题用于完善证据，不构成现场操作指令。</p><ul>{clarificationQuestions.map((question) => <li key={question}>{question}</li>)}</ul></section>}{report.mode === "fault_analysis" && <div className="report-collaboration">{agentError && <div className="inline-alert" role="alert">Agent 协作记录加载失败：{agentError}</div>}<AgentTimeline runs={agentRuns} /><HypothesisTree branches={branches} evidenceIds={new Set(evidenceById.keys())} onEvidence={(id) => setSelectedEvidence(evidenceById.get(id) ?? null)} /></div>}<ReportViews report={report} onEvidence={(id) => setSelectedEvidence(evidenceById.get(id) ?? null)} /><EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} /></div>;
}
