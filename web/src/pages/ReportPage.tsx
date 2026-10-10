import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, FileCheck2, FileWarning } from "lucide-react";
import { getReport } from "../api/client";
import EvidenceDrawer from "../components/EvidenceDrawer";
import ReportViews from "../components/ReportViews";
import type { ConverterReport, Evidence } from "../types";

function reportMarkdown(report: ConverterReport) {
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
  const [report, setReport] = useState<ConverterReport | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getReport(taskId).then(setReport).catch((reason) => setError(reason instanceof Error ? reason.message : "报告暂不可用。")); }, [taskId]);
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
  return <div className="report-page"><Link className="back-link" to={`/tasks/${taskId}`}><ArrowLeft size={15} />返回任务进度</Link><div className="report-heading"><div><span className="section-kicker">REPORT / {taskId.slice(0, 8).toUpperCase()}</span><h1>工程分析报告<span>.</span></h1><p>结论与证据相互关联，点击来源编号查看原文摘录。</p></div><div className="report-actions"><span className={`completion-badge ${report.completion_status}`}><FileCheck2 size={14} />{report.completion_status === "completed" ? "完整报告" : "部分报告"}</span><button className="secondary-button" onClick={exportMarkdown}><Download size={15} />导出 Markdown</button></div></div><ReportViews report={report} onEvidence={(id) => setSelectedEvidence(evidenceById.get(id) ?? null)} /><EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} /></div>;
}
