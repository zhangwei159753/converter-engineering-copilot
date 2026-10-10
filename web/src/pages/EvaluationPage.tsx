import { useEffect, useMemo, useState } from "react";
import { Check, ClipboardCheck, FileUp, History, RefreshCw, ShieldAlert } from "lucide-react";
import { getEvaluationHistory, getEvaluationSummary, importReviewBundle, listEvaluationExamples, saveEvaluationReview } from "../api/client";
import type { EvaluationExample, EvaluationReviewEvent, EvaluationSummary, EvidenceReviewLabel } from "../types";

const labelOptions: Array<{ value: EvidenceReviewLabel; label: string }> = [
  { value: "supported", label: "支持" }, { value: "unsupported", label: "不支持" }, { value: "unverified", label: "无法判断" },
];
const applicabilityLabel = { direct: "同设备", related: "同系列参考", cross_equipment: "跨设备参考", unknown: "设备未标注" } as const;

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("无法读取所选 JSON 文件。"));
    reader.readAsText(file);
  });
}

export default function EvaluationPage() {
  const [summary, setSummary] = useState<EvaluationSummary | null>(null);
  const [examples, setExamples] = useState<EvaluationExample[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [mode, setMode] = useState("");
  const [status, setStatus] = useState("");
  const [sourceType, setSourceType] = useState("");
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("evaluation-reviewer") ?? "");
  const [labels, setLabels] = useState<EvaluationExample["evidence_labels"]>([]);
  const [retrievalIds, setRetrievalIds] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [history, setHistory] = useState<EvaluationReviewEvent[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const selected = examples.find((example) => example.review_id === selectedId) ?? examples[0];
  const evidenceById = useMemo(() => new Map((selected?.evidence ?? []).map((item) => [item.evidence_id, item])), [selected]);

  async function refresh() {
    const [nextSummary, nextRows] = await Promise.all([
      getEvaluationSummary(), listEvaluationExamples({ mode, status, source_type: sourceType, limit: 100 }),
    ]);
    setSummary(nextSummary);
    setExamples(nextRows.items);
    setSelectedId((current) => nextRows.items.some((item) => item.review_id === current) ? current : nextRows.items[0]?.review_id ?? "");
  }

  useEffect(() => { refresh().catch((reason) => setError(reason instanceof Error ? reason.message : "评测台账加载失败。")); }, [mode, status, sourceType]);
  useEffect(() => {
    if (!selected) return;
    setLabels(selected.evidence_labels);
    setRetrievalIds(selected.expected_retrieval_ids);
    setNote(selected.note ?? "");
    getEvaluationHistory(selected.review_id).then(setHistory).catch(() => setHistory([]));
  }, [selected?.review_id]);

  async function upload(file?: File) {
    if (!file) return;
    setError(""); setNotice(""); setBusy(true);
    try {
      const bundle = JSON.parse(await readFile(file)) as unknown;
      const result = await importReviewBundle(bundle);
      setNotice(result.reused ? "该 review bundle 已导入，复用原有快照。" : `已导入 ${result.imported_count} 条待复核样例。`);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bundle 导入失败，请检查 JSON 文件。 ");
    } finally { setBusy(false); }
  }

  function changeLabel(evidenceId: string, claimId: string, label: EvidenceReviewLabel) {
    setLabels((current) => {
      const rest = current.filter((item) => item.evidence_id !== evidenceId || item.claim_id !== claimId);
      return [...rest, { evidence_id: evidenceId, claim_id: claimId, label }];
    });
  }

  async function submitReview() {
    if (!selected) return;
    setBusy(true); setError(""); setNotice("");
    try {
      localStorage.setItem("evaluation-reviewer", reviewer);
      await saveEvaluationReview(selected.review_id, { reviewer_id: reviewer, evidence_labels: labels, expected_retrieval_ids: retrievalIds, note });
      setNotice("复核已保存，审计历史已追加。");
      await refresh();
      setHistory(await getEvaluationHistory(selected.review_id));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "复核保存失败。"); }
    finally { setBusy(false); }
  }

  const counts = Object.values(summary?.modes ?? {}).reduce((total, item) => ({
    total: total.total + item.total, pending: total.pending + item.pending, reviewed: total.reviewed + item.reviewed,
  }), { total: 0, pending: 0, reviewed: 0 });
  const sourceTypes = Object.entries(summary?.modes ?? {})
    .filter(([modeName]) => !mode || modeName === mode)
    .flatMap(([, item]) => Object.keys(item.source_types));
  const uniqueSourceTypes = [...new Set(sourceTypes)].sort();
  const outputContext = selected?.output_context ?? {};

  return <div className="evaluation-page">
    <div className="workspace-heading"><div><div className="breadcrumb"><span>质量管理</span><span>／</span><span>人工评测</span></div><h1>把模型输出放进复核闭环<span>.</span></h1><p>复核证据与检索标签，保留每次修改记录；未复核数据不会进入质量指标。</p></div><div className="heading-stamp"><span>HUMAN REVIEW</span><strong>{String(counts.total).padStart(2, "0")}</strong><small>LOCAL EVALUATION</small></div></div>
    {error && <div className="inline-alert" role="alert">{error}</div>}{notice && <div className="inline-notice" role="status">{notice}</div>}
    <section className="eval-overview" aria-label="评测概览">
      <div><small>全部样例</small><strong>{counts.total}</strong></div><div><small>待复核</small><strong>{counts.pending}</strong></div><div><small>已复核</small><strong>{counts.reviewed}</strong></div>
      <label className="eval-upload"><FileUp size={16} />{busy ? "处理中…" : "导入 review bundle"}<input aria-label="导入 review bundle" type="file" accept="application/json,.json" disabled={busy} onChange={(event) => upload(event.target.files?.[0])} /></label>
    </section>
    <div className="eval-filters"><label>工作流<select value={mode} onChange={(event) => setMode(event.target.value)}><option value="">全部工作流</option><option value="fault_analysis">故障分析</option><option value="technical_research">技术研究</option></select></label><label>复核状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="pending">待复核</option><option value="reviewed">已复核</option></select></label><label>来源类型<select value={sourceType} onChange={(event) => setSourceType(event.target.value)}><option value="">全部来源</option>{uniqueSourceTypes.map((value) => <option key={value} value={value}>{value}</option>)}</select></label><div className="eval-metric-note"><ShieldAlert size={14} />{summary?.metric_status.retrieval_recall_at_k === "insufficient_labels" || summary?.metric_status.manual_evidence_support_rate === "insufficient_labels" ? "标签不足，未达到条件的指标显示 null。" : "质量指标使用已复核标签计算。"} 检索 {summary?.metric_sample_counts.retrieval_labeled_examples ?? 0} 条 · 证据 {summary?.metric_sample_counts.reviewed_evidence_labels ?? 0} 条</div></div>
    <div className="eval-layout">
      <aside className="eval-queue"><div className="aside-title"><span>复核队列</span><span className="count-badge">{examples.length.toString().padStart(2, "0")}</span></div>{examples.length ? examples.map((example) => <button key={example.review_id} className={`eval-queue-row ${selected?.review_id === example.review_id ? "selected" : ""}`} onClick={() => setSelectedId(example.review_id)}><span className={`eval-queue-state ${example.status}`}>{example.status === "reviewed" ? <Check size={12} /> : "待"}</span><span><strong>{example.example_id}</strong><small>{example.mode === "fault_analysis" ? "故障分析" : "技术研究"} · {example.source_type} · {example.bundle_id.slice(-6)}</small></span></button>) : <p className="eval-empty">暂无样例。请导入一次评测运行导出的 review bundle。</p>}</aside>
      <section className="eval-review-panel">{selected ? <>
        <div className="eval-review-head"><div><span className="section-kicker">{selected.mode} · {selected.dataset_version}</span><h2>{selected.example_id}</h2><p>来源：{selected.source_type} · 任务：{selected.task_id}</p></div><span className={`eval-status ${selected.status}`}>{selected.status === "reviewed" ? `已复核 v${selected.label_version}` : "待复核"}</span></div>
        <div className="eval-inputs"><strong>评测输入快照</strong><pre>{JSON.stringify(selected.inputs, null, 2)}</pre></div>
        {(outputContext.quality_warnings?.length || outputContext.clarification_questions?.length || outputContext.failure) ? <section className="eval-output-context" aria-label="模型输出复核上下文"><div className="eval-section-head"><div><span className="panel-index">!</span><strong>运行质量上下文</strong></div><small>{outputContext.completion_status ?? "状态未知"}</small></div>{outputContext.failure && <div className="inline-alert">运行失败：{outputContext.failure.error_code ?? "unknown"} · {outputContext.failure.error_type ?? "未知错误"}{outputContext.failure.field_paths?.length ? <small>字段：{outputContext.failure.field_paths.join("、")}</small> : null}</div>}{outputContext.quality_warnings?.map((warning) => <p className="context-warning" key={warning}>{warning}</p>)}{outputContext.clarification_questions?.length ? <div className="clarification-questions"><strong>建议向现场补充的信息</strong><ul>{outputContext.clarification_questions.map((question) => <li key={question}>{question}</li>)}</ul></div> : null}</section> : null}
        <div className="eval-section-head"><div><span className="panel-index">01</span><strong>证据与主张</strong></div><small>检查摘录、定位与来源后再标注</small></div>
        {selected.claims.length ? selected.claims.map((claim) => <article className="eval-claim" key={claim.claim_id}><div className="eval-claim-copy"><span>{claim.claim_id}</span><p>{claim.text}</p></div>{claim.evidence_ids?.length ? claim.evidence_ids.map((evidenceId) => {
          const evidence = evidenceById.get(evidenceId);
          const current = labels.find((item) => item.evidence_id === evidenceId && item.claim_id === claim.claim_id)?.label;
          return <div className="eval-evidence" key={`${claim.claim_id}-${evidenceId}`}><div className="eval-evidence-meta"><strong>{evidenceId}</strong><span>{evidence?.source_type ?? "证据快照"} · {evidence?.locator ?? "无定位信息"}</span></div><div className={`applicability-badge ${evidence?.applicability ?? "unknown"}`}>{applicabilityLabel[evidence?.applicability ?? "unknown"]}{evidence?.equipment_type ? ` · ${evidence.equipment_type}` : ""}</div><p>{evidence?.excerpt ?? "该证据未包含可显示的摘录。"}</p>{evidence?.url && <a href={evidence.url} target="_blank" rel="noreferrer">查看来源 ↗</a>}<div className="eval-label-options" role="group" aria-label={`${claim.claim_id} 对 ${evidenceId} 的标注`}>{labelOptions.map((option) => <button type="button" key={option.value} className={current === option.value ? "active" : ""} onClick={() => changeLabel(evidenceId, claim.claim_id, option.value)}>{option.label}</button>)}</div></div>;
        }) : <div className="eval-no-evidence">该主张没有关联证据。</div>}</article>) : <div className="eval-empty">该样例没有生成主张。</div>}
        <div className="eval-section-head"><div><span className="panel-index">02</span><strong>检索相关性</strong></div><small>选择本次运行中应被检索到的证据</small></div>
        <div className="eval-retrieval-list">{selected.evidence.length ? selected.evidence.map((item) => <label key={item.evidence_id}><input type="checkbox" checked={retrievalIds.includes(item.evidence_id)} onChange={(event) => setRetrievalIds((ids) => event.target.checked ? [...ids, item.evidence_id] : ids.filter((id) => id !== item.evidence_id))} /><span><strong>{item.evidence_id}</strong><small>{item.locator ?? item.source_type ?? "证据快照"}</small><span className={`applicability-badge ${item.applicability ?? "unknown"}`}>{applicabilityLabel[item.applicability ?? "unknown"]}{item.equipment_type ? ` · ${item.equipment_type}` : ""}</span></span></label>) : <p className="eval-empty">没有检索证据快照，检索指标暂不可标注。</p>}</div>
        <div className="eval-review-form"><label>复核人<input value={reviewer} maxLength={120} onChange={(event) => setReviewer(event.target.value)} placeholder="输入本地复核人标识" /></label><label>复核备注<textarea value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} placeholder="记录判定依据或需要回看的问题" /></label><button type="button" className="primary-button" disabled={busy || !reviewer.trim()} onClick={submitReview}><ClipboardCheck size={15} />{busy ? "保存中…" : "保存复核"}</button></div>
        <div className="eval-history"><div className="aside-title"><span><History size={14} /> 复核历史</span><button type="button" aria-label="刷新复核历史" onClick={() => getEvaluationHistory(selected.review_id).then(setHistory)}><RefreshCw size={13} /></button></div>{history.length ? history.map((event) => <div className="eval-history-row" key={`${event.review_id}-${event.label_version}`}><strong>v{event.label_version} · {event.reviewer_id}</strong><small>{new Date(event.reviewed_at).toLocaleString("zh-CN")} · {event.evidence_labels.length} 个证据标签</small>{event.note && <p>{event.note}</p>}</div>) : <p className="eval-empty">保存复核后会在这里留下不可覆盖的历史版本。</p>}</div>
      </> : <div className="eval-empty eval-empty-large"><ClipboardCheck size={22} /><strong>评测工作台已就绪</strong><p>导入一个 review bundle 后，在这里检查证据并创建人工标签。</p></div>}</section>
    </div>
    <div className="eval-governance"><ShieldAlert size={15} /><span>人工标签只记录评审者对当前快照的判断，不代表现场验证结论，也不证明维护收益。演示样例不会被视作真实现场数据。</span></div>
  </div>;
}
