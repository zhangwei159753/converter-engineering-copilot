import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, RotateCcw } from "lucide-react";
import { getTask, retryTask, streamTaskEvents } from "../api/client";
import TaskProgress from "../components/TaskProgress";
import type { TaskEvent, TaskRecord } from "../types";

function safeFailureGuidance(errorCode: string | null): string {
  switch (errorCode) {
    case "attempt_limit":
      return "任务连续恢复次数已达上限，请检查本地服务日志后手动重试。";
    case "task_timeout":
      return "任务执行超时。可缩小资料范围后重试，并检查本地服务状态。";
    case "workflow_error":
      return "工作流执行失败。可检查服务配置和资料状态后重试。";
    default:
      return "任务未能完成。请检查本地服务状态后重试。";
  }
}

export default function TaskPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const workOrderId = searchParams.get("workOrderId");
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
      if (["completed", "partial", "failed"].includes(event.status) || event.stage === "recovered" || event.stage === "workflow") void loadTask();
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
      navigate(`/tasks/${result.task.task_id}${workOrderId ? `?workOrderId=${encodeURIComponent(workOrderId)}` : ""}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重试失败。");
    } finally {
      setRetrying(false);
    }
  }

  if (!task) return <div className="loading-panel">{error || "正在读取任务记录……"}</div>;
  const terminal = ["completed", "partial", "failed"].includes(task.status);
  const isResearch = task.mode === "technical_research";
  const isCase = task.mode === "case_retrieval";
  const modeTitle = isResearch ? "技术调研与实验设计" : isCase ? "检修案例检索" : "故障现象分析";
  return <div className="task-page"><Link className="back-link" to={workOrderId ? `/work-orders?selected=${encodeURIComponent(workOrderId)}` : "/"}><ArrowLeft size={15} />{workOrderId ? "返回来源工单" : "返回工作台"}</Link><div className="task-heading"><div><span className="section-kicker">TASK / {task.task_id.slice(0, 8).toUpperCase()}</span><h1>{modeTitle}<span>.</span></h1><p>{String(isResearch ? task.inputs.question ?? "技术调研" : isCase ? task.inputs.equipment_type ?? "历史案例库" : task.inputs.equipment_type ?? "变流器")} · {String(isResearch ? task.inputs.scope ?? "公开资料与本地文档" : task.inputs.symptom ?? "工程任务")}</p></div><span className="task-time">创建于 {new Date(task.created_at).toLocaleString("zh-CN")}</span></div>
    <TaskProgress status={task.status} events={events} attempt={task.attempt} maxAttempts={task.max_attempts} />
    {task.status === "failed" && <div className="inline-alert failure-guidance" role="status">{safeFailureGuidance(task.error_code)}</div>}
    {error && <div className="inline-alert" role="alert">{error}</div>}
    <div className="task-actions">{terminal && task.status !== "failed" && <Link className="primary-button" to={`/tasks/${task.task_id}/report${workOrderId ? `?workOrderId=${encodeURIComponent(workOrderId)}` : ""}`}>查看分析报告<ArrowUpRight size={16} /></Link>}{(task.status === "failed" || task.status === "partial") && <button className="secondary-button" onClick={retry} disabled={retrying}><RotateCcw size={15} />{retrying ? "正在重试…" : "重试任务"}</button>}</div>
  </div>;
}
