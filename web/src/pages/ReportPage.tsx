import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, FileCheck2, FileWarning } from "lucide-react";
import { getReport } from "../api/client";
import EvidenceDrawer from "../components/EvidenceDrawer";
import ReportViews from "../components/ReportViews";
import type { ConverterReport, Evidence } from "../types";

function reportMarkdown(report: ConverterReport) {
  const hypotheses = report.result.hypotheses ?? [];
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
