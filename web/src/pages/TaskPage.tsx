import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, RotateCcw } from "lucide-react";
import { getTask, retryTask, streamTaskEvents } from "../api/client";
import TaskProgress from "../components/TaskProgress";
import type { TaskEvent, TaskRecord } from "../types";

export default function TaskPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);

  const loadTask = useCallback(async () => {
    try {
      setTask(await getTask(taskId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法加载任务。");
    }
  }, [taskId]);

  useEffect(() => {
    void loadTask();
    const controller = new AbortController();
    void streamTaskEvents(taskId, (event) => {
      setEvents((current) => event.event_id && current.some((item) => item.event_id === event.event_id) ? current : [...current, event]);
      if (["completed", "partial", "failed"].includes(event.status)) void loadTask();
      if (event.status === "reconnecting") setError(event.message);
      else setError("");
    }, controller.signal);
    return () => controller.abort();
  }, [taskId, loadTask]);

  async function retry() {
    setRetrying(true);
    setError("");
    try {
      const result = await retryTask(taskId);
      navigate(`/tasks/${result.task.task_id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重试失败。");
    } finally {
      setRetrying(false);
    }
  }

  if (!task) return <div className="loading-panel">{error || "正在读取任务记录……"}</div>;
  const terminal = ["completed", "partial", "failed"].includes(task.status);
  const isResearch = task.mode === "technical_research";
  return <div className="task-page"><Link className="back-link" to="/"><ArrowLeft size={15} />返回工作台</Link><div className="task-heading"><div><span className="section-kicker">TASK / {task.task_id.slice(0, 8).toUpperCase()}</span><h1>{isResearch ? "技术调研与实验设计" : "故障现象分析"}<span>.</span></h1><p>{String(isResearch ? task.inputs.question ?? "技术调研" : task.inputs.equipment_type ?? "变流器")} · {String(isResearch ? task.inputs.scope ?? "公开资料与本地文档" : task.inputs.symptom ?? "工程任务")}</p></div><span className="task-time">创建于 {new Date(task.created_at).toLocaleString("zh-CN")}</span></div>
    <TaskProgress status={task.status} events={events} />
    {error && <div className="inline-alert" role="alert">{error}</div>}
    <div className="task-actions">{terminal && task.status !== "failed" && <Link className="primary-button" to={`/tasks/${task.task_id}/report`}>查看分析报告<ArrowUpRight size={16} /></Link>}{(task.status === "failed" || task.status === "partial") && <button className="secondary-button" onClick={retry} disabled={retrying}><RotateCcw size={15} />{retrying ? "正在重试…" : "重试任务"}</button>}</div>
  </div>;
}
