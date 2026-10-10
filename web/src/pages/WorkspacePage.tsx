import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, FilePlus2, Gauge, Lightbulb, UploadCloud, Zap } from "lucide-react";
import { createTask, importCases, listTasks } from "../api/client";
import ModeForm from "../components/ModeForm";
import type { TaskRecord } from "../types";

function shortDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export default function WorkspacePage() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    listTasks().then(setTasks).catch(() => setTasks([]));
  }, []);

  async function submit(mode: "fault_analysis" | "technical_research", input: Record<string, unknown>) {
    setSubmitting(true);
    setError("");
    try {
      const { task } = await createTask({ mode, inputs: input });
      navigate(`/tasks/${task.task_id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务创建失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  async function upload(file?: File) {
    if (!file) return;
    setNotice("");
    setError("");
    try {
      const result = await importCases(file);
      setNotice(`已导入 ${result.chunk_count} 个文档片段、${result.imported_case_count} 条案例。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文档导入失败。");
    }
  }

  return (
    <div className="workspace-page">
      <div className="workspace-heading"><div><div className="breadcrumb"><span>工作空间</span><ArrowDownRight size={13} /><span>分析工作台</span></div><h1>把现场现象，变成可核验的判断<span>.</span></h1><p>针对变流器研发与运维的证据驱动分析工作台</p></div><div className="heading-stamp"><span>WORKSPACE</span><strong>01</strong><small>POWER CONVERSION SYSTEMS</small></div></div>
      <div className="workspace-layout"><section className="primary-workspace"><div className="panel-caption"><div><span className="panel-index">A</span><span>新建工程任务</span></div><span className="caption-right"><i />系统就绪</span></div><ModeForm onSubmit={submit} submitting={submitting} />{error && <div className="inline-alert" role="alert">{error}</div>}{notice && <div className="inline-notice">{notice}</div>}</section>
        <aside className="workspace-aside"><div className="metric-card"><div className="metric-icon"><Gauge size={17} /></div><span className="section-kicker">SESSION STATUS</span><strong>工作空间已就绪</strong><p>本地数据库与检索目录可用，任务运行后会保留进度与报告。</p><div className="metric-footer"><span><i className="live-dot" />LOCAL MODE</span><span>01 / 03 MODES</span></div></div>
          <div className="aside-section"><div className="aside-title"><span>最近任务</span><span className="count-badge">{tasks.length.toString().padStart(2, "0")}</span></div>{tasks.length ? <div className="task-history">{tasks.slice(0, 5).map((task) => <button className="history-row" key={task.task_id} onClick={() => navigate(`/tasks/${task.task_id}`)}><span className={`history-mark ${task.status}`}><Zap size={13} /></span><span className="history-copy"><strong>{String(task.inputs.symptom ?? "变流器分析任务")}</strong><small>{task.mode === "fault_analysis" ? "故障现象分析" : task.mode} · {shortDate(task.created_at)}</small></span><ArrowUpRight size={14} /></button>)}</div> : <div className="history-empty"><span className="empty-dash">—</span><p>这里会显示最近执行的工程任务。</p></div>}</div>
          <label className="import-card"><span className="import-icon"><UploadCloud size={18} /></span><span className="import-copy"><strong>导入技术资料</strong><small>PDF · Markdown · CSV</small></span><FilePlus2 size={16} /><input type="file" accept=".pdf,.md,.csv" aria-label="导入技术资料" onChange={(event) => upload(event.target.files?.[0])} /></label>
          <div className="tip-card"><Lightbulb size={16} /><p><strong>分析建议</strong><br />补充负载、电网与环境信息，有助于排除工况相关因素。</p></div>
        </aside>
      </div>
    </div>
  );
}
