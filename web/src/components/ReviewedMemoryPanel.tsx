import { useEffect, useState } from "react";
import { ArrowUpRight, BookOpenCheck, CircleAlert, LoaderCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { searchMemory } from "../api/client";
import type { MemoryMatch } from "../types";

interface Props {
  workOrderId: string;
  equipmentType: string;
  symptom: string;
}

export default function ReviewedMemoryPanel({ workOrderId, equipmentType, symptom }: Props) {
  const [items, setItems] = useState<MemoryMatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void searchMemory(symptom, equipmentType, 5).then((response) => {
      if (current) setItems(response.synthetic_demo ? [] : response.items.filter((item) => !item.synthetic_demo));
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : "无法读取已复核历史案例。");
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [workOrderId, equipmentType, symptom]);

  return <section className="wo-memory-panel" aria-labelledby="wo-memory-title">
    <div className="wo-section-title"><span id="wo-memory-title"><BookOpenCheck size={13} /> 已复核历史案例</span><small>REVIEWED MEMORY</small></div>
    <p className="wo-memory-intro">仅检索工程师已接受且来源可追溯的历史关系；相似度不代表因果概率，也不构成设备操作指令。</p>
    {loading && <div className="wo-memory-empty"><LoaderCircle size={14} className="spin" />正在查找已复核案例…</div>}
    {!loading && error && <div className="wo-memory-empty error" role="alert"><CircleAlert size={14} />{error}</div>}
    {!loading && !error && !items.length && <div className="wo-memory-empty">暂无符合条件的已复核历史案例。</div>}
    {!loading && !error && items.map((item) => <article className="wo-memory-match" key={`${item.source_work_order_id}-${item.source_task_id}-${item.source_review_version}`}>
      <div className="wo-memory-case"><strong>{item.cause}</strong><span>{item.equipment_type} · {item.symptom}</span></div>
      <div className="wo-memory-provenance">来源工单 <code>{item.source_work_order_id}</code> · 复核版本 V{item.source_review_version}</div>
      {item.applicability_differences.length > 0 && <ul className="wo-memory-differences">{item.applicability_differences.map((difference) => <li key={difference}>{difference.includes("Equipment model differs") ? "设备型号与已复核案例不同。" : difference}</li>)}</ul>}
      {item.evidence_ids.length > 0 && <div className="wo-memory-evidence">支持证据 {item.evidence_ids.map((evidenceId) => <Link key={evidenceId} to={`/tasks/${encodeURIComponent(item.source_task_id)}/report?workOrderId=${encodeURIComponent(item.source_work_order_id)}`} aria-label={`打开来源报告查看证据 ${evidenceId}`}>{evidenceId}<ArrowUpRight size={11} /></Link>)}</div>}
    </article>)}
    <small className="wo-memory-caveat">检索相关性仅用于查找历史记录。请打开来源报告核验引用，再由工程师判断是否适用于当前工单。</small>
  </section>;
}
