import { ArrowUpRight, BookOpen, ExternalLink, X } from "lucide-react";
import type { Evidence } from "../types";

const applicabilityLabel = {
  direct: "目标设备直接匹配",
  related: "同系列参考",
  cross_equipment: "跨设备背景资料",
  unknown: "设备适用性未知",
} as const;

export default function EvidenceDrawer({ evidence, onClose }: { evidence: Evidence | null; onClose: () => void }) {
  if (!evidence) return null;
  return (
    <div className="drawer-backdrop" role="presentation" onClick={onClose}>
      <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title" onClick={(event) => event.stopPropagation()}>
        <div className="drawer-top"><span><BookOpen size={16} /> 来源证据</span><button className="icon-button" onClick={onClose} aria-label="关闭证据"><X size={18} /></button></div>
        <div className="drawer-content"><div className="source-type">{evidence.source_type.replaceAll("_", " ").toUpperCase()}</div><h2 id="evidence-title">{evidence.title}</h2><div className="source-locator">{evidence.locator || "来源定位未提供"}<span>·</span>{evidence.source_status === "available" ? "可访问" : evidence.source_status === "conflicting" ? "存在冲突" : "来源不可用"}</div><div className={`applicability-badge ${evidence.applicability ?? "unknown"}`}>{applicabilityLabel[evidence.applicability ?? "unknown"]}{evidence.equipment_type ? ` · ${evidence.equipment_type}` : ""}</div>
          <blockquote>{evidence.excerpt}</blockquote>
          {evidence.url && <a className="source-link" href={evidence.url} target="_blank" rel="noreferrer">打开原始来源<ExternalLink size={14} /></a>}
          <div className="source-meta"><span>证据 ID</span><code>{evidence.evidence_id}</code><span>采集时间</span><code>{new Date(evidence.accessed_at).toLocaleString("zh-CN")}</code></div>
          <p className="drawer-note"><ArrowUpRight size={13} />引用内容用于辅助判断，请核对原始文档与现场条件。</p>
        </div>
      </aside>
    </div>
  );
}
