import { AlertTriangle, Check, CircleDashed, Clock3, SkipForward } from "lucide-react";
import type { AgentRun } from "../types";

const roleNames: Record<AgentRun["role"], string> = {
  coordinator: "协调员",
  evidence_researcher: "证据检索员",
  hypothesis_analyst: "假设分析员",
  evidence_auditor: "证据审查员",
  report_synthesizer: "报告综合员",
};

const statusNames: Record<AgentRun["status"], string> = {
  queued: "排队中",
  running: "执行中",
  completed: "已完成",
  failed: "执行失败",
  skipped: "已跳过",
  incomplete: "未完成",
};

function StatusGlyph({ status }: { status: AgentRun["status"] }) {
  if (status === "completed") return <Check size={13} aria-hidden="true" />;
  if (status === "failed" || status === "incomplete") return <AlertTriangle size={13} aria-hidden="true" />;
  if (status === "skipped") return <SkipForward size={13} aria-hidden="true" />;
  if (status === "running") return <CircleDashed size={13} aria-hidden="true" className="agent-spin" />;
  return <Clock3 size={13} aria-hidden="true" />;
}

export default function AgentTimeline({ runs }: { runs: AgentRun[] }) {
  return <section className="agent-panel" aria-labelledby="agent-timeline-title">
    <div className="agent-panel-head">
      <div><span className="panel-index">TRACE</span><h2 id="agent-timeline-title">Agent 执行记录</h2></div>
      <span className="trace-count">{String(runs.length).padStart(2, "0")} RUNS</span>
    </div>
    {runs.length === 0
      ? <div className="agent-empty">暂无 Agent 执行记录</div>
      : <ol className="agent-timeline">{runs.map((run) => <li className={`agent-run ${run.status}`} key={run.agent_run_id}>
        <span className={`agent-run-marker ${run.status}`} aria-label={statusNames[run.status]}><StatusGlyph status={run.status} /></span>
        <div className="agent-run-body">
          <div className="agent-run-heading">
            <div><strong>{roleNames[run.role]}</strong><code>{run.stage}</code></div>
            <span className={`agent-status ${run.status}`}>{statusNames[run.status]}</span>
          </div>
          <p>{run.summary || "此阶段未返回摘要。"}</p>
          {(run.branch_id || run.output_refs.length > 0) && <div className="agent-run-refs">
            {run.branch_id && <code>分支 · {run.branch_id}</code>}
            {run.output_refs.slice(0, 4).map((ref) => <code key={ref}>{ref}</code>)}
            {run.output_refs.length > 4 && <small>+{run.output_refs.length - 4}</small>}
          </div>}
          <time dateTime={run.started_at}>{new Date(run.started_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
        </div>
      </li>)}</ol>}
  </section>;
}
