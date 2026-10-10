import { Check, Circle, LoaderCircle, Radio, X } from "lucide-react";
import type { TaskEvent, TaskStatus } from "../types";

const stages = [
  { id: "queued", label: "任务排队" },
  { id: "workflow", label: "信息检索" },
  { id: "generate", label: "证据分析" },
  { id: "complete", label: "报告整理" },
];

export default function TaskProgress({ status, events }: { status: TaskStatus; events: TaskEvent[] }) {
  const latest = events.at(-1);
  const terminal = ["completed", "partial", "failed"].includes(status);
  const activeIndex = status === "queued" ? 0 : status === "running" ? Math.max(1, stages.findIndex((stage) => stage.id === latest?.stage)) : 3;
  return (
    <div className="progress-panel">
      <div className="progress-heading"><div><span className="section-kicker">RUN / 实时执行</span><h2>任务进度</h2></div><span className={`status-pill ${status}`}><i />{status === "running" ? "执行中" : status === "queued" ? "排队中" : status === "failed" ? "失败" : status === "partial" ? "部分完成" : "已完成"}</span></div>
      <div className="progress-track">
        {stages.map((stage, index) => {
          const done = terminal || index < activeIndex;
          const active = !terminal && index === activeIndex;
          const Icon = status === "failed" && index === activeIndex ? X : done ? Check : active ? LoaderCircle : Circle;
          return <div className={`progress-step ${done ? "done" : ""} ${active ? "active" : ""}`} key={stage.id}><span className="step-icon"><Icon size={15} /></span><span>{stage.label}</span>{index < stages.length - 1 && <i className="step-line" />}</div>;
        })}
      </div>
      <div className="event-console"><div className="console-head"><span><Radio size={13} /> EXECUTION LOG</span><span>{events.length.toString().padStart(2, "0")} EVENTS</span></div>
        {events.length ? events.slice(-6).map((event) => <div className="event-row" key={event.event_id}><time>{new Date(event.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><span className="event-marker" /><span>{event.message}</span></div>) : <div className="empty-log">等待执行事件……</div>}
      </div>
    </div>
  );
}
