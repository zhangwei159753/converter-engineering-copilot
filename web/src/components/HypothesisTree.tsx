import { AlertTriangle, ArrowDownRight, CircleCheck, CircleDashed, CircleX, FileQuestion } from "lucide-react";
import type { HypothesisBranch } from "../types";

const gateLabels: Record<HypothesisBranch["gate_status"], string> = {
  pending: "待审查",
  qualified: "通过证据门禁",
  blocked: "未通过门禁",
  review_incomplete: "审查未完成",
};

function GateIcon({ status }: { status: HypothesisBranch["gate_status"] }) {
  if (status === "qualified") return <CircleCheck size={14} aria-hidden="true" />;
  if (status === "blocked") return <CircleX size={14} aria-hidden="true" />;
  if (status === "review_incomplete") return <AlertTriangle size={14} aria-hidden="true" />;
  return <CircleDashed size={14} aria-hidden="true" />;
}

function EvidenceRefs({
  title,
  ids,
  evidenceIds,
  tone,
  onEvidence,
}: {
  title: string;
  ids: string[];
  evidenceIds: Set<string>;
  tone: "support" | "oppose";
  onEvidence: (id: string) => void;
}) {
  return <div className={`hypothesis-evidence ${tone}`}>
    <span className="hypothesis-evidence-title">{title}</span>
    {ids.length === 0 ? <small className="evidence-none">无引用</small> : <div className="evidence-ref-list">{ids.map((id) => evidenceIds.has(id)
      ? <button key={id} type="button" onClick={() => onEvidence(id)} aria-label={id}>{id}</button>
      : <span className="evidence-ref-missing" key={id}>{id} · 来源缺失</span>)}</div>}
  </div>;
}

export default function HypothesisTree({
  branches,
  evidenceIds,
  onEvidence,
}: {
  branches: HypothesisBranch[];
  evidenceIds: Set<string>;
  onEvidence: (id: string) => void;
}) {
  return <section className="hypothesis-panel" aria-labelledby="hypothesis-tree-title">
    <div className="agent-panel-head">
      <div><span className="panel-index">BRANCHES</span><h2 id="hypothesis-tree-title">候选原因与审查</h2></div>
      <span className="trace-count">{branches.length} / 3</span>
    </div>
    <p className="hypothesis-caution">证据门禁控制报告是否保留候选；结果用于工程审查，不替代现场规程。</p>
    {branches.length === 0
      ? <div className="hypothesis-empty"><FileQuestion size={17} /><span>没有候选原因通过证据门禁</span></div>
      : <ol className="hypothesis-tree">{branches.map((branch, index) => <li className={`hypothesis-branch ${branch.disposition}`} key={branch.branch_id}>
        <div className="branch-rail"><span>{String(index + 1).padStart(2, "0")}</span>{index < branches.length - 1 && <i aria-hidden="true" />}</div>
        <article className="branch-card">
          <div className="branch-card-head">
            <span className={`branch-gate ${branch.gate_status}`}><GateIcon status={branch.gate_status} />{gateLabels[branch.gate_status]}</span>
            <code>{branch.applicability === "direct" ? "目标设备" : branch.applicability === "cross_equipment" ? "跨设备" : branch.applicability === "related" ? "相关设备" : "适用性未知"}</code>
          </div>
          <h3>{branch.hypothesis}</h3>
          {branch.mechanism_summary && <p className="branch-mechanism">{branch.mechanism_summary}</p>}
          <div className="branch-links">
            <EvidenceRefs title="支持证据" ids={branch.supporting_evidence_ids} evidenceIds={evidenceIds} tone="support" onEvidence={onEvidence} />
            <EvidenceRefs title="反证" ids={branch.opposing_evidence_ids} evidenceIds={evidenceIds} tone="oppose" onEvidence={onEvidence} />
          </div>
          {(branch.missing_fields.length > 0 || branch.checks.length > 0) && <details className="branch-followup">
            <summary><ArrowDownRight size={13} />待核验信息与检查项</summary>
            <div>{branch.missing_fields.length > 0 && <p><strong>缺失信息</strong>{branch.missing_fields.join("、")}</p>}{branch.checks.length > 0 && <p><strong>建议核查</strong>{branch.checks.join("；")}</p>}</div>
          </details>}
        </article>
      </li>)}</ol>}
  </section>;
}
