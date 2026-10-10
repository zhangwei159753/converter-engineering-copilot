import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Activity, AlertTriangle, ArrowUpRight, Check, ChevronRight, ClipboardList, Clock3, FileText, RotateCw, Search, ShieldCheck, TriangleAlert } from "lucide-react";
import { createWorkOrderAnalysis, getWorkOrder, listWorkOrders, saveWorkOrderReview } from "../api/client";
import ReviewedMemoryPanel from "../components/ReviewedMemoryPanel";
import type { WorkOrder, WorkOrderAnalysis, WorkOrderDetail, WorkOrderReview } from "../types";

const STATUS_LABEL: Record<WorkOrder["status"], string> = { open: "待研判", investigating: "处理中", resolved: "已解决" };
const SEVERITY_LABEL: Record<WorkOrder["severity"], string> = { high: "高", medium: "中", low: "低" };
const VERDICT_LABEL: Record<WorkOrderReview["verdict"], string> = {
  accepted: "接受建议", needs_more_information: "需补充信息", rejected: "不采纳",
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

function taskStatusLabel(status: string) {
  return ({ queued: "排队中", running: "分析中", completed: "分析完成", partial: "部分完成", failed: "分析失败" } as Record<string, string>)[status] ?? status;
}

export default function WorkOrdersPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [detail, setDetail] = useState<WorkOrderDetail | null>(null);
  const [selectedId, setSelectedId] = useState(searchParams.get("selected") ?? "");
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [acting, setActing] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [verdict, setVerdict] = useState<WorkOrderReview["verdict"]>("needs_more_information");
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState("");

  const loadOrders = useCallback(async () => {
    setLoading(true);
    try {
      const response = await listWorkOrders({ q: query.trim(), status });
      setOrders(response.items);
      const nextId = response.items.some((item) => item.work_order_id === selectedId)
        ? selectedId
        : response.items[0]?.work_order_id ?? "";
      setSelectedId(nextId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法加载工单队列。");
    } finally {
      setLoading(false);
    }
  }, [query, status, selectedId]);

  const loadDetail = useCallback(async (workOrderId: string) => {
    if (!workOrderId) { setDetail(null); return; }
    try {
      const response = await getWorkOrder(workOrderId);
      if (selectedIdRef.current !== workOrderId) return;
      setDetail(response);
      setError("");
      const selectedStillExists = response.analyses.some((item) => item.task.task_id === selectedTaskId);
      setSelectedTaskId(selectedStillExists ? selectedTaskId : response.analyses[0]?.task.task_id ?? "");
    } catch (reason) {
      if (selectedIdRef.current !== workOrderId) return;
      setError(reason instanceof Error ? reason.message : "无法加载工单详情。");
    }
  }, [selectedTaskId]);

  useEffect(() => { void loadOrders(); }, [loadOrders]);
  useEffect(() => { void loadDetail(selectedId); }, [selectedId, loadDetail]);
  useEffect(() => {
    if (selectedId) setSearchParams({ selected: selectedId }, { replace: true });
  }, [selectedId, setSearchParams]);

  const currentDetail = detail?.work_order.work_order_id === selectedId ? detail : null;
  const selected = currentDetail?.work_order ?? orders.find((item) => item.work_order_id === selectedId) ?? null;
  const activeAnalysis: WorkOrderAnalysis | null = useMemo(
    () => currentDetail?.analyses.find((item) => item.task.task_id === selectedTaskId) ?? currentDetail?.analyses[0] ?? null,
    [currentDetail, selectedTaskId],
  );

  async function startAnalysis() {
    if (!selected) return;
    setActing(true); setError(""); setNotice("");
    try {
      const result = await createWorkOrderAnalysis(selected.work_order_id);
      await loadDetail(selected.work_order_id);
      await loadOrders();
      if (selectedIdRef.current === selected.work_order_id) {
        setSelectedTaskId(result.task.task_id);
        navigate(`/tasks/${result.task.task_id}?workOrderId=${encodeURIComponent(selected.work_order_id)}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法启动工单分析。");
    } finally { setActing(false); }
  }

  async function submitReview(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !activeAnalysis) return;
    setActing(true); setError(""); setNotice("");
    try {
      await saveWorkOrderReview(selected.work_order_id, activeAnalysis.task.task_id, { reviewer, verdict, note });
      await loadDetail(selected.work_order_id);
      setNotice("审核记录已追加保存。");
      setNote("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法保存审核记录。");
    } finally { setActing(false); }
  }

  const counts = useMemo(() => ({
    total: orders.length,
    open: orders.filter((item) => item.status === "open").length,
    investigating: orders.filter((item) => item.status === "investigating").length,
    high: orders.filter((item) => item.severity === "high").length,
  }), [orders]);

  return <div className="work-orders-page">
    <div className="work-orders-heading">
      <div><span className="section-kicker">FIELD SERVICE / WORK ORDER DESK</span><h1>故障工单<span>.</span></h1><p>从现场记录到可追溯研判，再由工程师完成审核。</p></div>
      <div className="readonly-stamp"><ShieldCheck size={15} /><span><strong>只读演示连接器</strong><small>合成数据 · 不连接设备</small></span></div>
    </div>

    <div className="work-order-metrics">
      <div className="wo-metric"><span>当前队列</span><strong>{String(counts.total).padStart(2, "0")}</strong><small>条合成工单</small></div>
      <div className="wo-metric"><span>待研判</span><strong>{String(counts.open).padStart(2, "0")}</strong><small>等待工程师分析</small></div>
      <div className="wo-metric"><span>处理中</span><strong>{String(counts.investigating).padStart(2, "0")}</strong><small>需要持续跟进</small></div>
      <div className="wo-metric attention"><span>高优先级</span><strong>{String(counts.high).padStart(2, "0")}</strong><small>先核查证据完整度</small></div>
    </div>

    <div className="wo-layout">
      <section className="wo-queue-panel">
        <div className="wo-panel-head"><div><span className="panel-index">01</span><strong>现场工单队列</strong></div><span className="demo-pill">SYNTHETIC</span></div>
        <div className="wo-filters"><label className="wo-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工单、设备或告警码" /></label><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按工单状态筛选"><option value="">全部状态</option><option value="open">待研判</option><option value="investigating">处理中</option><option value="resolved">已解决</option></select></div>
        <div className="wo-queue-list">
          {loading && <div className="wo-empty"><RotateCw size={16} className="spin" />正在读取工单…</div>}
          {!loading && orders.map((item) => <button key={item.work_order_id} className={`wo-row ${selectedId === item.work_order_id ? "selected" : ""}`} onClick={() => { setSelectedId(item.work_order_id); setNotice(""); }}>
            <span className={`severity-rail ${item.severity}`} />
            <span className="wo-row-copy"><span className="wo-row-top"><code>{item.work_order_id}</code><span className={`status-tag ${item.status}`}>{STATUS_LABEL[item.status]}</span></span><strong>{item.alarm_code} <i>·</i> {item.equipment_model}</strong><small>{item.symptom}</small><span className="wo-row-meta"><span><Clock3 size={11} />{formatTime(item.occurred_at)}</span><span>{item.analysis_count ?? 0} 次分析</span><span className={`severity-text ${item.severity}`}>{SEVERITY_LABEL[item.severity]}优先级</span></span></span>
            <ChevronRight size={15} className="wo-row-chevron" />
          </button>)}
          {!loading && orders.length === 0 && <div className="wo-empty"><ClipboardList size={19} /><span>没有符合筛选条件的工单。</span></div>}
        </div>
        <div className="wo-queue-foot">只读数据源 · 刷新页面可重新载入</div>
      </section>

      <section className="wo-detail-panel">
        {selected ? <>
          <div className="wo-detail-head"><div><span className="section-kicker">WORK ORDER / {selected.work_order_id}</span><h2>{selected.alarm_code}<span className={`severity-dot ${selected.severity}`} /></h2><p>{selected.symptom}</p></div><span className={`status-tag ${selected.status}`}>{STATUS_LABEL[selected.status]}</span></div>
          <div className="wo-source-note"><span className="source-marker">DEMO</span>以下工单与设备数据为合成样例，仅演示只读接入、研判与审核流程。</div>

          <div className="wo-section-title"><span>设备与运行上下文</span><small>ASSET CONTEXT</small></div>
          <div className="wo-field-grid">
            <div><span>设备型号</span><strong>{selected.equipment_model}</strong></div><div><span>设备序列号</span><strong>{selected.serial_number}</strong></div>
            <div><span>固件版本</span><strong>{selected.firmware_version ?? <em>未提供</em>}</strong></div><div><span>告警时间</span><strong>{new Date(selected.occurred_at).toLocaleString("zh-CN")}</strong></div>
            {Object.entries(selected.operating_conditions).map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><strong>{value === null ? <em>未提供</em> : value}</strong></div>)}
          </div>

          <div className={`wo-completeness ${selected.missing_fields.length ? "incomplete" : "complete"}`}>
            {selected.missing_fields.length ? <TriangleAlert size={15} /> : <Check size={15} />}
            <div><strong>{selected.missing_fields.length ? `待补充 ${selected.missing_fields.length} 项现场信息` : "工单字段完整"}</strong><small>{selected.missing_fields.length ? selected.missing_fields.join(" · ") : "仍需由工程师核对原始记录。"}</small></div>
          </div>
          <div className="wo-attachments"><FileText size={14} /><span>关联材料</span>{selected.attachment_names.length ? selected.attachment_names.map((name) => <code key={name}>{name}</code>) : <em>未附材料</em>}</div>

          <ReviewedMemoryPanel workOrderId={selected.work_order_id} equipmentType={selected.equipment_model} symptom={selected.symptom} />

          <div className="wo-action-row"><div><span className="wo-section-caption">ANALYSIS QUEUE</span><strong>{currentDetail?.analyses.length ?? 0} 条关联分析</strong></div><button className="primary-button" onClick={startAnalysis} disabled={acting || !currentDetail}><Activity size={15} />{acting ? "正在提交…" : "发起辅助研判"}<ArrowUpRight size={14} /></button></div>

          {currentDetail?.analyses.map((analysis) => <button key={analysis.task.task_id} className={`wo-analysis-row ${selectedTaskId === analysis.task.task_id ? "active" : ""}`} onClick={() => setSelectedTaskId(analysis.task.task_id)}>
            <span className={`analysis-state ${analysis.task.status}`}><Activity size={13} /></span><span className="analysis-copy"><strong>{taskStatusLabel(analysis.task.status)}</strong><small>{analysis.task.task_id.slice(0, 12)} · 尝试 {analysis.task.attempt}/{analysis.task.max_attempts}{analysis.latest_review ? ` · ${VERDICT_LABEL[analysis.latest_review.verdict]}` : " · 待人工审核"}</small></span><ChevronRight size={14} />
          </button>)}

          {activeAnalysis && <div className="wo-review-area">
            <div className="wo-review-head"><div><span className="wo-section-caption">ENGINEER REVIEW</span><strong>分析复核</strong></div><span>{activeAnalysis.report ? `${activeAnalysis.report.evidence.length} 条引用证据` : taskStatusLabel(activeAnalysis.task.status)}</span></div>
            {activeAnalysis.report ? <>
              <div className="wo-report-preview"><span><Check size={13} />分析结果已生成</span><p>{activeAnalysis.report.result.hypotheses?.[0]?.hypothesis ?? "报告已就绪，请打开完整报告核验结论与引用。"}</p><Link to={`/tasks/${activeAnalysis.task.task_id}/report?workOrderId=${encodeURIComponent(selected.work_order_id)}`}>查看证据报告<ArrowUpRight size={13} /></Link></div>
              <form className="wo-review-form" onSubmit={submitReview}>
                <label>审核人<input required maxLength={120} value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="输入审核人姓名" /></label>
                <label>审核结论<select value={verdict} onChange={(event) => setVerdict(event.target.value as WorkOrderReview["verdict"])}><option value="accepted">接受建议</option><option value="needs_more_information">需补充信息</option><option value="rejected">不采纳</option></select></label>
                <label className="full-width">审核备注<textarea maxLength={2000} rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录支持或否决判断的依据、待补充数据等" /></label>
                <button className="secondary-button" type="submit" disabled={acting}><ShieldCheck size={14} />{acting ? "保存中…" : "保存审核记录"}</button>
              </form>
            </> : <div className="wo-pending-report">{activeAnalysis.task.status === "failed" ? <AlertTriangle size={15} /> : <Clock3 size={15} />}{activeAnalysis.task.status === "failed" ? "分析任务失败，当前没有可供审核的报告。" : "任务生成报告后，可在此查看证据并提交人工审核。"}<Link to={`/tasks/${activeAnalysis.task.task_id}?workOrderId=${encodeURIComponent(selected.work_order_id)}`}>查看任务进度<ArrowUpRight size={13} /></Link></div>}
            {activeAnalysis.review_history.length > 0 && <div className="wo-history"><div className="wo-section-title"><span>审核历史</span><small>APPEND ONLY</small></div>{activeAnalysis.review_history.map((item) => <div className="wo-history-row" key={`${item.task_id}-${item.version}`}><span className="history-version">V{item.version}</span><div><strong>{VERDICT_LABEL[item.verdict]} <small>· {item.reviewer}</small></strong>{item.note && <p>{item.note}</p>}<time>{new Date(item.reviewed_at).toLocaleString("zh-CN")}</time></div></div>)}</div>}
          </div>}
          {currentDetail && !currentDetail.analyses.length && <div className="wo-first-analysis"><AlertTriangle size={16} /><span>该工单尚无研判记录。发起分析前，请先确认设备型号、故障现象和可用现场数据。</span></div>}
        </> : <div className="wo-empty detail-empty"><ClipboardList size={24} /><span>选择一条工单查看现场上下文。</span></div>}
      </section>
    </div>
    {error && <div className="inline-alert" role="alert">{error}</div>}{notice && <div className="inline-notice">{notice}</div>}
    <div className="wo-safety-line"><ShieldCheck size={14} /><span>系统仅生成辅助分析与信息核对提示，不下发设备参数，不替代厂商手册、现场安全规程或合格工程师判断。</span></div>
  </div>;
}
